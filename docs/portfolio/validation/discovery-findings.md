# Discovery Findings

## Status

Four prior exploratory interview summaries have been added to the evidence
record. They cover two Project Managers and two R&D Leads from one internal
organizational context.

All findings below are **Self-reported**. No workflow was observed, no timing was
independently measured, and no AI Release Intelligence (ARI) concept test was
documented. The interviews predated the current protocol and were not
consistently anchored in the last actual Go/No-Go review. They therefore do not
yet count toward the problem-validation gate.

All four hypotheses remain **Unvalidated**.

## Sample composition

| Metric | Recorded count |
| --- | ---: |
| Exploratory interview summaries | 4 |
| Participants fully eligible under the current protocol | 0 |
| Participants requiring eligibility follow-up | 4 |
| Clear release-management role fit | 1 |
| Project Managers | 2 |
| R&D Leads | 2 |
| Organizational contexts represented | 1 |
| Participants outside the author's current employer | 0 |
| Observed release-review workflows | 0 |
| Documented ARI concept tests | 0 |

The sample is anonymized as `P01`-`P04`. Role fit is not the same as protocol
eligibility: the current criteria also require recent release-decision
participation and a reconstructable last actual review.

## Evidence inventory

| Evidence source | Verified observations | Self-reported participants | Boundary |
| --- | ---: | ---: | --- |
| Prior interview summaries | 0 | 4 | Retrospective summaries supplied by the researcher. |
| Workflow observations | 0 | 0 | None completed. |
| ARI concept tests | 0 | 0 | None documented. |
| Controlled pilot | 0 | 0 | Not started. |

## Quantitative statements

These figures describe different activities and must not be added together or
presented as release-review time.

| Participant | Self-reported statement | Interpretation boundary |
| --- | --- | --- |
| P01 | Approximately 1.5 hours per day across several manual PM activities; release notes about 10 minutes per week; flow metrics 1-2 hours per month. | The daily total includes delivery reporting, board maintenance, status communication, and monitoring. Bitbucket and Jenkins monitoring was described as low-effort. |
| P02 | PM involvement reduced preparation for a status presentation from an estimated 4-6 hours to 1-2 hours of participation. | A recalled comparison about reporting work, not release review and not an observed measurement. |
| P03 | Approximately 3 hours per week on status updates: a one-hour delivery sync, two 45-minute team meetings, and about 30 minutes of urgent requests. | The source summary called the meetings “daily” while listing two in the weekly calculation; cadence requires confirmation. This is not isolated Go/No-Go review time. |
| P04 | No time baseline supplied; human-hours saved was named as the preferred AI-solution effectiveness metric. | A desired metric, not a measured outcome. |

## Exploratory signals - not gated patterns

The research plan requires at least two **eligible** participants before a
recurring public pattern is recorded. None of these interviews is fully eligible
under that protocol. The signals below organize exploratory notes for follow-up
only; they are not validated patterns and do not estimate prevalence.

| Signal ID | Safe exploratory statement | Supporting summaries | Contradicting or limiting evidence | Evidence class | Follow-up implication |
| --- | --- | ---: | --- | --- | --- |
| X01 | All four summaries mention manual delivery-status collection, updating, or reporting. | 4/4 exploratory summaries | Activities and time burdens differed; most were broader than release readiness. | Self-reported | Test whether any of this work occurs inside an eligible participant's last Go/No-Go review. |
| X02 | All four summaries mention status moving across multiple surfaces or people. | 4/4 exploratory summaries | Only one participant clearly held a release-management role; surface count was not observed. | Self-reported | Reconstruct actual release-evidence navigation before assessing H2. |
| X03 | All four summaries mention dependencies, blockers, stakeholder action, or cross-team coordination as delivery risks. | 4/4 exploratory summaries | The accounts concern delivery predictability as well as release readiness. | Self-reported | Probe whether and how these risks become Go/No-Go evidence. |
| X04 | Two R&D summaries describe research work and weak formalization as harder to estimate. | 2/4 exploratory summaries | This concerns planning and estimation, not ARI's current scope. | Self-reported | Keep estimation outside ARI unless separate eligible evidence emerges. |

## Role-level evidence

### P01 - Project Manager and Release Manager

- Reported recurring manual work across release notes, flow metrics, status
  messages, board/status-color maintenance, and Bitbucket/Jenkins monitoring.
- Reported code freezes and cases where late risk discovery delayed delivery or
  rushed work led to production defects.
- Reported cross-division process and coordination friction.
- Said additional capacity would be invested in process improvement.

**Boundary:** Bitbucket/Jenkins monitoring was described as taking little time.
The approximately 1.5-hour daily total must not be attributed to release review
or to work ARI currently automates.

### P02 - R&D Lead

- Reported iterative estimation, with technical decomposition required even for
  an early estimate.
- Reported inter-team testing dependencies and underestimated, weakly formalized
  research work as common causes of schedule variance.
