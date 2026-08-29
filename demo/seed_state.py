"""Safe, deterministic state machine for the public synthetic demo repository."""

from __future__ import annotations

import json
import math
import os
import re
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, Self, cast
from urllib.parse import quote, urlsplit

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

REQUIRED_TARGET = "floppy522/ai-release-intelligence-demo"
REQUIRED_OWNER = "floppy522"
REQUIRED_REPOSITORY = "ai-release-intelligence-demo"
PAGE_SIZE = 100
MAX_PAGES = 10
MAX_RECORDS = 200
MAX_MANIFEST_BYTES = 128 * 1024
MAX_COMMAND_TIMEOUT_SECONDS = 120
MAX_COMMAND_OUTPUT_BYTES = 16 * 1024 * 1024
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
MARKER_PATTERN = re.compile(r"^ari-demo:v1:[a-z0-9-]{1,48}$")
KEY_PATTERN = re.compile(r"^[a-z0-9-]{1,48}$")
LABEL_PATTERN = re.compile(r"^[a-z0-9-]{1,48}$")
LOGIN_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
CHECK_PATH_PATTERN = re.compile(
    r"^/floppy522/ai-release-intelligence-demo/(?:"
    r"runs/[1-9][0-9]{0,18}(?:/jobs/[1-9][0-9]{0,18})?"
    r"|actions/runs/[1-9][0-9]{0,18}/jobs?/[1-9][0-9]{0,18})$"
)


class SeedError(RuntimeError):
    """A constant-safe failure suitable for the public command boundary."""


def _safe_single_line(value: str, field: str, maximum: int) -> str:
    if not value or len(value) > maximum or "\n" in value or "\r" in value:
        raise ValueError(f"{field} is not a bounded single line")
    if any(not character.isprintable() for character in value):
        raise ValueError(f"{field} contains a control character")
    return value


def _safe_multiline(value: str, field: str, maximum: int) -> str:
    if not value or len(value) > maximum or "\x00" in value or "\r" in value:
        raise ValueError(f"{field} is not bounded text")
    if any(
        not character.isprintable() and character not in {"\n", "\t"}
        for character in value
    ):
        raise ValueError(f"{field} contains a control character")
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RepositoryManifest(StrictModel):
    owner: StrictStr
    name: StrictStr
    visibility: Literal["public"]


class CheckManifest(StrictModel):
    name: StrictStr = Field(min_length=1, max_length=255)
    category: Literal["BLOCKING", "ADVISORY"]
    expected_conclusion: Literal["success", "failure"]

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        return _safe_single_line(value, "check name", 255)


