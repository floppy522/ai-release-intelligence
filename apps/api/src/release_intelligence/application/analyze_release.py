from release_intelligence.adapters.fixtures.github_source import load_demo_release
from release_intelligence.domain.assessment import assess_release
from release_intelligence.domain.models import ReadinessAssessment


def assess_fixture_release() -> ReadinessAssessment:
    return assess_release(load_demo_release())
