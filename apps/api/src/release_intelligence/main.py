from fastapi import FastAPI

from release_intelligence.api.schemas import AssessmentResponse
from release_intelligence.application.analyze_release import assess_fixture_release
from release_intelligence.domain.models import ReadinessAssessment

app = FastAPI(title="AI Release Intelligence")


@app.get("/api/demo/analysis", response_model=AssessmentResponse)
def get_demo_analysis() -> ReadinessAssessment:
    return assess_fixture_release()