class IssueManifest(StrictModel):
    key: StrictStr
    marker: StrictStr
    title: StrictStr
    milestone: StrictStr
    labels: tuple[StrictStr, ...] = Field(min_length=1, max_length=4)
    closed: StrictBool
    body: StrictStr
    assignee: StrictStr | None = None

    @field_validator("key")
    @classmethod
    def valid_key(cls, value: str) -> str:
        if KEY_PATTERN.fullmatch(value) is None:
            raise ValueError("issue key is not canonical")
        return value

    @field_validator("marker")
    @classmethod
    def valid_marker(cls, value: str) -> str:
        if MARKER_PATTERN.fullmatch(value) is None:
            raise ValueError("issue marker is not canonical")
        return value

    @field_validator("title")
    @classmethod
    def valid_title(cls, value: str) -> str:
        return _safe_single_line(value, "issue title", 512)

    @field_validator("milestone")
    @classmethod
    def valid_milestone(cls, value: str) -> str:
        return _safe_single_line(value, "issue milestone", 160)

    @field_validator("labels")
    @classmethod
    def valid_labels(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("issue labels are not unique")
        if any(LABEL_PATTERN.fullmatch(label) is None for label in value):
            raise ValueError("issue label is not canonical")
        return value

    @field_validator("body")
    @classmethod
    def valid_body(cls, value: str) -> str:
        return _safe_multiline(value, "issue body", 8192)

    @field_validator("assignee")
    @classmethod
    def valid_assignee(cls, value: str | None) -> str | None:
        if value is not None and LOGIN_PATTERN.fullmatch(value) is None:
            raise ValueError("assignee is not canonical")
        return value

    @model_validator(mode="after")
    def marker_is_literal_evidence(self) -> Self:
        if f"[{self.marker}]" not in self.title:
            raise ValueError("issue title is missing the exact marker")
        if f"<!-- {self.marker} -->" not in self.body:
            raise ValueError("issue body is missing the exact marker")
        return self


class PullRequestManifest(StrictModel):
    marker: StrictStr
    title: StrictStr
    issue_key: StrictStr
    head: StrictStr
    base: StrictStr
    milestone: StrictStr
    merged: StrictBool

    @field_validator("marker")
    @classmethod
    def valid_marker(cls, value: str) -> str:
        if MARKER_PATTERN.fullmatch(value) is None:
            raise ValueError("pull request marker is not canonical")
        return value

    @field_validator("title", "head", "base", "milestone")
    @classmethod
    def valid_text(cls, value: str) -> str:
        return _safe_single_line(value, "pull request field", 512)

    @field_validator("issue_key")
    @classmethod
    def valid_issue_key(cls, value: str) -> str:
        if KEY_PATTERN.fullmatch(value) is None:
            raise ValueError("pull request issue key is not canonical")
        return value

    @model_validator(mode="after")
    def marker_is_literal_evidence(self) -> Self:
        if f"[{self.marker}]" not in self.title:
            raise ValueError("pull request title is missing the exact marker")
        return self


class DemoStateManifest(StrictModel):
    name: StrictStr = Field(min_length=1, max_length=160)
    expected_status: Literal["NEEDS_DECISION", "READY"]
    rationale: StrictStr = Field(min_length=1, max_length=1024)


class SeedManifest(StrictModel):
    schema_version: StrictInt
    repository: RepositoryManifest
    milestone: StrictStr
    milestone_number: StrictInt
    previous_milestone: StrictStr
    previous_milestone_number: StrictInt
    main_branch: StrictStr
    candidate_branch: StrictStr
    previous_release_branch: StrictStr
    labels: tuple[StrictStr, ...] = Field(min_length=4, max_length=12)
    checks: tuple[CheckManifest, ...] = Field(min_length=2, max_length=2)
    issues: tuple[IssueManifest, ...] = Field(min_length=4, max_length=8)
    pull_requests: tuple[PullRequestManifest, ...] = Field(min_length=3, max_length=6)
    demo_states: tuple[DemoStateManifest, ...] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def canonical_fixture(self) -> Self:
        if self.schema_version != 1:
            raise ValueError("schema version is not supported")
        if self.repository != RepositoryManifest(
            owner=REQUIRED_OWNER,
            name=REQUIRED_REPOSITORY,
            visibility="public",
        ):
            raise ValueError("repository identity is not canonical")
        identity = (
            self.milestone,
            self.milestone_number,
            self.previous_milestone,
            self.previous_milestone_number,
            self.main_branch,
            self.candidate_branch,
            self.previous_release_branch,
        )
        if identity != (
            "Release 2026.08.10",
            7,
            "Release 2026.08.03",
            6,
            "main",
            "release/2026-08-10",
            "release/2026-08-03",
        ):
            raise ValueError("fixture release identity is not canonical")
        managed = {
            "code-change",
            "release-ops",
            "release-blocker",
            "migration-required",
        }
        if len(self.labels) != len(set(self.labels)) or set(self.labels) != managed:
            raise ValueError("managed labels are not canonical")
        checks = {
            (check.name, check.category, check.expected_conclusion)
            for check in self.checks
        }
        if checks != {
            ("blocking-suite", "BLOCKING", "success"),
            ("advisory-synthetic", "ADVISORY", "failure"),
        }:
            raise ValueError("checks are not canonical")
        issues = {issue.key: issue for issue in self.issues}
        if set(issues) != {
            "previous-code",
            "current-code",
            "release-operations",
            "resolved-blocker",
        }:
            raise ValueError("managed issues are not canonical")
        if len({issue.marker for issue in self.issues}) != len(self.issues):
            raise ValueError("managed issue markers are not unique")
        if any(not set(issue.labels) <= managed for issue in self.issues):
            raise ValueError("issue uses an unmanaged manifest label")
        if any(
            issue.milestone not in {self.milestone, self.previous_milestone}
            for issue in self.issues
        ):
            raise ValueError("issue milestone is not managed")
        if issues["release-operations"].assignee != REQUIRED_OWNER:
            raise ValueError("operations assignee is not canonical")
        pulls = {pull.marker: pull for pull in self.pull_requests}
        expected = {
            "ari-demo:v1:previous-release": (
                "previous-code",
                "fixture/previous-release-demo",
                self.previous_release_branch,
                self.previous_milestone,
            ),
            "ari-demo:v1:previous-main": (
                "previous-code",
                "fixture/previous-main-demo",
                self.main_branch,
                self.previous_milestone,
            ),
            "ari-demo:v1:current-main": (
                "current-code",
                "fixture/current-main-demo",
                self.main_branch,
                self.milestone,
            ),
        }
        if set(pulls) != set(expected):
            raise ValueError("managed pull requests are not canonical")
        for marker, relationship in expected.items():
            pull = pulls[marker]
            if (
                pull.issue_key,
                pull.head,
                pull.base,
                pull.milestone,
            ) != relationship or pull.merged is not True:
                raise ValueError("pull request topology is not canonical")
        if {state.expected_status for state in self.demo_states} != {
            "NEEDS_DECISION",
            "READY",
        }:
            raise ValueError("demo states are not canonical")
        return self

    @property
    def target(self) -> str:
        return f"{self.repository.owner}/{self.repository.name}"

    @property
    def managed_labels(self) -> frozenset[str]:
        return frozenset(self.labels)

    @property
    def issues_by_key(self) -> Mapping[str, IssueManifest]:
        return {issue.key: issue for issue in self.issues}

    @property
    def pulls_by_marker(self) -> Mapping[str, PullRequestManifest]:
        return {pull.marker: pull for pull in self.pull_requests}


def load_manifest(path: Path) -> SeedManifest:
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ValueError("manifest is too large")
        document = yaml.safe_load(raw.decode("utf-8"))
        return SeedManifest.model_validate(document)
    except (
        OSError,
        UnicodeError,
        yaml.YAMLError,
        TypeError,
        ValueError,
        ValidationError,
    ) as error:
        raise SeedError("seed manifest is invalid") from error


@dataclass(frozen=True)
class BootstrapSpec:
    message: str
    name: str
    email: str
    date: str


BOOTSTRAP_SPEC = BootstrapSpec(
    message="Initialize fictional release demo",
    name="Fictional Release Demo",
    email="fictional-release-demo@example.invalid",
    date="2026-08-01T00:00:00Z",
)


@dataclass(frozen=True)
class RepositoryState:
    name_with_owner: str
    visibility: str
    url: str


@dataclass(frozen=True)
class MilestoneState:
    number: int
    title: str


@dataclass(frozen=True)
class LabelState:
    name: str
    color: str
    description: str


@dataclass(frozen=True)
class CommitState:
    sha: str
    parents: tuple[str, ...]
    message: str
    tree_sha: str


@dataclass(frozen=True)
class IssueState:
    number: int
    title: str
    body: str
    labels: frozenset[str]
    milestone_number: int | None
    state: Literal["open", "closed"]
    assignees: tuple[str, ...]


@dataclass(frozen=True)
class IssueDraft:
    title: str
    body: str
    labels: frozenset[str]
    milestone_number: int
    state: Literal["open", "closed"]
    assignees: tuple[str, ...]


@dataclass(frozen=True)
class IssuePatch:
    title: str
    body: str | None
    labels: frozenset[str]
    milestone_number: int
    state: Literal["open", "closed"]
    assignees: tuple[str, ...]


@dataclass(frozen=True)
class PullRequestState:
    number: int
    title: str
    body: str
    labels: frozenset[str]
    milestone_number: int | None
    state: Literal["open", "closed"]
    merged: bool
    head_ref: str
    base_ref: str
    head_sha: str
    merge_commit_sha: str | None
    head_repository: str
    base_repository: str


@dataclass(frozen=True)
class PullRequestDraft:
    title: str
    body: str
    labels: frozenset[str]
    milestone_number: int
    head_ref: str
    base_ref: str


@dataclass(frozen=True)
class PullRequestPatch:
    title: str
    body: str
    labels: frozenset[str]
    milestone_number: int


@dataclass(frozen=True)
class SearchHit:
    number: int
    title: str
    is_pull_request: bool


@dataclass(frozen=True)
class SearchPage:
    total_count: int
    items: tuple[SearchHit, ...]


@dataclass(frozen=True)
class CheckRunState:
    name: str
    status: str
    conclusion: str | None
    html_url: str
    head_sha: str


@dataclass(frozen=True)
class CheckPage:
    total_count: int
    items: tuple[CheckRunState, ...]


@dataclass(frozen=True)
class CommandOutput:
    returncode: int
    stdout: str


class CommandRunner(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        environment: dict[str, str] | None = None,
        timeout_seconds: float | None = None,
        allowed_returncodes: frozenset[int] = frozenset({0}),
    ) -> CommandOutput: ...


class _CommandTimedOut(Exception):
    pass


class _CommandOutputExceeded(Exception):
    pass


@dataclass(frozen=True)
class SubprocessRunner:
    default_timeout_seconds: float = 30
    max_output_bytes: int = 1024 * 1024
    termination_grace_seconds: float = 0.5

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.default_timeout_seconds)
            or not 0 < self.default_timeout_seconds <= MAX_COMMAND_TIMEOUT_SECONDS
            or not 1 <= self.max_output_bytes <= MAX_COMMAND_OUTPUT_BYTES
            or not math.isfinite(self.termination_grace_seconds)
            or not 0 < self.termination_grace_seconds <= 5
        ):
            raise ValueError("subprocess runner bounds are invalid")

    @staticmethod
    def _environment(overrides: dict[str, str] | None) -> dict[str, str]:
        environment = dict(os.environ)
        if overrides is not None:
            environment.update(overrides)
        environment.update(
            {
                "GCM_INTERACTIVE": "Never",
                "GH_HOST": "github.com",
                "GH_PAGER": "cat",
                "GH_PROMPT_DISABLED": "1",
                "GIT_PAGER": "cat",
                "GIT_TERMINAL_PROMPT": "0",
            }
        )
        return environment

    @staticmethod
    def _send_signal(process: subprocess.Popen[bytes], requested: int) -> None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, requested)
            elif process.poll() is not None:
                return
            elif requested == signal.SIGTERM:
                process.terminate()
            else:
                process.kill()
        except ProcessLookupError:
            pass
        except OSError as error:
            if process.poll() is None:
                raise SeedError("external command could not be terminated") from error

    def _stop(self, process: subprocess.Popen[bytes]) -> None:
        if os.name == "posix":
            self._send_signal(process, signal.SIGTERM)
            if process.poll() is None:
                try:
                    process.wait(timeout=self.termination_grace_seconds)
                except subprocess.TimeoutExpired:
                    pass
            else:
                process.wait()
            self._send_signal(process, signal.SIGKILL)
            if process.poll() is None:
                try:
                    process.wait(timeout=self.termination_grace_seconds)
                except subprocess.TimeoutExpired as error:
                    raise SeedError(
                        "external command could not be terminated"
                    ) from error
            return
        if process.poll() is not None:
            process.wait()
            return
        self._send_signal(process, signal.SIGTERM)
        try:
            process.wait(timeout=self.termination_grace_seconds)
            return
        except subprocess.TimeoutExpired:
            self._send_signal(process, signal.SIGKILL)
        try:
            process.wait(timeout=self.termination_grace_seconds)
        except subprocess.TimeoutExpired as error:
            raise SeedError("external command could not be terminated") from error

    def _collect(
        self, process: subprocess.Popen[bytes], timeout: float
    ) -> tuple[bytes, bytes]:
        stdout = process.stdout
        stderr = process.stderr
        if stdout is None or stderr is None:
            raise SeedError("external command returned invalid output")
        buffers = {stdout.fileno(): bytearray(), stderr.fileno(): bytearray()}
        selector = selectors.DefaultSelector()
        selector.register(stdout, selectors.EVENT_READ)
        selector.register(stderr, selectors.EVENT_READ)
        deadline = time.monotonic() + timeout
        try:
            while selector.get_map():
                remaining_time = deadline - time.monotonic()
                if remaining_time <= 0:
                    raise _CommandTimedOut
                events = selector.select(timeout=min(remaining_time, 0.05))
                for key, _ in events:
                    total = sum(len(buffer) for buffer in buffers.values())
                    remaining_output = self.max_output_bytes - total
                    try:
                        chunk = os.read(
                            key.fd,
                            min(64 * 1024, remaining_output + 1),
                        )
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    buffer = buffers[key.fd]
                    buffer.extend(chunk[:remaining_output])
                    if len(chunk) > remaining_output:
                        raise _CommandOutputExceeded
            remaining_time = deadline - time.monotonic()
            if remaining_time <= 0:
                raise _CommandTimedOut
            try:
                process.wait(timeout=remaining_time)
            except subprocess.TimeoutExpired as error:
                raise _CommandTimedOut from error
        finally:
            selector.close()
        return bytes(buffers[stdout.fileno()]), bytes(buffers[stderr.fileno()])

    @staticmethod
    def _close_pipes(process: subprocess.Popen[bytes]) -> None:
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        environment: dict[str, str] | None = None,
        timeout_seconds: float | None = None,
        allowed_returncodes: frozenset[int] = frozenset({0}),
    ) -> CommandOutput:
        timeout = (
            self.default_timeout_seconds if timeout_seconds is None else timeout_seconds
        )
        if not math.isfinite(timeout) or not 0 < timeout <= MAX_COMMAND_TIMEOUT_SECONDS:
            raise SeedError("external command timeout is invalid")
        process: subprocess.Popen[bytes] | None = None
        try:
            process = subprocess.Popen(
                list(argv),
                cwd=None if cwd is None else str(cwd),
                env=self._environment(environment),
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                bufsize=0,
            )
            try:
                stdout, _stderr = self._collect(process, timeout)
            except _CommandTimedOut as error:
                self._stop(process)
                raise SeedError("external command timed out") from error
            except _CommandOutputExceeded as error:
                self._stop(process)
                raise SeedError("external command output exceeded limit") from error
            except OSError as error:
                self._stop(process)
                raise SeedError("external command failed") from error
        except (OSError, ValueError) as error:
            raise SeedError("external command is unavailable") from error
        finally:
            if process is not None:
                if process.poll() is None:
                    self._stop(process)
                self._close_pipes(process)
        if process.returncode not in allowed_returncodes:
            raise SeedError("external command failed")
        try:
            decoded = stdout.decode("utf-8")
        except UnicodeDecodeError as error:
            raise SeedError("external command returned invalid output") from error
        return CommandOutput(returncode=process.returncode, stdout=decoded)


