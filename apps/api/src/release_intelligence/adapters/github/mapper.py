from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Never
from urllib.parse import unquote, urlparse

from release_intelligence.ports.github import (
    CommitComparison,
    GitHubCheck,
    GitHubCommit,
    GitHubIssueTimelineEvent,
    GitHubItem,
    GitHubItemKind,
    GitHubMilestone,
    GitHubPullRequest,
    RepoRef,
)


class GitHubPayloadError(ValueError):
    """An internal marker for malformed or unsafe upstream payloads."""


def _invalid() -> Never:
    raise GitHubPayloadError("invalid GitHub payload")


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        return _invalid()
    return value


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        return _invalid()
    return value


def _string(value: object, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value:
        return _invalid()
    return value


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return _invalid()
    return value


def _timestamp(value: object, *, optional: bool = False) -> datetime | None:
    raw = _string(value, optional=optional)
    if raw is None:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return _invalid()
    if parsed.tzinfo is None:
        return _invalid()
    return parsed.astimezone(UTC)


def _github_url(value: object) -> str:
    raw = _string(value)
    assert raw is not None
    parsed = urlparse(raw)
    if parsed.scheme != "https" or parsed.hostname != "github.com":
        return _invalid()
    return raw


def _labels(value: object) -> tuple[str, ...]:
    labels: list[str] = []
    for item in _list(value):
        name = _string(_mapping(item).get("name"))
        assert name is not None
        labels.append(name)
    return tuple(labels)


def _assignees(value: object) -> tuple[str, ...]:
    assignees: list[str] = []
    for item in _list(value):
        login = _string(_mapping(item).get("login"))
        assert login is not None
        assignees.append(login)
    return tuple(assignees)


def _milestone_number(value: object) -> int | None:
    if value is None:
        return None
    return _integer(_mapping(value).get("number"))


def map_milestone(payload: object) -> GitHubMilestone:
    item = _mapping(payload)
    return GitHubMilestone(
        source_id=str(_integer(item.get("id"))),
        number=_integer(item.get("number")),
        url=_github_url(item.get("html_url")),
        state=_required_string(item.get("state")),
        created_at=_required_timestamp(item.get("created_at")),
        updated_at=_required_timestamp(item.get("updated_at")),
        due_on=_timestamp(item.get("due_on"), optional=True),
    )


def map_item(payload: object) -> GitHubItem:
    item = _mapping(payload)
    kind = (
        GitHubItemKind.PULL_REQUEST
        if isinstance(item.get("pull_request"), Mapping)
        else GitHubItemKind.ISSUE
    )
    return GitHubItem(
        source_id=str(_integer(item.get("id"))),
        number=_integer(item.get("number")),
        kind=kind,
        url=_github_url(item.get("html_url")),
        state=_required_string(item.get("state")),
        labels=_labels(item.get("labels")),
        assignees=_assignees(item.get("assignees")),
        milestone_number=_milestone_number(item.get("milestone")),
        created_at=_required_timestamp(item.get("created_at")),
        updated_at=_required_timestamp(item.get("updated_at")),
    )


def map_timeline_event(payload: object) -> GitHubIssueTimelineEvent | None:
    event = _mapping(payload)
    if event.get("event") != "cross-referenced":
        return None
    source = _mapping(event.get("source"))
    if source.get("type") != "issue":
        return None
    issue = _mapping(source.get("issue"))
    if not isinstance(issue.get("pull_request"), Mapping):
        return None
    pull_request_number = _integer(issue.get("number"))
    pull_request_url = _github_url(issue.get("html_url"))
    return GitHubIssueTimelineEvent(
        source_id=str(_integer(event.get("id"))),
        source_repository=_pull_request_repository(
            pull_request_url, pull_request_number
        ),
        pull_request_number=pull_request_number,
        pull_request_url=pull_request_url,
        created_at=_required_timestamp(event.get("created_at")),
    )


def _pull_request_repository(url: str, pull_request_number: int) -> RepoRef:
    parsed = urlparse(url)
    segments = parsed.path.strip("/").split("/")
    if parsed.query or parsed.fragment or len(segments) != 4 or segments[2] != "pull":
        return _invalid()
    owner, name = unquote(segments[0]), unquote(segments[1])
    if not owner or not name or "/" in owner or "/" in name:
        return _invalid()
    try:
        url_number = int(segments[3])
    except ValueError:
        return _invalid()
    if url_number != pull_request_number:
        return _invalid()
    return RepoRef(owner=owner, name=name)


def map_pull_request(payload: object) -> GitHubPullRequest:
    item = _mapping(payload)
    head = _mapping(item.get("head"))
    base = _mapping(item.get("base"))
    merge_commit_sha = _string(item.get("merge_commit_sha"), optional=True)
    return GitHubPullRequest(
        source_id=str(_integer(item.get("id"))),
        number=_integer(item.get("number")),
        url=_github_url(item.get("html_url")),
        state=_required_string(item.get("state")),
        labels=_labels(item.get("labels")),
        assignees=_assignees(item.get("assignees")),
        milestone_number=_milestone_number(item.get("milestone")),
        head_ref=_required_string(head.get("ref")),
        head_sha=_required_string(head.get("sha")),
        base_ref=_required_string(base.get("ref")),
        base_sha=_required_string(base.get("sha")),
        merge_commit_sha=merge_commit_sha,
        merged_at=_timestamp(item.get("merged_at"), optional=True),
        created_at=_required_timestamp(item.get("created_at")),
        updated_at=_required_timestamp(item.get("updated_at")),
    )


def map_check(payload: object) -> GitHubCheck:
    item = _mapping(payload)
    run_id = _integer(item.get("id"))
    return GitHubCheck(
        source_id=str(run_id),
        run_id=run_id,
        name=_required_string(item.get("name")),
        url=_github_url(item.get("html_url")),
        head_sha=_required_string(item.get("head_sha")),
        status=_required_string(item.get("status")),
        conclusion=_string(item.get("conclusion"), optional=True),
        started_at=_timestamp(item.get("started_at"), optional=True),
        completed_at=_timestamp(item.get("completed_at"), optional=True),
    )


def map_comparison(payload: object) -> CommitComparison:
    item = _mapping(payload)
    base_commit = _mapping(item.get("base_commit"))
    merge_base_commit = _mapping(item.get("merge_base_commit"))
    commits = tuple(map_commit(commit) for commit in _list(item.get("commits")))
    total = _integer(item.get("total_commits"))
    if total < len(commits):
        return _invalid()
    return CommitComparison(
        status=_required_string(item.get("status")),
        ahead_by=_integer(item.get("ahead_by")),
        behind_by=_integer(item.get("behind_by")),
        total_commits=total,
        url=_github_url(item.get("html_url")),
        base_sha=_required_string(base_commit.get("sha")),
        merge_base_sha=_required_string(merge_base_commit.get("sha")),
        commits=commits,
    )


def map_commit(payload: object) -> GitHubCommit:
    item = _mapping(payload)
    commit = _mapping(item.get("commit"))
    committer = _mapping(commit.get("committer"))
    return GitHubCommit(
        sha=_required_string(item.get("sha")),
        url=_github_url(item.get("html_url")),
        committed_at=_required_timestamp(committer.get("date")),
    )


def map_commit_sha(payload: object) -> str:
    item = _mapping(payload)
    sha = _required_string(item.get("sha"))
    if len(sha) != 40 or any(
        character not in "0123456789abcdefABCDEF" for character in sha
    ):
        return _invalid()
    return sha.lower()


def _required_string(value: object) -> str:
    result = _string(value)
    assert result is not None
    return result


def _required_timestamp(value: object) -> datetime:
    result = _timestamp(value)
    assert result is not None
    return result
