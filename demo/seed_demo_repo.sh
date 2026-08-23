#!/usr/bin/env bash
# Seed only the bounded, fictional public release-demo repository.
# This helper deliberately performs no deletion and never prints credentials.
set -euo pipefail
IFS=$'\n\t'

readonly REQUIRED_OWNER='floppy522'
readonly REQUIRED_REPOSITORY='ai-release-intelligence-demo'
readonly REQUIRED_TARGET="${REQUIRED_OWNER}/${REQUIRED_REPOSITORY}"
readonly SCRIPT_DIRECTORY="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)"
readonly PROJECT_ROOT="$(CDPATH='' cd -- "${SCRIPT_DIRECTORY}/.." && pwd -P)"
readonly DEFAULT_MANIFEST="${SCRIPT_DIRECTORY}/seed_manifest.yaml"
readonly REPOSITORY_TEMPLATE="${SCRIPT_DIRECTORY}/repository"

temporary_directory=''
resolved_manifest=''

cleanup() {
  if [ -n "${temporary_directory}" ] && [ -d "${temporary_directory}" ]; then
    rm -rf -- "${temporary_directory}"
  fi
}
trap cleanup EXIT HUP INT TERM

fail() {
  printf '%s\n' "seed-demo-repo: $*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command is unavailable: $1"
}

[ "$#" -eq 1 ] || fail "usage: $0 ${REQUIRED_TARGET}"
target="$1"
[ "${target}" = "${REQUIRED_TARGET}" ] || fail "refusing target outside ${REQUIRED_TARGET}"
[ -d "${REPOSITORY_TEMPLATE}" ] || fail "repository template is missing"

manifest_path="${ARI_SEED_MANIFEST:-${DEFAULT_MANIFEST}}"
[ -f "${manifest_path}" ] || fail "manifest is missing"
require_command gh
require_command uv
require_command git

# All YAML and JSON parsing uses the locked API environment, never an ambient
# system Python installation. Keep the familiar command spelling below so every
# parser invocation shares this constrained boundary.
python3() {
  uv run --project "${PROJECT_ROOT}/apps/api" python "$@"
}

temporary_directory="$(mktemp -d "${TMPDIR:-/tmp}/ari-demo-seed.XXXXXX")"
resolved_manifest="${temporary_directory}/manifest.json"

# Safe-load and reject anything outside the intentionally small manifest schema
# before a single mutation. Values become JSON data, never shell source.
python3 - "${manifest_path}" "${resolved_manifest}" <<'PY'
import json
import re
import sys

import yaml

source, destination = sys.argv[1:]
with open(source, encoding="utf-8") as stream:
    data = yaml.safe_load(stream)

branch = re.compile(r"^release/\d{4}-\d{2}-\d{2}$")
marker = re.compile(r"^ari-demo:v1:[a-z0-9-]{1,48}$")
key = re.compile(r"^[a-z0-9-]{1,48}$")
label = re.compile(r"^[a-z0-9-]{1,48}$")

def reject(message: str) -> None:
    raise SystemExit(f"unsafe seed manifest: {message}")

