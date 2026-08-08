"""Real PostgreSQL contracts for atomic, append-only human decisions."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from release_intelligence.adapters.persistence.policies import PolicyRepository
from release_intelligence.adapters.persistence.repositories import AnalysisRepository
from release_intelligence.application.decisions import (
    DecisionFindingNotFoundError,
    DecisionKind,
    DecisionPersistenceError,
    DecisionService,
)
from release_intelligence.domain.models import (
    EvidenceRef,
    ReadinessAssessment,
    ReleaseSnapshot,
    ReleaseStatus,
    SnapshotVersion,
)
from release_intelligence.domain.policy import CheckCategory, ReleasePolicy
from release_intelligence.domain.rules.checks import evaluate_checks
from release_intelligence.ports.github import GitHubCheck

API_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 7, 14, 30, tzinfo=UTC)
REPOSITORY_ID = "987654"


def _require_database_url() -> str:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        raise RuntimeError(
            "DATABASE_URL is required for PostgreSQL decision integration tests"
        )
    if not database_url.startswith(("postgresql://", "postgresql+asyncpg://")):
        raise RuntimeError("DATABASE_URL must use a PostgreSQL URL")
    return database_url


def _asyncpg_url(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


@pytest.fixture(scope="session")
def database_url() -> str:
    return _require_database_url()


@pytest.fixture(scope="session", autouse=True)
def migrated_database(database_url: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=API_ROOT,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture
async def postgres(database_url: str) -> AsyncIterator[asyncpg.Connection]:
    connection = await asyncpg.connect(_asyncpg_url(database_url))
    await connection.execute("TRUNCATE TABLE repository_connections CASCADE")
    try:
        yield connection
    finally:
        await connection.close()


@pytest.fixture
async def repository(database_url: str) -> AsyncIterator[AnalysisRepository]:
    store = AnalysisRepository(database_url, clock=lambda: NOW)
    try:
        yield store
    finally:
        await store.close()


def policy() -> ReleasePolicy:
    return ReleasePolicy(
        main_branch="main",
        candidate_branch="release/2026-08-10",
        milestone_number=7,
        code_change_label="code-change",
        release_ops_label="release-ops",
        blocker_label="release-blocker",
        check_categories={
            "api": CheckCategory.BLOCKING,
            "security": CheckCategory.ADVISORY,
        },
    )


def snapshot() -> ReleaseSnapshot:
    checks = (
        GitHubCheck(
            source_id="101",
            run_id=101,
            name="api",
            url="https://github.com/acme/widgets/runs/101",
            head_sha="a" * 40,
            status="completed",
            conclusion="failure",
            started_at=NOW,
            completed_at=NOW,
        ),
        GitHubCheck(
            source_id="201",
            run_id=201,
            name="security",
            url="https://github.com/acme/widgets/runs/201",
            head_sha="a" * 40,
            status="completed",
            conclusion="failure",
            started_at=NOW,
            completed_at=NOW,
        ),
    )
    return ReleaseSnapshot(
        release_name="Milestone 7",
        issue_number="7",
        milestone_number=7,
        issue_labels=(),
        linked_pr_numbers=(),
        issue_evidence=EvidenceRef(
            "milestone-7",
            "github_milestone",
            "7",
            "https://github.com/acme/widgets/milestone/7",
            "github:milestone:7",
        ),
        snapshot_version=SnapshotVersion.GITHUB_V1,
        repository_id=REPOSITORY_ID,
        repository_full_name="acme/widgets",
        fetch_started_at=NOW,
        fetched_at=NOW,
        candidate_ref="release/2026-08-10",
        candidate_sha="a" * 40,
        checks=checks,
    )


@pytest.fixture
async def decision_run(
    database_url: str,
    repository: AnalysisRepository,
    postgres: asyncpg.Connection,
) -> tuple[UUID, UUID, UUID, str]:
    release_snapshot = snapshot()
    configured_policy = policy()
    findings = evaluate_checks(release_snapshot, configured_policy, decisions=())
    assessment = ReadinessAssessment(
        status=ReleaseStatus.NOT_READY,
        findings=findings,
    )
    await repository.create_run(
        snapshot=release_snapshot,
        findings=findings,
        assessment=assessment,
        policy_version="bootstrap-v1",
        source_fetched_at=NOW,
    )
    policy_store = PolicyRepository(database_url)
    try:
        record = await policy_store.create_version(
            repository_id=REPOSITORY_ID,
            policy=configured_policy,
            expected_version=None,
        )
    finally:
        await policy_store.close()
    run_id = await repository.create_run(
        snapshot=release_snapshot,
        findings=findings,
        assessment=assessment,
        policy_version=f"configuration:{record.version}",
        source_fetched_at=NOW,
    )
    rows = await postgres.fetch(
        "SELECT id, rule_id FROM readiness_findings "
        "WHERE analysis_run_id = $1 ORDER BY position",
        run_id,
    )
    blocking_id = next(
        row["id"] for row in rows if row["rule_id"].startswith("checks.blocking")
    )
    advisory_id = next(
        row["id"] for row in rows if row["rule_id"].startswith("checks.advisory")
    )
    fingerprint = next(
        finding.evidence[0].fingerprint
        for finding in findings
        if finding.rule_id == "checks.advisory_requires_decision"
    )
    return run_id, advisory_id, blocking_id, fingerprint


async def test_changed_decision_appends_lineage_and_persists_each_reassessment(
    repository: AnalysisRepository,
    postgres: asyncpg.Connection,
    decision_run: tuple[UUID, UUID, UUID, str],
) -> None:
    run_id, finding_id, _blocking_id, fingerprint = decision_run
    service = DecisionService(clock=lambda: NOW, store=repository)

    accepted = await service.record_for_run(
        run_id=run_id,
        finding_id=finding_id,
        fingerprint=fingerprint,
        kind=DecisionKind.ACCEPTED_RISK,
        reason="Reviewed by release lead",
        actor="github:7",
        authorized_repository_id=REPOSITORY_ID,
    )
    blocked = await service.record_for_run(
        run_id=run_id,
        finding_id=finding_id,
        fingerprint=fingerprint,
        kind=DecisionKind.RELEASE_BLOCKER,
        reason="New evidence requires remediation",
        actor="github:8",
        authorized_repository_id=REPOSITORY_ID,
    )

    rows = await postgres.fetch(
        "SELECT id, decision, reason, actor_id, supersedes_decision_id, "
        "decision_sequence, assessment_status, assessment_payload "
        "FROM human_decisions WHERE analysis_run_id = $1 "
        "ORDER BY decision_sequence",
        run_id,
    )
    assert len(rows) == 2
    assert rows[0]["supersedes_decision_id"] is None
    assert rows[1]["supersedes_decision_id"] == rows[0]["id"]
    assert [row["decision_sequence"] for row in rows] == [1, 2]
    assert rows[0]["decision"] == "ACCEPTED_RISK"
    assert rows[0]["reason"] == "Reviewed by release lead"
    assert rows[0]["actor_id"] == "github:7"
    assert rows[1]["decision"] == "RELEASE_BLOCKER"
    assert rows[0]["assessment_status"] == accepted.assessment.status.value
    assert rows[1]["assessment_status"] == blocked.assessment.status.value
    assert rows[0]["assessment_payload"] != rows[1]["assessment_payload"]
    stored = await repository.get_run(run_id)
    assert stored.assessment == blocked.assessment
    assert stored.findings == blocked.assessment.findings
    with pytest.raises(asyncpg.PostgresError, match="immutable analysis records"):
        await postgres.execute(
            "UPDATE human_decisions SET reason = 'rewritten' WHERE id = $1",
            rows[0]["id"],
        )
    with pytest.raises(asyncpg.PostgresError, match="immutable analysis records"):
        await postgres.execute(
            "DELETE FROM human_decisions WHERE id = $1", rows[0]["id"]
        )


async def test_decision_and_reassessment_roll_back_together(
    repository: AnalysisRepository,
    postgres: asyncpg.Connection,
    decision_run: tuple[UUID, UUID, UUID, str],
) -> None:
    run_id, finding_id, _blocking_id, fingerprint = decision_run
    await postgres.execute(
        """
        CREATE FUNCTION reject_test_decision_insert() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'test decision assessment failure';
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER reject_test_decision_insert
        BEFORE INSERT ON human_decisions
        FOR EACH ROW EXECUTE FUNCTION reject_test_decision_insert();
        """
    )
    try:
        with pytest.raises(
            DecisionPersistenceError, match="Decision persistence unavailable"
        ):
            await DecisionService(clock=lambda: NOW, store=repository).record_for_run(
                run_id=run_id,
                finding_id=finding_id,
                fingerprint=fingerprint,
                kind=DecisionKind.ACCEPTED_RISK,
                reason="Reviewed",
                actor="github:7",
                authorized_repository_id=REPOSITORY_ID,
            )
    finally:
        await postgres.execute(
            "DROP TRIGGER IF EXISTS reject_test_decision_insert ON human_decisions;"
            "DROP FUNCTION IF EXISTS reject_test_decision_insert();"
        )
    assert (
        await postgres.fetchval(
            "SELECT count(*) FROM human_decisions WHERE analysis_run_id = $1", run_id
        )
        == 0
    )


async def test_concurrent_decisions_form_one_serial_lineage(
    database_url: str,
    postgres: asyncpg.Connection,
    decision_run: tuple[UUID, UUID, UUID, str],
) -> None:
    run_id, finding_id, _blocking_id, fingerprint = decision_run
    repositories = [
        AnalysisRepository(database_url, clock=lambda: NOW),
        AnalysisRepository(database_url, clock=lambda: NOW),
    ]
    try:
        await asyncio.gather(
            DecisionService(clock=lambda: NOW, store=repositories[0]).record_for_run(
                run_id=run_id,
                finding_id=finding_id,
                fingerprint=fingerprint,
                kind=DecisionKind.ACCEPTED_RISK,
                reason="Reviewer one",
                actor="github:7",
                authorized_repository_id=REPOSITORY_ID,
            ),
            DecisionService(clock=lambda: NOW, store=repositories[1]).record_for_run(
                run_id=run_id,
                finding_id=finding_id,
                fingerprint=fingerprint,
                kind=DecisionKind.RELEASE_BLOCKER,
                reason="Reviewer two",
                actor="github:8",
                authorized_repository_id=REPOSITORY_ID,
            ),
        )
    finally:
        await asyncio.gather(*(repository.close() for repository in repositories))

    rows = await postgres.fetch(
        "SELECT id, supersedes_decision_id, decision_sequence FROM human_decisions "
        "WHERE analysis_run_id = $1",
        run_id,
    )
    assert len(rows) == 2
    roots = [row for row in rows if row["supersedes_decision_id"] is None]
    children = [row for row in rows if row["supersedes_decision_id"] is not None]
    assert len(roots) == len(children) == 1
    assert children[0]["supersedes_decision_id"] == roots[0]["id"]
    assert sorted(row["decision_sequence"] for row in rows) == [1, 2]


async def test_repository_retention_cascade_removes_a_two_decision_lineage(
    repository: AnalysisRepository,
    postgres: asyncpg.Connection,
    decision_run: tuple[UUID, UUID, UUID, str],
) -> None:
    run_id, finding_id, _blocking_id, fingerprint = decision_run
    service = DecisionService(clock=lambda: NOW, store=repository)
    await service.record_for_run(
        run_id=run_id,
        finding_id=finding_id,
        fingerprint=fingerprint,
        kind=DecisionKind.ACCEPTED_RISK,
        reason="Reviewed by release lead",
        actor="github:7",
        authorized_repository_id=REPOSITORY_ID,
    )
    await service.record_for_run(
        run_id=run_id,
        finding_id=finding_id,
        fingerprint=fingerprint,
        kind=DecisionKind.RELEASE_BLOCKER,
        reason="New evidence requires remediation",
        actor="github:8",
        authorized_repository_id=REPOSITORY_ID,
    )
    latest_id = await postgres.fetchval(
        "SELECT id FROM human_decisions WHERE analysis_run_id = $1 "
        "ORDER BY decision_sequence DESC LIMIT 1",
        run_id,
    )

    with pytest.raises(asyncpg.PostgresError, match="immutable analysis records"):
        await postgres.execute(
            "DELETE FROM human_decisions WHERE id = $1", latest_id
        )

    await postgres.execute(
        "DELETE FROM repository_connections "
        "WHERE external_repository_id = $1",
        REPOSITORY_ID,
    )

    assert await postgres.fetchval("SELECT count(*) FROM human_decisions") == 0
    assert await postgres.fetchval("SELECT count(*) FROM repository_connections") == 0


@pytest.mark.parametrize("case", ["unknown", "stale", "noneligible", "repository"])
async def test_unknown_stale_noneligible_and_repository_mismatch_fail_closed(
    repository: AnalysisRepository,
    postgres: asyncpg.Connection,
    decision_run: tuple[UUID, UUID, UUID, str],
    case: str,
) -> None:
    run_id, finding_id, blocking_id, fingerprint = decision_run
    if case == "unknown":
        finding_id = uuid4()
    elif case == "stale":
        fingerprint = "sha256:" + "b" * 64
    elif case == "noneligible":
        finding_id = blocking_id
        fingerprint = await postgres.fetchval(
            "SELECT fingerprint FROM finding_evidence WHERE finding_id = $1",
            blocking_id,
        )
    repository_id = "other-repository" if case == "repository" else REPOSITORY_ID

    with pytest.raises(DecisionFindingNotFoundError):
        await DecisionService(clock=lambda: NOW, store=repository).record_for_run(
            run_id=run_id,
            finding_id=finding_id,
            fingerprint=fingerprint,
            kind=DecisionKind.ACCEPTED_RISK,
            reason="Reviewed",
            actor="github:7",
            authorized_repository_id=repository_id,
        )
    assert (
        await postgres.fetchval(
            "SELECT count(*) FROM human_decisions WHERE analysis_run_id = $1", run_id
        )
        == 0
    )
