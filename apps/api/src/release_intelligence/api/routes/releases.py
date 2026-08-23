from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Annotated, Self, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.exc import SQLAlchemyError

from release_intelligence.api.dependencies import (
    AuthStore,
    CurrentUserDependency,
    get_auth_store,
    get_clock,
    require_repository_access,
)
from release_intelligence.api.schemas import EvidenceResponse
from release_intelligence.application.analyze_release import (
    AnalysisRequest,
    AnalysisService,
    MissingCandidateRef,
    MissingMilestone,
    MissingReleasePolicy,
)
from release_intelligence.domain.assessment import refresh_snapshot_freshness
from release_intelligence.domain.models import (
    ReadinessFinding,
    ReleaseSnapshot,
    ReleaseStatus,
)
from release_intelligence.ports.github import GitHubUnauthorized, RepoRef
from release_intelligence.ports.policies import PolicyPersistenceError
from release_intelligence.ports.repositories import (
    IncompatibleSnapshotError,
    StoredFindingMetadata,
)

router = APIRouter(prefix="/api/analyses", tags=["analyses"])


class AnalysisCreateRequest(BaseModel):
    repository_id: str = Field(min_length=1, max_length=255)
    milestone_number: int = Field(gt=0)
    candidate_ref: str = Field(
        min_length=18,
        max_length=18,
        pattern=r"^release/\d{4}-\d{2}-\d{2}$",
    )

    @model_validator(mode="after")
    def validate_candidate_date(self) -> Self:
        try:
            date.fromisoformat(self.candidate_ref.removeprefix("release/"))
        except ValueError:
            raise ValueError(
                "candidate_ref must contain a valid calendar date"
            ) from None
        return self


class AnalysisAccepted(BaseModel):
    run_id: UUID


class AnalysisRunFindingResponse(BaseModel):
    finding_id: UUID | None
    decision_eligible: bool
    decision_fingerprint: str | None
    rule_id: str
    severity: str
    summary: str
    required_action: str
    evidence: tuple[EvidenceResponse, ...]


class AnalysisRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: UUID
    status: ReleaseStatus
    snapshot: ReleaseSnapshot
    release_name: str
    repository_id: str
    repository_full_name: str
    source_fetched_at: datetime
    findings: tuple[AnalysisRunFindingResponse, ...]


def get_analysis_service(request: Request) -> AnalysisService:
    service = getattr(request.app.state, "analysis_service", None)
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Analysis unavailable")
    return cast(AnalysisService, service)


@router.post("", response_model=AnalysisAccepted, status_code=status.HTTP_202_ACCEPTED)
async def create_analysis(
    payload: AnalysisCreateRequest,
    user: CurrentUserDependency,
    store: Annotated[AuthStore, Depends(get_auth_store)],
    service: Annotated[AnalysisService, Depends(get_analysis_service)],
) -> AnalysisAccepted:
    repository = await require_repository_access(
        user_id=user.id, repository_id=payload.repository_id, store=store
    )
    owner, separator, name = repository.full_name.partition("/")
    if not separator or not owner or not name or "/" in name:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Repository identity is invalid",
        )
    request = AnalysisRequest(
        repository_id=repository.repository_id,
        repository=RepoRef(owner=owner, name=name),
        installation_id=repository.installation_id,
        milestone_number=payload.milestone_number,
        candidate_ref=payload.candidate_ref,
    )
    try:
        run_id = await service.run(request, actor=user.id)
    except MissingMilestone:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Milestone was not found"
        ) from None
    except MissingCandidateRef:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Candidate branch was not found",
        ) from None
    except MissingReleasePolicy:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Release policy is required",
        ) from None
    except GitHubUnauthorized:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "GitHub repository access denied"
        ) from None
    except (PolicyPersistenceError, SQLAlchemyError):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Analysis persistence unavailable",
        ) from None
    return AnalysisAccepted(run_id=run_id)


@router.get("/{run_id}", response_model=AnalysisRunResponse)
async def get_analysis(
    run_id: UUID,
    user: CurrentUserDependency,
    store: Annotated[AuthStore, Depends(get_auth_store)],
    service: Annotated[AnalysisService, Depends(get_analysis_service)],
    clock: Annotated[Callable[[], datetime], Depends(get_clock)],
) -> AnalysisRunResponse | JSONResponse:
    try:
        run = await service.get(run_id)
    except IncompatibleSnapshotError as error:
        await _require_run_access(user.id, error.repository_id, store)
        return JSONResponse(
            {
                "detail": "Analysis snapshot version is unsupported; refresh or upgrade",
                "status": ReleaseStatus.INSUFFICIENT_DATA.value,
            },
            status_code=status.HTTP_409_CONFLICT,
        )
    except KeyError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Analysis was not found"
        ) from None
    except SQLAlchemyError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Analysis persistence unavailable",
        ) from None
    await _require_run_access(user.id, run.snapshot.repository_id, store)
    freshness = refresh_snapshot_freshness(
        run.assessment,
        run.snapshot,
        now=clock(),
    )
    return AnalysisRunResponse(
        run_id=run.id,
        status=freshness.status,
        snapshot=run.snapshot,
        release_name=run.snapshot.release_name,
        repository_id=run.snapshot.repository_id,
        repository_full_name=run.snapshot.repository_full_name,
        source_fetched_at=run.source_fetched_at,
        findings=tuple(
            _finding_response(finding, run.finding_metadata, freshness.status)
            for finding in freshness.findings
        ),
    )


def _finding_response(
    finding: ReadinessFinding,
    metadata: tuple[StoredFindingMetadata, ...],
    status_value: ReleaseStatus,
) -> AnalysisRunFindingResponse:
    match = next(
        (item for item in metadata if item.finding == finding),
        None,
    )
    eligible = (
        match is not None
        and status_value is ReleaseStatus.NEEDS_DECISION
        and match.decision_eligible
    )
    return AnalysisRunFindingResponse(
        finding_id=match.finding_id if match is not None else None,
        decision_eligible=eligible,
        decision_fingerprint=(
            match.decision_fingerprint if match is not None and eligible else None
        ),
        rule_id=finding.rule_id,
        severity=finding.severity,
        summary=finding.summary,
        required_action=finding.required_action,
        evidence=tuple(
            EvidenceResponse.model_validate(evidence, from_attributes=True)
            for evidence in finding.evidence
        ),
    )


async def _require_run_access(
    user_id: str, repository_id: str, store: AuthStore
) -> None:
    try:
        await require_repository_access(
            user_id=user_id, repository_id=repository_id, store=store
        )
    except HTTPException as error:
        if error.status_code == status.HTTP_403_FORBIDDEN:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "Analysis was not found"
            ) from None
        raise
