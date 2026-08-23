from __future__ import annotations

import unicodedata
from decimal import Decimal
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PrivateAttr,
    StringConstraints,
)

FindingSeverity = Literal[
    "BLOCKING",
    "DECISION_REQUIRED",
    "WARNING",
    "INSUFFICIENT_DATA",
]
ExplanationConfidence = Literal["LOW", "MEDIUM", "HIGH"]
AI_EXPLANATION_PENDING_CONTENT = '{"state":"pending"}'
AI_EXPLANATION_UNAVAILABLE_CONTENT = '{"state":"unavailable"}'
UNSAFE_UNICODE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})


def normalize_safe_unicode(
    value: object,
    *,
    maximum: int,
    truncate: bool = False,
) -> str:
    """NFC-normalize and bound text after rejecting unsafe Unicode categories."""

    if type(value) is not str:
        raise ValueError("safe text must be a string")
    normalized = unicodedata.normalize("NFC", value)
    if any(
        unicodedata.category(character) in UNSAFE_UNICODE_CATEGORIES
        for character in normalized
    ):
        raise ValueError("safe text contains a prohibited Unicode character")
    stripped = normalized.strip()
    if truncate:
        return stripped[:maximum]
    if len(stripped) > maximum:
        raise ValueError("safe text exceeds its code-point limit")
    return stripped


def _normalize_text(value: object) -> str:
    return normalize_safe_unicode(value, maximum=2_000)


def _normalize_label(value: object) -> str:
    return normalize_safe_unicode(value, maximum=200)


def _normalize_identifier(value: object) -> str:
    return normalize_safe_unicode(value, maximum=255)


BoundedText = Annotated[
    str,
    BeforeValidator(_normalize_text),
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000),
]
BoundedLabel = Annotated[
    str,
    BeforeValidator(_normalize_label),
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]
BoundedIdentifier = Annotated[
    str,
    BeforeValidator(_normalize_identifier),
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
]
ModelIdentifier = Annotated[
    str,
    BeforeValidator(_normalize_label),
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$",
    ),
]


class AIExplanationUnavailable(RuntimeError):
    """The optional provider could not produce a safe explanation."""

    def __init__(self) -> None:
        super().__init__("AI explanation unavailable")


class ExplanationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: BoundedIdentifier
    source_type: BoundedLabel
    source_id: BoundedLabel


class ExplanationFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_id: BoundedIdentifier
    rule_id: BoundedIdentifier
    severity: FindingSeverity
    summary: BoundedLabel
    required_action: BoundedLabel
    evidence: tuple[ExplanationEvidence, ...] = Field(min_length=1, max_length=100)


class ExplanationInput(BaseModel):
    """Allowlisted, normalized deterministic facts passed to an AI provider."""

    model_config = ConfigDict(extra="forbid")

    deterministic_status: Literal[
        "READY", "NOT_READY", "NEEDS_DECISION", "INSUFFICIENT_DATA"
    ]
    release_name: BoundedLabel
    source_fetched_at: BoundedLabel
    findings: tuple[ExplanationFinding, ...] = Field(max_length=1_000)
    limitations: tuple[BoundedText, ...] = Field(min_length=1, max_length=20)


class ExplanationGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: BoundedLabel
    explanation: BoundedText
    severity: FindingSeverity
    finding_ids: tuple[BoundedIdentifier, ...] = Field(min_length=1, max_length=100)
    evidence_ids: tuple[BoundedIdentifier, ...] = Field(min_length=1, max_length=200)


class ExplanationAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: BoundedLabel
    finding_ids: tuple[BoundedIdentifier, ...] = Field(min_length=1, max_length=100)
    evidence_ids: tuple[BoundedIdentifier, ...] = Field(min_length=1, max_length=200)


class ExplanationMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: ModelIdentifier
    latency_seconds: Decimal = Field(ge=0, le=15, decimal_places=6)
    input_tokens: int = Field(ge=0, le=10_000_000)
    output_tokens: int = Field(ge=0, le=10_000_000)
    cost: Decimal = Field(ge=0, le=20_000_000, decimal_places=6)


class AIExplanation(BaseModel):
    """Strict Structured Outputs schema; deterministic status is intentionally absent."""

    model_config = ConfigDict(extra="forbid")

    summary: BoundedText
    groups: tuple[ExplanationGroup, ...] = Field(max_length=20)
    actions: tuple[ExplanationAction, ...] = Field(max_length=100)
    limitations: tuple[BoundedText, ...] = Field(min_length=1, max_length=20)
    confidence: ExplanationConfidence
    finding_ids: tuple[BoundedIdentifier, ...] = Field(max_length=1_000)
    evidence_ids: tuple[BoundedIdentifier, ...] = Field(max_length=2_000)
    _metadata: ExplanationMetadata | None = PrivateAttr(default=None)

    @property
    def metadata(self) -> ExplanationMetadata | None:
        return self._metadata

    def attach_metadata(self, metadata: ExplanationMetadata) -> None:
        self._metadata = metadata


class AIExplanationProvider(Protocol):
    async def explain(self, input: ExplanationInput) -> AIExplanation: ...


class AIExplanationStore(Protocol):
    """Atomic one-attempt storage keyed by immutable analysis run."""

    async def load_explanation(self, run_id: UUID) -> str | None: ...

    async def reserve_explanation(self, run_id: UUID) -> bool: ...

    async def complete_explanation(self, run_id: UUID, content: str) -> None: ...

    async def fail_explanation(self, run_id: UUID) -> None: ...
