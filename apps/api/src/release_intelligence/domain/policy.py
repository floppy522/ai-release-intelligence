from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator


class PolicyValidationError(Exception):
    """A release policy violates a cross-field domain invariant."""


class UnknownCheckPolicyError(PolicyValidationError):
    """At least one discovered CI check has no explicit category."""


class CheckCategory(StrEnum):
    BLOCKING = "BLOCKING"
    ADVISORY = "ADVISORY"
    IGNORED = "IGNORED"


class ReleasePolicy(BaseModel):
    """Immutable, complete policy used by deterministic release rules."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    main_branch: str
    candidate_branch: str
    milestone_number: int = Field(gt=0)
    code_change_label: str
    release_ops_label: str
    blocker_label: str
    check_categories: Mapping[str, CheckCategory]
    previous_milestone_number: int | None = Field(default=None, gt=0)
    previous_release_branch: str | None = None

    def model_copy(
        self, *, update: Mapping[str, Any] | None = None, deep: bool = False
    ) -> Self:
        del deep
        payload = self.model_dump(mode="json")
        if update:
            payload.update(update)
        return self.__class__.model_validate(payload)

    @field_serializer("check_categories")
    def serialize_check_categories(
        self, categories: Mapping[str, CheckCategory]
    ) -> dict[str, str]:
        return {name: category.value for name, category in categories.items()}

    @model_validator(mode="after")
    def validate_invariants(self) -> Self:
        canonical_strings = {
            "main_branch": self.main_branch.strip(),
            "candidate_branch": self.candidate_branch.strip(),
            "code_change_label": self.code_change_label.strip(),
            "release_ops_label": self.release_ops_label.strip(),
            "blocker_label": self.blocker_label.strip(),
        }
        if self.previous_release_branch is not None:
            canonical_strings["previous_release_branch"] = (
                self.previous_release_branch.strip()
            )
        for field_name, value in canonical_strings.items():
            object.__setattr__(self, field_name, value)

        required_names = {
            "main_branch": self.main_branch,
            "candidate_branch": self.candidate_branch,
            "code_change_label": self.code_change_label,
            "release_ops_label": self.release_ops_label,
            "blocker_label": self.blocker_label,
        }
        blank = [name for name, value in required_names.items() if not value.strip()]
        if blank:
            raise PolicyValidationError(f"required values must not be blank: {blank[0]}")

        labels = (
            self.code_change_label.strip().lower(),
            self.release_ops_label.strip().lower(),
            self.blocker_label.strip().lower(),
        )
        if len(set(labels)) != len(labels):
            raise PolicyValidationError("issue type and blocker labels must be distinct")

        normalized_checks: dict[str, CheckCategory] = {}
        for raw_name, category in self.check_categories.items():
            check_name = raw_name.strip()
            if not check_name:
                raise PolicyValidationError("check names must not be blank")
            if len(check_name) > 255:
                raise PolicyValidationError("check names must not exceed 255 characters")
            if check_name in normalized_checks:
                raise PolicyValidationError("duplicate check names after normalization")
            normalized_checks[check_name] = category
        if len(normalized_checks) > 100:
            raise PolicyValidationError("at most 100 checks may be configured")
        object.__setattr__(
            self,
            "check_categories",
            MappingProxyType(dict(sorted(normalized_checks.items()))),
        )

        self._validate_release_branch(self.candidate_branch, "candidate_branch")
        if self.candidate_branch == self.main_branch:
            raise PolicyValidationError("candidate branch must differ from main branch")
        has_previous_milestone = self.previous_milestone_number is not None
        has_previous_branch = self.previous_release_branch is not None
        if has_previous_milestone != has_previous_branch:
            raise PolicyValidationError(
                "previous milestone and branch must be configured together"
            )
        if self.previous_release_branch is not None:
            self._validate_release_branch(
                self.previous_release_branch, "previous_release_branch"
            )
            if self.previous_milestone_number == self.milestone_number:
                raise PolicyValidationError(
                    "previous milestone must differ from current milestone"
                )
            if self.previous_release_branch in {
                self.candidate_branch,
                self.main_branch,
            }:
                raise PolicyValidationError(
                    "previous branch must differ from candidate and main branches"
                )
        return self

    @staticmethod
    def _validate_release_branch(branch: str, field_name: str) -> None:
        prefix = "release/"
        if not branch.startswith(prefix):
            raise PolicyValidationError(f"{field_name} must use release/YYYY-MM-DD")
        try:
            date.fromisoformat(branch.removeprefix(prefix))
        except ValueError:
            raise PolicyValidationError(
                f"{field_name} must contain a valid calendar date"
            ) from None


def validate_check_policy(
    policy: ReleasePolicy, *, discovered: set[str]
) -> None:
    missing = sorted(discovered.difference(policy.check_categories))
    if missing:
        raise UnknownCheckPolicyError(
            "discovered checks require a category: " + ", ".join(missing)
        )
