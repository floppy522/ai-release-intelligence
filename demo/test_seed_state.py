"""Behavioral contracts for the synthetic demo repository state machine."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from hashlib import sha1
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from demo import seed_state

MANIFEST_PATH = ROOT / "demo" / "seed_manifest.yaml"
TEMPLATE_PATH = ROOT / "demo" / "repository"
WRAPPER_PATH = ROOT / "demo" / "seed_demo_repo.sh"
TARGET = "floppy522/ai-release-intelligence-demo"


def _manifest(*, raw: dict[str, Any] | None = None) -> Any:
    document = raw
    if document is None:
        loaded = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
        assert isinstance(loaded, dict)
        document = loaded
    return seed_state.SeedManifest.model_validate(document)


class InMemorySeedClient:
    """One semantic fake whose state survives every reconciliation call."""

    def __init__(self) -> None:
        self.repos: dict[str, Any] = {}
        self.milestones: dict[int, Any] = {}
        self.labels: dict[str, Any] = {}
        self.refs: dict[str, str] = {}
        self.commits: dict[str, Any] = {}
        self.issues: dict[int, Any] = {}
        self.pulls: dict[int, Any] = {}
        self.checks: dict[str, tuple[Any, ...]] = {}
        self.mutation_log: list[tuple[str, Any]] = []
        self.detail_fetches: list[tuple[str, int]] = []
        self.search_pages: dict[tuple[str, str, int], Any] = {}
        self.milestone_pages: dict[int, tuple[Any, ...]] = {}
        self.check_pages: dict[tuple[str, int], Any] = {}
        self.permission = "admin"
        self.auth_error = False
        self.authenticated = False
        self.auto_checks = True
        self.next_number = 1
        self.bootstrap_sha = ""

    @staticmethod
    def _sha(*parts: object) -> str:
        encoded = "\0".join(str(part) for part in parts).encode()
        return sha1(encoded, usedforsecurity=False).hexdigest()

    def authenticate(self) -> None:
        if self.auth_error:
            raise seed_state.SeedError("GitHub authentication failed")
        self.authenticated = True

    def ensure_repository(self, target: str, template: Path, spec: Any) -> str:
        assert template == TEMPLATE_PATH
        self.bootstrap_sha = self._sha(
            "bootstrap", spec.message, spec.name, spec.email, spec.date
        )
        if target not in self.repos:
            self.repos[target] = seed_state.RepositoryState(
                name_with_owner=target,
                visibility="PUBLIC",
                url=f"https://github.com/{target}",
            )
            self.commits[self.bootstrap_sha] = seed_state.CommitState(
                sha=self.bootstrap_sha,
                parents=(),
                message=spec.message,
                tree_sha=self._sha("tree", "bootstrap"),
            )
            self.refs["main"] = self.bootstrap_sha
            self.mutation_log.append(("create_repository", target))
        return self.bootstrap_sha

    def get_repository(self, target: str) -> Any:
        return self.repos[target]

    def collaborator_permission(self, target: str, login: str) -> str:
        assert target == TARGET
        assert login == "floppy522"
        return self.permission

    def list_milestones(
        self, target: str, *, page: int, per_page: int
    ) -> tuple[Any, ...]:
        assert target == TARGET
        if page in self.milestone_pages:
            return self.milestone_pages[page]
        ordered = tuple(self.milestones[number] for number in sorted(self.milestones))
        start = (page - 1) * per_page
        return ordered[start : start + per_page]

    def create_milestone(self, target: str, title: str) -> Any:
        assert target == TARGET
        number = max(self.milestones, default=0) + 1
        milestone = seed_state.MilestoneState(number=number, title=title)
        self.milestones[number] = milestone
        self.mutation_log.append(("create_milestone", milestone))
        return milestone

    def get_label(self, target: str, name: str) -> Any | None:
        assert target == TARGET
        return self.labels.get(name)

    def upsert_label(self, target: str, label: Any) -> None:
        assert target == TARGET
        if self.labels.get(label.name) != label:
            self.labels[label.name] = label
            self.mutation_log.append(("upsert_label", label))

    def search_marker(
        self,
        target: str,
        marker: str,
        *,
        kind: str,
        page: int,
        per_page: int,
    ) -> Any:
        assert target == TARGET
        override = self.search_pages.get((kind, marker, page))
        if override is not None:
            return override
        token = f"[{marker}]"
        records = self.issues if kind == "issue" else self.pulls
        hits = tuple(
            seed_state.SearchHit(
                number=record.number,
                title=record.title,
                is_pull_request=kind == "pr",
            )
            for record in sorted(records.values(), key=lambda item: item.number)
            if token in record.title
        )
        start = (page - 1) * per_page
        return seed_state.SearchPage(
            total_count=len(hits), items=hits[start : start + per_page]
        )

    def get_issue(self, target: str, number: int) -> Any:
        assert target == TARGET
        self.detail_fetches.append(("issue", number))
        return self.issues[number]

    def create_issue(self, target: str, draft: Any) -> Any:
        assert target == TARGET
        number = self._next_number()
        issue = seed_state.IssueState(
            number=number,
            title=draft.title,
            body=draft.body,
            labels=draft.labels,
            milestone_number=draft.milestone_number,
            state=draft.state,
            assignees=draft.assignees,
        )
        self.issues[number] = issue
        self.mutation_log.append(("create_issue", draft))
        return issue

    def update_issue(self, target: str, number: int, patch: Any) -> Any:
        assert target == TARGET
        current = self.issues[number]
        issue = replace(
            current,
            title=patch.title,
            body=current.body if patch.body is None else patch.body,
            labels=patch.labels,
            milestone_number=patch.milestone_number,
            state=patch.state,
            assignees=patch.assignees,
        )
        self.issues[number] = issue
        self.mutation_log.append(("update_issue", (number, patch)))
        return issue

    def update_issue_body(self, target: str, number: int, body: str) -> Any:
        assert target == TARGET
        issue = replace(self.issues[number], body=body)
        self.issues[number] = issue
        self.mutation_log.append(("update_issue_body", (number, body)))
        return issue

    def get_ref(self, target: str, branch: str) -> str | None:
        assert target == TARGET
        return self.refs.get(branch)

    def create_ref(self, target: str, branch: str, sha: str) -> None:
        assert target == TARGET
        assert branch not in self.refs
        self.refs[branch] = sha
        self.mutation_log.append(("create_ref", (branch, sha)))
        if branch == "release/2026-08-10" and self.auto_checks:
            self.checks.setdefault(
                sha,
                (
                    seed_state.CheckRunState(
                        name="blocking-suite",
                        status="completed",
                        conclusion="success",
                        html_url=f"https://github.com/{target}/runs/7001",
                        head_sha=sha,
                    ),
                    seed_state.CheckRunState(
                        name="advisory-synthetic",
                        status="completed",
                        conclusion="failure",
                        html_url=f"https://github.com/{target}/runs/7002",
                        head_sha=sha,
                    ),
                ),
            )

    def get_commit(self, target: str, sha: str) -> Any:
        assert target == TARGET
        return self.commits[sha]

    def create_commit(
        self, target: str, parent_sha: str, message: str, spec: Any
    ) -> Any:
        assert target == TARGET
        sha = self._sha("commit", parent_sha, message, spec.name, spec.email, spec.date)
        commit = seed_state.CommitState(
            sha=sha,
            parents=(parent_sha,),
            message=message,
            tree_sha=self.commits[parent_sha].tree_sha,
        )
        self.commits[sha] = commit
        self.mutation_log.append(("create_commit", commit))
        return commit

    def get_pull(self, target: str, number: int) -> Any:
        assert target == TARGET
        self.detail_fetches.append(("pr", number))
        return self.pulls[number]

    def create_pull(self, target: str, draft: Any) -> Any:
        assert target == TARGET
        number = self._next_number()
        pull = seed_state.PullRequestState(
            number=number,
            title=draft.title,
            body=draft.body,
            labels=draft.labels,
            milestone_number=draft.milestone_number,
            state="open",
            merged=False,
            head_ref=draft.head_ref,
            base_ref=draft.base_ref,
            head_sha=self.refs[draft.head_ref],
            merge_commit_sha=None,
            head_repository=target,
            base_repository=target,
        )
        self.pulls[number] = pull
        self.mutation_log.append(("create_pull", draft))
        return pull

    def update_pull(self, target: str, number: int, patch: Any) -> Any:
        assert target == TARGET
        pull = replace(
            self.pulls[number],
            title=patch.title,
            body=patch.body,
            labels=patch.labels,
            milestone_number=patch.milestone_number,
        )
        self.pulls[number] = pull
        self.mutation_log.append(("update_pull", (number, patch)))
        return pull

    def merge_pull(
        self, target: str, number: int, *, head_sha: str, commit_title: str
    ) -> Any:
        assert target == TARGET
        pull = self.pulls[number]
        assert pull.state == "open"
        assert pull.head_sha == head_sha
        base_sha = self.refs[pull.base_ref]
        merge_sha = self._sha("merge", number, base_sha, head_sha, commit_title)
        self.commits[merge_sha] = seed_state.CommitState(
            sha=merge_sha,
            parents=(base_sha, head_sha),
            message=commit_title,
            tree_sha=self.commits[head_sha].tree_sha,
        )
        self.refs[pull.base_ref] = merge_sha
        merged = replace(pull, state="closed", merged=True, merge_commit_sha=merge_sha)
        self.pulls[number] = merged
        self.mutation_log.append(("merge_pull", (number, head_sha, merge_sha)))
        return merged

    def list_check_runs(
        self, target: str, sha: str, *, page: int, per_page: int
    ) -> Any:
        assert target == TARGET
        override = self.check_pages.get((sha, page))
        if override is not None:
            return override
        checks = self.checks.get(sha, ())
        start = (page - 1) * per_page
        return seed_state.CheckPage(
            total_count=len(checks), items=checks[start : start + per_page]
        )

    def pause(self, seconds: float) -> None:
        assert seconds >= 0

    def _next_number(self) -> int:
        number = self.next_number
        self.next_number += 1
        return number


def _seed(fake: InMemorySeedClient, manifest: Any | None = None) -> Any:
    return seed_state.seed_repository(
        _manifest() if manifest is None else manifest,
        fake,
        template=TEMPLATE_PATH,
        check_attempts=2,
        check_wait_seconds=0,
    )


def _record_for_marker(records: dict[int, Any], marker: str) -> Any:
    token = f"[{marker}]"
    matches = [record for record in records.values() if token in record.title]
    assert len(matches) == 1
    return matches[0]


def _managed_pulls(fake: InMemorySeedClient) -> tuple[Any, Any, Any]:
    return (
        _record_for_marker(fake.pulls, "ari-demo:v1:previous-release"),
        _record_for_marker(fake.pulls, "ari-demo:v1:previous-main"),
        _record_for_marker(fake.pulls, "ari-demo:v1:current-main"),
    )


def _operations_issue(fake: InMemorySeedClient) -> Any:
    return _record_for_marker(fake.issues, "ari-demo:v1:release-operations")


def _assert_exact_topology(fake: InMemorySeedClient) -> None:
    previous_release, previous_main, current_main = _managed_pulls(fake)
    bootstrap = fake.bootstrap_sha
    assert fake.commits[previous_release.head_sha].parents == (bootstrap,)
    assert fake.commits[previous_release.merge_commit_sha].parents == (
        bootstrap,
        previous_release.head_sha,
    )
    assert fake.refs["release/2026-08-03"] == previous_release.merge_commit_sha
    assert fake.commits[previous_main.head_sha].parents == (bootstrap,)
    assert fake.commits[previous_main.merge_commit_sha].parents == (
        bootstrap,
        previous_main.head_sha,
    )
    assert fake.commits[current_main.head_sha].parents == (
        previous_main.merge_commit_sha,
    )
    assert fake.commits[current_main.merge_commit_sha].parents == (
        previous_main.merge_commit_sha,
        current_main.head_sha,
    )
    assert fake.refs["main"] == current_main.merge_commit_sha
    assert fake.refs["release/2026-08-10"] == current_main.merge_commit_sha
    for pull in (previous_release, previous_main, current_main):
        assert fake.refs[pull.head_ref] == pull.head_sha
        assert pull.merged is True
        assert pull.state == "closed"
        assert pull.head_repository == TARGET
        assert pull.base_repository == TARGET
        assert "Related to #" in pull.body
        assert "Fixes #" not in pull.body


def test_shared_state_converges_empty_repeat_and_repairable_partial() -> None:
    """A pre-merge-SHA guard must not reject the same post-merge fixture."""

    fake = InMemorySeedClient()
    _seed(fake)
    assert fake.mutation_log
    assert all("delete" not in operation for operation, _ in fake.mutation_log)
    _assert_exact_topology(fake)

    fake.mutation_log.clear()
    _seed(fake)
    assert fake.mutation_log == []
    _assert_exact_topology(fake)

    _, previous_main, current_main = _managed_pulls(fake)
    fake.pulls[current_main.number] = replace(
        current_main,
        state="open",
        merged=False,
        merge_commit_sha=InMemorySeedClient._sha("github-test-merge"),
    )
    fake.refs["main"] = previous_main.merge_commit_sha
    del fake.refs["release/2026-08-10"]
    del fake.refs["release/2026-08-03"]
    del fake.refs[current_main.head_ref]
    current_issue = _record_for_marker(fake.issues, "ari-demo:v1:current-code")
    fake.issues[current_issue.number] = replace(
        current_issue,
        state="closed",
        labels=frozenset({"code-change", "Release-Ops", "unrelated"}),
    )

    _seed(fake)
    _assert_exact_topology(fake)
    repaired = fake.issues[current_issue.number]
    assert repaired.state == "open"
    assert repaired.labels == frozenset(
        {"code-change", "migration-required", "unrelated"}
    )
    assert {entry[0] for entry in fake.mutation_log} >= {
        "create_ref",
        "merge_pull",
        "update_issue",
    }


def test_schema_valid_metacharacters_remain_literal_state_data(tmp_path: Path) -> None:
    """Interpreting a valid title/body as syntax would create the sentinel."""

    loaded = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    sentinel = tmp_path / "literal-argv-sentinel"
    title = (
        "[ari-demo:v1:current-code] literal ; $(touch "
        f"{sentinel}) 'quoted' & still data"
    )
    body = (
        "<!-- ari-demo:v1:current-code -->\n"
        f"line one; $(touch {sentinel})\nline two `touch {sentinel}` & literal\n"
    )
    issues = loaded["issues"]
    assert isinstance(issues, list)
    current = next(issue for issue in issues if issue["key"] == "current-code")
    current["title"] = title
    current["body"] = body
    manifest = _manifest(raw=loaded)

    fake = InMemorySeedClient()
    _seed(fake, manifest)
    stored = _record_for_marker(fake.issues, "ari-demo:v1:current-code")
    operations = _operations_issue(fake)
    assert stored.title == title
    assert stored.body == body
    assert operations.assignees == ("floppy522",)
    assert "ari-demo:v1:current-code" in stored.title
    assert any(
        operation == "create_issue" and payload.title == title and payload.body == body
        for operation, payload in fake.mutation_log
    )
    assert not sentinel.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("milestone_number", "7"),
        ("candidate_branch", "release/2026-08-10; unsafe"),
    ],
)
def test_manifest_model_rejects_coercion_and_noncanonical_fields(
    field: str, value: object
) -> None:
    """Loose coercion or partial validation would admit an unsafe manifest."""

    loaded = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    loaded[field] = value
    with pytest.raises(ValueError):
        seed_state.SeedManifest.model_validate(loaded)


def test_manifest_model_forbids_unknown_fields() -> None:
    """An ignored extension could bypass the intentionally bounded schema."""

    loaded = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    loaded["implementation"] = "shell"
    with pytest.raises(ValueError):
        seed_state.SeedManifest.model_validate(loaded)


def test_identical_rerun_uses_observed_pr_head_and_merge_identities() -> None:
    """Replacing observed merge identities with pre-merge refs must fail this test."""

    fake = InMemorySeedClient()
    _seed(fake)
    observed = tuple(
        (pull.head_sha, pull.merge_commit_sha) for pull in _managed_pulls(fake)
    )
    fake.mutation_log.clear()
    _seed(fake)
    assert (
        tuple((pull.head_sha, pull.merge_commit_sha) for pull in _managed_pulls(fake))
        == observed
    )
    assert fake.mutation_log == []


def test_invalid_polling_configuration_fails_before_repository_mutation() -> None:
    """Invalid local control input must not create a remote repository."""

    fake = InMemorySeedClient()
    with pytest.raises(seed_state.SeedError, match="polling is invalid"):
        seed_state.seed_repository(
            _manifest(),
            fake,
            template=TEMPLATE_PATH,
            check_attempts=25,
            check_wait_seconds=0,
        )
    assert fake.repos == {}
    assert fake.mutation_log == []


def test_authentication_failure_stops_before_repository_mutation() -> None:
    """A missing authenticated GitHub session cannot reach any mutation."""

    fake = InMemorySeedClient()
    fake.auth_error = True
    with pytest.raises(seed_state.SeedError, match="authentication failed"):
        _seed(fake)
    assert fake.repos == {}
    assert fake.mutation_log == []


def test_cli_refuses_every_noncanonical_target_before_authentication(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A wrong owner or repository must not reach the injected client."""

    fake = InMemorySeedClient()
    assert (
        seed_state.main(["someone-else/ai-release-intelligence-demo"], client=fake) == 1
    )
    assert fake.authenticated is False
    assert fake.mutation_log == []
    assert "usage:" in capsys.readouterr().err


