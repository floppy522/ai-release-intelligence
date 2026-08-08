from unittest.mock import Mock

import pytest

from release_intelligence.adapters.persistence.policies import PolicyRepository
from release_intelligence.domain.policy import CheckCategory, ReleasePolicy
from release_intelligence.ports.policies import PolicyPersistenceError


async def test_repository_revalidates_policy_before_opening_transaction() -> None:
    valid = ReleasePolicy(
        main_branch="main",
        candidate_branch="release/2026-08-10",
        milestone_number=7,
        code_change_label="code-change",
        release_ops_label="release-ops",
        blocker_label="release-blocker",
        check_categories={"api": CheckCategory.BLOCKING},
    )
    unchecked = valid
    object.__setattr__(unchecked, "candidate_branch", "main")
    repository = object.__new__(PolicyRepository)
    sessions = Mock(side_effect=AssertionError("transaction opened"))
    repository._sessions = sessions

    with pytest.raises(PolicyPersistenceError):
        await repository.create_version(
            repository_id="987654",
            policy=unchecked,
            expected_version=None,
        )

    sessions.assert_not_called()


async def test_repository_wraps_serializer_failures_before_transaction() -> None:
    policy = ReleasePolicy(
        main_branch="main",
        candidate_branch="release/2026-08-10",
        milestone_number=7,
        code_change_label="code-change",
        release_ops_label="release-ops",
        blocker_label="release-blocker",
        check_categories={"api": CheckCategory.BLOCKING},
    )
    object.__setattr__(policy, "check_categories", {"api": object()})
    repository = object.__new__(PolicyRepository)
    sessions = Mock(side_effect=AssertionError("transaction opened"))
    repository._sessions = sessions

    with pytest.raises(PolicyPersistenceError):
        await repository.create_version(
            repository_id="987654", policy=policy, expected_version=None
        )
    sessions.assert_not_called()
