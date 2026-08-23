from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit

MAX_EVIDENCE_URL_LENGTH = 2_048
MAX_GITHUB_IDENTIFIER = 9_223_372_036_854_775_807

_REPOSITORY = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?/"
    r"[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?$"
)
_SHA = re.compile(r"^[0-9a-f]{40}$")


class InvalidEvidenceURL(ValueError):
    """An evidence value is not a supported repository-bound GitHub locator."""

    def __init__(self) -> None:
        super().__init__("invalid GitHub evidence URL")


class GitHubEvidenceKind(StrEnum):
    ISSUE = "issue"
    PULL = "pull"
    MILESTONE = "milestone"
    CHECK_RUN = "check_run"
    ACTIONS_RUN = "actions_run"
    ACTIONS_JOB = "actions_job"
    COMMIT = "commit"
    COMMIT_CHECKS = "commit_checks"
    COMPARE = "compare"


@dataclass(frozen=True, slots=True)
class GitHubEvidenceLocator:
    repository: str
    kind: GitHubEvidenceKind
    identifiers: tuple[str, ...]
    canonical_url: str


def parse_github_evidence_url(
    url: object, expected_repo: object
) -> GitHubEvidenceLocator:
    """Parse, validate, and canonicalize an evidence URL without dereferencing it."""

    if (
        type(url) is not str
        or not url
        or len(url) > MAX_EVIDENCE_URL_LENGTH
        or len(url.encode("utf-8", errors="surrogatepass")) > MAX_EVIDENCE_URL_LENGTH
        or not url.isascii()
        or any(not "!" <= character <= "~" for character in url)
        or type(expected_repo) is not str
        or _REPOSITORY.fullmatch(expected_repo) is None
        or "%" in url
        or "\\" in url
    ):
        raise InvalidEvidenceURL()
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
        username = parsed.username
        password = parsed.password
    except (UnicodeError, ValueError):
        raise InvalidEvidenceURL() from None
    if not (
        parsed.scheme == "https"
        and parsed.netloc == "github.com"
        and hostname == "github.com"
        and port is None
        and username is None
        and password is None
        and not parsed.query
        and not parsed.fragment
        and parsed.path.startswith("/")
        and "//" not in parsed.path
    ):
        raise InvalidEvidenceURL()
    parts = parsed.path.removeprefix("/").split("/")
    if len(parts) < 4 or any(part in {"", ".", ".."} for part in parts):
        raise InvalidEvidenceURL()
    owner, repository, *resource = parts
    observed_repo = f"{owner}/{repository}"
    if (
        _REPOSITORY.fullmatch(observed_repo) is None
        or observed_repo.casefold() != expected_repo.casefold()
    ):
        raise InvalidEvidenceURL()

    kind, identifiers = _parse_resource(resource)
    canonical_path = "/".join((expected_repo, *resource))
    return GitHubEvidenceLocator(
        repository=expected_repo,
        kind=kind,
        identifiers=identifiers,
        canonical_url=f"https://github.com/{canonical_path}",
    )


def _parse_resource(
    resource: list[str],
) -> tuple[GitHubEvidenceKind, tuple[str, ...]]:
    if len(resource) == 2 and resource[0] in {"issues", "pull", "milestone", "runs"}:
        identifier = _positive_identifier(resource[1])
        kinds = {
            "issues": GitHubEvidenceKind.ISSUE,
            "pull": GitHubEvidenceKind.PULL,
            "milestone": GitHubEvidenceKind.MILESTONE,
            "runs": GitHubEvidenceKind.CHECK_RUN,
        }
        return kinds[resource[0]], (identifier,)
    if len(resource) == 2 and resource[0] == "commit" and _SHA.fullmatch(resource[1]):
        return GitHubEvidenceKind.COMMIT, (resource[1],)
    if (
        len(resource) == 3
        and resource[0] == "commit"
        and resource[2] == "checks"
        and _SHA.fullmatch(resource[1])
    ):
        return GitHubEvidenceKind.COMMIT_CHECKS, (resource[1],)
    if len(resource) == 2 and resource[0] == "compare":
        base, separator, head = resource[1].partition("...")
        if separator and _SHA.fullmatch(base) and _SHA.fullmatch(head):
            return GitHubEvidenceKind.COMPARE, (base, head)
        raise InvalidEvidenceURL()
    if len(resource) == 3 and resource[:2] == ["actions", "runs"]:
        return GitHubEvidenceKind.ACTIONS_RUN, (_positive_identifier(resource[2]),)
    if len(resource) == 4 and resource[0] == "runs" and resource[2] == "jobs":
        return GitHubEvidenceKind.ACTIONS_JOB, (
            _positive_identifier(resource[1]),
            _positive_identifier(resource[3]),
        )
    if (
        len(resource) == 5
        and resource[:2] == ["actions", "runs"]
        and resource[3] in {"job", "jobs"}
    ):
        return GitHubEvidenceKind.ACTIONS_JOB, (
            _positive_identifier(resource[2]),
            _positive_identifier(resource[4]),
        )
    raise InvalidEvidenceURL()


def _positive_identifier(value: str) -> str:
    maximum = str(MAX_GITHUB_IDENTIFIER)
    if not (
        value.isascii()
        and value.isdigit()
        and not value.startswith("0")
        and (
            len(value) < len(maximum)
            or (len(value) == len(maximum) and value <= maximum)
        )
    ):
        raise InvalidEvidenceURL()
    return value