def string(value: object, field: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        reject(field)
    if "\x00" in value or not value.isprintable() and "\n" not in value:
        reject(field)
    return value

def mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        reject(field)
    return value

def sequence(value: object, field: str, *, minimum: int, maximum: int) -> list[object]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        reject(field)
    return value

root = mapping(data, "root")
required = {
    "schema_version", "repository", "milestone", "milestone_number",
    "previous_milestone", "previous_milestone_number", "main_branch",
    "candidate_branch", "previous_release_branch", "labels", "checks",
    "issues", "pull_requests", "demo_states",
}
if set(root) != required or root.get("schema_version") != 1:
    reject("root schema")
repository = mapping(root["repository"], "repository")
if repository != {"owner": "floppy522", "name": "ai-release-intelligence-demo", "visibility": "public"}:
    reject("repository")
if string(root["milestone"], "milestone") != "Release 2026.08.10":
    reject("milestone")
if string(root["previous_milestone"], "previous_milestone") != "Release 2026.08.03":
    reject("previous_milestone")
if root["milestone_number"] != 7 or root["previous_milestone_number"] != 6:
    reject("milestone number")
if string(root["main_branch"], "main_branch") != "main":
    reject("main_branch")
for field in ("candidate_branch", "previous_release_branch"):
    if branch.fullmatch(string(root[field], field)) is None:
        reject(field)
if root["candidate_branch"] != "release/2026-08-10" or root["previous_release_branch"] != "release/2026-08-03":
    reject("release branch")
labels = [string(item, "label", maximum=48) for item in sequence(root["labels"], "labels", minimum=4, maximum=12)]
if set(labels) != {"code-change", "release-ops", "release-blocker", "migration-required"} or any(label.fullmatch(item) is None for item in labels):
    reject("labels")
checks = sequence(root["checks"], "checks", minimum=2, maximum=2)
expected_checks = {
    ("blocking-suite", "BLOCKING", "success"),
    ("advisory-synthetic", "ADVISORY", "failure"),
}
check_values = set()
for item in checks:
    entry = mapping(item, "check")
    if set(entry) != {"name", "category", "expected_conclusion"}:
        reject("check fields")
    check_values.add(tuple(string(entry[field], field, maximum=255) for field in ("name", "category", "expected_conclusion")))
if check_values != expected_checks:
    reject("checks")
issues = sequence(root["issues"], "issues", minimum=4, maximum=8)
issue_keys = set()
issue_markers = set()
for item in issues:
    entry = mapping(item, "issue")
    allowed = {"key", "marker", "title", "milestone", "labels", "closed", "body", "assignee"}
    if not set(entry) <= allowed or not {"key", "marker", "title", "milestone", "labels", "closed", "body"} <= set(entry):
        reject("issue fields")
    issue_key = string(entry["key"], "issue key", maximum=48)
    issue_marker = string(entry["marker"], "issue marker", maximum=64)
    if key.fullmatch(issue_key) is None or marker.fullmatch(issue_marker) is None or issue_key in issue_keys or issue_marker in issue_markers:
        reject("issue identity")
    issue_keys.add(issue_key)
    issue_markers.add(issue_marker)
    title = string(entry["title"], "issue title", maximum=512)
    if issue_marker not in title or "\n" in title:
        reject("issue title")
    if string(entry["milestone"], "issue milestone") not in {root["milestone"], root["previous_milestone"]}:
        reject("issue milestone")
    issue_labels = [string(value, "issue label", maximum=48) for value in sequence(entry["labels"], "issue labels", minimum=1, maximum=4)]
    if len(set(issue_labels)) != len(issue_labels) or not set(issue_labels) <= set(labels):
        reject("issue labels")
    if type(entry["closed"]) is not bool or not string(entry["body"], "issue body", maximum=8192):
        reject("issue content")
    if "assignee" in entry and key.fullmatch(string(entry["assignee"], "assignee", maximum=39)) is None:
        reject("assignee")
if {"previous-code", "current-code", "release-operations", "resolved-blocker"} != issue_keys:
    reject("issue set")
pulls = sequence(root["pull_requests"], "pull requests", minimum=3, maximum=6)
pull_markers = set()
for item in pulls:
    entry = mapping(item, "pull request")
    if set(entry) != {"marker", "title", "issue_key", "head", "base", "milestone", "merged"}:
        reject("pull fields")
    pull_marker = string(entry["marker"], "pull marker", maximum=64)
    if marker.fullmatch(pull_marker) is None or pull_marker in pull_markers:
        reject("pull marker")
    pull_markers.add(pull_marker)
    title = string(entry["title"], "pull title", maximum=512)
    if pull_marker not in title or "\n" in title:
        reject("pull title")
    if string(entry["issue_key"], "pull issue key", maximum=48) not in issue_keys:
        reject("pull issue key")
    head = string(entry["head"], "pull head", maximum=255)
    base = string(entry["base"], "pull base", maximum=255)
    if not head.startswith("fixture/") or base not in {"main", root["previous_release_branch"]}:
        reject("pull branch")
    if string(entry["milestone"], "pull milestone") not in {root["milestone"], root["previous_milestone"]} or entry["merged"] is not True:
        reject("pull state")
if pull_markers != {"ari-demo:v1:previous-release", "ari-demo:v1:previous-main", "ari-demo:v1:current-main"}:
    reject("pull set")
states = sequence(root["demo_states"], "demo states", minimum=2, maximum=2)
if {mapping(item, "demo state").get("expected_status") for item in states} != {"NEEDS_DECISION", "READY"}:
    reject("demo states")
with open(destination, "w", encoding="utf-8") as stream:
    json.dump(root, stream, sort_keys=True)
PY

manifest_value() {
  python3 - "${resolved_manifest}" "$1" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
value = data
for part in sys.argv[2].split("."):
    value = value[int(part)] if part.isdigit() else value[part]
if not isinstance(value, (str, int, bool)):
    raise SystemExit("manifest scalar expected")
print(str(value).lower() if isinstance(value, bool) else value)
PY
}

manifest_count() {
  python3 - "${resolved_manifest}" "$1" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
value = data
for part in sys.argv[2].split("."):
    value = value[int(part)] if part.isdigit() else value[part]
if not isinstance(value, list):
    raise SystemExit("manifest list expected")
print(len(value))
PY
}

manifest_body_file() {
  python3 - "${resolved_manifest}" "$1" "$2" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
value = data
for part in sys.argv[2].split("."):
    value = value[int(part)] if part.isdigit() else value[part]
if not isinstance(value, str):
    raise SystemExit("manifest body expected")
with open(sys.argv[3], "w", encoding="utf-8", newline="\n") as stream:
    stream.write(value)
PY
}

manifest_labels() {
  python3 - "${resolved_manifest}" "$1" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
value = data
for part in sys.argv[2].split("."):
    value = value[int(part)] if part.isdigit() else value[part]
if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
    raise SystemExit("manifest labels expected")
print(",".join(value))
PY
}

find_marker_number() {
  json_payload="$1"
  stable_marker="$2"
  kind="$3"
  python3 - "${json_payload}" "${stable_marker}" "${kind}" <<'PY'
import json
import sys

try:
    records = json.loads(sys.argv[1])
except json.JSONDecodeError as error:
    raise SystemExit("invalid GitHub list response") from error
if isinstance(records, dict):
    total = records.get("total_count")
    items = records.get("items")
    if type(total) is not int or total < 0 or total > 100 or not isinstance(items, list) or len(items) != total:
        raise SystemExit("incomplete GitHub Search response")
    records = items
if not isinstance(records, list):
    raise SystemExit("invalid GitHub list response")
marker, kind = sys.argv[2:]
matches = [record for record in records if isinstance(record, dict) and marker in record.get("title", "")]
if len(matches) > 1:
    raise SystemExit(f"conflicting {kind} matches for stable marker")
if not matches:
    raise SystemExit(0)
number = matches[0].get("number")
if type(number) is not int or number <= 0:
    raise SystemExit("invalid GitHub marker match")
print(number)
PY
}

# GitHub Search returns a declared total, unlike fuzzy `gh issue list`. The
# managed fixture is capped at one result per marker and fails closed on any
# overflow or duplicate outside a first list page.
search_marker() {
  search_kind="$1"
  stable_marker="$2"
  gh api --method GET '/search/issues' -f "q=repo:${target} ${stable_marker} in:title is:${search_kind}" -f 'per_page=100'
}

require_expected_managed_ref() {
  managed_ref="$1"
  expected_sha="$2"
  observed_sha="$(ref_sha "${managed_ref}")"
  [ "${observed_sha}" = "${expected_sha}" ] || fail "expected managed ref does not match deterministic ancestry"
}

find_pr_number() {
  json_payload="$1"
  stable_marker="$2"
  expected_head="$3"
  expected_base="$4"
  python3 - "${json_payload}" "${stable_marker}" "${expected_head}" "${expected_base}" <<'PY'
import json
import sys

try:
    records = json.loads(sys.argv[1])
except json.JSONDecodeError as error:
    raise SystemExit("invalid GitHub PR list response") from error
if not isinstance(records, list):
    raise SystemExit("invalid GitHub PR list response")
marker, head, base = sys.argv[2:]
matches = [record for record in records if isinstance(record, dict) and marker in record.get("title", "")]
if len(matches) > 1:
    raise SystemExit("conflicting pull request matches for stable marker")
if not matches:
    raise SystemExit(0)
record = matches[0]
if record.get("headRefName") != head or record.get("baseRefName") != base:
    raise SystemExit("conflicting pull request branch relationship")
number = record.get("number")
if type(number) is not int or number <= 0:
    raise SystemExit("invalid GitHub pull request match")
print(number)
PY
}

gh auth status --hostname github.com >/dev/null 2>&1 || fail "GitHub authentication failed"

if ! gh repo view "${target}" --json nameWithOwner,visibility >/dev/null 2>&1; then
  # Initial content upload is isolated in a disposable directory. No credential
  # is embedded in a remote URL; GitHub CLI's configured git helper is used.
  staging_directory="${temporary_directory}/repository"
  mkdir -p "${staging_directory}"
  cp -R "${REPOSITORY_TEMPLATE}/." "${staging_directory}/"
  git -C "${staging_directory}" init -q
  git -C "${staging_directory}" add --all
  GIT_AUTHOR_DATE='2026-08-01T00:00:00Z' GIT_COMMITTER_DATE='2026-08-01T00:00:00Z' git -C "${staging_directory}" -c commit.gpgSign=false -c user.name='Fictional Release Demo' -c user.email='fictional-release-demo@example.invalid' commit -qm 'Initialize fictional release demo'
  git -C "${staging_directory}" branch -M main
  gh repo create "${target}" --public --source "${staging_directory}" --remote origin --push >/dev/null
fi

repository_metadata="$(gh repo view "${target}" --json nameWithOwner,visibility)"
python3 - "${repository_metadata}" "${target}" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
if not isinstance(payload, dict):
    raise SystemExit("invalid repository metadata")
if payload.get("nameWithOwner") != sys.argv[2]:
    raise SystemExit("repository identity mismatch")
if payload.get("visibility") != "PUBLIC":
    raise SystemExit("repository visibility must be PUBLIC")
PY

ensure_milestone() {
  wanted_number="$1"
  wanted_title="$2"
  existing="$(gh api "repos/${target}/milestones?state=all&per_page=100")"
  milestone_number="$(python3 - "${existing}" "${wanted_number}" "${wanted_title}" <<'PY'
import json
import sys

try:
    milestones = json.loads(sys.argv[1])
except json.JSONDecodeError as error:
    raise SystemExit("invalid milestone response") from error
if not isinstance(milestones, list):
    raise SystemExit("invalid milestone response")
number, title = int(sys.argv[2]), sys.argv[3]
at_number = [item for item in milestones if isinstance(item, dict) and item.get("number") == number]
at_title = [item for item in milestones if isinstance(item, dict) and item.get("title") == title]
if len(at_number) > 1 or len(at_title) > 1:
    raise SystemExit("conflicting milestone matches")
if at_number and at_number[0].get("title") != title:
    raise SystemExit("conflicting milestone number")
if at_title and at_title[0].get("number") != number:
    raise SystemExit("conflicting milestone title")
print("present" if at_number else "missing")
PY
)"
  if [ "${milestone_number}" = 'missing' ]; then
    created="$(gh api --method POST "repos/${target}/milestones" -f "title=${wanted_title}")"
    created_number="$(python3 - "${created}" <<'PY'
import json
import sys
payload = json.loads(sys.argv[1])
number = payload.get("number") if isinstance(payload, dict) else None
if type(number) is not int:
    raise SystemExit("invalid created milestone")
print(number)
PY
)"
    [ "${created_number}" = "${wanted_number}" ] || fail "milestone numbering conflicts with required synthetic fixture"
  fi
}