- Reported status transmission through team syncs and recurring PM questions
  about blockers, Jira representation, and remaining duration.
- Proposed historical assignee/task-type estimation as a possible tool.

**Boundary:** Release-decision participation, last-release workflow, and ARI
concept response were not established.

### P03 - R&D Lead

- Reported quarterly capacity-based planning, risk buffers of 20-30%, and lower
  estimate accuracy for research tasks.
- Reported Jira or Confluence records for blockers and status updates through
  team meetings or a weekly delivery sync.
- Reported approximately three hours per week on status communication.
- Proposed voice-note-to-private-Jira-task capture as a possible tool.

**Boundary:** The strongest needs concern personal status capture and delivery
visibility, not a documented Go/No-Go workflow.

### P04 - Project Manager

- Reported that Jira, stakeholder commitments, and current team reality can
  disagree; direct team information is treated as the practical source of truth.
- Reported recurring manual System Demo presentation and resource-allocation
  updates.
- Reported a failed adoption attempt where a bot reminded assignees to fill
  empty ticket fields but did not change behavior.
- Named human-hours saved as the main desired AI-solution metric.

**Boundary:** No last-release sequence, release-decision authority, or measured
time baseline was recorded.

## Contradictions and negative evidence

- P01 described Bitbucket and Jenkins monitoring as low-effort. This weakens any
  assumption that repository/CI navigation alone explains the broader manual
  workload.
- The largest quantified burdens relate to status communication, reporting,
  meetings, and board maintenance. ARI currently addresses only a narrower
  release-readiness subset.
- P04's reminder-bot example suggests that automation does not solve weak ticket
  hygiene when required human behavior remains unchanged.
- The two R&D Leads proposed estimation and personal task-capture tools rather
  than release-readiness reports. These are adjacent needs, not evidence of ARI
  usefulness.

## Researcher inferences requiring follow-up

These are explicitly **Inference**, not participant findings or validated
patterns:

- **Inference from P02 and P04:** cognitive load and context recovery may be
  material alongside mechanical page navigation. P02 described reduced status-
  preparation effort with PM support; P04 said free time would be used to
  organize a multi-project workspace. This was not measured.
- **Inference from P04:** if direct team statements override stale formal tools,
  evidence freshness and ownership may be trust requirements for a report. No
  release-specific discrepancy was observed.
- **Inference from P02 and P03:** their proposed estimation and task-capture
  tools represent adjacent jobs to be done. Adding them now would broaden ARI
  without release-specific evidence.

## Implications, not outcome claims

1. Run protocol-compliant follow-ups with P01 and P04 to establish recent
   Go/No-Go participation and reconstruct the last actual release.
2. Recruit external Release Managers and Technical Project Managers; the
   current sample contains no participants outside the author's employer.
3. Measure release-review activity separately from broader delivery reporting,
   meetings, waiting, and board maintenance.
4. Add explicit probes for stale Jira data, direct-team overrides, and the human
   behavior required to keep evidence trustworthy.
5. Keep status-generation, task estimation, voice-to-Jira, and new integrations
   outside the ARI roadmap until evidence meets the product-change threshold.

## Hypothesis status

| Hypothesis | Status | Current evidence | Weakening evidence | Next test |
| --- | --- | --- | --- | --- |
| H1 - Problem | Unvalidated; adjacent early signal | P01 reported recurring manual release-related and delivery work. | Active release-review time was not isolated; repository/CI monitoring was low-effort. | Last-actual-release interviews with eligible practitioners. |
| H2 - Workflow fragmentation | Unvalidated; adjacent early signal | Four participants reported status moving across several people or surfaces. | Evidence concerns broader delivery status and comes from one organizational context. | Observe or reconstruct actual Go/No-Go evidence navigation. |
| H3 - Solution usefulness | Unvalidated | None; no ARI concept test was documented. | R&D participants prioritized adjacent tools. | Problem-first interview followed by a neutral ARI concept task. |
| H4 - Outcome | Unvalidated | None. | No controlled timing, recall, evidence-coverage, or trust data. | Controlled manual-versus-ARI pilot after problem/solution fit is clearer. |

## Decision-gate assessment

| Gate | Current result | Reason |
| --- | --- | --- |
| Problem-validation gate | Not met | 0 interviews fully meet the current protocol; required denominator is unavailable. |
| Solution-validation gate | Not assessed | No documented ARI concept test. |
| Pilot gate | Not assessed | No controlled runs. |

## Limitations

- The sample contains four colleagues from one organizational context and no
  external participants.
- Interviews predated the current protocol; eligibility and recent
  release-decision participation were not recorded consistently.
- Evidence comes from researcher-provided summaries rather than recordings,
  transcripts, or direct workflow observation.
- All workflow and time statements are self-reported and vulnerable to recall
  and aggregation error.
- Several findings concern delivery management, estimation, and reporting rather
  than release readiness.
- No product concept test or pilot outcome is available.
- Findings cannot be generalized beyond this exploratory sample.