def build_bootstrap_repository(
    template: Path,
    destination: Path,
    *,
    spec: BootstrapSpec,
    runner: CommandRunner,
) -> str:
    if not template.is_dir() or destination.exists():
        raise SeedError("repository template is unavailable")
    try:
        shutil.copytree(template, destination)
    except OSError as error:
        raise SeedError("repository template is unavailable") from error
    git_environment = {
        "GIT_AUTHOR_DATE": spec.date,
        "GIT_AUTHOR_EMAIL": spec.email,
        "GIT_AUTHOR_NAME": spec.name,
        "GIT_COMMITTER_DATE": spec.date,
        "GIT_COMMITTER_EMAIL": spec.email,
        "GIT_COMMITTER_NAME": spec.name,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "LC_ALL": "C",
        "TZ": "UTC",
    }
    runner.run(
        ("git", "init", "--quiet", "--initial-branch=main"),
        cwd=destination,
        environment=git_environment,
    )
    runner.run(("git", "add", "--all"), cwd=destination, environment=git_environment)
    runner.run(
        (
            "git",
            "-c",
            "commit.gpgSign=false",
            "-c",
            f"user.name={spec.name}",
            "-c",
            f"user.email={spec.email}",
            "commit",
            "--quiet",
            "--no-gpg-sign",
            "-m",
            spec.message,
        ),
        cwd=destination,
        environment=git_environment,
    )
    sha = runner.run(
        ("git", "rev-parse", "HEAD"),
        cwd=destination,
        environment=git_environment,
    ).stdout.strip()
    if SHA_PATTERN.fullmatch(sha) is None:
        raise SeedError("deterministic bootstrap commit is invalid")
    return sha


class SeedClient(Protocol):
    def authenticate(self) -> None: ...

    def ensure_repository(
        self, target: str, template: Path, spec: BootstrapSpec
    ) -> str: ...

    def get_repository(self, target: str) -> RepositoryState: ...

    def collaborator_permission(self, target: str, login: str) -> str: ...

    def list_milestones(
        self, target: str, *, page: int, per_page: int
    ) -> tuple[MilestoneState, ...]: ...

    def create_milestone(self, target: str, title: str) -> MilestoneState: ...

    def get_label(self, target: str, name: str) -> LabelState | None: ...

    def upsert_label(self, target: str, label: LabelState) -> None: ...

    def search_marker(
        self,
        target: str,
        marker: str,
        *,
        kind: Literal["issue", "pr"],
        page: int,
        per_page: int,
    ) -> SearchPage: ...

    def get_issue(self, target: str, number: int) -> IssueState: ...

    def create_issue(self, target: str, draft: IssueDraft) -> IssueState: ...

    def update_issue(
        self, target: str, number: int, patch: IssuePatch
    ) -> IssueState: ...

    def update_issue_body(self, target: str, number: int, body: str) -> IssueState: ...

    def get_ref(self, target: str, branch: str) -> str | None: ...

    def create_ref(self, target: str, branch: str, sha: str) -> None: ...

    def get_commit(self, target: str, sha: str) -> CommitState: ...

    def create_commit(
        self, target: str, parent_sha: str, message: str, spec: BootstrapSpec
    ) -> CommitState: ...

    def get_pull(self, target: str, number: int) -> PullRequestState: ...

    def create_pull(self, target: str, draft: PullRequestDraft) -> PullRequestState: ...

    def update_pull(
        self, target: str, number: int, patch: PullRequestPatch
    ) -> PullRequestState: ...

    def merge_pull(
        self, target: str, number: int, *, head_sha: str, commit_title: str
    ) -> PullRequestState: ...

    def list_check_runs(
        self, target: str, sha: str, *, page: int, per_page: int
    ) -> CheckPage: ...

    def pause(self, seconds: float) -> None: ...


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise SeedError("GitHub returned invalid data")
    return cast(Mapping[str, object], value)


def _sequence(value: object) -> Sequence[object]:
    if not isinstance(value, list):
        raise SeedError("GitHub returned invalid data")
    return cast(Sequence[object], value)