# A new GitHub repository numbers milestones from one. Reserve five clearly
# fictional archived milestones so the prior/current demo evidence is #6/#7.
archive_number=1
while [ "${archive_number}" -le 5 ]; do
  ensure_milestone "${archive_number}" "Fictional archive ${archive_number}"
  archive_number=$((archive_number + 1))
done
ensure_milestone "$(manifest_value previous_milestone_number)" "$(manifest_value previous_milestone)"
ensure_milestone "$(manifest_value milestone_number)" "$(manifest_value milestone)"

gh label create 'code-change' --repo "${target}" --color '1d76db' --description 'Fictional release code change' --force
gh label create 'release-ops' --repo "${target}" --color '5319e7' --description 'Fictional release operations' --force
gh label create 'release-blocker' --repo "${target}" --color 'b60205' --description 'Fictional resolved release blocker' --force
gh label create 'migration-required' --repo "${target}" --color '0e8a16' --description 'Fictional migration evidence required' --force

assignee="$(manifest_value 'issues.2.assignee')"
assignee_permission="$(gh api "repos/${target}/collaborators/${assignee}/permission" --jq '.permission')"
case "${assignee_permission}" in
  admin|maintain|write) ;;
  *) fail "assignee is not permitted to receive managed issues" ;;
esac

