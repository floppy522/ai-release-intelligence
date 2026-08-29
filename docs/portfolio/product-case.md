# Product Case

## Executive summary

AI Release Intelligence is evidence-backed Go/No-Go decision support for
GitHub-native releases. It turns one GitHub milestone and one release-candidate
branch into a deterministic readiness assessment, then presents prioritized
findings with links to the underlying GitHub evidence. The MVP is for a small
team that wants a trustworthy status and required actions without assembling a
release review manually.

The product keeps release authority with people: an LLM never determines
readiness. It can optionally explain the already-determined assessment, but it
cannot change status, severity, evidence, or human decisions.

## User and job to be done

The primary persona is a Release Manager or Technical Project Manager in a
small GitHub-native team running planned releases.

When preparing a release, they want to obtain a trustworthy status and
prioritized required actions within minutes without manually reconciling GitHub
objects. In particular, they need to see whether scoped work is merged and in
the candidate branch, required checks and operational actions are complete,
blockers are resolved, and a back-merge is present.

## Current release-readiness workflow

Today, the reviewer opens GitHub milestone, issue, pull-request, branch, check,
and operations evidence separately, then manually reconciles the answers. The
experience-based manual baseline is 30–60 minutes; it is not externally
measured. It is a design input, not a product result.

The product target is a trustworthy, evidence-backed decision in no more than
five minutes. This target is not yet validated through external user research.

## Product hypothesis

If a team can analyze one milestone and release-candidate branch into a
deterministic, evidence-linked report, its release reviewer can move from
manual reconciliation to a faster, more auditable decision process. The MVP is
designed to test that workflow; it does not yet establish adoption, demand,
willingness to pay, or measured time savings.

## Decision principles

- Deterministic rules determine readiness; the LLM never determines readiness.
- Evidence links support every finding, rather than a composite readiness score
  that could average away a critical blocker.
- Human decisions handle advisory risk and are recorded with a reason, actor,
  timestamp, and evidence fingerprint.
- A decision is valid only for its exact evidence fingerprint; changed evidence
  invalidates a stale decision.
- Missing, incomplete, or stale mandatory data is a safety condition rather
  than a silent pass.

## Delivered MVP

The delivered modular-monolith MVP reads one GitHub milestone and one
`release/YYYY-MM-DD` candidate branch. It normalizes issues, pull requests,
links, commits, checks, labels, owners, and timestamps into an immutable
snapshot, then deterministically evaluates scope coverage, CI policy,
blockers, operations, migrations, and back-merges.

It returns one of four states: `READY`, `NOT_READY`, `NEEDS_DECISION`, or
`INSUFFICIENT_DATA`. Advisory checks can receive a human `Accepted risk` or
`Release blocker` decision. The system preserves the deterministic report if
GitHub data is incomplete or the optional AI provider is unavailable.

The delivered scope is intentionally narrow: one repository, one milestone,
and one candidate branch. Jira, Bitbucket, Jenkins, GitLab, multi-repository
releases, deployment orchestration, rollback, webhooks, scheduling, and
notifications are outside the MVP.

## Verified demo walkthrough

The public synthetic demo repository,
[ai-release-intelligence-demo](https://github.com/floppy522/ai-release-intelligence-demo),
contains fictional release evidence only. Its expected scenario is supported by
public demo links for the
[release workflow run](https://github.com/floppy522/ai-release-intelligence-demo/actions/runs/33214335082),
[successful blocking migration job](https://github.com/floppy522/ai-release-intelligence-demo/actions/runs/33214335082/job/98994493757),
and [release operations issue](https://github.com/floppy522/ai-release-intelligence-demo/issues/3).

1. Analyze milestone `Release 2026.08.10` against
   `release/2026-08-10`.
2. The intentionally failed `advisory-synthetic` check produces
   `NEEDS_DECISION` while the blocking suite passes.
3. Review the linked evidence and record a documented human decision for that
   advisory check.
4. Accepting the fictional risk produces `READY`; recording it as a release
   blocker produces `NOT_READY`.

## Success measures and evidence status

| Evidence class | Include |
| --- | --- |
| Verified | implementation, CI, demo topology, workflow jobs |
| Target | correctness thresholds, five-minute outcome |
| Unvalidated | adoption, demand, willingness to pay, measured human time saving |

The implementation, CI, synthetic-demo topology, and workflow jobs are
verifiable project evidence. Configured correctness thresholds and the
five-minute outcome are targets, not retained outcome measurements. No external
interviews were completed before design.

## Current limitations

The problem framing comes from release-management experience and has not yet
been externally validated with GitHub-native teams. The synthetic benchmark and
demo cannot represent every inconsistency found in real repositories.

Readiness still depends on repository policy and milestone hygiene. Human risk
acceptance remains a governance decision; the product does not automate release
authority. Measured human-vs-tool time savings and production-scale performance
results are not yet available.

## Next validation steps

Run structured discovery interviews with Release Managers and Technical Project
Managers in small GitHub-native teams to test the workflow, language, and
decision needs. Then observe comparable manual and product-assisted release
reviews, measuring elapsed human review time and correctness against an agreed
evidence set. Use those results to decide whether the five-minute target,
product scope, and willingness-to-pay hypothesis merit further investment.
