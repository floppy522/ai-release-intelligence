from release_intelligence.adapters.fixtures.github_source import load_demo_release
from release_intelligence.application.analyze_release import assess_fixture_release
from release_intelligence.domain.models import ReleaseStatus


def test_missing_pr_blocks_demo_release() -> None:
    assessment = assess_fixture_release()

    assert assessment.status is ReleaseStatus.NOT_READY
    assert len(assessment.findings) == 1
    assert assessment.findings[0].rule_id == "scope.code_change_requires_pr"
    assert assessment.findings[0].evidence[0].url.endswith("/issues/142")


def test_demo_release_uses_its_github_milestone_identity() -> None:
    snapshot = load_demo_release()

    assert snapshot.issue_number == "142"
    assert snapshot.milestone_number == 7
