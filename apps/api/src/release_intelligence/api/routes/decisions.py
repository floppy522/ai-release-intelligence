from __future__ import annotations

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.exc import SQLAlchemyError

from release_intelligence.api.dependencies import (
    AuthStore,
    CurrentUserDependency,
    get_auth_store,
)
from release_intelligence.api.routes.releases import (
    _require_run_access,
    get_analysis_service,
)
from release_intelligence.api.schemas import AssessmentResponse
from release_intelligence.application.analyze_release import AnalysisService
from release_intelligence.application.decisions import (
    DecisionConflictError,
    DecisionFindingNotFoundError,
    DecisionKind,
    DecisionPersistenceError,
    DecisionService,
    DecisionValidationError,
)
from release_intelligence.ports.repositories import IncompatibleSnapshotError

router = APIRouter(prefix="/api/analyses", tags=["decisions"])


class DecisionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_id: UUID
    fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    decision: DecisionKind
    reason: str = Field(min_length=1, max_length=4_000)

    @field_validator("reason")
    @classmethod
    def canonical_reason(cls, reason: str) -> str:
        canonical = reason.strip()
        if not canonical:
            raise ValueError("reason must not be blank")
        return canonical


class DecisionResponse(BaseModel):
    id: UUID
    analysis_run_id: UUID
    finding_id: UUID
    fingerprint: str
    decision: DecisionKind
    reason: str
    actor_id: str
    decided_at: str
    supersedes_decision_id: UUID | None
    blocks_release: bool
    assessment: AssessmentResponse


def get_decision_service(request: Request) -> DecisionService:
    service = getattr(request.app.state, "decision_service", None)
    if service is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Decision persistence unavailable"
        )
    return cast(DecisionService, service)


@router.post(
    "/{run_id}/decisions",
    response_model=DecisionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_decision(
    run_id: UUID,
    payload: DecisionCreateRequest,
    user: CurrentUserDependency,
    auth_store: Annotated[AuthStore, Depends(get_auth_store)],
    analysis_service: Annotated[AnalysisService, Depends(get_analysis_service)],
    decision_service: Annotated[DecisionService, Depends(get_decision_service)],
) -> DecisionResponse:
    try:
        run = await analysis_service.get(run_id)
    except IncompatibleSnapshotError as error:
        await _require_run_access(user.id, error.repository_id, auth_store)
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Analysis changed; refresh before deciding",
        ) from None
    except KeyError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Analysis was not found"
        ) from None
    except SQLAlchemyError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Decision persistence unavailable",
        ) from None
    await _require_run_access(user.id, run.snapshot.repository_id, auth_store)
    try:
        result = await decision_service.record_for_run(
            run_id=run_id,
            finding_id=payload.finding_id,
            fingerprint=payload.fingerprint,
            kind=payload.decision,
            reason=payload.reason,
            actor=user.id,
            authorized_repository_id=run.snapshot.repository_id,
        )
    except DecisionFindingNotFoundError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Finding is not eligible for a decision",
        ) from None
    except DecisionConflictError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Analysis changed; refresh before deciding",
        ) from None
    except DecisionValidationError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Decision is invalid",
        ) from None
    except (DecisionPersistenceError, SQLAlchemyError):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Decision persistence unavailable",
        ) from None

    decision = result.decision
    if decision.analysis_run_id is None or decision.finding_id is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Decision persistence unavailable",
        )
    return DecisionResponse(
        id=decision.id,
        analysis_run_id=decision.analysis_run_id,
        finding_id=decision.finding_id,
        fingerprint=decision.fingerprint,
        decision=decision.kind,
        reason=decision.reason,
        actor_id=decision.actor_id,
        decided_at=decision.decided_at.isoformat(),
        supersedes_decision_id=decision.supersedes_decision_id,
        blocks_release=decision.blocks_release,
        assessment=AssessmentResponse.model_validate(
            result.assessment, from_attributes=True
        ),
    )
