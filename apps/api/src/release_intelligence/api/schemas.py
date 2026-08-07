from pydantic import BaseModel


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
    status: str
    findings: tuple[FindingResponse, ...]