@pytest.mark.parametrize("preflight", ["private", "unassignable"])
def test_repository_identity_and_assignability_fail_before_fixture_mutation(
    preflight: str,
) -> None:
    """Only the canonical PUBLIC repository and assignable owner are accepted."""

    fake = InMemorySeedClient()
    fake.ensure_repository(TARGET, TEMPLATE_PATH, seed_state.BOOTSTRAP_SPEC)
    if preflight == "private":
        fake.repos[TARGET] = replace(fake.repos[TARGET], visibility="PRIVATE")
    else:
        fake.permission = "read"
    fake.mutation_log.clear()
    with pytest.raises(seed_state.SeedError):
        _seed(fake)
    assert fake.milestones == {}
    assert fake.issues == {}
    assert fake.pulls == {}
    assert fake.mutation_log == []


@pytest.mark.parametrize("kind", ["issue", "pr"])
def test_marker_search_rejects_exact_duplicate_on_second_page(kind: str) -> None:
    """A first-page-only or fuzzy lookup would silently accept the duplicate."""

    fake = InMemorySeedClient()
    _seed(fake)
    marker = (
        "ari-demo:v1:current-code" if kind == "issue" else "ari-demo:v1:current-main"
    )
    records = fake.issues if kind == "issue" else fake.pulls
    original = _record_for_marker(records, marker)
    duplicate_number = 999
    records[duplicate_number] = replace(original, number=duplicate_number)
    first = seed_state.SearchHit(
        number=original.number,
        title=original.title,
        is_pull_request=kind == "pr",
    )
    second = seed_state.SearchHit(
        number=duplicate_number,
        title=original.title,
        is_pull_request=kind == "pr",
    )
    fake.search_pages[(kind, marker, 1)] = seed_state.SearchPage(
        total_count=2, items=(first,)
    )
    fake.search_pages[(kind, marker, 2)] = seed_state.SearchPage(
        total_count=2, items=(second,)
    )
    old_body = _operations_issue(fake).body
    fake.mutation_log.clear()

    with pytest.raises(seed_state.SeedError, match="marker search conflict"):
        _seed(fake)
    assert _operations_issue(fake).body == old_body
    assert fake.mutation_log == []
    assert (kind, original.number) in fake.detail_fetches
    assert (kind, duplicate_number) in fake.detail_fetches


