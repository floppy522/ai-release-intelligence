from __future__ import annotations

import pytest

from release_intelligence.security.urls import (
    GitHubEvidenceKind,
    InvalidEvidenceURL,
    parse_github_evidence_url,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data",
        "https://127.0.0.1/acme/widgets/issues/1",
        "https://evil.example/github.com/acme/widgets/actions/runs/1",
        "https://github.com/other/widgets/actions/runs/1",
        "javascript:alert(1)",
        "https://user:secret@github.com/acme/widgets/issues/1",
        "https://github.com:443/acme/widgets/issues/1",
        "https://github.com/acme/widgets/issues/1?token=secret",
        "https://github.com/acme/widgets/issues/1#secret",
        "https://github.com\n/acme/widgets/issues/1",
        "\thttps://github.com/acme/widgets/issues/1",
        "https://github.com/acme/widgets/issues/1\rignored",
        "https://github.com/acme/widgets/issues/%31",
        "https://github.com/acme%2fother/widgets/issues/1",
        "https://github.com/acme/../widgets/issues/1",
        "https://github.com/acme/widgets/issues/01",
        "https://github.com/acme/widgets/issues/0",
        f"https://github.com/acme/widgets/issues/{2**63}",
        "https://github.com。evil.example/acme/widgets/issues/1",
        "https://github.com/acme/widgets/releases/1",
    ],
)
def test_untrusted_evidence_url_is_rejected(url: str) -> None:
    with pytest.raises(InvalidEvidenceURL):
        parse_github_evidence_url(url, expected_repo="acme/widgets")


def test_oversized_url_is_rejected_before_parsing() -> None:
    value = "https://github.com/acme/widgets/issues/1" + ("x" * 2_048)

    with pytest.raises(InvalidEvidenceURL):
        parse_github_evidence_url(value, expected_repo="acme/widgets")


@pytest.mark.parametrize(
    ("url", "kind", "identifiers"),
    [
        (
            "https://github.com/acme/widgets/issues/17",
            GitHubEvidenceKind.ISSUE,
            ("17",),
        ),
        ("https://github.com/acme/widgets/pull/18", GitHubEvidenceKind.PULL, ("18",)),
        (
            "https://github.com/acme/widgets/milestone/7",
            GitHubEvidenceKind.MILESTONE,
            ("7",),
        ),
        (
            "https://github.com/acme/widgets/runs/101",
            GitHubEvidenceKind.CHECK_RUN,
            ("101",),
        ),
        (
            "https://github.com/acme/widgets/runs/800/jobs/700",
            GitHubEvidenceKind.ACTIONS_JOB,
            ("800", "700"),
        ),
        (
            "https://github.com/acme/widgets/actions/runs/800/jobs/700",
            GitHubEvidenceKind.ACTIONS_JOB,
            ("800", "700"),
        ),
        (
            "https://github.com/acme/widgets/actions/runs/800",
            GitHubEvidenceKind.ACTIONS_RUN,
            ("800",),
        ),
        (
            f"https://github.com/acme/widgets/commit/{'a' * 40}",
            GitHubEvidenceKind.COMMIT,
            ("a" * 40,),
        ),
        (
            f"https://github.com/acme/widgets/commit/{'a' * 40}/checks",
            GitHubEvidenceKind.COMMIT_CHECKS,
            ("a" * 40,),
        ),
        (
            f"https://github.com/acme/widgets/compare/{'a' * 40}...{'b' * 40}",
            GitHubEvidenceKind.COMPARE,
            ("a" * 40, "b" * 40),
        ),
    ],
)
def test_supported_evidence_url_returns_a_typed_repository_bound_locator(
    url: str,
    kind: GitHubEvidenceKind,
    identifiers: tuple[str, ...],
) -> None:
    locator = parse_github_evidence_url(url, expected_repo="acme/widgets")

    assert locator.repository == "acme/widgets"
    assert locator.kind is kind
    assert locator.identifiers == identifiers
    assert locator.canonical_url == url


def test_repository_identity_is_compared_case_insensitively_but_returned_canonically() -> (
    None
):
    locator = parse_github_evidence_url(
        "https://github.com/ACME/Widgets/issues/1",
        expected_repo="acme/widgets",
    )

    assert locator.repository == "acme/widgets"
    assert locator.canonical_url == "https://github.com/acme/widgets/issues/1"
