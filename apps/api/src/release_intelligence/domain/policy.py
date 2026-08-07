from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    check_categories: dict[str, CheckCategory]
    previous_milestone_number: int | None = Field(default=None, gt=0)
    previous_release_branch: str | None = None

    @model_validator(mode="after")
    def validate_invariants(self) -> Self:
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
            self.code_change_label.strip().casefold(),
            self.release_ops_label.strip().casefold(),
            self.blocker_label.strip().casefold(),
        )
        if len(set(labels)) != len(labels):
            raise PolicyValidationError("issue type and blocker labels must be distinct")

        if any(not check_name.strip() for check_name in self.check_categories):
            raise PolicyValidationError("check names must not be blank")
        self._validate_release_branch(self.candidate_branch, "candidate_branch")
        if self.previous_release_branch is not None:
            self._validate_release_branch(
                self.previous_release_branch, "previous_release_branch"
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