def test_milestone_pagination_rejects_page_two_title_conflict() -> None:
    """Stopping after the first full page would miss a conflicting milestone."""

    fake = InMemorySeedClient()
    _seed(fake)
    old_body = _operations_issue(fake).body
    first_page = tuple(
        seed_state.MilestoneState(
            number=number,
            title=(
                fake.milestones[number].title
                if number in fake.milestones
                else f"Unmanaged milestone {number}"
            ),
        )
        for number in range(1, 101)
    )
    fake.milestone_pages[1] = first_page
    fake.milestone_pages[2] = (
        seed_state.MilestoneState(number=101, title="Release 2026.08.10"),
    )
    fake.mutation_log.clear()

    with pytest.raises(seed_state.SeedError, match="milestone conflict"):
        _seed(fake)
    assert _operations_issue(fake).body == old_body
    assert fake.mutation_log == []


def test_search_total_overflow_fails_closed_before_detail_fetch() -> None:
    """An unbounded Search result must not be mistaken for no managed Issue."""

    fake = InMemorySeedClient()
    _seed(fake)
    marker = "ari-demo:v1:previous-code"
    fake.search_pages[("issue", marker, 1)] = seed_state.SearchPage(
        total_count=201, items=()
    )
    old_fetches = list(fake.detail_fetches)
    old_body = _operations_issue(fake).body
    fake.mutation_log.clear()

    with pytest.raises(seed_state.SeedError, match="search result limit"):
        _seed(fake)
    assert fake.detail_fetches == old_fetches
    assert _operations_issue(fake).body == old_body
    assert fake.mutation_log == []


