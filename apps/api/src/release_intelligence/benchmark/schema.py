from __future__ import annotations

import re
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_ID = re.compile(r"^[a-z][a-z0-9_-]{0,99}$")
_RULE_ID = re.compile(r"^[a-z][a-z0-9_.:-]{0,238}$")
_VERSION = re.compile(r"^[1-9][0-9]{0,2}\.[0-9]{1,3}\.[0-9]{1,3}$")
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BenchmarkFinding(StrictModel):
    rule_id: str
    source_id: str = Field(min_length=1, max_length=255)
    severity: str
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=10)

    @field_validator("rule_id")
    @classmethod
    def valid_rule_id(cls, value: str) -> str:
        if _RULE_ID.fullmatch(value) is None:
            raise ValueError("rule_id must be canonical")
        return value

    @field_validator("severity")
    @classmethod
    def valid_severity(cls, value: str) -> str:
        allowed = {"BLOCKING", "DECISION_REQUIRED", "INSUFFICIENT_DATA"}
        if value not in allowed:
            raise ValueError("severity is not supported")
        return value

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)) or any(not item.strip() for item in value):
            raise ValueError("evidence IDs must be nonblank and unique")
        return value

    @property
    def risk_identity(self) -> tuple[str, str]:
        return self.rule_id, self.source_id


class BenchmarkPrediction(StrictModel):
    status: str
    findings: tuple[BenchmarkFinding, ...] = Field(max_length=100)

    @field_validator("status")
    @classmethod
    def valid_status(cls, value: str) -> str:
        allowed = {"READY", "NOT_READY", "NEEDS_DECISION", "INSUFFICIENT_DATA"}
        if value not in allowed:
            raise ValueError("status is not supported")
        return value

    @model_validator(mode="after")
    def unique_risks(self) -> Self:
        risks = [finding.risk_identity for finding in self.findings]
        if len(risks) != len(set(risks)):
            raise ValueError("risk identities must be unique")
        severities = {finding.severity for finding in self.findings}
        valid = {
            "READY": not severities,
            "NOT_READY": "BLOCKING" in severities,
            "NEEDS_DECISION": (
                "BLOCKING" not in severities and "DECISION_REQUIRED" in severities
            ),
            "INSUFFICIENT_DATA": "INSUFFICIENT_DATA" in severities,
        }
        if not valid[self.status]:
            raise ValueError("status is not justified by finding severities")
        return self


class BenchmarkScenario(StrictModel):
    id: str
    name: str = Field(min_length=1, max_length=160)
    category: str
    fixture: str
    evidence_ids: tuple[str, ...] = Field(max_length=100)
    expected: BenchmarkPrediction

    @field_validator("id", "category", "fixture")
    @classmethod
    def valid_id(cls, value: str) -> str:
        if _ID.fullmatch(value) is None:
            raise ValueError("identifier must be canonical")
        return value

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)) or any(not item.strip() for item in value):
            raise ValueError("evidence IDs must be nonblank and unique")
        return value

    @model_validator(mode="after")
    def expected_evidence_is_registered(self) -> Self:
        registered = set(self.evidence_ids)
        if any(
            evidence_id not in registered
            for finding in self.expected.findings
            for evidence_id in finding.evidence_ids
        ):
            raise ValueError("expected evidence must be registered")
        return self


class BenchmarkCatalog(StrictModel):
    version: str
    categories: dict[str, int] = Field(min_length=1, max_length=20)
    scenarios: tuple[BenchmarkScenario, ...] = Field(min_length=1, max_length=1000)

    @field_validator("version")
    @classmethod
    def valid_version(cls, value: str) -> str:
        if _VERSION.fullmatch(value) is None:
            raise ValueError("version must be bounded semantic version")
        return value

    @model_validator(mode="after")
    def validate_catalog(self) -> Self:
        identifiers = [scenario.id for scenario in self.scenarios]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("scenario IDs must be unique")
        actual: dict[str, int] = {}
        for scenario in self.scenarios:
            actual[scenario.category] = actual.get(scenario.category, 0) + 1
        if any(
            _ID.fullmatch(category) is None or count <= 0
            for category, count in self.categories.items()
        ):
            raise ValueError("declared categories must be canonical and positive")
        if dict(sorted(self.categories.items())) != dict(sorted(actual.items())):
            raise ValueError("declared category counts do not match scenarios")
        return self


def read_bounded_text(path: Path) -> str:
    with path.open("rb") as document:
        raw = document.read(MAX_DOCUMENT_BYTES + 1)
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise ValueError("benchmark document exceeds the size limit")
    return raw.decode("utf-8")


def load_catalog(path: Path) -> BenchmarkCatalog:
    try:
        raw = yaml.safe_load(read_bounded_text(path))
        return BenchmarkCatalog.model_validate(raw)
    except (OSError, UnicodeError, yaml.YAMLError, TypeError, ValueError) as error:
        raise ValueError("catalog is not valid safe YAML") from error
