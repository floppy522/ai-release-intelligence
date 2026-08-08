from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol


class GitHubError(Exception):
    """A caller-safe GitHub integration failure."""


class GitHubRateLimited(GitHubError):
    def __init__(self, reset_at: datetime | None = None) -> None:
        super().__init__("GitHub request was rate limited")
        self.reset_at = reset_at


class GitHubUnauthorized(GitHubError):
    def __init__(self) -> None:
        super().__init__("GitHub request was unauthorized")


class GitHubNotFound(GitHubError):
    def __init__(self) -> None:
        super().__init__("GitHub resource was not found")


class GitHubPartialData(GitHubError):
    def __init__(self) -> None:
        super().__init__("GitHub data is incomplete")


class GitHubResponse(Protocol):
    @property
    def status_code(self) -> int: ...

    @property
    def headers(self) -> Mapping[str, str]: ...

    def raise_for_status(self) -> None: ...

    def json(self) -> Mapping[str, object]: ...


class GitHubHttpClient(Protocol):
    async def post(self, path: str, **kwargs: object) -> GitHubResponse: ...

    async def get(self, path: str, **kwargs: object) -> GitHubResponse: ...


@dataclass(frozen=True, slots=True)
class RepoRef:
    owner: str
    name: str


@dataclass(frozen=True, slots=True)
class GitHubRateLimit:
    remaining: int | None = None
    reset_at: datetime | None = None


class GitHubItemKind(StrEnum):
    ISSUE = "ISSUE"
    PULL_REQUEST = "PULL_REQUEST"


@dataclass(frozen=True, slots=True)
class GitHubMilestone:
    source_id: str
    number: int
    url: str
    state: str
    created_at: datetime
    updated_at: datetime
    due_on: datetime | None


@dataclass(frozen=True, slots=True)
class GitHubItem:
    source_id: str
    number: int
    kind: GitHubItemKind
    url: str
    state: str
    labels: tuple[str, ...]
    assignees: tuple[str, ...]
    milestone_number: int | None
    created_at: datetime
    updated_at: datetime
    body: str = ""


@dataclass(frozen=True, slots=True)
class GitHubIssueTimelineEvent:
    source_id: str
    source_repository: RepoRef
    pull_request_number: int
    pull_request_url: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class GitHubPullRequest:
    source_id: str
    number: int
    url: str
    state: str
    labels: tuple[str, ...]
    assignees: tuple[str, ...]
    milestone_number: int | None
    head_ref: str
    head_sha: str
    base_ref: str
    base_sha: str
    merge_commit_sha: str | None
    merged_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class GitHubCheck:
    source_id: str
    run_id: int
    name: str
    url: str
    head_sha: str
    status: str
    conclusion: str | None
    started_at: datetime | None
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class GitHubCommit:
    sha: str
    url: str
    committed_at: datetime


@dataclass(frozen=True, slots=True)
class CommitComparison:
    status: str
    ahead_by: int
    behind_by: int
    total_commits: int
    url: str
    base_sha: str
    merge_base_sha: str
    commits: tuple[GitHubCommit, ...]
    head_sha: str = ""


class GitHubSource(Protocol):
    async def get_milestone(
        self, repo: RepoRef, milestone: int
    ) -> GitHubMilestone: ...

    async def list_milestone_items(
        self, repo: RepoRef, milestone: int
    ) -> tuple[GitHubItem, ...]: ...

    async def list_issue_timeline(
        self, repo: RepoRef, issue_number: int
    ) -> tuple[GitHubIssueTimelineEvent, ...]: ...

    async def get_pull_request(
        self, repo: RepoRef, pull_number: int
    ) -> GitHubPullRequest: ...

    async def resolve_ref(self, repo: RepoRef, ref: str) -> str: ...

    async def list_checks_for_ref(
        self, repo: RepoRef, ref: str
    ) -> tuple[GitHubCheck, ...]: ...

    async def compare_commits(
        self, repo: RepoRef, base: str, head: str
    ) -> CommitComparison: ...
