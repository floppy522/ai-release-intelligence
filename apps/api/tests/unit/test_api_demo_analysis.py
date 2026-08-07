from fastapi.testclient import TestClient

from release_intelligence.main import app


def test_demo_analysis_returns_evidence_backed_status() -> None:
    with TestClient(app) as client:
        response = client.get("/api/demo/analysis")

    assert response.status_code == 200
    assert response.json()["status"] == "NOT_READY"
    assert response.json()["findings"][0]["evidence"][0]["source_id"] == "142"
