from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Self, TypeVar
from urllib.parse import quote, urljoin, urlparse

import httpx
from pydantic import SecretStr

from release_intelligence.adapters.github.mapper import (
    GitHubPayloadError,
    map_check,
    map_comparison,
    map_item,
    map_milestone,
    map_pull_request,
    map_timeline_event,
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
        self._api_origin = self._origin(str(self._client.base_url))
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
        return tuple(event for event in events if event is not None)

    async def get_pull_request(
        self, repo: RepoRef, pull_number: int
    ) -> GitHubPullRequest:
        payload = await self._get_json(
            f"{self._repo_path(repo)}/pulls/{pull_number}"
        )
        return self._map_one(payload, map_pull_request)

    async def list_checks_for_ref(
        self, repo: RepoRef, ref: str
    ) -> tuple[GitHubCheck, ...]:
        return await self._paginate_keyed_list(
            f"{self._repo_path(repo)}/commits/{quote(ref, safe='')}/check-runs",
            key="check_runs",
            params={"per_page": 100},
            mapper=map_check,
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
        next_url: str | None = path
        next_params: dict[str, QueryValue] | None = {"per_page": 100}
        for page in range(1, MAX_PAGES + 1):
            response, payload = await self._request(next_url, params=next_params)
            current = self._map_one(payload, map_comparison)
            if comparison is None:
                comparison = current
            elif replace(current, commits=()) != replace(comparison, commits=()):
                raise GitHubPartialData()
            commits.extend(current.commits)
            try:
                next_url = self._next_link(response)
            except GitHubPayloadError:
                raise GitHubPartialData() from None
            if next_url is None:
                if comparison.total_commits != len(commits):
                    raise GitHubPartialData()
                return replace(comparison, commits=tuple(commits))
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
    ) -> tuple[T, ...]:
        def select(payload: object) -> list[object]:
            if not isinstance(payload, dict):
                raise GitHubPayloadError("invalid GitHub payload")
            return self._require_list(payload.get(key))

        return await self._paginate(path, params=params, items=select, mapper=mapper)

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
        self._raise_for_status(response.status_code)
        payload: object | None = None
        parse_failure: GitHubPartialData | None = None
        try:
            payload = response.json()
        except (ValueError, TypeError):
            parse_failure = GitHubPartialData()
        if parse_failure is not None:
            raise parse_failure
        return response, payload

    def _raise_for_status(self, status_code: int) -> None:
        if status_code in (403, 429):
            raise GitHubRateLimited()
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

    def _next_link(self, response: httpx.Response) -> str | None:
        try:
            target = response.links.get("next", {}).get("url")
        except (KeyError, TypeError, ValueError):
            raise GitHubPayloadError("invalid GitHub pagination") from None
        if target is None:
            return None
        if not isinstance(target, str):
            raise GitHubPayloadError("invalid GitHub pagination")
        absolute = urljoin(str(response.request.url), target)
        if self._origin(absolute) != self._api_origin:
            raise GitHubPayloadError("invalid GitHub pagination")
        return absolute

    @staticmethod
    def _origin(url: str) -> tuple[str, str, int | None]:
        parsed = urlparse(url)
        return parsed.scheme, parsed.hostname or "", parsed.port

    @staticmethod
    def _require_list(payload: object) -> list[object]:
        if not isinstance(payload, list):
            raise GitHubPayloadError("invalid GitHub payload")
        return payload

    @staticmethod
    def _map_one(payload: object, mapper: Callable[[object], T]) -> T:
        try:
            return mapper(payload)
        except (GitHubPayloadError, TypeError, ValueError, AttributeError):
            raise GitHubPartialData() from None
