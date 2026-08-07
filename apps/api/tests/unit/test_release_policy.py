import pytest

from release_intelligence.domain.policy import (
    CheckCategory,
    PolicyValidationError,
    ReleasePolicy,
    UnknownCheckPolicyError,
    validate_check_policy,
)

BASE_POLICY = {
    "main_branch": "main",
    "candidate_branch": "release/2026-08-10",
    "milestone_number": 7,
    "code_change_label": "code-change",
    "release_ops_label": "release-ops",
    "blocker_label": "release-blocker",
    "check_categories": {
        "api": CheckCategory.BLOCKING,
        "security": CheckCategory.ADVISORY,
    },
}


def test_policy_rejects_duplicate_issue_type_labels() -> None:
    with pytest.raises(PolicyValidationError, match="labels must be distinct"):
        ReleasePolicy(
            **{
                **BASE_POLICY,
                "release_ops_label": "code-change",
            }
        )


def test_policy_treats_github_label_names_as_case_insensitive() -> None:
    with pytest.raises(PolicyValidationError, match="labels must be distinct"):
        ReleasePolicy(
            **{
                **BASE_POLICY,
                "release_ops_label": "CODE-CHANGE",
            }
        )


@pytest.mark.parametrize(
    "candidate_branch",
    ["feature/release", "release/2026-02-30"],
)
def test_policy_requires_calendar_valid_release_candidate(
    candidate_branch: str,
) -> None:
    with pytest.raises(PolicyValidationError):
        ReleasePolicy(**{**BASE_POLICY, "candidate_branch": candidate_branch})


def test_every_discovered_check_requires_a_category() -> None:
    policy = ReleasePolicy(**BASE_POLICY)

    with pytest.raises(UnknownCheckPolicyError, match="new-security-scan"):
        validate_check_policy(policy, discovered={"api", "new-security-scan"})


@pytest.mark.parametrize(
    "field",
    [
        "main_branch",
        "candidate_branch",
        "code_change_label",
        "release_ops_label",
        "blocker_label",
    ],
)
def test_policy_rejects_blank_required_names(field: str) -> None:
    with pytest.raises(PolicyValidationError):
        ReleasePolicy(**{**BASE_POLICY, field: "  "})


def test_policy_preserves_optional_previous_release_context() -> None:
    policy = ReleasePolicy(
        **BASE_POLICY,
        previous_milestone_number=6,
        previous_release_branch="release/2026-08-03",
    )

    assert policy.previous_milestone_number == 6
    assert policy.previous_release_branch == "release/2026-08-03"
    assert policy.check_categories["api"] is CheckCategory.BLOCKING
