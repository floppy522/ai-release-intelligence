from __future__ import annotations

from collections.abc import Callable, Hashable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Self, TypeVar
from urllib.parse import (
    SplitResult,
    parse_qsl,
    quote,
    unquote,
    urlencode,
    urljoin,
    urlsplit,
    urlunsplit,
)

import httpx
from pydantic import SecretStr

from release_intelligence.adapters.github.mapper import (
    GitHubPayloadError,
    map_check,
    map_commit_sha,
    map_comparison,
    map_item,
    map_milestone,
    map_pull_request,
    map_timeline_event,
)
from release_intelligence.adapters.github.rate_limits import (
    secondary_rate_limit,
    valid_retry_after,
)
from release_intelligence.ports.github import (
    CommitComparison,
    GitHubCheck,
    GitHubCommit,
    GitHubIssueTimelineEvent,
    GitHubItem,
    GitHubMilestone,
    GitHubNotFound,
    GitHubPartialData,
    GitHubPullRequest,
    GitHubRateLimit,
    GitHubRateLimited,
    GitHubUnauthorized,
    RepoRef,
)

T = TypeVar("T")
QueryValue = str | int | float | bool | None
MAX_PAGES = 20
API_VERSION = "2022-11-28"
API_BASE_URL = "https://api.github.com"


