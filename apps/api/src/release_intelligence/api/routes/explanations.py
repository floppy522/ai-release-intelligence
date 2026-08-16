from __future__ import annotations

import logging
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from release_intelligence.api.dependencies import (
    AuthStore,
    CurrentUserDependency,
    get_auth_store,
    require_repository_access,
)
from release_intelligence.api.routes.releases import get_analysis_service
from release_intelligence.application.analyze_release import AnalysisService
from release_intelligence.application.explanations import (
    AIExplanationRejected,
    ExplanationService,
)
from release_intelligence.ports.ai import (
    AIExplanation,
    AIExplanationUnavailable,
    ExplanationMetadata,
)
from release_intelligence.ports.repositories import IncompatibleSnapshotError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/analyses", tags=["analyses"])


class ExplanationUnavailableResponse(BaseModel):
    state: Literal["unavailable"] = "unavailable"


class ExplanationAvailableResponse(BaseModel):
    state: Literal["available"] = "available"
    explanation: AIExplanation
    metadata: ExplanationMetadata


ExplanationResponse = ExplanationAvailableResponse | ExplanationUnavailableResponse


@router.post("/{run_id}/explanation", response_model=ExplanationResponse)
async def create_explanation(
    run_id: UUID,
    request: Request,
    user: CurrentUserDependency,
    store: Annotated[AuthStore, Depends(get_auth_store)],
    analysis_service: Annotated[AnalysisService, Depends(get_analysis_service)],
) -> ExplanationResponse:
    try:
        run = await analysis_service.get(run_id)
    except IncompatibleSnapshotError as error:
        await _require_run_access(user.id, error.repository_id, store)
        return ExplanationUnavailableResponse()
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
    explanation_service = cast(
        ExplanationService | None,
        getattr(request.app.state, "explanation_service", None),
    )
    if explanation_service is None:
        return ExplanationUnavailableResponse()
    try:
        explanation = await explanation_service.generate(run)
    except (AIExplanationRejected, AIExplanationUnavailable):
        logger.info("AI explanation unavailable")
        return ExplanationUnavailableResponse()
    metadata = explanation.metadata
    if metadata is None:
        logger.info("AI explanation unavailable")
        return ExplanationUnavailableResponse()
    logger.info(
        "AI explanation generated",
        extra={
            "ai_model": metadata.model,
            "ai_latency_seconds": str(metadata.latency_seconds),
            "ai_input_tokens": metadata.input_tokens,
            "ai_output_tokens": metadata.output_tokens,
            "ai_cost": str(metadata.cost),
        },
    )
    return ExplanationAvailableResponse(
        explanation=explanation,
        metadata=metadata,
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
