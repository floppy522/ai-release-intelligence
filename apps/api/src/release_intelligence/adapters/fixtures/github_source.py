from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from release_intelligence.domain.models import EvidenceRef, ReleaseSnapshot
from release_intelligence.ports.github import (
    CommitComparison,
    GitHubCheck,
    GitHubIssueTimelineEvent,
    GitHubItem,
    GitHubMilestone,
    GitHubNotFound,
    GitHubPullRequest,
    RepoRef,
)

E2E_REPOSITORY = RepoRef(owner="floppy522", name="ai-release-intelligence-demo")
E2E_REPOSITORY_ID = "987654"
E2E_INSTALLATION_ID = 424242
E2E_MILESTONE_NUMBER = 7
E2E_CANDIDATE_REF = "release/2026-08-10"
E2E_CANDIDATE_SHA = "a" * 40


class FixtureGitHubSource:
    """Bounded deterministic GitHub source enabled only by the E2E composition root."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        now = self._now()
        self._created_at = now - timedelta(minutes=2)
        self._updated_at = now - timedelta(minutes=1)

    async def get_milestone(self, repo: RepoRef, milestone: int) -> GitHubMilestone:
        self._require_repository(repo)
        if milestone != E2E_MILESTONE_NUMBER:
            raise GitHubNotFound()
        return GitHubMilestone(
            source_id=str(E2E_MILESTONE_NUMBER),
            number=E2E_MILESTONE_NUMBER,
            url=self._url(f"milestone/{E2E_MILESTONE_NUMBER}"),
            state="open",
            created_at=self._created_at,
            updated_at=self._updated_at,
            due_on=None,
        )

    async def list_milestone_items(
        self, repo: RepoRef, milestone: int
    ) -> tuple[GitHubItem, ...]:
        await self.get_milestone(repo, milestone)
        return ()

    async def list_issue_timeline(
        self, repo: RepoRef, issue_number: int
    ) -> tuple[GitHubIssueTimelineEvent, ...]:
        self._require_repository(repo)
        del issue_number
        raise GitHubNotFound()

    async def get_pull_request(
        self, repo: RepoRef, pull_number: int
    ) -> GitHubPullRequest:
        self._require_repository(repo)
        del pull_number
        raise GitHubNotFound()

    async def resolve_ref(self, repo: RepoRef, ref: str) -> str:
        self._require_repository(repo)
        if ref != E2E_CANDIDATE_REF:
            raise GitHubNotFound()
        return E2E_CANDIDATE_SHA

    async def list_checks_for_ref(
        self, repo: RepoRef, ref: str
    ) -> tuple[GitHubCheck, ...]:
        self._require_repository(repo)
        if ref != E2E_CANDIDATE_SHA:
            raise GitHubNotFound()
        return (
            self._check(7000, "blocking-suite", "success"),
            self._check(7001, "advisory-tests", "failure"),
        )

    async def compare_commits(
        self, repo: RepoRef, base: str, head: str
    ) -> CommitComparison:
        self._require_repository(repo)
        del base, head
        raise GitHubNotFound()

    def _check(self, run_id: int, name: str, conclusion: str) -> GitHubCheck:
        return GitHubCheck(
            source_id=str(run_id),
            run_id=run_id,
            name=name,
            url=self._url(f"runs/{run_id}"),
            head_sha=E2E_CANDIDATE_SHA,
            status="completed",
            conclusion=conclusion,
            started_at=self._created_at,
            completed_at=self._updated_at,
        )

    @staticmethod
    def _require_repository(repo: RepoRef) -> None:
        if repo != E2E_REPOSITORY:
            raise GitHubNotFound()

    @staticmethod
    def _url(path: str) -> str:
        return "https://github.com/floppy522/ai-release-intelligence-demo/" + path

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value.astimezone(UTC)


def load_demo_release() -> ReleaseSnapshot:
    issue_evidence = EvidenceRef(
        evidence_id="github-issue-142",
        source_type="github_issue",
        source_id="142",
        url="https://github.com/example/release-demo/issues/142",
        fingerprint="github:issue:142",
    )
    return ReleaseSnapshot(
        release_name="Release 2026.08.10",
        issue_number="142",
        milestone_number=7,
        issue_labels=("code-change",),
        linked_pr_numbers=(),
        issue_evidence=issue_evidence,
        repository_id="fixture:demo",
        repository_full_name="example/release-demo",
    )
