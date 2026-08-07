from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Annotated, Self, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.exc import SQLAlchemyError

from release_intelligence.api.dependencies import (
    AuthStore,
    CurrentUserDependency,
    get_auth_store,
    get_clock,
    require_repository_access,
)
from release_intelligence.application.analyze_release import (
    AnalysisRequest,
    AnalysisService,
    MissingCandidateRef,
    MissingMilestone,
    assess,
)
from release_intelligence.domain.models import ReleaseSnapshot, ReleaseStatus
from release_intelligence.ports.github import GitHubUnauthorized, RepoRef

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
            raise ValueError("candidate_ref must contain a valid calendar date") from None
        return self


class AnalysisAccepted(BaseModel):
    run_id: UUID


class AnalysisRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: UUID
    status: ReleaseStatus
    snapshot: ReleaseSnapshot


def get_analysis_service(request: Request) -> AnalysisService:
    service = getattr(request.app.state, "analysis_service", None)
    if service is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Analysis unavailable"
        )
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
    except GitHubUnauthorized:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "GitHub repository access denied"
        ) from None
    except SQLAlchemyError:
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
) -> AnalysisRunResponse:
    try:
        run = await service.get(run_id)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Analysis was not found") from None
    except SQLAlchemyError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Analysis persistence unavailable",
        ) from None
    try:
        await require_repository_access(
            user_id=user.id, repository_id=run.snapshot.repository_id, store=store
        )
    except HTTPException as error:
        if error.status_code == status.HTTP_403_FORBIDDEN:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "Analysis was not found"
            ) from None
        raise
    freshness = assess(run.snapshot, policy=None, decisions=(), now=clock())
    effective_status = (
        ReleaseStatus.INSUFFICIENT_DATA
        if freshness.status is ReleaseStatus.INSUFFICIENT_DATA
        else run.assessment.status
    )
    return AnalysisRunResponse(
        run_id=run.id,
        status=effective_status,
        snapshot=run.snapshot,
    )