def _string(value: object, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise SeedError("GitHub returned invalid data")
    return value


def _integer(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise SeedError("GitHub returned invalid data")
    return value


def _json_document(output: str) -> object:
    try:
        return json.loads(output)
    except (json.JSONDecodeError, RecursionError) as error:
        raise SeedError("GitHub returned invalid data") from error


HTTP_STATUS_LINE_PATTERN = re.compile(
    r"^HTTP/(?:1\.0|1\.1|2(?:\.0)?) ([1-5][0-9]{2})(?: [^\r\n]*)?$"
)


def _included_http_response(output: str) -> tuple[int, str]:
    separator = "\r\n\r\n" if "\r\n\r\n" in output else "\n\n"
    if separator not in output:
        raise SeedError("GitHub request status is unavailable")
    headers, body = output.split(separator, 1)
    lines = headers.splitlines()
    if not lines:
        raise SeedError("GitHub request status is unavailable")
    match = HTTP_STATUS_LINE_PATTERN.fullmatch(lines[0])
    if match is None or any(line.startswith("HTTP/") for line in lines[1:]):
        raise SeedError("GitHub request status is unavailable")
    return int(match.group(1)), body


@dataclass(frozen=True)
class GitHubGitSeedClient:
    runner: CommandRunner
    timeout_seconds: float = 30

    def _run(
        self,
        argv: Sequence[str],
        *,
        allowed_returncodes: frozenset[int] = frozenset({0}),
    ) -> CommandOutput:
        return self.runner.run(
            tuple(argv),
            timeout_seconds=self.timeout_seconds,
            allowed_returncodes=allowed_returncodes,
        )

    def _json(self, argv: Sequence[str]) -> object:
        return _json_document(self._run(argv).stdout)

    def _read_optional_json(self, endpoint: str) -> object | None:
        output = self._run(
            ("gh", "api", "--include", "--method", "GET", endpoint),
            allowed_returncodes=frozenset({0, 1}),
        )
        status, body = _included_http_response(output.stdout)
        if output.returncode == 1:
            if status == 404:
                return None
            raise SeedError("GitHub request failed")
        if status != 200:
            raise SeedError("GitHub request failed")
        return _json_document(body)

    def authenticate(self) -> None:
        self._run(("gh", "auth", "status", "--hostname", "github.com"))

    def ensure_repository(
        self, target: str, template: Path, spec: BootstrapSpec
    ) -> str:
        with tempfile.TemporaryDirectory(prefix="ari-demo-bootstrap-") as directory:
            repository = Path(directory) / "repository"
            bootstrap_sha = build_bootstrap_repository(
                template, repository, spec=spec, runner=self.runner
            )
            observed = self._read_optional_json(f"repos/{target}")
            if observed is None:
                self._run(
                    (
                        "gh",
                        "repo",
                        "create",
                        target,
                        "--public",
                        "--source",
                        str(repository),
                        "--remote",
                        "origin",
                        "--push",
                    )
                )
        return bootstrap_sha

    def get_repository(self, target: str) -> RepositoryState:
        payload = _mapping(
            self._json(
                (
                    "gh",
                    "repo",
                    "view",
                    target,
                    "--json",
                    "nameWithOwner,visibility,url",
                )
            )
        )
        return RepositoryState(
            name_with_owner=_string(payload.get("nameWithOwner")),
            visibility=_string(payload.get("visibility")),
            url=_string(payload.get("url")),
        )

    def collaborator_permission(self, target: str, login: str) -> str:
        payload = _mapping(
            self._json(
                (
                    "gh",
                    "api",
                    f"repos/{target}/collaborators/{quote(login, safe='')}/permission",
                )
            )
        )
        return _string(payload.get("permission"))

    def list_milestones(
        self, target: str, *, page: int, per_page: int
    ) -> tuple[MilestoneState, ...]:
        payload = _sequence(
            self._json(
                (
                    "gh",
                    "api",
                    "--method",
                    "GET",
                    f"repos/{target}/milestones",
                    "-f",
                    "state=all",
                    "-f",
                    f"per_page={per_page}",
                    "-f",
                    f"page={page}",
                )
            )
        )
        return tuple(
            MilestoneState(
                number=_integer(_mapping(item).get("number")),
                title=_string(_mapping(item).get("title")),
            )
            for item in payload
        )

    def create_milestone(self, target: str, title: str) -> MilestoneState:
        payload = _mapping(
            self._json(
                (
                    "gh",
                    "api",
                    "--method",
                    "POST",
                    f"repos/{target}/milestones",
                    "-f",
                    f"title={title}",
                )
            )
        )
        return MilestoneState(
            number=_integer(payload.get("number")),
            title=_string(payload.get("title")),
        )

    def get_label(self, target: str, name: str) -> LabelState | None:
        document = self._read_optional_json(
            f"repos/{target}/labels/{quote(name, safe='')}"
        )
        if document is None:
            return None
        payload = _mapping(document)
        description = payload.get("description")
        return LabelState(
            name=_string(payload.get("name")),
            color=_string(payload.get("color")),
            description=(
                "" if description is None else _string(description, allow_empty=True)
            ),
        )

    def upsert_label(self, target: str, label: LabelState) -> None:
        current = self.get_label(target, label.name)
        if current == label:
            return
        if current is None:
            argv = (
                "gh",
                "api",
                "--method",
                "POST",
                f"repos/{target}/labels",
                "-f",
                f"name={label.name}",
                "-f",
                f"color={label.color}",
                "-f",
                f"description={label.description}",
            )
        else:
            argv = (
                "gh",
                "api",
                "--method",
                "PATCH",
                f"repos/{target}/labels/{quote(label.name, safe='')}",
                "-f",
                f"new_name={label.name}",
                "-f",
                f"color={label.color}",
                "-f",
                f"description={label.description}",
            )
        self._run(argv)

    def search_marker(
        self,
        target: str,
        marker: str,
        *,
        kind: Literal["issue", "pr"],
        page: int,
        per_page: int,
    ) -> SearchPage:
        payload = _mapping(
            self._json(
                (
                    "gh",
                    "api",
                    "--method",
                    "GET",
                    "/search/issues",
                    "-f",
                    f'q=repo:{target} "[{marker}]" in:title is:{kind}',
                    "-f",
                    f"per_page={per_page}",
                    "-f",
                    f"page={page}",
                )
            )
        )
        if payload.get("incomplete_results") is not False:
            raise SeedError("GitHub returned incomplete data")
        total = payload.get("total_count")
        if type(total) is not int or total < 0:
            raise SeedError("GitHub returned invalid data")
        hits: list[SearchHit] = []
        for item in _sequence(payload.get("items")):
            record = _mapping(item)
            is_pull = isinstance(record.get("pull_request"), dict)
            if is_pull != (kind == "pr"):
                raise SeedError("GitHub returned invalid data")
            hits.append(
                SearchHit(
                    number=_integer(record.get("number")),
                    title=_string(record.get("title")),
                    is_pull_request=is_pull,
                )
            )
        return SearchPage(total_count=total, items=tuple(hits))

    @staticmethod
    def _labels(payload: Mapping[str, object]) -> frozenset[str]:
        return frozenset(
            _string(_mapping(label).get("name"))
            for label in _sequence(payload.get("labels"))
        )

    @staticmethod
    def _milestone_number(payload: Mapping[str, object]) -> int | None:
        milestone = payload.get("milestone")
        if milestone is None:
            return None
        return _integer(_mapping(milestone).get("number"))

    @staticmethod
    def _issue(payload: Mapping[str, object]) -> IssueState:
        if isinstance(payload.get("pull_request"), dict):
            raise SeedError("GitHub returned invalid data")
        state = _string(payload.get("state"))
        if state not in {"open", "closed"}:
            raise SeedError("GitHub returned invalid data")
        body = payload.get("body")
        return IssueState(
            number=_integer(payload.get("number")),
            title=_string(payload.get("title")),
            body="" if body is None else _string(body, allow_empty=True),
            labels=GitHubGitSeedClient._labels(payload),
            milestone_number=GitHubGitSeedClient._milestone_number(payload),
            state=cast(Literal["open", "closed"], state),
            assignees=tuple(
                _string(_mapping(item).get("login"))
                for item in _sequence(payload.get("assignees"))
            ),
        )

    def get_issue(self, target: str, number: int) -> IssueState:
        return self._issue(
            _mapping(self._json(("gh", "api", f"repos/{target}/issues/{number}")))
        )

    @staticmethod
    def _fields(values: Sequence[tuple[str, str]]) -> tuple[str, ...]:
        fields: list[str] = []
        for key, value in values:
            fields.extend(("-f", f"{key}={value}"))
        return tuple(fields)

    def create_issue(self, target: str, draft: IssueDraft) -> IssueState:
        values = [
            ("title", draft.title),
            ("body", draft.body),
            ("milestone", str(draft.milestone_number)),
            *(("labels[]", label) for label in sorted(draft.labels)),
            *(("assignees[]", login) for login in draft.assignees),
        ]
        created = self._issue(
            _mapping(
                self._json(
                    (
                        "gh",
                        "api",
                        "--method",
                        "POST",
                        f"repos/{target}/issues",
                        *self._fields(values),
                    )
                )
            )
        )
        if created.state != draft.state:
            return self.update_issue(
                target,
                created.number,
                IssuePatch(
                    title=draft.title,
                    body=draft.body,
                    labels=draft.labels,
                    milestone_number=draft.milestone_number,
                    state=draft.state,
                    assignees=draft.assignees,
                ),
            )
        return created

    def update_issue(self, target: str, number: int, patch: IssuePatch) -> IssueState:
        values: list[tuple[str, str]] = [
            ("title", patch.title),
            ("milestone", str(patch.milestone_number)),
            ("state", patch.state),
            *(("labels[]", label) for label in sorted(patch.labels)),
            *(("assignees[]", login) for login in patch.assignees),
        ]
        if patch.body is not None:
            values.insert(1, ("body", patch.body))
        return self._issue(
            _mapping(
                self._json(
                    (
                        "gh",
                        "api",
                        "--method",
                        "PATCH",
                        f"repos/{target}/issues/{number}",
                        *self._fields(values),
                    )
                )
            )
        )

    def update_issue_body(self, target: str, number: int, body: str) -> IssueState:
        return self._issue(
            _mapping(
                self._json(
                    (
                        "gh",
                        "api",
                        "--method",
                        "PATCH",
                        f"repos/{target}/issues/{number}",
                        "-f",
                        f"body={body}",
                    )
                )
            )
        )

    def get_ref(self, target: str, branch: str) -> str | None:
        document = self._read_optional_json(
            f"repos/{target}/git/ref/heads/{quote(branch, safe='')}"
        )
        if document is None:
            return None
        payload = _mapping(document)
        sha = _string(_mapping(payload.get("object")).get("sha"))
        if SHA_PATTERN.fullmatch(sha) is None:
            raise SeedError("GitHub returned invalid data")
        return sha

    def create_ref(self, target: str, branch: str, sha: str) -> None:
        self._run(
            (
                "gh",
                "api",
                "--method",
                "POST",
                f"repos/{target}/git/refs",
                "-f",
                f"ref=refs/heads/{branch}",
                "-f",
                f"sha={sha}",
            )
        )

    @staticmethod
    def _commit(payload: Mapping[str, object]) -> CommitState:
        sha = _string(payload.get("sha"))
        tree_sha = _string(_mapping(payload.get("tree")).get("sha"))
        parents = tuple(
            _string(_mapping(parent).get("sha"))
            for parent in _sequence(payload.get("parents"))
        )
        if (
            SHA_PATTERN.fullmatch(sha) is None
            or SHA_PATTERN.fullmatch(tree_sha) is None
        ):
            raise SeedError("GitHub returned invalid data")
        if any(SHA_PATTERN.fullmatch(parent) is None for parent in parents):
            raise SeedError("GitHub returned invalid data")
        return CommitState(
            sha=sha,
            parents=parents,
            message=_string(payload.get("message")),
            tree_sha=tree_sha,
        )

    def get_commit(self, target: str, sha: str) -> CommitState:
        return self._commit(
            _mapping(self._json(("gh", "api", f"repos/{target}/git/commits/{sha}")))
        )

    def create_commit(
        self, target: str, parent_sha: str, message: str, spec: BootstrapSpec
    ) -> CommitState:
        parent = self.get_commit(target, parent_sha)
        values = (
            ("message", message),
            ("tree", parent.tree_sha),
            ("parents[]", parent_sha),
            ("author[name]", spec.name),
            ("author[email]", spec.email),
            ("author[date]", spec.date),
            ("committer[name]", spec.name),
            ("committer[email]", spec.email),
            ("committer[date]", spec.date),
        )
        return self._commit(
            _mapping(
                self._json(
                    (
                        "gh",
                        "api",
                        "--method",
                        "POST",
                        f"repos/{target}/git/commits",
                        *self._fields(values),
                    )
                )
            )
        )

    @staticmethod
    def _pull(payload: Mapping[str, object]) -> PullRequestState:
        state = _string(payload.get("state"))
        if state not in {"open", "closed"} or type(payload.get("merged")) is not bool:
            raise SeedError("GitHub returned invalid data")
        head = _mapping(payload.get("head"))
        base = _mapping(payload.get("base"))
        head_sha = _string(head.get("sha"))
        merge_value = payload.get("merge_commit_sha")
        merge_sha = None if merge_value is None else _string(merge_value)
        if SHA_PATTERN.fullmatch(head_sha) is None or (
            merge_sha is not None and SHA_PATTERN.fullmatch(merge_sha) is None
        ):
            raise SeedError("GitHub returned invalid data")
        body = payload.get("body")
        return PullRequestState(
            number=_integer(payload.get("number")),
            title=_string(payload.get("title")),
            body="" if body is None else _string(body, allow_empty=True),
            labels=GitHubGitSeedClient._labels(payload),
            milestone_number=GitHubGitSeedClient._milestone_number(payload),
            state=cast(Literal["open", "closed"], state),
            merged=cast(bool, payload.get("merged")),
            head_ref=_string(head.get("ref")),
            base_ref=_string(base.get("ref")),
            head_sha=head_sha,
            merge_commit_sha=merge_sha,
            head_repository=_string(_mapping(head.get("repo")).get("full_name")),
            base_repository=_string(_mapping(base.get("repo")).get("full_name")),
        )

    def get_pull(self, target: str, number: int) -> PullRequestState:
        return self._pull(
            _mapping(self._json(("gh", "api", f"repos/{target}/pulls/{number}")))
        )

    def create_pull(self, target: str, draft: PullRequestDraft) -> PullRequestState:
        created = self._pull(
            _mapping(
                self._json(
                    (
                        "gh",
                        "api",
                        "--method",
                        "POST",
                        f"repos/{target}/pulls",
                        *self._fields(
                            (
                                ("title", draft.title),
                                ("body", draft.body),
                                ("head", draft.head_ref),
                                ("base", draft.base_ref),
                            )
                        ),
                    )
                )
            )
        )
        return self.update_pull(
            target,
            created.number,
            PullRequestPatch(
                title=draft.title,
                body=draft.body,
                labels=draft.labels,
                milestone_number=draft.milestone_number,
            ),
        )

    def update_pull(
        self, target: str, number: int, patch: PullRequestPatch
    ) -> PullRequestState:
        self._json(
            (
                "gh",
                "api",
                "--method",
                "PATCH",
                f"repos/{target}/pulls/{number}",
                *self._fields((("title", patch.title), ("body", patch.body))),
            )
        )
        self._json(
            (
                "gh",
                "api",
                "--method",
                "PATCH",
                f"repos/{target}/issues/{number}",
                *self._fields(
                    (
                        ("milestone", str(patch.milestone_number)),
                        *(("labels[]", label) for label in sorted(patch.labels)),
                    )
                ),
            )
        )
        return self.get_pull(target, number)

    def merge_pull(
        self, target: str, number: int, *, head_sha: str, commit_title: str
    ) -> PullRequestState:
        payload = _mapping(
            self._json(
                (
                    "gh",
                    "api",
                    "--method",
                    "PUT",
                    f"repos/{target}/pulls/{number}/merge",
                    "-f",
                    f"sha={head_sha}",
                    "-f",
                    "merge_method=merge",
                    "-f",
                    f"commit_title={commit_title}",
                )
            )
        )
        if payload.get("merged") is not True:
            raise SeedError("managed pull request merge failed")
        return self.get_pull(target, number)

    def list_check_runs(
        self, target: str, sha: str, *, page: int, per_page: int
    ) -> CheckPage:
        payload = _mapping(
            self._json(
                (
                    "gh",
                    "api",
                    "--method",
                    "GET",
                    f"repos/{target}/commits/{sha}/check-runs",
                    "-f",
                    f"per_page={per_page}",
                    "-f",
                    f"page={page}",
                )
            )
        )
        total = payload.get("total_count")
        if type(total) is not int or total < 0:
            raise SeedError("GitHub returned invalid data")
        runs: list[CheckRunState] = []
        for item in _sequence(payload.get("check_runs")):
            run = _mapping(item)
            conclusion = run.get("conclusion")
            if conclusion is not None and not isinstance(conclusion, str):
                raise SeedError("GitHub returned invalid data")
            runs.append(
                CheckRunState(
                    name=_string(run.get("name")),
                    status=_string(run.get("status")),
                    conclusion=conclusion,
                    html_url=_string(run.get("html_url")),
                    head_sha=_string(run.get("head_sha")),
                )
            )
        return CheckPage(total_count=total, items=tuple(runs))

    def pause(self, seconds: float) -> None:
        time.sleep(seconds)


LABEL_DEFINITIONS = (
    LabelState(
        name="code-change",
        color="1d76db",
        description="Fictional release code change",
    ),
    LabelState(
        name="release-ops",
        color="5319e7",
        description="Fictional release operations",
    ),
    LabelState(
        name="release-blocker",
        color="b60205",
        description="Fictional resolved release blocker",
    ),
    LabelState(
        name="migration-required",
        color="0e8a16",
        description="Fictional migration evidence required",
    ),
)

EXPECTED_MIGRATION_CHECKS = {
    "blocking-suite": ("completed", "success"),
    "advisory-synthetic": ("completed", "failure"),
}


@dataclass(frozen=True)
class ExistingTopology:
    previous_release_merge: str | None
    previous_main_merge: str | None
    current_main_merge: str | None


@dataclass(frozen=True)
class SeedResult:
    bootstrap_sha: str
    final_main_sha: str
    migration_url: str
    issue_numbers: Mapping[str, int]
    pull_numbers: Mapping[str, int]


def _all_milestones(client: SeedClient, target: str) -> tuple[MilestoneState, ...]:
    records: list[MilestoneState] = []
    for page_number in range(1, MAX_PAGES + 1):
        page = client.list_milestones(target, page=page_number, per_page=PAGE_SIZE)
        records.extend(page)
        if len(records) > MAX_RECORDS:
            raise SeedError("milestone result limit exceeded")
        if len(page) < PAGE_SIZE:
            return tuple(records)
    raise SeedError("milestone page limit exceeded")


def _search_hits(
    client: SeedClient,
    target: str,
    marker: str,
    *,
    kind: Literal["issue", "pr"],
) -> tuple[SearchHit, ...]:
    records: list[SearchHit] = []
    expected_total: int | None = None
    numbers: set[int] = set()
    for page_number in range(1, MAX_PAGES + 1):
        page = client.search_marker(
            target,
            marker,
            kind=kind,
            page=page_number,
            per_page=PAGE_SIZE,
        )
        if page.total_count > MAX_RECORDS:
            raise SeedError("search result limit exceeded")
        if expected_total is None:
            expected_total = page.total_count
        elif page.total_count != expected_total:
            raise SeedError("marker search conflict")
        for hit in page.items:
            if hit.number in numbers or hit.is_pull_request != (kind == "pr"):
                raise SeedError("marker search conflict")
            numbers.add(hit.number)
            records.append(hit)
        if len(records) > page.total_count:
            raise SeedError("marker search conflict")
        if len(records) == page.total_count:
            return tuple(records)
        if not page.items:
            raise SeedError("marker search conflict")
    raise SeedError("search page limit exceeded")


def _find_issue(
    client: SeedClient, target: str, manifest: IssueManifest
) -> IssueState | None:
    hits = _search_hits(client, target, manifest.marker, kind="issue")
    details = tuple(client.get_issue(target, hit.number) for hit in hits)
    token = f"[{manifest.marker}]"
    if any(token not in detail.title for detail in details) or len(details) > 1:
        raise SeedError("marker search conflict")
    return details[0] if details else None


def _find_pull(
    client: SeedClient, target: str, manifest: PullRequestManifest
) -> PullRequestState | None:
    hits = _search_hits(client, target, manifest.marker, kind="pr")
    details = tuple(client.get_pull(target, hit.number) for hit in hits)
    token = f"[{manifest.marker}]"
    if any(token not in detail.title for detail in details) or len(details) > 1:
        raise SeedError("marker search conflict")
    return details[0] if details else None


def _ensure_milestones(
    client: SeedClient,
    target: str,
    existing: tuple[MilestoneState, ...],
    manifest: SeedManifest,
) -> Mapping[str, int]:
    numbers = [milestone.number for milestone in existing]
    if len(numbers) != len(set(numbers)):
        raise SeedError("milestone conflict")
    expected = tuple(
        (number, f"Fictional archive {number}") for number in range(1, 6)
    ) + (
        (manifest.previous_milestone_number, manifest.previous_milestone),
        (manifest.milestone_number, manifest.milestone),
    )
    records = list(existing)
    for wanted_number, wanted_title in expected:
        at_number = [
            milestone for milestone in records if milestone.number == wanted_number
        ]
        at_title = [
            milestone for milestone in records if milestone.title == wanted_title
        ]
        if len(at_number) > 1 or len(at_title) > 1:
            raise SeedError("milestone conflict")
        if at_number and at_number[0].title != wanted_title:
            raise SeedError("milestone conflict")
        if at_title and at_title[0].number != wanted_number:
            raise SeedError("milestone conflict")
        if not at_number:
            created = client.create_milestone(target, wanted_title)
            if created.number != wanted_number or created.title != wanted_title:
                raise SeedError("milestone conflict")
            records.append(created)
    return {
        manifest.previous_milestone: manifest.previous_milestone_number,
        manifest.milestone: manifest.milestone_number,
    }


def _commit_or_conflict(client: SeedClient, target: str, sha: str) -> CommitState:
    if SHA_PATTERN.fullmatch(sha) is None:
        raise SeedError("managed topology conflict")
    try:
        return client.get_commit(target, sha)
    except (KeyError, LookupError) as error:
        raise SeedError("managed topology conflict") from error


def _validate_pull_topology(
    client: SeedClient,
    target: str,
    expected: PullRequestManifest,
    observed: PullRequestState,
    parent_sha: str,
) -> str | None:
    if (
        observed.head_ref != expected.head
        or observed.base_ref != expected.base
        or observed.head_repository != target
        or observed.base_repository != target
        or SHA_PATTERN.fullmatch(observed.head_sha) is None
    ):
        raise SeedError("managed topology conflict")
    head = _commit_or_conflict(client, target, observed.head_sha)
    if head.parents != (parent_sha,) or head.message != expected.title:
        raise SeedError("managed topology conflict")
    feature_ref = client.get_ref(target, expected.head)
    if feature_ref is not None and feature_ref != observed.head_sha:
        raise SeedError("managed topology conflict")
    if observed.merged:
        merge_sha = observed.merge_commit_sha
        if (
            observed.state != "closed"
            or merge_sha is None
            or SHA_PATTERN.fullmatch(merge_sha) is None
        ):
            raise SeedError("managed topology conflict")
        merge = _commit_or_conflict(client, target, merge_sha)
        if merge.parents != (parent_sha, observed.head_sha):
            raise SeedError("managed topology conflict")
        return merge_sha
    # GitHub may expose an ephemeral test-merge SHA for an open PR. It is not
    # managed evidence; only the post-merge identity is authoritative.
    if observed.state != "open":
        raise SeedError("managed topology conflict")
    return None


def _validate_existing_topology(
    client: SeedClient,
    target: str,
    manifest: SeedManifest,
    bootstrap_sha: str,
    pulls: Mapping[str, PullRequestState | None],
) -> ExistingTopology:
    definitions = manifest.pulls_by_marker
    previous_release = pulls["ari-demo:v1:previous-release"]
    previous_main = pulls["ari-demo:v1:previous-main"]
    current_main = pulls["ari-demo:v1:current-main"]
    previous_release_merge = (
        None
        if previous_release is None
        else _validate_pull_topology(
            client,
            target,
            definitions["ari-demo:v1:previous-release"],
            previous_release,
            bootstrap_sha,
        )
    )
    previous_main_merge = (
        None
        if previous_main is None
        else _validate_pull_topology(
            client,
            target,
            definitions["ari-demo:v1:previous-main"],
            previous_main,
            bootstrap_sha,
        )
    )
    if current_main is not None and previous_main_merge is None:
        raise SeedError("managed topology conflict")
    current_main_merge = (
        None
        if current_main is None
        else _validate_pull_topology(
            client,
            target,
            definitions["ari-demo:v1:current-main"],
            current_main,
            cast(str, previous_main_merge),
        )
    )
    previous_release_ref = client.get_ref(target, manifest.previous_release_branch)
    expected_previous_release = previous_release_merge or bootstrap_sha
    if (
        previous_release_ref is not None
        and previous_release_ref != expected_previous_release
    ):
        raise SeedError("managed topology conflict")
    main_ref = client.get_ref(target, manifest.main_branch)
    expected_main = current_main_merge or previous_main_merge or bootstrap_sha
    if main_ref != expected_main:
        raise SeedError("managed topology conflict")
    candidate_ref = client.get_ref(target, manifest.candidate_branch)
    if candidate_ref is not None and (
        current_main_merge is None or candidate_ref != current_main_merge
    ):
        raise SeedError("managed topology conflict")
    return ExistingTopology(
        previous_release_merge=previous_release_merge,
        previous_main_merge=previous_main_merge,
        current_main_merge=current_main_merge,
    )


def _issue_state(
    closed: bool,
) -> Literal["open", "closed"]:
    return "closed" if closed else "open"


def _unmanaged_labels(
    labels: frozenset[str], managed: frozenset[str]
) -> frozenset[str]:
    managed_names = {label.casefold() for label in managed}
    return frozenset(label for label in labels if label.casefold() not in managed_names)


def _reconcile_issues(
    client: SeedClient,
    target: str,
    manifest: SeedManifest,
    existing: Mapping[str, IssueState | None],
    milestone_numbers: Mapping[str, int],
) -> dict[str, IssueState]:
    reconciled: dict[str, IssueState] = {}
    for definition in manifest.issues:
        observed = existing[definition.key]
        wanted_labels = frozenset(definition.labels)
        wanted_state = _issue_state(definition.closed)
        wanted_assignees = (
            (definition.assignee,) if definition.assignee is not None else ()
        )
        milestone_number = milestone_numbers[definition.milestone]
        if observed is None:
            reconciled[definition.key] = client.create_issue(
                target,
                IssueDraft(
                    title=definition.title,
                    body=definition.body,
                    labels=wanted_labels,
                    milestone_number=milestone_number,
                    state=wanted_state,
                    assignees=wanted_assignees,
                ),
            )
            continue
        preserved_labels = _unmanaged_labels(observed.labels, manifest.managed_labels)
        labels = preserved_labels | wanted_labels
        assignees = tuple(
            sorted(
                set(observed.assignees)
                | ({definition.assignee} if definition.assignee is not None else set())
            )
        )
        preserve_body = definition.key == "release-operations"
        body_matches = preserve_body or observed.body == definition.body
        if (
            observed.title == definition.title
            and body_matches
            and observed.labels == labels
            and observed.milestone_number == milestone_number
            and observed.state == wanted_state
            and observed.assignees == assignees
        ):
            reconciled[definition.key] = observed
            continue
        reconciled[definition.key] = client.update_issue(
            target,
            observed.number,
            IssuePatch(
                title=definition.title,
                body=None if preserve_body else definition.body,
                labels=labels,
                milestone_number=milestone_number,
                state=wanted_state,
                assignees=assignees,
            ),
        )
    return reconciled


def _pull_body(marker: str, issue_number: int) -> str:
    return (
        f"<!-- {marker} -->\n"
        "Fictional-only release evidence.\n\n"
        f"Related to #{issue_number}\n"
    )


def _valid_feature_commit(
    client: SeedClient,
    target: str,
    sha: str,
    *,
    parent_sha: str,
    message: str,
) -> None:
    commit = _commit_or_conflict(client, target, sha)
    if commit.parents != (parent_sha,) or commit.message != message:
        raise SeedError("managed topology conflict")


def _reconcile_pull_metadata(
    client: SeedClient,
    target: str,
    manifest: SeedManifest,
    definition: PullRequestManifest,
    observed: PullRequestState,
    *,
    issue_number: int,
    milestone_number: int,
) -> PullRequestState:
    body = _pull_body(definition.marker, issue_number)
    labels = _unmanaged_labels(observed.labels, manifest.managed_labels) | {
        "code-change"
    }
    if (
        observed.title == definition.title
        and observed.body == body
        and observed.labels == labels
        and observed.milestone_number == milestone_number
    ):
        return observed
    return client.update_pull(
        target,
        observed.number,
        PullRequestPatch(
            title=definition.title,
            body=body,
            labels=frozenset(labels),
            milestone_number=milestone_number,
        ),
    )


def _ensure_pull_stage(
    client: SeedClient,
    target: str,
    manifest: SeedManifest,
    definition: PullRequestManifest,
    observed: PullRequestState | None,
    *,
    parent_sha: str,
    issue_number: int,
    milestone_number: int,
) -> PullRequestState:
    base_sha = client.get_ref(target, definition.base)
    if base_sha is None:
        if definition.base != manifest.previous_release_branch:
            raise SeedError("managed topology conflict")
        restored_sha = (
            observed.merge_commit_sha
            if observed is not None
            and observed.merged
            and observed.merge_commit_sha is not None
            else parent_sha
        )
        client.create_ref(target, definition.base, restored_sha)
        base_sha = restored_sha
    feature_sha = client.get_ref(target, definition.head)
    if observed is not None:
        if feature_sha is None:
            client.create_ref(target, definition.head, observed.head_sha)
            feature_sha = observed.head_sha
        if feature_sha != observed.head_sha:
            raise SeedError("managed topology conflict")
        _valid_feature_commit(
            client,
            target,
            feature_sha,
            parent_sha=parent_sha,
            message=definition.title,
        )
    else:
        if feature_sha is None:
            commit = client.create_commit(
                target, parent_sha, definition.title, BOOTSTRAP_SPEC
            )
            if commit.parents != (parent_sha,) or commit.message != definition.title:
                raise SeedError("managed topology conflict")
            client.create_ref(target, definition.head, commit.sha)
            feature_sha = commit.sha
        else:
            _valid_feature_commit(
                client,
                target,
                feature_sha,
                parent_sha=parent_sha,
                message=definition.title,
            )
        observed = client.create_pull(
            target,
            PullRequestDraft(
                title=definition.title,
                body=_pull_body(definition.marker, issue_number),
                labels=frozenset({"code-change"}),
                milestone_number=milestone_number,
                head_ref=definition.head,
                base_ref=definition.base,
            ),
        )
        if (
            observed.head_sha != feature_sha
            or observed.head_ref != definition.head
            or observed.base_ref != definition.base
        ):
            raise SeedError("managed topology conflict")
    observed = _reconcile_pull_metadata(
        client,
        target,
        manifest,
        definition,
        observed,
        issue_number=issue_number,
        milestone_number=milestone_number,
    )
    if not observed.merged:
        if client.get_ref(target, definition.base) != parent_sha:
            raise SeedError("managed topology conflict")
        observed = client.merge_pull(
            target,
            observed.number,
            head_sha=observed.head_sha,
            commit_title=definition.title,
        )
    merge_sha = _validate_pull_topology(
        client, target, definition, observed, parent_sha
    )
    if merge_sha is None:
        raise SeedError("managed topology conflict")
    return observed


def _all_checks(client: SeedClient, target: str, sha: str) -> tuple[CheckRunState, ...]:
    records: list[CheckRunState] = []
    expected_total: int | None = None
    for page_number in range(1, MAX_PAGES + 1):
        page = client.list_check_runs(target, sha, page=page_number, per_page=PAGE_SIZE)
        if page.total_count > MAX_RECORDS:
            raise SeedError("check result limit exceeded")
        if expected_total is None:
            expected_total = page.total_count
        elif page.total_count != expected_total:
            raise SeedError("migration check conflict")
        records.extend(page.items)
        if len(records) > page.total_count:
            raise SeedError("migration check conflict")
        if len(records) == page.total_count:
            return tuple(records)
        if not page.items:
            raise SeedError("migration check conflict")
    raise SeedError("check page limit exceeded")


def _normalize_check_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise SeedError("migration check URL is invalid") from error
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or CHECK_PATH_PATTERN.fullmatch(parsed.path) is None
    ):
        raise SeedError("migration check URL is invalid")
    return f"https://github.com{parsed.path}"


def _wait_for_migration_check(
    client: SeedClient,
    target: str,
    candidate_sha: str,
    *,
    attempts: int,
    wait_seconds: float,
) -> str:
    if (
        SHA_PATTERN.fullmatch(candidate_sha) is None
        or not 1 <= attempts <= 24
        or not 0 <= wait_seconds <= 60
    ):
        raise SeedError("migration check polling is invalid")
    for attempt in range(1, attempts + 1):
        runs = _all_checks(client, target, candidate_sha)
        names = tuple(run.name for run in runs)
        if any(name not in EXPECTED_MIGRATION_CHECKS for name in names) or any(
            names.count(name) != 1 for name in set(names)
        ):
            raise SeedError("migration check conflict")
        normalized_urls: dict[str, str] = {}
        complete = len(runs) == len(EXPECTED_MIGRATION_CHECKS)
        for run in runs:
            if run.head_sha != candidate_sha:
                raise SeedError("migration check conflict")
            normalized_urls[run.name] = _normalize_check_url(run.html_url)
            expected_status, expected_conclusion = EXPECTED_MIGRATION_CHECKS[run.name]
            if run.status == expected_status:
                if run.conclusion != expected_conclusion:
                    raise SeedError("migration check conflict")
            elif run.status in {"queued", "in_progress"} and run.conclusion is None:
                complete = False
            else:
                raise SeedError("migration check conflict")
        if complete and set(names) == set(EXPECTED_MIGRATION_CHECKS):
            return normalized_urls["blocking-suite"]
        if attempt < attempts:
            client.pause(wait_seconds)
    raise SeedError("migration check unavailable")


def _migration_body(body: str, url: str) -> str:
    return f"{body.rstrip(chr(10))}\n\n### Migration evidence\n{url}\n"


def seed_repository(
    manifest: SeedManifest,
    client: SeedClient,
    *,
    template: Path,
    check_attempts: int = 12,
    check_wait_seconds: float = 5,
) -> SeedResult:
    if manifest.target != REQUIRED_TARGET:
        raise SeedError("repository target is not permitted")
    if not 1 <= check_attempts <= 24 or not 0 <= check_wait_seconds <= 60:
        raise SeedError("migration check polling is invalid")
    target = manifest.target
    client.authenticate()
    bootstrap_sha = client.ensure_repository(target, template, BOOTSTRAP_SPEC)
    if SHA_PATTERN.fullmatch(bootstrap_sha) is None:
        raise SeedError("deterministic bootstrap commit is invalid")
    repository = client.get_repository(target)
    if repository != RepositoryState(
        name_with_owner=target,
        visibility="PUBLIC",
        url=f"https://github.com/{target}",
    ):
        raise SeedError("repository identity is not canonical PUBLIC")
    if client.collaborator_permission(target, REQUIRED_OWNER) not in {
        "admin",
        "maintain",
        "write",
    }:
        raise SeedError("managed assignee is not assignable")

    milestones = _all_milestones(client, target)
    existing_issues = {
        issue.key: _find_issue(client, target, issue) for issue in manifest.issues
    }
    existing_pulls = {
        pull.marker: _find_pull(client, target, pull) for pull in manifest.pull_requests
    }
    _validate_existing_topology(client, target, manifest, bootstrap_sha, existing_pulls)

    milestone_numbers = _ensure_milestones(client, target, milestones, manifest)
    for label in LABEL_DEFINITIONS:
        client.upsert_label(target, label)
    issues = _reconcile_issues(
        client,
        target,
        manifest,
        existing_issues,
        milestone_numbers,
    )

    definitions = manifest.pulls_by_marker
    previous_release_definition = definitions["ari-demo:v1:previous-release"]
    previous_release = _ensure_pull_stage(
        client,
        target,
        manifest,
        previous_release_definition,
        existing_pulls[previous_release_definition.marker],
        parent_sha=bootstrap_sha,
        issue_number=issues[previous_release_definition.issue_key].number,
        milestone_number=milestone_numbers[previous_release_definition.milestone],
    )
    previous_main_definition = definitions["ari-demo:v1:previous-main"]
    previous_main = _ensure_pull_stage(
        client,
        target,
        manifest,
        previous_main_definition,
        existing_pulls[previous_main_definition.marker],
        parent_sha=bootstrap_sha,
        issue_number=issues[previous_main_definition.issue_key].number,
        milestone_number=milestone_numbers[previous_main_definition.milestone],
    )
    if previous_main.merge_commit_sha is None:
        raise SeedError("managed topology conflict")
    current_main_definition = definitions["ari-demo:v1:current-main"]
    current_main = _ensure_pull_stage(
        client,
        target,
        manifest,
        current_main_definition,
        existing_pulls[current_main_definition.marker],
        parent_sha=previous_main.merge_commit_sha,
        issue_number=issues[current_main_definition.issue_key].number,
        milestone_number=milestone_numbers[current_main_definition.milestone],
    )
    if (
        previous_release.merge_commit_sha is None
        or current_main.merge_commit_sha is None
    ):
        raise SeedError("managed topology conflict")
    final_main_sha = current_main.merge_commit_sha
    candidate_sha = client.get_ref(target, manifest.candidate_branch)
    if candidate_sha is None:
        client.create_ref(target, manifest.candidate_branch, final_main_sha)
        candidate_sha = final_main_sha
    if (
        candidate_sha != final_main_sha
        or client.get_ref(target, manifest.main_branch) != final_main_sha
        or client.get_ref(target, manifest.previous_release_branch)
        != previous_release.merge_commit_sha
    ):
        raise SeedError("managed topology conflict")
    for definition, pull in (
        (previous_release_definition, previous_release),
        (previous_main_definition, previous_main),
        (current_main_definition, current_main),
    ):
        if client.get_ref(target, definition.head) != pull.head_sha:
            raise SeedError("managed topology conflict")

    migration_url = _wait_for_migration_check(
        client,
        target,
        candidate_sha,
        attempts=check_attempts,
        wait_seconds=check_wait_seconds,
    )
    operations_definition = manifest.issues_by_key["release-operations"]
    operations = issues["release-operations"]
    wanted_body = _migration_body(operations_definition.body, migration_url)
    if operations.body != wanted_body:
        operations = client.update_issue_body(target, operations.number, wanted_body)
        issues["release-operations"] = operations

    return SeedResult(
        bootstrap_sha=bootstrap_sha,
        final_main_sha=final_main_sha,
        migration_url=migration_url,
        issue_numbers={key: issue.number for key, issue in issues.items()},
        pull_numbers={
            previous_release_definition.marker: previous_release.number,
            previous_main_definition.marker: previous_main.number,
            current_main_definition.marker: current_main.number,
        },
    )


def main(argv: Sequence[str] | None = None, *, client: SeedClient | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        if arguments != [REQUIRED_TARGET]:
            raise SeedError(
                "usage: seed_demo_repo.sh floppy522/ai-release-intelligence-demo"
            )
        manifest_path = Path(
            os.environ.get(
                "ARI_SEED_MANIFEST",
                str(Path(__file__).with_name("seed_manifest.yaml")),
            )
        )
        wait_text = os.environ.get("ARI_SEED_MIGRATION_WAIT_SECONDS", "5")
        try:
            wait_seconds = float(wait_text)
        except ValueError as error:
            raise SeedError("migration check polling is invalid") from error
        runner = client or GitHubGitSeedClient(runner=SubprocessRunner())
        seed_repository(
            load_manifest(manifest_path),
            runner,
            template=Path(__file__).with_name("repository"),
            check_attempts=12,
            check_wait_seconds=wait_seconds,
        )
    except SeedError as error:
        print(f"seed-demo-repo: {error}", file=sys.stderr)
        return 1
    print(
        "seed-demo-repo: fictional demo evidence reconciled; "
        "workflow results remain a remote publish gate."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