ref_sha() {
  gh api "repos/${target}/git/ref/heads/$1" --jq '.object.sha'
}

ensure_branch() {
  branch_name="$1"
  source_branch="$2"
  source_sha="$(ref_sha "${source_branch}")"
  if gh api "repos/${target}/git/ref/heads/${branch_name}" >/dev/null 2>&1; then
    require_expected_managed_ref "${branch_name}" "${source_sha}"
    return
  fi
  gh api --method POST "repos/${target}/git/refs" -f "ref=refs/heads/${branch_name}" -f "sha=${source_sha}" >/dev/null
}

create_feature_branch() {
  branch_name="$1"
  source_branch="$2"
  commit_message="$3"
  source_sha="$(ref_sha "${source_branch}")"
  if gh api "repos/${target}/git/ref/heads/${branch_name}" >/dev/null 2>&1; then
    existing_sha="$(ref_sha "${branch_name}")"
    existing_commit="$(gh api "repos/${target}/git/commits/${existing_sha}")"
    python3 - "${existing_commit}" "${source_sha}" "${commit_message}" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
parents = payload.get("parents") if isinstance(payload, dict) else None
message = payload.get("message") if isinstance(payload, dict) else None
parent_sha = parents[0].get("sha") if isinstance(parents, list) and len(parents) == 1 and isinstance(parents[0], dict) else None
if parent_sha != sys.argv[2] or message != sys.argv[3]:
    raise SystemExit("expected managed ref has stale or unrelated ancestry")
PY
    return
  fi
  source_tree="$(gh api "repos/${target}/git/commits/${source_sha}" --jq '.tree.sha')"
  commit_sha="$(gh api --method POST "repos/${target}/git/commits" -f "message=${commit_message}" -f "tree=${source_tree}" -f "parents[]=${source_sha}" -f 'author[name]=Fictional Release Demo' -f 'author[email]=fictional-release-demo@example.invalid' -f 'author[date]=2026-08-01T00:00:00Z' -f 'committer[name]=Fictional Release Demo' -f 'committer[email]=fictional-release-demo@example.invalid' -f 'committer[date]=2026-08-01T00:00:00Z' --jq '.sha')"
  gh api --method POST "repos/${target}/git/refs" -f "ref=refs/heads/${branch_name}" -f "sha=${commit_sha}" >/dev/null
}

