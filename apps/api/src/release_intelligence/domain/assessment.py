from release_intelligence.domain.models import (
    ReadinessAssessment,
    ReadinessFinding,
    ReleaseSnapshot,
    ReleaseStatus,
)


def assess_release(snapshot: ReleaseSnapshot) -> ReadinessAssessment:
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
