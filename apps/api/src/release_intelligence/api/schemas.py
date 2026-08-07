from pydantic import BaseModel

from release_intelligence.domain.models import ReleaseStatus


class EvidenceResponse(BaseModel):
    evidence_id: str
    source_type: str
    source_id: str
    url: str
    fingerprint: str


class FindingResponse(BaseModel):
    rule_id: str
    severity: str
    summary: str
    required_action: str
    evidence: tuple[EvidenceResponse, ...]


class AssessmentResponse(BaseModel):
    status: ReleaseStatus
    findings: tuple[FindingResponse, ...]