issue_count="$(manifest_count issues)"
issue_index=0
while [ "${issue_index}" -lt "${issue_count}" ]; do
  issue_key="$(manifest_value "issues.${issue_index}.key")"
  issue_marker="$(manifest_value "issues.${issue_index}.marker")"
  issue_title="$(manifest_value "issues.${issue_index}.title")"
  issue_milestone="$(manifest_value "issues.${issue_index}.milestone")"
  issue_labels="$(manifest_labels "issues.${issue_index}.labels")"
  issue_closed="$(manifest_value "issues.${issue_index}.closed")"
  issue_body="${temporary_directory}/issue-${issue_index}.md"
  manifest_body_file "issues.${issue_index}.body" "${issue_body}"
  issue_matches="$(search_marker issue "${issue_marker}")"
  issue_number="$(find_marker_number "${issue_matches}" "${issue_marker}" issue)"
  if [ -z "${issue_number}" ]; then
    gh issue create --repo "${target}" --title "${issue_title}" --body-file "${issue_body}" --label "${issue_labels}" --milestone "${issue_milestone}" >/dev/null
    issue_matches="$(search_marker issue "${issue_marker}")"
    issue_number="$(find_marker_number "${issue_matches}" "${issue_marker}" issue)"
    [ -n "${issue_number}" ] || fail "created issue cannot be reconciled"
  fi
  gh issue edit "${issue_number}" --repo "${target}" --title "${issue_title}" --body-file "${issue_body}" --remove-label 'code-change' --remove-label 'release-ops' --remove-label 'release-blocker' --remove-label 'migration-required' --add-label "${issue_labels}" --milestone "${issue_milestone}" >/dev/null
  if [ "${issue_key}" = 'release-operations' ]; then
    gh issue edit "${issue_number}" --repo "${target}" --add-assignee "$(manifest_value "issues.${issue_index}.assignee")" >/dev/null
  fi
  if [ "${issue_closed}" = 'true' ]; then
    gh issue close "${issue_number}" --repo "${target}" >/dev/null
  else
    gh issue reopen "${issue_number}" --repo "${target}" >/dev/null
  fi
  printf '%s\t%s\n' "${issue_key}" "${issue_number}" >> "${temporary_directory}/issue-numbers.tsv"
  issue_index=$((issue_index + 1))