@pytest.mark.parametrize(
    "conflict",
    [
        "wrong_feature_parent",
        "wrong_feature_ref",
        "wrong_pr_head",
        "wrong_pr_base",
        "diverged_main",
        "candidate_before_main",
    ],
)
def test_managed_topology_conflicts_fail_closed(conflict: str) -> None:
    """Managed ancestry, refs, head/base, and candidate inclusion are exact."""

    fake = InMemorySeedClient()
    _seed(fake)
    previous_release, previous_main, current_main = _managed_pulls(fake)
    old_operations = replace(
        _operations_issue(fake),
        body=(
            "preserve these exact bytes\n\n### Migration evidence\n"
            f"https://github.com/{TARGET}/runs/6000\n"
        ),
    )
    fake.issues[old_operations.number] = old_operations
    if conflict == "wrong_feature_parent":
        fake.commits[current_main.head_sha] = replace(
            fake.commits[current_main.head_sha], parents=(fake.bootstrap_sha,)
        )
    elif conflict == "wrong_feature_ref":
        fake.refs[current_main.head_ref] = fake.bootstrap_sha
    elif conflict == "wrong_pr_head":
        fake.pulls[current_main.number] = replace(
            current_main, head_sha=previous_release.head_sha
        )
    elif conflict == "wrong_pr_base":
        fake.pulls[current_main.number] = replace(
            current_main, base_ref="release/2026-08-03"
        )
    elif conflict == "diverged_main":
        unrelated = InMemorySeedClient._sha("unrelated")
        fake.commits[unrelated] = seed_state.CommitState(
            sha=unrelated,
            parents=(current_main.merge_commit_sha,),
            message="unrelated",
            tree_sha=fake.commits[current_main.head_sha].tree_sha,
        )
        fake.refs["main"] = unrelated
    else:
        fake.refs["release/2026-08-10"] = previous_main.merge_commit_sha
    fake.mutation_log.clear()

    with pytest.raises(seed_state.SeedError, match="managed topology conflict"):
        _seed(fake)
    assert fake.issues[old_operations.number].body == old_operations.body
    assert fake.mutation_log == []


