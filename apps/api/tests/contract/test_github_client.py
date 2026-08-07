from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from release_intelligence.adapters.github.client import GitHubRestClient
from release_intelligence.ports.github import (
    GitHubNotFound,
    GitHubPartialData,
    GitHubRateLimited,
    GitHubUnauthorized,
    RepoRef,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "github"
REPO = RepoRef(owner="octo-fixtures", name="release-demo")
TOKEN = SecretStr("github-installation-token")


def _fixture(name: str) -> object:
    return json.loads((FIXTURES / name).read_text())


def _response(
    request: httpx.Request,
    status: int,
    payload: object,
    *,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return httpx.Response(status, json=payload, headers=headers, request=request)


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[GitHubRestClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(handler),
        timeout=httpx.Timeout(10.0),
    )
    return GitHubRestClient(token=TOKEN, client=http), http


async def test_client_follows_rfc_link_and_preserves_latest_rate_limit() -> None:
    source = _fixture("milestone_items.json")
    assert isinstance(source, list)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.params.get("page") == "2":
            return _response(
                request,
                200,
                [source[1]],
                headers={
                    "X-RateLimit-Remaining": "4997",
                    "X-RateLimit-Reset": "1786125600",
                },
            )
        return _response(
            request,
            200,
            [source[0]],
            headers={
                "Link": (
                    '<https://api.github.com/repos/octo-fixtures/release-demo/issues'
                    '?milestone=7&state=all&per_page=100&page=9>; rel="last", '
                    '</repos/octo-fixtures/release-demo/issues?milestone=7&state=all'
                    '&per_page=100&page=2>; rel="next"'
                ),
                "X-RateLimit-Remaining": "4998",
                "X-RateLimit-Reset": "1786125500",
            },
        )

    client, http = _client(handler)
    try:
        items = await client.list_milestone_items(REPO, 7)
    finally:
        await http.aclose()

    assert [(item.number, item.kind.value) for item in items] == [
        (141, "ISSUE"),
        (142, "PULL_REQUEST"),
    ]
    assert client.rate_limit.remaining == 4997
    assert client.rate_limit.reset_at == datetime.fromtimestamp(1786125600, UTC)
    assert len(requests) == 2
    assert requests[0].method == "GET"
    assert requests[0].headers["Accept"] == "application/vnd.github+json"
    assert requests[0].headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert requests[0].headers["Authorization"] == f"Bearer {TOKEN.get_secret_value()}"


async def test_get_milestone_maps_only_normalized_fields() -> None:
    payload = {
        "id": 7000007,
        "number": 7,
        "html_url": "https://github.com/octo-fixtures/release-demo/milestone/7",
        "state": "open",
        "created_at": "2026-08-01T08:00:00Z",
        "updated_at": "2026-08-07T08:00:00Z",
        "due_on": "2026-08-10T12:00:00Z",
    }
    client, http = _client(lambda request: _response(request, 200, payload))
    try:
        milestone = await client.get_milestone(REPO, 7)
    finally:
        await http.aclose()

    assert milestone.source_id == "7000007"
    assert milestone.number == 7
    assert milestone.state == "open"
    assert milestone.due_on == datetime(2026, 8, 10, 12, tzinfo=UTC)
    assert not hasattr(milestone, "title")
    assert not hasattr(milestone, "description")


async def test_issue_timeline_maps_only_pull_request_cross_references() -> None:
    payload = _fixture("issue_timeline.json")
    client, http = _client(lambda request: _response(request, 200, payload))
    try:
        events = await client.list_issue_timeline(REPO, 141)
    finally:
        await http.aclose()

    assert len(events) == 1
    assert events[0].source_id == "9200001"
    assert events[0].pull_request_number == 142
    assert events[0].pull_request_url.endswith("/pull/142")


async def test_pull_request_fixture_maps_refs_shas_and_timestamps() -> None:
    payload = _fixture("pull_request.json")
    client, http = _client(lambda request: _response(request, 200, payload))
    try:
        pull = await client.get_pull_request(REPO, 142)
    finally:
        await http.aclose()

    assert pull.number == 142
    assert pull.labels == ("code-change",)
    assert pull.assignees == ("lee-api",)
    assert pull.milestone_number == 7
    assert pull.head_ref == "feature/payment-retry"
    assert pull.base_ref == "main"
    assert pull.merge_commit_sha == "3" * 40
    assert pull.merged_at == datetime(2026, 8, 6, 15, tzinfo=UTC)


async def test_check_fixture_maps_runs_without_logs() -> None:
    payload = _fixture("check_runs.json")
    client, http = _client(lambda request: _response(request, 200, payload))
    try:
        checks = await client.list_checks_for_ref(REPO, "release/2026-08-10")
    finally:
        await http.aclose()

    assert [(check.name, check.conclusion) for check in checks] == [
        ("api", "success"),
        ("advisory-browser", "failure"),
    ]
    assert checks[0].run_id == 9400001
    assert checks[0].head_sha == "4" * 40
    assert not hasattr(checks[0], "output")
    assert not hasattr(checks[0], "logs")


async def test_compare_fixture_maps_commit_evidence() -> None:
    payload = _fixture("compare_commits.json")
    client, http = _client(lambda request: _response(request, 200, payload))
    try:
        comparison = await client.compare_commits(
            REPO, "main", "release/2026-08-10"
        )
    finally:
        await http.aclose()

    assert comparison.status == "ahead"
    assert comparison.ahead_by == 2
    assert comparison.behind_by == 0
    assert [commit.sha for commit in comparison.commits] == ["3" * 40, "4" * 40]
    assert comparison.commits[0].committed_at == datetime(
        2026, 8, 6, 15, tzinfo=UTC
    )


async def test_compare_follows_pagination_and_returns_complete_commit_set() -> None:
    payload = _fixture("compare_commits.json")
    assert isinstance(payload, dict)
    commits = payload["commits"]
    assert isinstance(commits, list)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        page_payload = {**payload}
        if request.url.params.get("page") == "2":
            page_payload["commits"] = [commits[1]]
            return _response(request, 200, page_payload)
        page_payload["commits"] = [commits[0]]
        return _response(
            request,
            200,
            page_payload,
            headers={"Link": '</compare/result?per_page=100&page=2>; rel="next"'},
        )

    client, http = _client(handler)
    try:
        comparison = await client.compare_commits(
            REPO, "main", "release/2026-08-10"
        )
    finally:
        await http.aclose()

    assert len(requests) == 2
    assert [commit.sha for commit in comparison.commits] == ["3" * 40, "4" * 40]


async def test_paths_encode_repository_segments_and_refs() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "/check-runs" in request.url.path:
            return _response(request, 200, {"total_count": 0, "check_runs": []})
        return _response(
            request,
            200,
            {
                "status": "identical",
                "ahead_by": 0,
                "behind_by": 0,
                "total_commits": 0,
                "html_url": "https://github.com/octo-fixtures/release-demo/compare/a...b",
                "base_commit": {"sha": "1" * 40, "html_url": "https://github.com/x"},
                "merge_base_commit": {
                    "sha": "1" * 40,
                    "html_url": "https://github.com/x",
                },
                "commits": [],
            },
        )

    client, http = _client(handler)
    unusual_repo = RepoRef(owner="space org", name="repo/name")
    try:
        await client.list_checks_for_ref(unusual_repo, "release/2026-08-10#candidate")
        await client.compare_commits(unusual_repo, "main:stable", "release/next")
    finally:
        await http.aclose()

    assert requests[0].url.raw_path.decode().startswith(
        "/repos/space%20org/repo%2Fname/commits/release%2F2026-08-10%23candidate/check-runs"
    )
    assert requests[1].url.raw_path.decode().split("?", 1)[0] == (
        "/repos/space%20org/repo%2Fname/compare/main%3Astable...release%2Fnext"
    )


async def test_pagination_stops_at_twenty_pages() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        page = int(request.url.params.get("page", "1"))
        return _response(
            request,
            200,
            [],
            headers={
                "Link": (
                    f"</repos/octo-fixtures/release-demo/issues?milestone=7&state=all"
                    f'&per_page=100&page={page + 1}>; rel="next"'
                )
            },
        )

    client, http = _client(handler)
    try:
        with pytest.raises(GitHubPartialData, match="incomplete"):
            await client.list_milestone_items(REPO, 7)
    finally:
        await http.aclose()

    assert len(requests) == 20


@pytest.mark.parametrize("status", [403, 429])
async def test_rate_limit_takes_precedence_and_stops_immediately(status: int) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _response(
            request,
            status,
            {"message": "token github-installation-token exhausted"},
            headers={
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": "1786125600",
                "Link": '</never-requested?page=2>; rel="next"',
            },
        )

    client, http = _client(handler)
    try:
        with pytest.raises(GitHubRateLimited, match="rate limited") as raised:
            await client.list_milestone_items(REPO, 7)
    finally:
        await http.aclose()

    assert len(requests) == 1
    assert client.rate_limit.remaining == 0
    assert TOKEN.get_secret_value() not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.parametrize(
    ("status", "error_type", "message"),
    [
        (401, GitHubUnauthorized, "unauthorized"),
        (404, GitHubNotFound, "not found"),
        (500, GitHubPartialData, "incomplete"),
    ],
)
async def test_http_failures_map_to_sanitized_typed_errors(
    status: int,
    error_type: type[Exception],
    message: str,
) -> None:
    client, http = _client(
        lambda request: _response(
            request,
            status,
            {"message": "github-installation-token internal diagnostic"},
            headers={"X-RateLimit-Remaining": "41", "X-RateLimit-Reset": "bad"},
        )
    )
    try:
        with pytest.raises(error_type, match=message) as raised:
            await client.get_milestone(REPO, 7)
    finally:
        await http.aclose()

    assert client.rate_limit.remaining == 41
    assert client.rate_limit.reset_at is None
    assert TOKEN.get_secret_value() not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.parametrize(
    "payload",
    [
        {"not": "a list"},
        [{"id": "wrong-type", "number": 141}],
        [{"id": 1, "number": 141, "labels": [{"name": 42}]}],
    ],
)
async def test_malformed_milestone_items_fail_closed(payload: object) -> None:
    client, http = _client(lambda request: _response(request, 200, payload))
    try:
        with pytest.raises(GitHubPartialData, match="incomplete"):
            await client.list_milestone_items(REPO, 7)
    finally:
        await http.aclose()


async def test_second_page_failure_rejects_partial_collection() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("page") == "2":
            return _response(request, 502, {"message": "upstream failed"})
        source = _fixture("milestone_items.json")
        assert isinstance(source, list)
        return _response(
            request,
            200,
            [source[0]],
            headers={"Link": '</items?page=2>; rel="next"'},
        )

    client, http = _client(handler)
    try:
        with pytest.raises(GitHubPartialData, match="incomplete"):
            await client.list_milestone_items(REPO, 7)
    finally:
        await http.aclose()


async def test_transport_failure_is_sanitized_partial_data() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(
            "github-installation-token timed out", request=request
        )

    client, http = _client(handler)
    try:
        with pytest.raises(GitHubPartialData, match="incomplete") as raised:
            await client.get_milestone(REPO, 7)
    finally:
        await http.aclose()

    assert TOKEN.get_secret_value() not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_contract_fixtures_contain_only_allowlisted_evidence_fields() -> None:
    forbidden = {
        "body",
        "body_text",
        "comment",
        "comments",
        "content",
        "diff",
        "files",
        "log",
        "logs",
        "output",
        "patch",
        "source_code",
        "text",
    }

    def keys(value: object) -> set[str]:
        if isinstance(value, dict):
            return set(value).union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value))
        return set()

    for path in sorted(FIXTURES.glob("*.json")):
        assert forbidden.isdisjoint(keys(_fixture(path.name))), path.name
