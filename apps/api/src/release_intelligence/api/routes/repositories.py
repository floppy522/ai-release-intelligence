from __future__ import annotations

from datetime import datetime
from typing import Annotated, Self, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from release_intelligence.api.dependencies import (
    AuthStore,
    CurrentUserDependency,
    get_auth_store,
    require_repository_access,
)
from release_intelligence.domain.policy import (
    CheckCategory,
    PolicyValidationError,
    ReleasePolicy,
    UnknownCheckPolicyError,
    validate_check_policy,
)
from release_intelligence.ports.policies import (
    PolicyPersistenceError,
    PolicyRecord,
    PolicyRepositoryPort,
    PolicyVersionConflictError,
)

router = APIRouter(prefix="/api/repositories", tags=["repositories"])
CheckName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class PolicyUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    main_branch: str = Field(max_length=255)
    candidate_branch: str = Field(max_length=255)
    milestone_number: int = Field(gt=0)
    code_change_label: str = Field(max_length=255)
    release_ops_label: str = Field(max_length=255)
    blocker_label: str = Field(max_length=255)
    discovered_checks: list[CheckName] = Field(max_length=100)
    check_categories: dict[CheckName, CheckCategory] = Field(max_length=100)
    previous_milestone_number: int | None = Field(default=None, gt=0)
    previous_release_branch: str | None = Field(default=None, max_length=255)
    expected_version: int | None = Field(default=None, gt=0, le=2_147_483_647)

    @model_validator(mode="after")
    def reject_duplicate_discovered_checks(self) -> Self:
        if len(set(self.discovered_checks)) != len(self.discovered_checks):
            raise ValueError("discovered checks must be unique")
        return self


class PolicyResponse(BaseModel):
    repository_id: str
    version: int
    policy: ReleasePolicy
    created_at: datetime


def get_policy_store(request: Request) -> PolicyRepositoryPort:
    store = getattr(request.app.state, "policy_store", None)
    if store is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Policy persistence unavailable"
        )
    return cast(PolicyRepositoryPort, store)


async def parse_policy_payload(request: Request) -> PolicyUpsertRequest:
    try:
        return PolicyUpsertRequest.model_validate(await request.json())
    except (ValidationError, ValueError):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Release policy request is invalid",
        ) from None


def canonical_repository_id(repository_id: int, request: Request) -> str:
    requested = request.url.path.rsplit("/", 2)[-2]
    canonical = str(repository_id)
    if (
        repository_id <= 0
        or repository_id > 9_223_372_036_854_775_807
        or requested != canonical
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Repository ID is invalid",
        )
    return canonical


@router.get("/{repository_id:int}/policy", response_model=PolicyResponse)
async def get_policy(
    repository_id: int,
    request: Request,
    user: CurrentUserDependency,
    auth_store: Annotated[AuthStore, Depends(get_auth_store)],
    policy_store: Annotated[PolicyRepositoryPort, Depends(get_policy_store)],
) -> PolicyResponse:
    canonical_id = canonical_repository_id(repository_id, request)
    repository = await require_repository_access(
        user_id=user.id, repository_id=canonical_id, store=auth_store
    )
    try:
        record = await policy_store.get_latest(repository.repository_id)
    except PolicyPersistenceError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Policy persistence unavailable"
        ) from None
    if record is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Release policy was not found"
        )
    return _response(record)


@router.put("/{repository_id:int}/policy", response_model=PolicyResponse)
async def put_policy(
    repository_id: int,
    request: Request,
    payload: Annotated[PolicyUpsertRequest, Depends(parse_policy_payload)],
    user: CurrentUserDependency,
    auth_store: Annotated[AuthStore, Depends(get_auth_store)],
    policy_store: Annotated[PolicyRepositoryPort, Depends(get_policy_store)],
) -> PolicyResponse:
    canonical_id = canonical_repository_id(repository_id, request)
    repository = await require_repository_access(
        user_id=user.id, repository_id=canonical_id, store=auth_store
    )
    try:
        policy = ReleasePolicy(
            **payload.model_dump(
                exclude={"discovered_checks", "expected_version"}
            )
        )
        validate_check_policy(policy, discovered=set(payload.discovered_checks))
    except UnknownCheckPolicyError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Every discovered check needs a category",
        ) from None
    except PolicyValidationError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Release policy is invalid"
        ) from None

    try:
        record = await policy_store.create_version(
            repository_id=repository.repository_id,
            policy=policy,
            expected_version=payload.expected_version,
        )
    except PolicyVersionConflictError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Policy changed; reload before saving"
        ) from None
    except PolicyPersistenceError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Policy persistence unavailable"
        ) from None
    return _response(record)


def _response(record: PolicyRecord) -> PolicyResponse:
    return PolicyResponse(
        repository_id=record.repository_id,
        version=record.version,
        policy=record.policy,
        created_at=record.created_at,
    )