done

issue_number_for_key() {
  python3 - "${temporary_directory}/issue-numbers.tsv" "$1" <<'PY'
import sys

for line in open(sys.argv[1], encoding="utf-8"):
    key, number = line.rstrip("\n").split("\t", 1)
    if key == sys.argv[2]:
        print(number)
        raise SystemExit(0)
raise SystemExit("missing seeded issue key")
PY
}

# Previous-release evidence first, then its corresponding main back-merge.
ensure_branch "$(manifest_value previous_release_branch)" "$(manifest_value main_branch)"
pull_count="$(manifest_count pull_requests)"
pull_index=0
while [ "${pull_index}" -lt "${pull_count}" ]; do
  pull_marker="$(manifest_value "pull_requests.${pull_index}.marker")"
  pull_title="$(manifest_value "pull_requests.${pull_index}.title")"
  pull_issue_key="$(manifest_value "pull_requests.${pull_index}.issue_key")"
  pull_head="$(manifest_value "pull_requests.${pull_index}.head")"
  pull_base="$(manifest_value "pull_requests.${pull_index}.base")"
  pull_milestone="$(manifest_value "pull_requests.${pull_index}.milestone")"
  issue_number="$(issue_number_for_key "${pull_issue_key}")"
  create_feature_branch "${pull_head}" "${pull_base}" "${pull_title}"
  pull_matches="$(gh pr list --repo "${target}" --state all --search "${pull_marker} in:title" --limit 10 --json number,title,headRefName,baseRefName)"
  pull_number="$(find_pr_number "${pull_matches}" "${pull_marker}" "${pull_head}" "${pull_base}")"
  pull_body="${temporary_directory}/pull-${pull_index}.md"
  printf '%s\n\nRelated to #%s\n' "<!-- ${pull_marker} --> Fictional-only release evidence." "${issue_number}" > "${pull_body}"
  if [ -z "${pull_number}" ]; then
    gh pr create --repo "${target}" --title "${pull_title}" --body-file "${pull_body}" --head "${pull_head}" --base "${pull_base}" >/dev/null
    pull_matches="$(gh pr list --repo "${target}" --state all --search "${pull_marker} in:title" --limit 10 --json number,title,headRefName,baseRefName)"
    pull_number="$(find_pr_number "${pull_matches}" "${pull_marker}" "${pull_head}" "${pull_base}")"
    [ -n "${pull_number}" ] || fail "created pull request cannot be reconciled"
  fi
  gh pr edit "${pull_number}" --repo "${target}" --title "${pull_title}" --body-file "${pull_body}" --add-label 'code-change' --milestone "${pull_milestone}" >/dev/null
  pull_state="$(gh api "repos/${target}/pulls/${pull_number}")"
  merged="$(python3 - "${pull_state}" <<'PY'