@pytest.mark.parametrize("failure", ["check", "url", "pagination"])
def test_operations_body_is_byte_preserved_until_all_gates_pass(failure: str) -> None:
    """A later check or pagination failure must not strip valid old evidence."""

    fake = InMemorySeedClient()
    _seed(fake)
    operations = _operations_issue(fake)
    old_body = (
        "custom operator bytes\n\n### Migration evidence\n"
        f"https://github.com/{TARGET}/runs/6123\n"
    )
    fake.issues[operations.number] = replace(operations, body=old_body)
    candidate = fake.refs["release/2026-08-10"]
    if failure == "check":
        fake.checks[candidate] = ()
    elif failure == "url":
        fake.checks[candidate] = (
            seed_state.CheckRunState(
                name="blocking-suite",
                status="completed",
                conclusion="success",
                html_url="https://github.com/attacker/other/runs/7001",
                head_sha=candidate,
            ),
        )
    else:
        marker = "ari-demo:v1:release-operations"
        fake.search_pages[("issue", marker, 1)] = seed_state.SearchPage(
            total_count=201, items=()
        )
    fake.mutation_log.clear()

    with pytest.raises(seed_state.SeedError):
        _seed(fake)
    assert fake.issues[operations.number].body == old_body
    assert fake.mutation_log == []


