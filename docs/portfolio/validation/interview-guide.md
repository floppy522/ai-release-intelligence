# External Validation Interview Guide

## Purpose and session shape

Use this guide for 30-40 minute interviews with practitioners who participate
in planned production-release decisions. The sequence matters: complete problem
discovery and baseline questions before showing AI Release Intelligence (ARI).

| Segment | Time | Product visible? |
| --- | ---: | --- |
| Consent and role fit | 3 minutes | No |
| Last actual release | 15 minutes | No |
| Quantitative baseline | 5 minutes | No |
| Concept test | 10 minutes | Yes |
| Wrap-up | 2-5 minutes | Yes |

## Opening and consent

1. Explain that this is research, not a sales call, and that critical feedback
   is more useful than positive feedback.
2. Confirm that the participant has joined a planned release decision in the
   previous six months.
3. Ask them not to share customer data, credentials, private URLs, internal
   ticket names, or sensitive incident details.
4. Confirm whether note-taking, recording, and screen sharing are allowed. These
   are separate permissions; default to notes only.
5. State that public reporting will be anonymized and aggregated. Do not promise
   anonymity that cannot be maintained in the private research record.

## Part A - Role and release context

- What was your role in the most recent planned production release you joined?
- How often do you participate in planned releases?
- Who made the final Go/No-Go decision in that release?
- What could you decide yourself, and what required another person's approval?
- What counted as the release scope?

If the participant answers with a general process, redirect: “Please anchor this
in the last release you can safely discuss. What happened first?”

## Part A - Last actual release walkthrough

Ask for events in sequence. Probe concrete actions before opinions.

- What triggered the review, and when did you personally start?
- What did you open first? What did you do next?
- Which tools, pages, dashboards, documents, or messages did you use?
- How did you decide that the intended scope was complete?
- How did you connect scoped issues to pull requests and the candidate branch?
- Which CI or status checks did you inspect? How did you treat a failed check?
- How did you verify critical blockers and dependencies?
- Which before-, during-, or after-release actions had to be confirmed?
- Were migrations involved? What evidence made them ready or not ready?
- Were risks accepted? Who accepted them, and where was the decision recorded?
- Was a back-merge required? If so, how was it verified?
- Where did you compare evidence manually or carry information mentally?
- What was uncertain at the decision point?
- What went wrong, was missed, or nearly went wrong?
- What happened after the decision?

Useful neutral probes:

- “What did you look at to know that?”
- “Where was that recorded?”
- “What happened in this release, specifically?”
- “What would have changed the decision?”
- “Was that a required check or your personal practice?”

## Part A - Quantitative baseline

- About how long did the last review take end to end?
- Of that time, how much was active checking or reconciliation rather than
  waiting for people or systems?
- What range would be more honest if one number is hard to recall?
- How many distinct surfaces or systems did you use?
- How often does a comparable review occur?
- Which checks were mandatory, policy-specific, or commonly skipped?
- How often did you revisit the same page or evidence?

Label all recalled numbers **Self-reported**. Only clocked or reproducibly
recorded measurements are **Verified**.

## Part B - Concept test

Only now open the current public synthetic demo. Give the participant a short,
neutral task: “Use this report to decide what you would do next. Think aloud and
open any evidence you consider necessary.” Do not explain the intended answer
unless the participant is blocked by the demo mechanics rather than the concept.

Observe before probing:

- the first status, finding, and evidence link inspected;
- navigation back to GitHub or other manual checks;
- misunderstandings, hesitation, and unprompted trust concerns;
- whether the participant can state a decision and required actions.

Then ask:

- What would you distrust in this report?
- Which evidence did you want to open first, and why?
- What is missing for a real Go/No-Go decision in your workflow?
- Which rule is wrong or too rigid for the release you described?
- Which checks must be configurable, and who should control the policy?
- When would you fall back to a manual review?
- What false positive would make the product too noisy?
- What false negative would make it unacceptable?
- How does `NEEDS_DECISION` fit or conflict with your risk-acceptance process?
- How does `INSUFFICIENT_DATA` fit or conflict with your process?
- Would you use this as decision support? Choose `Yes`, `Yes, with manual
  verification`, or `No`, and explain the boundary.

Avoid “Do you like it?”, “Would automation be useful?”, feature pitching, and
questions that assume ARI solves the participant's problem.

## Wrap-up

- What is the most important thing the current product model gets wrong?
- Is there another release practitioner with direct decision experience whom I
  should invite?
- May I contact you about an observed workflow or controlled pilot?
- May I use a short anonymized quotation? Record exact scope; silence is not
  permission.

Do not ask for willingness to pay unless pricing is a defined research question
for a later phase.

## Private note-taking structure

Use a pseudonymous participant file. Keep raw notes outside the public
repository.

```text
Participant ID:
Interview date and researcher:
Eligibility basis:
Role and safe company context:
Release frequency:
Go/No-Go owner and participant authority:
Consent: notes / recording / screen sharing / quotation:

LAST RELEASE
Sequence with timestamps where available:
Systems and surfaces:
Manual reconciliation steps:
Failed-CI treatment:
Blockers and dependencies:
Operations and migrations:
Accepted risks:
Back-merge:
Uncertainty, failure, or near-miss:

BASELINE
Self-reported total elapsed time:
Self-reported active review time or range:
Self-reported surface count:
Review frequency:

CONCEPT TEST
First evidence opened:
Observed behavior:
Trust concerns:
Missing critical input:
Policy/configuration need:
Manual fallback trigger:
Trust-destroying false positive:
Trust-destroying false negative:
Decision-support acceptance:

EVIDENCE ENTRIES
- [Verified | Self-reported | Inference] Observation:
  Source/timestamp:
  Supports or contradicts:
  Confidence/uncertainty:

RESEARCHER SYNTHESIS
Contradicting evidence:
Unexpected finding:
Potential implication, not yet a decision:
Follow-up consent and action:
```

After the session, classify evidence before synthesizing it. One interview may
reveal a severe flaw, but it does not establish a recurring pattern by itself.