import json
import sys
payload = json.loads(sys.argv[1])
print("true" if isinstance(payload, dict) and payload.get("merged") is True else "false")
PY
)"
  if [ "${merged}" != 'true' ]; then
    head_sha="$(python3 - "${pull_state}" <<'PY'
import json
import sys
payload = json.loads(sys.argv[1])
head = payload.get("head") if isinstance(payload, dict) else None
sha = head.get("sha") if isinstance(head, dict) else None
if not isinstance(sha, str) or len(sha) != 40:
    raise SystemExit("invalid pull request head")
print(sha)
PY
)"
    gh api --method PUT "repos/${target}/pulls/${pull_number}/merge" -f "sha=${head_sha}" -f 'merge_method=merge' -f "commit_title=${pull_title}" >/dev/null
  fi
  pull_index=$((pull_index + 1))
done

# The candidate is cut after the main-bound current change, keeping candidate
# inclusion evidence deterministic. Creating the branch starts the safe CI.
ensure_branch "$(manifest_value candidate_branch)" "$(manifest_value main_branch)"

# Actions assigns check-run/job URLs only after the candidate ref exists. Poll a
# bounded number of times and accept exactly one completed successful blocking
# check; never fabricate a migration URL.
candidate_sha="$(ref_sha "$(manifest_value candidate_branch)")"
migration_url=''
migration_attempt=1
migration_wait_seconds="${ARI_SEED_MIGRATION_WAIT_SECONDS:-5}"
while [ "${migration_attempt}" -le 12 ]; do
  check_payload="$(gh api "repos/${target}/commits/${candidate_sha}/check-runs?per_page=100")"
  migration_url="$(python3 - "${check_payload}" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
runs = payload.get("check_runs") if isinstance(payload, dict) else None
if not isinstance(runs, list):
    raise SystemExit("invalid migration check response")
matches = [
    run for run in runs
    if isinstance(run, dict)
    and run.get("name") == "blocking-suite"
    and run.get("status") == "completed"
    and run.get("conclusion") == "success"
    and isinstance(run.get("html_url"), str)
]
if len(matches) > 1:
    raise SystemExit("conflicting migration check evidence")
if matches:
    print(matches[0]["html_url"])
PY
)"
  if [ -n "${migration_url}" ]; then
    break
  fi
  migration_attempt=$((migration_attempt + 1))
  sleep "${migration_wait_seconds}"
done
[ -n "${migration_url}" ] || fail "migration check did not become successful before timeout"
operations_issue_number="$(issue_number_for_key 'release-operations')"
operations_body="${temporary_directory}/release-operations-with-migration.md"
manifest_body_file 'issues.2.body' "${operations_body}"
printf '\n\n### Migration evidence\n%s\n' "${migration_url}" >> "${operations_body}"
gh issue edit "${operations_issue_number}" --repo "${target}" --body-file "${operations_body}" >/dev/null

printf '%s\n' 'seed-demo-repo: fictional demo evidence reconciled; workflow results remain a remote publish gate.'