@pytest.mark.parametrize("bypass_value", [None, "1"])
def test_wrapper_is_locked_and_cannot_reenter_a_shell_implementation(
    tmp_path: Path, bypass_value: str | None
) -> None:
    """An environment marker must not bypass the locked Python entrypoint."""

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    argv_log = tmp_path / "uv.json"
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

Path(os.environ["ARGV_LOG"]).write_text(
    json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd()}), encoding="utf-8"
)
""",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    sentinel = tmp_path / "wrapper-sentinel"
    literal = f"line one\n$(touch {sentinel}); still one argv"
    environment = os.environ | {
        "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
        "ARGV_LOG": str(argv_log),
    }
    if bypass_value is not None:
        environment["ARI_SEED_IMPLEMENTATION"] = bypass_value

    completed = subprocess.run(
        ["bash", str(WRAPPER_PATH), literal],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    invocation = json.loads(argv_log.read_text(encoding="utf-8"))
    assert invocation == {
        "argv": [
            "run",
            "--project",
            "apps/api",
            "python",
            "demo/seed_state.py",
            literal,
        ],
        "cwd": str(ROOT),
    }
    assert not sentinel.exists()


class RecordingRunner:
    def __init__(self, *responses: tuple[int, str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def run(
        self,
        argv: tuple[str, ...] | list[str],
        *,
        cwd: Path | None = None,
        environment: dict[str, str] | None = None,
        timeout_seconds: float | None = None,
        allowed_returncodes: frozenset[int] = frozenset({0}),
    ) -> Any:
        self.calls.append(
            {
                "argv": tuple(argv),
                "cwd": cwd,
                "environment": environment,
                "timeout_seconds": timeout_seconds,
                "allowed_returncodes": allowed_returncodes,
            }
        )
        returncode, stdout = self.responses.pop(0)
        assert returncode in allowed_returncodes
        return seed_state.CommandOutput(returncode=returncode, stdout=stdout)


def _issue_response(
    *, title: str, body: str, labels: tuple[str, ...], assignees: tuple[str, ...]
) -> str:
    return json.dumps(
        {
            "number": 41,
            "title": title,
            "body": body,
            "state": "open",
            "labels": [{"name": label} for label in labels],
            "milestone": {"number": 7},
            "assignees": [{"login": assignee} for assignee in assignees],
            "pull_request": None,
        }
    )


def test_production_adapter_keeps_manifest_scalars_in_literal_argv_fields(
    tmp_path: Path,
) -> None:
    """Joining or shell-parsing API fields would break these exact argv assertions."""

    sentinel = tmp_path / "adapter-sentinel"
    marker = "ari-demo:v1:current-code"
    title = f"[{marker}] ; $(touch {sentinel}) literal"
    body = f"<!-- {marker} -->\n`touch {sentinel}` & literal\n"
    labels = ("code-change", "migration-required")
    assignees = ("floppy522",)
    runner = RecordingRunner(
        (0, _issue_response(title=title, body=body, labels=labels, assignees=assignees))
    )
    client = seed_state.GitHubGitSeedClient(runner=runner)
    draft = seed_state.IssueDraft(
        title=title,
        body=body,
        labels=frozenset(labels),
        milestone_number=7,
        state="open",
        assignees=assignees,
    )

    created = client.create_issue(TARGET, draft)
    argv = runner.calls[0]["argv"]
    assert created.title == title
    assert created.body == body
    assert argv[:5] == (
        "gh",
        "api",
        "--method",
        "POST",
        f"repos/{TARGET}/issues",
    )
    assert f"title={title}" in argv
    assert f"body={body}" in argv
    assert "assignees[]=floppy522" in argv
    assert sum(marker in argument for argument in argv) == 2
    assert not sentinel.exists()


def test_production_adapter_emits_exact_paginated_search_query() -> None:
    """A fuzzy PR list or missing page argument would not satisfy this boundary."""

    marker = "ari-demo:v1:current-main"
    runner = RecordingRunner(
        (
            0,
            json.dumps(
                {
                    "total_count": 1,
                    "items": [
                        {
                            "number": 23,
                            "title": f"[{marker}] exact",
                            "pull_request": {"url": "https://api.github.test/pulls/23"},
                        }
                    ],
                }
            ),
        )
    )
    client = seed_state.GitHubGitSeedClient(runner=runner)

    page = client.search_marker(TARGET, marker, kind="pr", page=2, per_page=100)
    argv = runner.calls[0]["argv"]
    assert page.total_count == 1
    assert argv == (
        "gh",
        "api",
        "--method",
        "GET",
        "/search/issues",
        "-f",
        f'q=repo:{TARGET} "[{marker}]" in:title is:pr',
        "-f",
        "per_page=100",
        "-f",
        "page=2",
    )


def test_subprocess_runner_uses_argv_shell_false_and_bounded_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removing shell=False or the timeout would expose the production boundary."""

    observed: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        observed["argv"] = argv
        observed.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout=b"ok", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    runner = seed_state.SubprocessRunner(
        default_timeout_seconds=7, max_output_bytes=128
    )
    literal = "a; $(touch never)\nsecond line"
    result = runner.run(("gh", "api", "endpoint", "-f", f"body={literal}"))
    assert result.stdout == "ok"
    assert observed["argv"] == [
        "gh",
        "api",
        "endpoint",
        "-f",
        f"body={literal}",
    ]
    assert observed["shell"] is False
    assert observed["timeout"] == 7
    assert observed["capture_output"] is True
    assert observed["check"] is False