class GitHubRestClient:
    """Read-only GitHub REST adapter.

    The default client owns a 10-second connect/read timeout. An injected
    ``httpx.AsyncClient`` remains caller-owned and must apply an equivalent or
    stricter timeout at its shared lifecycle boundary.
    """

    def __init__(
        self,
        *,
        token: SecretStr,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = token
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=API_BASE_URL,
            timeout=httpx.Timeout(10.0, connect=10.0, read=10.0),
        )
        try:
            self._api_origin = self._origin(str(self._client.base_url))
        except GitHubPayloadError:
            raise GitHubPartialData() from None
        self.rate_limit = GitHubRateLimit()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owned_client:
            await self._client.aclose()

    async def get_milestone(
        self, repo: RepoRef, milestone: int
    ) -> GitHubMilestone:
        payload = await self._get_json(
            f"{self._repo_path(repo)}/milestones/{milestone}"
        )
        return self._map_one(payload, map_milestone)

    async def list_milestone_items(
        self, repo: RepoRef, milestone: int
    ) -> tuple[GitHubItem, ...]:
        return await self._paginate_list(
            f"{self._repo_path(repo)}/issues",
            params={"milestone": milestone, "state": "all", "per_page": 100},
            mapper=map_item,
        )

    async def list_issue_timeline(
        self, repo: RepoRef, issue_number: int
    ) -> tuple[GitHubIssueTimelineEvent, ...]:
        events = await self._paginate_list(
            f"{self._repo_path(repo)}/issues/{issue_number}/timeline",
            params={"per_page": 100},
            mapper=map_timeline_event,
        )
        return tuple(
            event
            for event in events
            if event is not None
            and self._same_repository(event.source_repository, repo)
        )

    async def get_pull_request(
        self, repo: RepoRef, pull_number: int
    ) -> GitHubPullRequest:
        payload = await self._get_json(
            f"{self._repo_path(repo)}/pulls/{pull_number}"
        )
        return self._map_one(payload, map_pull_request)

    async def resolve_ref(self, repo: RepoRef, ref: str) -> str:
        payload = await self._get_json(
            f"{self._repo_path(repo)}/commits/{quote(ref, safe='')}"
        )
        return self._map_one(payload, map_commit_sha)

    async def list_checks_for_ref(
        self, repo: RepoRef, ref: str
    ) -> tuple[GitHubCheck, ...]:
        return await self._paginate_keyed_list(
            f"{self._repo_path(repo)}/commits/{quote(ref, safe='')}/check-runs",
            key="check_runs",
            params={"per_page": 100},
            mapper=map_check,
            identity=lambda check: check.run_id,
        )

    async def compare_commits(
        self, repo: RepoRef, base: str, head: str
    ) -> CommitComparison:
        path = (
            f"{self._repo_path(repo)}/compare/"
            f"{quote(base, safe='')}...{quote(head, safe='')}"
        )
        comparison: CommitComparison | None = None
        commits: list[GitHubCommit] = []
        seen_commit_shas: set[str] = set()
        seen_page_urls: set[str] = set()
        next_url: str | None = path
        next_params: dict[str, QueryValue] | None = {"per_page": 100}
        for page in range(1, MAX_PAGES + 1):
            response, payload = await self._request(next_url, params=next_params)
            try:
                page_url = self._canonical_url(str(response.request.url))
            except GitHubPayloadError:
                raise GitHubPartialData() from None
            if page_url in seen_page_urls:
                raise GitHubPartialData()
            seen_page_urls.add(page_url)
            current = self._map_one(payload, map_comparison)
            if comparison is None:
                comparison = current
            elif replace(current, commits=()) != replace(comparison, commits=()):
                raise GitHubPartialData()
            for commit in current.commits:
                if commit.sha in seen_commit_shas:
                    raise GitHubPartialData()
                seen_commit_shas.add(commit.sha)
                commits.append(commit)
            try:
                next_url = self._next_link(response)
            except GitHubPayloadError:
                raise GitHubPartialData() from None
            if next_url is None:
                if comparison.total_commits != len(seen_commit_shas):
                    raise GitHubPartialData()
                return replace(comparison, commits=tuple(commits))
            try:
                if self._canonical_url(next_url) in seen_page_urls:
                    raise GitHubPartialData()
            except GitHubPayloadError:
                raise GitHubPartialData() from None
            if page == MAX_PAGES:
                raise GitHubPartialData()
            next_params = None
        raise GitHubPartialData()

    def _repo_path(self, repo: RepoRef) -> str:
        return f"/repos/{quote(repo.owner, safe='')}/{quote(repo.name, safe='')}"

    async def _paginate_list(
        self,
        path: str,
        *,
        params: dict[str, QueryValue],
        mapper: Callable[[object], T],
    ) -> tuple[T, ...]:
        return await self._paginate(
            path,
            params=params,
            items=lambda payload: self._require_list(payload),
            mapper=mapper,
        )

    async def _paginate_keyed_list(
        self,
        path: str,
        *,
        key: str,
        params: dict[str, QueryValue],
        mapper: Callable[[object], T],
        identity: Callable[[T], Hashable],
    ) -> tuple[T, ...]:
        result: list[T] = []
        seen: set[Hashable] = set()
        expected_total: int | None = None
        next_url: str | None = path
        next_params: dict[str, QueryValue] | None = params
        for page in range(1, MAX_PAGES + 1):
            response, payload = await self._request(next_url, params=next_params)
            try:
                if not isinstance(payload, dict):
                    raise GitHubPayloadError("invalid GitHub payload")
                page_total = self._require_count(payload.get("total_count"))
                if expected_total is None:
                    expected_total = page_total
                elif page_total != expected_total:
                    raise GitHubPayloadError("inconsistent GitHub total")
                for raw_item in self._require_list(payload.get(key)):
                    mapped = mapper(raw_item)
                    item_identity = identity(mapped)
                    if item_identity in seen:
                        raise GitHubPayloadError("duplicate GitHub item")
                    seen.add(item_identity)
                    result.append(mapped)
                if len(seen) > expected_total:
                    raise GitHubPayloadError("invalid GitHub total")
                next_url = self._next_link(response)
            except (GitHubPayloadError, TypeError, ValueError, AttributeError):
                raise GitHubPartialData() from None
            if next_url is None:
                if len(seen) != expected_total:
                    raise GitHubPartialData()
                return tuple(result)
            if page == MAX_PAGES:
                raise GitHubPartialData()
            next_params = None
        raise GitHubPartialData()

    async def _paginate(
        self,
        path: str,
        *,
        params: dict[str, QueryValue],
        items: Callable[[object], list[object]],
        mapper: Callable[[object], T],
    ) -> tuple[T, ...]:
        result: list[T] = []
        next_url: str | None = path
        next_params: dict[str, QueryValue] | None = params
        for page in range(1, MAX_PAGES + 1):
            response, payload = await self._request(next_url, params=next_params)
            try:
                result.extend(mapper(item) for item in items(payload))
                next_url = self._next_link(response)
            except (GitHubPayloadError, TypeError, ValueError, AttributeError):
                raise GitHubPartialData() from None
            if next_url is None:
                return tuple(result)
            if page == MAX_PAGES:
                raise GitHubPartialData()
            next_params = None
        raise GitHubPartialData()

    async def _get_json(self, path: str) -> object:
        _, payload = await self._request(path, params=None)
        return payload

    async def _request(
        self, path: str | None, *, params: dict[str, QueryValue] | None
    ) -> tuple[httpx.Response, object]:
        if path is None:
            raise GitHubPartialData()
        response: httpx.Response | None = None
        transport_failure: GitHubPartialData | None = None
        try:
            response = await self._client.get(
                path,
                params=params,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {self._token.get_secret_value()}",
                    "X-GitHub-Api-Version": API_VERSION,
                },
            )
        except httpx.HTTPError:
            transport_failure = GitHubPartialData()
        if transport_failure is not None:
            raise transport_failure
        assert response is not None
        self._capture_rate_limit(response.headers)
        self._raise_for_status(response)
        payload: object | None = None
        parse_failure: GitHubPartialData | None = None
        try:
            payload = response.json()
        except (ValueError, TypeError):
            parse_failure = GitHubPartialData()
        if parse_failure is not None:
            raise parse_failure
        return response, payload

    def _raise_for_status(self, response: httpx.Response) -> None:
        status_code = response.status_code
        if status_code == 429:
            raise GitHubRateLimited(self.rate_limit.reset_at)
        if status_code == 403:
            if self.rate_limit.remaining == 0 or valid_retry_after(
                response.headers.get("Retry-After")
            ) or self._has_secondary_rate_limit_message(response):
                raise GitHubRateLimited(self.rate_limit.reset_at)
            raise GitHubUnauthorized()
        if status_code == 401:
            raise GitHubUnauthorized()
        if status_code == 404:
            raise GitHubNotFound()
        if status_code < 200 or status_code >= 300:
            raise GitHubPartialData()

    def _capture_rate_limit(self, headers: httpx.Headers) -> None:
        self.rate_limit = GitHubRateLimit(
            remaining=self._nonnegative_int(headers.get("X-RateLimit-Remaining")),
            reset_at=self._reset_at(headers.get("X-RateLimit-Reset")),
        )

    @staticmethod
    def _nonnegative_int(value: str | None) -> int | None:
        try:
            parsed = int(value) if value is not None else None
        except ValueError:
            return None
        return parsed if parsed is not None and parsed >= 0 else None

    @staticmethod
    def _reset_at(value: str | None) -> datetime | None:
        seconds = GitHubRestClient._nonnegative_int(value)
        if seconds is None:
            return None
        try:
            return datetime.fromtimestamp(seconds, UTC)
        except (OverflowError, OSError, ValueError):
            return None

    @staticmethod
    def _has_secondary_rate_limit_message(response: httpx.Response) -> bool:
        payload: object | None = None
        try:
            payload = response.json()
        except (TypeError, ValueError):
            return False
        return secondary_rate_limit(payload)

    def _next_link(self, response: httpx.Response) -> str | None:
        try:
            target = response.links.get("next", {}).get("url")
            if target is None:
                return None
            if not isinstance(target, str):
                raise GitHubPayloadError("invalid GitHub pagination")
            absolute = urljoin(str(response.request.url), target)
            if self._origin(absolute) != self._api_origin:
                raise GitHubPayloadError("invalid GitHub pagination")
            return absolute
        except (GitHubPayloadError, KeyError, TypeError, UnicodeError, ValueError):
            raise GitHubPayloadError("invalid GitHub pagination") from None

    @classmethod
    def _origin(cls, url: str) -> tuple[str, str, int | None]:
        parsed = cls._validated_url(url)
        port = parsed.port
        if (parsed.scheme.lower(), port) in (("http", 80), ("https", 443)):
            port = None
        return parsed.scheme.lower(), parsed.hostname or "", port

    @classmethod
    def _canonical_url(cls, url: str) -> str:
        parsed = cls._validated_url(url)
        hostname = parsed.hostname or ""
        default_port = (parsed.scheme.lower(), parsed.port) in (
            ("http", 80),
            ("https", 443),
        )
        port = "" if parsed.port is None or default_port else f":{parsed.port}"
        netloc = f"{hostname}{port}"
        query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
        return urlunsplit(
            (parsed.scheme.lower(), netloc, parsed.path or "/", query, "")
        )

    @staticmethod
    def _validated_url(url: str) -> SplitResult:
        try:
            if not url or GitHubRestClient._invalid_percent_encoding(url):
                raise GitHubPayloadError("invalid GitHub URL")
            if any(character.isspace() for character in url):
                raise GitHubPayloadError("invalid GitHub URL")
            decoded = unquote(url)
            if any(ord(character) < 32 for character in decoded):
                raise GitHubPayloadError("invalid GitHub URL")
            if any(ord(character) == 127 for character in decoded):
                raise GitHubPayloadError("invalid GitHub URL")
            parsed = urlsplit(url)
            scheme = parsed.scheme.lower()
            hostname = parsed.hostname
            port = parsed.port
            if (
                scheme not in ("http", "https")
                or hostname is None
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
            ):
                raise GitHubPayloadError("invalid GitHub URL")
            if port is not None and not 1 <= port <= 65535:
                raise GitHubPayloadError("invalid GitHub URL")
            labels = hostname.split(".")
            if any(
                not label
                or len(label) > 63
                or label.startswith("-")
                or label.endswith("-")
                or not label.isascii()
                or not all(
                    character.isalnum() or character == "-" for character in label
                )
                for label in labels
            ):
                raise GitHubPayloadError("invalid GitHub URL")
            parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
            return parsed
        except (GitHubPayloadError, TypeError, UnicodeError, ValueError):
            raise GitHubPayloadError("invalid GitHub URL") from None

    @staticmethod
    def _invalid_percent_encoding(url: str) -> bool:
        hexadecimal = frozenset("0123456789abcdefABCDEF")
        for index, character in enumerate(url):
            if character == "%" and (
                index + 2 >= len(url)
                or url[index + 1] not in hexadecimal
                or url[index + 2] not in hexadecimal
            ):
                return True
        return False

    @staticmethod
    def _require_list(payload: object) -> list[object]:
        if not isinstance(payload, list):
            raise GitHubPayloadError("invalid GitHub payload")
        return payload

    @staticmethod
    def _require_count(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise GitHubPayloadError("invalid GitHub total")
        return value

    @staticmethod
    def _same_repository(left: RepoRef, right: RepoRef) -> bool:
        return (left.owner.casefold(), left.name.casefold()) == (
            right.owner.casefold(),
            right.name.casefold(),
        )

    @staticmethod
    def _map_one(payload: object, mapper: Callable[[object], T]) -> T:
        try:
            return mapper(payload)
        except (GitHubPayloadError, TypeError, ValueError, AttributeError):
            raise GitHubPartialData() from None
