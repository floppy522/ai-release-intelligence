from release_intelligence.domain.models import (
    EvidenceRef,
    ReadinessAssessment,
    ReadinessFinding,
    ReleaseSnapshot,
    ReleaseStatus,
)


def assess_release(snapshot: ReleaseSnapshot) -> ReadinessAssessment:
    normalized_missing_pr = next(
        (
            item
            for item in snapshot.items
            if "code-change" in item.labels
            and not any(link.issue_number == item.number for link in snapshot.links)
        ),
        None,
    )
    if normalized_missing_pr is not None:
        evidence = EvidenceRef(
            evidence_id=f"github-issue-{normalized_missing_pr.source_id}",
            source_type="github_issue",
            source_id=str(normalized_missing_pr.number),
            url=normalized_missing_pr.url,
            fingerprint=(
                f"github:issue:{normalized_missing_pr.source_id}:"
                f"{normalized_missing_pr.updated_at.isoformat()}"
            ),
        )
        finding = ReadinessFinding(
            rule_id="scope.code_change_requires_pr",
            severity="BLOCKING",
            summary=f"Issue #{normalized_missing_pr.number} has no linked PR",
            required_action=f"Link a merged PR to Issue #{normalized_missing_pr.number}",
            evidence=(evidence,),
        )
        return ReadinessAssessment(
            status=ReleaseStatus.NOT_READY,
            findings=(finding,),
        )

    if "code-change" in snapshot.issue_labels and not snapshot.linked_pr_numbers:
        finding = ReadinessFinding(
            rule_id="scope.code_change_requires_pr",
            severity="BLOCKING",
            summary=f"Issue #{snapshot.issue_number} has no linked PR",
            required_action=f"Link a merged PR to Issue #{snapshot.issue_number}",
            evidence=(snapshot.issue_evidence,),
        )
        return ReadinessAssessment(
            status=ReleaseStatus.NOT_READY,
            findings=(finding,),
        )

    return ReadinessAssessment(status=ReleaseStatus.READY, findings=())
