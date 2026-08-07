from release_intelligence.domain.models import EvidenceRef, ReleaseSnapshot


def load_demo_release() -> ReleaseSnapshot:
    issue_evidence = EvidenceRef(
        evidence_id="github-issue-142",
        source_type="github_issue",
        source_id="142",
        url="https://github.com/example/release-demo/issues/142",
        fingerprint="github:issue:142",
    )
    return ReleaseSnapshot(
        release_name="Release 2026.08.10",
        issue_number="142",
        issue_labels=("code-change",),
        linked_pr_numbers=(),
        issue_evidence=issue_evidence,
    )