def test_subprocess_runner_timeout_and_nonzero_errors_are_constant_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Credential-bearing stderr must never be copied into a raised error."""

    runner = seed_state.SubprocessRunner(
        default_timeout_seconds=1, max_output_bytes=128
    )

    def time_out(*args: Any, **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(args[0], 1, output=b"token=timeout-secret")

    monkeypatch.setattr(subprocess, "run", time_out)
    with pytest.raises(seed_state.SeedError) as timeout_error:
        runner.run(("gh", "auth", "status"))
    assert str(timeout_error.value) == "external command timed out"
    assert "secret" not in str(timeout_error.value)

    def fail(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            argv, 23, stdout=b"", stderr=b"Authorization: nonzero-secret"
        )

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(seed_state.SeedError) as nonzero_error:
        runner.run(("gh", "api", "endpoint"))
    assert str(nonzero_error.value) == "external command failed"
    assert "secret" not in str(nonzero_error.value)


def test_subprocess_runner_rejects_output_over_its_fixed_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A gh response larger than the configured cap must fail closed."""

    def oversized(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(argv, 0, stdout=b"x" * 129, stderr=b"")

    monkeypatch.setattr(subprocess, "run", oversized)
    runner = seed_state.SubprocessRunner(
        default_timeout_seconds=1, max_output_bytes=128
    )
    with pytest.raises(seed_state.SeedError) as caught:
        runner.run(("gh", "api", "endpoint"))
    assert str(caught.value) == "external command output exceeded limit"


def test_production_adapter_rejects_malformed_json_with_constant_error() -> None:
    """Malformed gh output must not leak response bytes or create partial objects."""

    runner = RecordingRunner((0, "not-json Authorization: response-secret"))
    client = seed_state.GitHubGitSeedClient(runner=runner)
    with pytest.raises(seed_state.SeedError) as caught:
        client.get_repository(TARGET)
    assert str(caught.value) == "GitHub returned invalid data"
    assert "secret" not in str(caught.value)


def test_bootstrap_commit_is_deterministic_with_locked_identity_and_dates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ambient Git identity, time, or signing must not change the bootstrap SHA."""

    spec = seed_state.BootstrapSpec(
        message="Initialize fictional release demo",
        name="Fictional Release Demo",
        email="fictional-release-demo@example.invalid",
        date="2026-08-01T00:00:00Z",
    )
    runner = seed_state.SubprocessRunner(
        default_timeout_seconds=20, max_output_bytes=4096
    )
    for name in (
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
    ):
        monkeypatch.delenv(name, raising=False)
    first = seed_state.build_bootstrap_repository(
        TEMPLATE_PATH, tmp_path / "first", spec=spec, runner=runner
    )
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Ambient attacker")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "ambient@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Different ambient identity")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "other@example.invalid")
    second = seed_state.build_bootstrap_repository(
        TEMPLATE_PATH, tmp_path / "second", spec=spec, runner=runner
    )
    assert first == second
    assert len(first) == 40
