#!/usr/bin/env python3
"""Track platform CI (GitLab MR gate) runs and hand a verified build to phase 3.

Commands:
  observe    Register a run against the platform's own MR pipeline (no Jenkins trigger).
  adopt      Register and immediately claim an already-finished gate build (recovery).
  note       Append one audit event to an existing run.
  reconcile  Poll the GitLab pipeline and, on success, claim the Jenkins gate build.
  serve      Optional webhook receiver (legacy; the platform signal comes from GitLab).

设计前提（2026-10）：MR 提交后 GitLab 流水线自动驱动 Jenkins 门禁构建，本模块
**不再触发 Jenkins**。它只做三件事：只读观察平台流水线、认领平台已跑出的门禁构建、
在构建成功后启动受信任的 phase-3 单元（OTA + 回归）。产物与设备动作全部由
phase3_runner.py 按 profile 执行。
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import gitlab_pipeline as platform
from jenkins_api import (DEFAULT_BASE_URL, JenkinsError, SHA_PATTERN,
                         build_artifacts, build_console_text, build_http_opener,
                         build_metadata, credentials, job_builds, make_url,
                         read_error_hint)
from orchestrator_config import configured_value

DEFAULT_STATE_DIR = Path.home() / ".local/state/openharmony-ci-orchestrator"
DEFAULT_GATE_JOB = "OpenHarmony-V6.1-MR-Gate"
CHIP_JOB_TEMPLATE = "OpenHarmony-V6.1-MR-{}"
DEFAULT_CHIP = "a333"
DEFAULT_ARTIFACT_PATTERN = "openharmony_V6.1/out/ota/*.zip"
BUILD_LOOKUP_LIMIT = 40
GATE_PLAN_MARKERS = ("mr_iid", "source_sha", "chips")
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
PROFILE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
TERMINAL_RUN_STATUSES = frozenset(
    ("build_failed", "blocked", "gate_failed", "gate_aborted", "gate_not_in_scope")
)
MAX_EVENTS = 100
SKILL_DIR = Path(__file__).resolve().parents[1]
PROFILES_DIR = SKILL_DIR / "profiles"

PHASE3_UNIT_TEMPLATE = "openharmony-ci-phase3@{}"
PHASE3_UNIT_MAX_RESTARTS = 3
# "等设备上线"的重试冷却：设备池常常零在线，不能每 5 分钟都在日志里刷一次。
DEFAULT_DEVICE_WAIT_COOLDOWN = 300


class OrchestratorError(RuntimeError):
    """A user-actionable state, webhook, or platform reconciliation error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def state_directory(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def ensure_state_directory(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    (root / "runs").mkdir(exist_ok=True, mode=0o700)
    (root / "agents").mkdir(exist_ok=True, mode=0o700)


@contextmanager
def state_lock(root: Path):
    ensure_state_directory(root)
    lock_path = root / ".lock"
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def run_path(root: Path, run_id: str) -> Path:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise OrchestratorError(f"Invalid run id: {run_id!r}")
    return root / "runs" / f"{run_id}.json"


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise OrchestratorError(f"Run state does not exist: {path}") from error
    except json.JSONDecodeError as error:
        raise OrchestratorError(f"Invalid JSON state: {path}") from error
    if not isinstance(value, dict):
        raise OrchestratorError(f"Run state must be a JSON object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as temp:
        json.dump(value, temp, ensure_ascii=False, indent=2, sort_keys=True)
        temp.write("\n")
        temp.flush()
        os.fsync(temp.fileno())
        temp_path = Path(temp.name)
    os.chmod(temp_path, 0o600)
    os.replace(temp_path, path)


def add_event(state: dict[str, Any], kind: str, detail: str) -> None:
    events = state.setdefault("events", [])
    events.append({"at": utc_now(), "kind": kind, "detail": detail})
    if len(events) > MAX_EVENTS:
        del events[:-MAX_EVENTS]
    state["updated_at"] = utc_now()


def new_run_id() -> str:
    return f"ci-{int(time.time())}-{secrets.token_hex(4)}"


def safe_url(value: Any, expected_origin: tuple[str, str] | None = None) -> str:
    if not isinstance(value, str) or not value:
        raise OrchestratorError("Expected a non-empty Jenkins URL")
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise OrchestratorError(f"Invalid Jenkins URL: {value!r}")
    origin = (parsed.scheme, parsed.netloc)
    if expected_origin is not None and origin != expected_origin:
        # 部分部署会返回与访问地址不同的自引用 origin；仍允许显式白名单
        # （JENKINS_TRUSTED_ORIGINS，逗号分隔 host:port 或 http(s)://host:port）。
        trusted = frozenset(
            parsed_origin(item)
            for item in (configured_value("JENKINS_TRUSTED_ORIGINS") or "").split(",")
            if item.strip()
        )
        if origin not in trusted:
            raise OrchestratorError(f"Jenkins URL origin does not match the tracked job: {value!r}")
    return value.rstrip("/")


def origin_of(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    return parsed.scheme, parsed.netloc


def parsed_origin(value: str) -> tuple[str, str]:
    """Normalize a trusted-origin entry to a (scheme, netloc) tuple."""
    value = value.strip()
    if "://" in value:
        return origin_of(value)
    return ("http", value)


def absolute_directory(value: str, name: str) -> str:
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise OrchestratorError(f"{name} must be an existing directory: {path}")
    return str(path)


def jenkins_headers() -> dict[str, str]:
    headers = {"User-Agent": "openharmony-ci-orchestrator/phase2", "Accept": "application/json"}
    headers.update(credentials())
    return headers


def jenkins_base_url(explicit: str | None = None) -> str:
    return safe_url(explicit or configured_value("JENKINS_BASE_URL") or DEFAULT_BASE_URL)


def profile_record(profile_id: str | None) -> dict[str, Any]:
    """Read one trusted profile's contract (Jenkins job/chip/artifact pattern)."""
    if not profile_id:
        return {}
    if not PROFILE_PATTERN.fullmatch(profile_id):
        raise OrchestratorError(f"Invalid profile id: {profile_id!r}")
    path = PROFILES_DIR / f"{profile_id}.json"
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise OrchestratorError(f"Trusted profile does not exist: {profile_id}") from error
    except json.JSONDecodeError as error:
        raise OrchestratorError(f"Trusted profile is invalid JSON: {path}") from error
    if not isinstance(profile, dict) or profile.get("id") != profile_id:
        raise OrchestratorError(f"Trusted profile has an invalid id: {path}")
    return profile


def profile_chip(profile: dict[str, Any]) -> str:
    """Derive the chip whose gate build must be claimed (from jenkins.job)."""
    jenkins = profile.get("jenkins") or {}
    job = str(jenkins.get("job") or "")
    prefix = CHIP_JOB_TEMPLATE.format("")
    if job.startswith(prefix) and len(job) > len(prefix):
        return job[len(prefix):]
    chip = str(jenkins.get("chip") or DEFAULT_CHIP)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", chip):
        raise OrchestratorError(f"Trusted profile has an invalid chip: {chip!r}")
    return chip


def profile_artifact_pattern(profile: dict[str, Any]) -> str:
    artifact = profile.get("artifact") or {}
    pattern = artifact.get("relative_path") or DEFAULT_ARTIFACT_PATTERN
    if not isinstance(pattern, str) or not pattern:
        raise OrchestratorError("Trusted profile has an invalid artifact relative_path")
    return pattern


# --------------------------------------------------------------------------- #
# 平台观察（GitLab，只读）
# --------------------------------------------------------------------------- #

def stamp_platform(state: dict[str, Any], mr: dict[str, Any], run_state: str,
                   stale: bool | None = None) -> None:
    """Refresh the platform snapshot; `stale` decides whether the run may proceed.

    `stale=None` 表示调用方无法判定（例如只更新了流水线状态），保持既有标记不动。
    """
    platform_state = platform.observed_platform(mr, run_state)
    recorded_sha = state.get("trigger", {}).get("source_sha")
    if stale is None:
        stale = bool((state.get("trigger", {}).get("platform") or {}).get("stale_source"))
    if stale and recorded_sha:
        # 门禁构建对应的提交**不是**本 run 登记的提交：结论不可用，必须等平台对登记提交重跑。
        platform_state["stale_source"] = {
            "recorded_source_sha": recorded_sha,
            "pipeline_sha": platform_state.get("pipeline_sha"),
            "current_mr_sha": platform_state.get("mr_sha"),
        }
    merged = {**(state["trigger"].get("platform") or {}), **platform_state,
              "observed_at": utc_now()}
    if not stale:
        # 合并会保留旧键，必须显式删除，否则"平台已重跑登记提交"也解不开 stale（实测死锁）。
        merged.pop("stale_source", None)
    state.setdefault("trigger", {})["platform"] = merged


def run_is_stale(state: dict[str, Any], mr: dict[str, Any],
                 pipeline_record: dict[str, Any] | None) -> bool:
    """Is the recorded commit different from the commit the platform actually built?

    判据只看**流水线实际构建的提交**（`pipeline.sha`），而不是 MR 当前 head：
    - pipeline.sha == 登记 SHA → 结论可用（MR 再前进也不影响已完成构建的结论）；
    - pipeline.sha != 登记 SHA → 该流水线验证的是别的提交，本次 run 不能用；
    - 流水线尚未创建（刚提交/被跳过）→ 用 MR head 兜底判断，
      此时若 MR 已经前进，说明该 run 登记的提交已被取代。

    这样"平台对登记提交重跑了流水线"能自动解除此前的 stale 标记（旧实现按 MR head 判断，
    一旦 MR 前进就永久卡住）。
    """
    recorded = str(state.get("trigger", {}).get("source_sha") or "")
    if not recorded:
        return False
    if pipeline_record is not None:
        pipeline_sha = str(pipeline_record.get("sha") or "")
        return bool(pipeline_sha) and pipeline_sha != recorded
    mr_sha = str(mr.get("sha") or "")
    return bool(mr_sha) and mr_sha != recorded


def refresh_gate_state(state: dict[str, Any], *, host: str | None = None,
                       project: str | None = None) -> str:
    """Refresh one run's gate state from GitLab and return the new run state."""
    trigger = state.get("trigger", {})
    mr_iid = trigger.get("mr_iid")
    if not str(mr_iid or "").isdigit():
        raise OrchestratorError(f"Run has no usable MR iid: {mr_iid!r}")
    mr = platform.merge_request(mr_iid, host=host, project=project)
    target_branch = str((trigger.get("platform") or {}).get("target_branch")
                        or trigger.get("target_branch") or mr.get("target_branch") or "")
    pipeline_record = platform.head_pipeline(mr)
    if pipeline_record is None:
        # 平台尚未为该 MR 创建流水线（刚提交/流水线被跳过）：保持 pending，不当作失败。
        state["status"] = "gate_pending"
        stamp_platform(state, mr, "gate_pending", stale=run_is_stale(state, mr, None))
        state["trigger"]["platform"].update({"pipeline_id": None, "target_branch": target_branch})
        add_event(state, "gate_pending",
                  "GitLab has not created a pipeline for this merge request yet")
        return state["status"]
    stale = run_is_stale(state, mr, pipeline_record)
    previous_stale = bool((trigger.get("platform") or {}).get("stale_source"))
    run_state = platform.run_state_for(pipeline_record, target_branch=target_branch)
    state["status"] = run_state
    stamp_platform(state, mr, run_state, stale=stale)
    state["trigger"]["platform"]["target_branch"] = target_branch
    if previous_stale and not stale:
        add_event(state, "stale_source_cleared",
                  f"Pipeline {pipeline_record.get('id')} now builds the registered "
                  f"source SHA {trigger.get('source_sha')}; resuming")
    add_event(state, run_state,
              f"GitLab pipeline {pipeline_record.get('id')} is "
              f"{pipeline_record.get('status')!r} -> {run_state}")
    return run_state

# --------------------------------------------------------------------------- #
# 认领平台已跑出的门禁构建
# --------------------------------------------------------------------------- #

def build_parameters(metadata: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for action in metadata.get("actions", []):
        if not isinstance(action, dict):
            continue
        for parameter in action.get("parameters", []):
            if not isinstance(parameter, dict):
                continue
            name = parameter.get("name")
            value = parameter.get("value")
            if isinstance(name, str) and isinstance(value, (str, int, float, bool)):
                result[name] = str(value)
    return result


def expected_build_url(job_url: str, build_number: int) -> str:
    if build_number < 1:
        raise OrchestratorError("Jenkins build number must be positive")
    return f"{job_url.rstrip('/')}/{build_number}"


def parameters_match(expected: dict[str, str], actual: dict[str, str]) -> tuple[bool, str]:
    mismatches = []
    for name, value in expected.items():
        if actual.get(name) != value:
            mismatches.append(f"{name} expected {value!r}, got {actual.get(name)!r}")
    return not mismatches, "; ".join(mismatches)


def gate_identity(trigger: dict[str, Any]) -> dict[str, str]:
    """The MR identity a claimed build must carry (platform gate job contract)."""
    platform_state = trigger.get("platform") or {}
    identity = {
        "MR_IID": str(trigger.get("mr_iid") or ""),
        "SOURCE_SHA": str(trigger.get("source_sha") or ""),
    }
    target_branch = str(platform_state.get("target_branch") or trigger.get("target_branch") or "")
    if target_branch:
        identity["TARGET_BRANCH"] = target_branch
    return {name: value for name, value in identity.items() if value}


def fetch_build_parameters(opener: Any, build_url: str, headers: dict[str, str],
                           timeout: int) -> tuple[dict[str, Any], dict[str, str]]:
    metadata = build_metadata(opener, build_url, headers, timeout)
    return metadata, build_parameters(metadata)


def locate_gate_build(opener: Any, base_url: str, headers: dict[str, str], timeout: int,
                      trigger: dict[str, Any], gate_job: str) -> dict[str, Any]:
    """Find the platform gate build for this run: recorded URL first, live lookup second."""
    platform_state = trigger.get("platform") or {}
    identity = gate_identity(trigger)
    gate_job_url = make_url(base_url, gate_job)
    candidates: list[str] = []
    recorded = platform_state.get("gate_build_url")
    if isinstance(recorded, str) and recorded:
        candidates.append(recorded)
    try:
        recent = job_builds(opener, gate_job_url, headers, timeout, limit=BUILD_LOOKUP_LIMIT)
    except JenkinsError as error:
        raise OrchestratorError(read_error_hint(error)) from error
    for build in recent:
        number = build.get("number")
        if isinstance(number, int):
            candidates.append(expected_build_url(gate_job_url, number))
    observed: list[str] = []
    for candidate in dict.fromkeys(candidates):
        try:
            url = safe_url(candidate, origin_of(gate_job_url))
        except OrchestratorError:
            continue
        metadata, parameters = fetch_build_parameters(opener, url, headers, timeout)
        matches, reason = parameters_match(identity, parameters)
        if matches:
            return {"url": url, "number": metadata.get("number"),
                    "result": metadata.get("result"), "building": metadata.get("building"),
                    "parameters": parameters, "identity": identity}
        observed.append(f"#{metadata.get('number')}: {reason}")
    raise OrchestratorError(
        "No Jenkins MR-gate build matches this run's MR identity "
        f"({identity}); platform CI may not have run yet or its builds were rotated out. "
        f"Checked {len(observed)} build(s): {observed[:3]}"
    )


def parse_gate_plan(console_text: str) -> dict[str, Any] | None:
    """Read the per-run plan (mr_iid/source_sha/chips[]) printed by the gate's checkout step.

    gate-plan.json 既被归档也被打印到 consoleText；这里从 console 取，因为它与
    「这次构建确实属于本 run」的日志证据在同一处，便于复核。
    """
    for line in reversed((console_text or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and all(marker in value for marker in GATE_PLAN_MARKERS):
            return value
    return None


def gate_plan_for(opener: Any, build: dict[str, Any], headers: dict[str, str],
                  timeout: int) -> dict[str, Any] | None:
    try:
        console = build_console_text(opener, build["url"], headers, timeout=timeout)
    except JenkinsError:
        return None
    return parse_gate_plan(console)


def locate_chip_build(opener: Any, base_url: str, headers: dict[str, str], timeout: int,
                      trigger: dict[str, Any], chip: str) -> dict[str, Any]:
    identity = gate_identity(trigger)
    job = CHIP_JOB_TEMPLATE.format(chip)
    job_url = make_url(base_url, job)
    observed: list[str] = []
    try:
        recent = job_builds(opener, job_url, headers, timeout, limit=BUILD_LOOKUP_LIMIT)
    except JenkinsError as error:
        raise OrchestratorError(read_error_hint(error)) from error
    for build in recent:
        number = build.get("number")
        if not isinstance(number, int):
            continue
        url = expected_build_url(job_url, number)
        parameters = build_parameters(build)
        if not parameters:
            _, parameters = fetch_build_parameters(opener, url, headers, timeout)
        matches, _ = parameters_match(identity, parameters)
        if not matches:
            continue
        if build.get("building"):
            observed.append(f"{job} #{number} still building")
            continue
        if build.get("result") != "SUCCESS":
            observed.append(f"{job} #{number} {build.get('result')}")
            continue
        return {"job": job, "job_url": job_url, "url": url, "number": number,
                "result": "SUCCESS", "parameters": parameters}
    raise OrchestratorError(
        f"No successful {job} build matches {identity}; "
        f"observed: {observed or 'no matching build'}"
    )


def collect_build_artifacts(opener: Any, build_url: str, headers: dict[str, str],
                            timeout: int) -> tuple[list[str], str | None]:
    try:
        artifacts = build_artifacts(opener, build_url, headers, timeout)
    except JenkinsError as error:
        return [], str(error)[:500]
    return sorted(
        item["relativePath"] for item in artifacts
        if isinstance(item.get("relativePath"), str)
    ), None


def adopt_gate_build(root: Path, state: dict[str, Any], opener: Any, headers: dict[str, str],
                     timeout: int) -> str:
    """Claim the platform's own gate build for this run and record it as `build`."""
    trigger = state["trigger"]
    base_url = jenkins_base_url((trigger.get("platform") or {}).get("jenkins_base_url"))
    trigger.setdefault("platform", {})["jenkins_base_url"] = base_url
    gate_job = (trigger.get("platform") or {}).get("gate_job") or DEFAULT_GATE_JOB
    chip = trigger.get("chip") or profile_chip(profile_record(
        (state.get("phase3") or {}).get("profile")))
    if not chip:
        raise OrchestratorError(
            "Run has no chip selected; register with --phase3-profile so the chip is fixed"
        )
    try:
        gate = locate_gate_build(opener, base_url, headers, timeout, trigger, gate_job)
    except (JenkinsError, OrchestratorError) as error:
        # 汇总门禁构建可能被保留策略清理，或本机没有该作业的读权限；芯片构建身份 + console
        # 检出证据才是硬门禁，因此这里降级继续，但把原因写进 run 状态（不静默）。
        trigger["platform"]["gate_build_lookup_error"] = str(error)[:500]
        add_event(state, "gate_build_unavailable",
                  f"Could not claim {gate_job} for MR {trigger.get('mr_iid')}; "
                  f"falling back to chip-build identity checks: {str(error)[:200]}")
        gate = None
    if gate is not None:
        trigger["platform"].update({
            "gate_build_url": gate["url"],
            "gate_build_number": gate["number"],
            "gate_result": gate["result"],
        })
        add_event(state, "gate_build_found",
                  f"Claimed {gate_job} #{gate['number']} for MR {trigger.get('mr_iid')}")
        if gate.get("building") or gate.get("result") != "SUCCESS":
            state["status"] = "gate_pending"
            add_event(state, "gate_build_pending",
                      f"Gate build {gate['url']} is not finished successfully yet")
            return state["status"]
        plan = gate_plan_for(opener, gate, headers, timeout)
        plan_chips: list[str] = []
        if plan is not None:
            raw_chips = plan.get("chips")
            if isinstance(raw_chips, list):
                plan_chips = [str(item) for item in raw_chips]
            trigger["platform"]["gate_plan"] = plan
        if plan_chips and chip not in plan_chips:
            raise OrchestratorError(
                f"Platform gate built {plan_chips} but this run must verify {chip!r}"
            )
    try:
        chip_build = locate_chip_build(opener, base_url, headers, timeout, trigger, chip)
    except JenkinsError as error:
        raise OrchestratorError(read_error_hint(error)) from error
    artifacts, artifact_error = collect_build_artifacts(
        opener, chip_build["url"], headers, timeout
    )
    trigger.update({
        "job": chip_build["job"],
        "job_url": chip_build["job_url"],
        "build_number": chip_build["number"],
        "build_url": chip_build["url"],
        "parameters": chip_build["parameters"],
        "chip": chip,
    })
    state["build"] = {
        "url": chip_build["url"],
        "number": chip_build["number"],
        "result": "SUCCESS",
        "verified_parameters": True,
        "chip": chip,
        "gate_build_url": gate["url"] if gate else None,
        "artifacts": artifacts,
        "artifact_error": artifact_error,
    }
    state["status"] = "build_succeeded"
    add_event(state, "adopted",
              f"Adopted platform build {chip_build['job']} #{chip_build['number']}")
    return state["status"]


# --------------------------------------------------------------------------- #
# Run creation
# --------------------------------------------------------------------------- #

def create_run(root: Path, args: argparse.Namespace) -> dict[str, Any]:
    source_sha = args.source_sha
    if not isinstance(source_sha, str) or not SHA_PATTERN.fullmatch(source_sha):
        raise OrchestratorError(f"Invalid source SHA: {source_sha!r}")
    if not str(args.mr_iid or "").isdigit():
        raise OrchestratorError(f"Invalid MR iid: {args.mr_iid!r}")
    if args.phase3_profile and args.agent_sandbox != "danger-full-access":
        raise OrchestratorError(
            "A phase-3 profile requires --agent-sandbox danger-full-access"
        )
    profile = profile_record(args.phase3_profile)
    chip = profile_chip(profile) if profile else DEFAULT_CHIP
    repo_dir = absolute_directory(args.repo_dir, "repo-dir")
    run_id = args.run_id or new_run_id()
    path = run_path(root, run_id)
    if path.exists():
        raise OrchestratorError(f"Run id already exists: {run_id}")
    base_url = jenkins_base_url(args.base_url)
    chip_job = CHIP_JOB_TEMPLATE.format(chip)
    state: dict[str, Any] = {
        "schema_version": 3,
        "run_id": run_id,
        "phase": 2,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "status": "gate_pending",
        "trigger": {
            "job": chip_job,
            "job_url": make_url(base_url, chip_job),
            "source_branch": args.source_branch or "",
            "source_sha": source_sha,
            "target_branch": args.target_branch or "",
            "mr_iid": str(args.mr_iid),
            "chip": chip,
            "parameters": {},
            "queue_url": None,
            "build_number": None,
            "build_url": None,
            "platform": {
                "source": args.mode,
                "jenkins_base_url": base_url,
                "gate_job": DEFAULT_GATE_JOB,
            },
        },
        "build": {
            "result": None,
            "verified_parameters": False,
            "artifacts": [],
        },
        "agent": {
            "status": "not_started",
            "repo_dir": repo_dir,
            "sandbox": args.agent_sandbox,
        },
        "phase3": {
            "status": "not_requested" if not args.phase3_profile else "not_started",
            "profile": args.phase3_profile,
            "regression_profiles": args.regression_profile,
            "artifact_pattern": profile_artifact_pattern(profile) if profile else None,
            "dry_run": bool(getattr(args, "dry_run", False)),
            "attempts": [],
        },
        "events": [],
    }
    add_event(state, "observed",
              f"Registered run against platform CI for MR {state['trigger']['mr_iid']}")
    if args.mode == "adopt":
        adopt_gate_build(root, state, build_http_opener(), jenkins_headers(),
                         args.timeout_seconds)
    else:
        try:
            refresh_gate_state(state)
        except (platform.GitLabError, OrchestratorError) as error:
            state["status"] = "gate_pending"
            add_event(state, "gate_observe_failed", str(error))
    write_json(path, state)
    return state


# --------------------------------------------------------------------------- #
# phase-3 unit handoff
# --------------------------------------------------------------------------- #

def phase3_unit_state(run_id: str) -> str:
    """查询 phase-3 systemd 单元的当前状态；查不到即视为 inactive。"""
    unit = PHASE3_UNIT_TEMPLATE.format(run_id) + ".service"
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", unit],
            text=True, capture_output=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return (result.stdout or "").strip() or "inactive"


def start_phase3_unit(root: Path, state: dict[str, Any]) -> None:
    """用 systemd oneshot 模板单元执行 phase 3（确定性执行、无 LLM、无 shell 60s 超时）。

    历史实现用 `dsh --profile headless` 起一个 LLM 会话去跑一条本来确定的命令，实测有三类硬缺陷：
    会话 shell 不继承自定义环境变量且会过滤密钥（导致 JENKINS_USER/TOKEN 半配 -> 异或报错）、
    会话对长命令约 60s 超时会拦腰杀掉分钟级 OTA（run 卡在 running 且设备留下半截包）、
    以及依赖 PATH 里的 glab 等外部命令。改为单元后：环境由 EnvironmentFile 显式提供、
    无命令超时限制、独立于 reconcile 进程（不被 cgroup 连带杀）、可用 systemctl 观察与重试。
    """
    run_id = state["run_id"]
    unit = PHASE3_UNIT_TEMPLATE.format(run_id) + ".service"
    result = subprocess.run(
        ["systemctl", "--user", "start", "--no-block", unit],
        text=True, capture_output=True, timeout=60,
    )
    detail = ((result.stdout or "") + (result.stderr or "")).strip()
    if result.returncode != 0:
        state.setdefault("phase3", {})["unit"] = {
            "name": unit, "state": "start_failed", "detail": detail[:500],
        }
        raise OrchestratorError(f"Failed to start {unit}: {detail[:300]}")
    phase3 = state.setdefault("phase3", {})
    phase3["unit"] = {"name": unit, "state": "started"}
    state.setdefault("agent", {}).update({
        "status": "launched", "mode": "systemd-unit", "unit": unit, "launched_at": utc_now(),
    })
    add_event(state, "phase3_unit_started", f"Started {unit}")


def reconcile_phase3(root: Path, state: dict[str, Any]) -> str | None:
    """Once a build succeeded, keep exactly one phase-3 unit responsible for the run.

    Returns the reconcile action when the phase-3 path owns this run, or None when
    the caller must continue with gate-build adoption.
    """
    status = state.get("status")
    if status not in ("gate_succeeded", "build_succeeded"):
        return None
    unit_state = phase3_unit_state(state["run_id"])
    if unit_state in ("active", "activating", "reloading"):
        return "phase3_running"
    if unit_state == "unknown":
        # 无法查询 systemd（例如非 systemd 环境）：保持旧行为，避免重复触发。
        return "phase3_unit_unknown"
    if unit_state == "start_failed":
        return "phase3_unit_start_failed"
    phase3 = state.get("phase3", {})
    phase3_status = phase3.get("status")
    if phase3_status == "waiting_for_device" and status == "build_succeeded":
        # 设备离线：这不是失败，也不该消耗 unit_restarts（否则整机拔线几次就把 run 逼死）。
        # 按冷却窗口重试，等设备回来即可继续。
        cooldown = int(phase3.get("device_wait_cooldown_seconds", DEFAULT_DEVICE_WAIT_COOLDOWN))
        last = str(phase3.get("device_wait_at") or "")
        waited = 0.0
        try:
            if last:
                waited = (datetime.now(timezone.utc)
                          - datetime.fromisoformat(last)).total_seconds()
        except ValueError:
            waited = cooldown
        if waited < cooldown:
            return "waiting_for_device"
        phase3["device_wait_at"] = utc_now()
        phase3["device_wait_attempts"] = int(phase3.get("device_wait_attempts", 0)) + 1
        add_event(state, "device_wait_retry",
                  f"No pool device was online; retrying phase 3 "
                  f"(attempt {phase3['device_wait_attempts']})")
        try:
            start_phase3_unit(root, state)
        except (OSError, OrchestratorError) as error:
            add_event(state, "phase3_unit_start_failed", str(error))
            return "phase3_unit_start_failed"
        return "phase3_unit_started"

    if phase3_status not in (None, "", "not_started", "running"):
        # 单元已结束且 run 状态已收敛（runner 的终态是 passed / fail / inconclusive /
        # dry_run / failed），交给 phase-3 自身的 claim/重试策略处理，不再自动重启。
        return f"phase3_{phase3_status}"
    if status == "gate_succeeded":
        # 首次交接：先由调用方认领平台构建（adopt_gate_build），再启动单元。
        return None
    restarts = int(state.get("phase3", {}).get("unit_restarts", 0))
    if restarts >= PHASE3_UNIT_MAX_RESTARTS:
        add_event(state, "phase3_restart_exhausted",
                  f"phase-3 unit ended {restarts} times without converging; manual action required")
        return "phase3_restart_exhausted"
    # 计数必须落盘，否则上限永远触发不了（实测会每分钟无限重启）。
    state.setdefault("phase3", {})["unit_restarts"] = restarts + 1
    add_event(state, "phase3_unit_restart",
              f"phase-3 unit is {unit_state} and run state is {phase3_status!r}; "
              f"restarting (attempt {restarts + 1})")
    try:
        start_phase3_unit(root, state)
    except (OSError, OrchestratorError) as error:
        state.setdefault("agent", {}).update({"status": "launch_failed", "error": str(error)})
        add_event(state, "phase3_unit_start_failed", str(error))
        return "phase3_unit_start_failed"
    return "phase3_unit_started"


# --------------------------------------------------------------------------- #
# reconcile
# --------------------------------------------------------------------------- #

def reconcile_one(root: Path, state: dict[str, Any], opener: Any, headers: dict[str, str],
                  timeout: int) -> dict[str, Any]:
    run_id = state["run_id"]
    status = state.get("status")
    if status in TERMINAL_RUN_STATUSES:
        return {"run_id": run_id, "status": status, "action": "terminal"}

    if status == "gate_pending":
        try:
            status = refresh_gate_state(state)
        except (platform.GitLabError, OrchestratorError) as error:
            add_event(state, "gate_observe_failed", str(error))
            return {"run_id": run_id, "status": state.get("status"),
                    "action": "gate_observe_failed"}
        if status == "gate_pending":
            return {"run_id": run_id, "status": status, "action": "gate_pending"}
        if status != "gate_succeeded":
            return {"run_id": run_id, "status": status, "action": status}

    if status == "gate_succeeded":
        # 交接前重新核对 MR 仍是同一提交：门禁可能跑了几小时，期间 MR 被更新过，
        # 那时旧流水线的"成功"不适用于当前提交，必须让用户在平台侧重跑流水线。
        try:
            status = refresh_gate_state(state)
        except (platform.GitLabError, OrchestratorError) as error:
            add_event(state, "gate_observe_failed", str(error))
            return {"run_id": run_id, "status": state.get("status"),
                    "action": "gate_observe_failed"}
        if status != "gate_succeeded":
            return {"run_id": run_id, "status": status, "action": status}

    if (state.get("trigger", {}).get("platform") or {}).get("stale_source"):
        state["status"] = "blocked"
        add_event(state, "stale_source",
                  "MR advanced while the gate ran; rerun the platform pipeline for the new head")
        return {"run_id": run_id, "status": state["status"], "action": "stale_source"}

    handled = reconcile_phase3(root, state)
    if handled is not None:
        return {"run_id": run_id, "status": state.get("status"), "action": handled}

    if status == "gate_succeeded":
        try:
            adopted = adopt_gate_build(root, state, opener, headers, timeout)
        except (JenkinsError, OrchestratorError) as error:
            state["status"] = "blocked"
            state.setdefault("build", {})["adopt_error"] = str(error)[:500]
            add_event(state, "gate_adopt_failed", str(error))
            return {"run_id": run_id, "status": state["status"], "action": "gate_adopt_failed"}
        if adopted != "build_succeeded":
            return {"run_id": run_id, "status": adopted, "action": "gate_build_pending"}
        # 认领完成后立刻交接，不必等下一次 tick。
        state.setdefault("phase3", {})["unit_restarts"] = (
            int(state.get("phase3", {}).get("unit_restarts", 0)) + 1
        )
        try:
            start_phase3_unit(root, state)
        except (OSError, OrchestratorError) as error:
            state.setdefault("agent", {}).update({"status": "launch_failed", "error": str(error)})
            add_event(state, "phase3_unit_start_failed", str(error))
            return {"run_id": run_id, "status": state.get("status"),
                    "action": "phase3_unit_start_failed"}
        return {"run_id": run_id, "status": state.get("status"), "action": "phase3_unit_started"}

    return {"run_id": run_id, "status": state.get("status"), "action": "noop"}


def rearm_run(root: Path, run_id: str) -> dict[str, Any]:
    """把因环境问题终结的 run 重新拉回可推进状态（blocked / gate_* 失败等）。

    为什么需要显式入口：`reconcile` 跳过终结 run，而"凭据配好、网络恢复、门禁重新跑过"
    这些修复都发生在 run 之外（实测：一次真实 OTA 因路径缺陷 blocked 后，只能靠人工 restart
    systemd 单元）。rearm 只解冻状态，不做任何设备动作；随后由 reconcile 正常推进。
    """
    with state_lock(root):
        path = run_path(root, run_id)
        state = load_json(path)
        before = state.get("status")
        trigger = state.setdefault("trigger", {})
        platform_state = trigger.setdefault("platform", {})
        had_stale = "stale_source" in platform_state
        platform_state.pop("stale_source", None)
        state.setdefault("build", {}).pop("adopt_error", None)
        state.setdefault("build", {}).pop("legacy_self_trigger", None)
        platform_state.pop("gate_build_lookup_error", None)
        # 平台已成功的 run 回到 gate_succeeded（重新认领），否则回到 gate_pending（重新观察）
        state["status"] = ("gate_succeeded" if platform_state.get("pipeline_status") == "success"
                           else "gate_pending")
        state.setdefault("phase3", {}).pop("device_wait_at", None)
        add_event(state, "rearmed",
                  f"Re-armed from {before!r} to {state['status']!r}"
                  + (" (cleared stale_source marker)" if had_stale else ""))
        write_json(path, state)
        return {"run_id": run_id, "from": before, "to": state["status"]}


def selected_paths(root: Path, run_id: str | None) -> list[Path]:
    if run_id:
        return [run_path(root, run_id)]
    # archive/ 下的历史 run 不参与轮询（glob 不递归）。
    return sorted((root / "runs").glob("*.json"))


def retire_legacy_runs(root: Path) -> list[str]:
    """Close runs created by the removed self-triggering flow (one-time migration).

    2026-10 之前本插件自己触发旧主机（`192.168.13.121`）的 AllWinner/RockChip 作业，这类 run
    没有 `trigger.platform.pipeline_id`，状态停留在 `queued`/`building`/`build_succeeded`。
    门禁改为平台驱动后它们再也无法被认领，继续轮询只会每分钟产生一次 403/404 噪音。这里把它们
    收敛为 `blocked` 终态（保留事件与 phase3 结论作审计），配合 `--prune-days` 归档。
    """
    retired: list[str] = []
    for path in selected_paths(root, None):
        try:
            state = load_json(path)
        except OrchestratorError:
            continue
        trigger = state.get("trigger") or {}
        if (trigger.get("platform") or {}).get("pipeline_id") is not None:
            continue
        if str(trigger.get("job") or "").startswith(CHIP_JOB_TEMPLATE.format("")):
            continue
        if state.get("status") in TERMINAL_RUN_STATUSES:
            continue
        state["status"] = "blocked"
        state.setdefault("build", {})["legacy_self_trigger"] = True
        add_event(state, "legacy_retired",
                  "Run belonged to the removed self-triggering Jenkins flow; "
                  "the platform gate is the only accepted source now")
        write_json(path, state)
        retired.append(state["run_id"])
    return retired


def prune_terminal_runs(root: Path, days: int, now: datetime | None = None) -> list[str]:
    """Move long-finished runs out of the polling set (audit history is kept)."""
    if days <= 0:
        return []
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    archive = root / "runs" / "archive"
    archive.mkdir(parents=True, exist_ok=True, mode=0o700)
    moved: list[str] = []
    for path in sorted((root / "runs").glob("*.json")):
        try:
            state = load_json(path)
        except OrchestratorError:
            continue
        if state.get("status") not in TERMINAL_RUN_STATUSES:
            continue
        stamp = state.get("updated_at") or state.get("created_at") or ""
        try:
            updated = datetime.fromisoformat(str(stamp))
        except ValueError:
            continue
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        if updated > cutoff:
            continue
        destination = archive / path.name
        if destination.exists():
            destination = archive / f"{path.stem}-{int(time.time())}.json"
        os.replace(path, destination)
        moved.append(path.stem)
    return moved


def prune_artifacts(root: Path, days: int, keep: int = 3, now: datetime | None = None) -> dict[str, Any]:
    """Reclaim OTA packages of finished runs (每个包约 750–800 MB，只增不删会吃满磁盘).

    保留策略（不碰未终结 run 的包，避免删掉正在投递的文件）：
    - 最近 `keep` 个 run 的包无条件保留；
    - 其余 run 的包在 `updated_at` 超过 `days` 天后删除；
    - 不删 run 的 JSON（审计仍在 runs/ 或 archive/）。
    """
    artifacts_root = root / "artifacts"
    if not artifacts_root.is_dir():
        return {"removed": [], "freed_bytes": 0, "kept": 0}
    entries: list[tuple[Path, datetime, str]] = []
    for path in sorted(artifacts_root.iterdir()):
        if not path.is_dir():
            continue
        state_path = root / "runs" / f"{path.name}.json"
        archived = root / "runs" / "archive" / f"{path.name}.json"
        stamp, active = None, False
        for candidate in (state_path, archived):
            if candidate.is_file():
                try:
                    state = load_json(candidate)
                except OrchestratorError:
                    continue
                active = active or state.get("status") not in TERMINAL_RUN_STATUSES
                text = str(state.get("updated_at") or state.get("created_at") or "")
                try:
                    stamp = datetime.fromisoformat(text)
                except ValueError:
                    stamp = None
                break
        if active:
            continue  # 进行中的 run：绝不碰它的包
        entries.append((path, stamp or datetime.fromtimestamp(path.stat().st_mtime, timezone.utc),
                        path.name))
    entries.sort(key=lambda item: item[1], reverse=True)
    keep_names = {name for _, _, name in entries[:max(0, keep)]}
    # 上限保护：timedelta 不接受任意大天数（干跑时会传超大值来看"会删什么"）。
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=min(max(0, days), 36500))
    removed: list[str] = []
    freed = 0
    for path, stamp, name in entries:
        if name in keep_names or days <= 0 or stamp > cutoff:
            continue
        try:
            size = sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
            shutil.rmtree(path)
        except OSError:
            continue
        removed.append(name)
        freed += size
    return {"removed": removed, "freed_bytes": freed, "kept": min(len(entries), max(0, keep))}


def reconcile_runs(root: Path, timeout: int, run_id: str | None = None,
                   skip_terminal: bool = True, prune_days: int = 0,
                   keep_artifacts: int = 3) -> dict[str, Any]:
    headers = jenkins_headers()
    opener = build_http_opener()
    outcomes = []
    errors = []
    with state_lock(root):
        retired = retire_legacy_runs(root) if run_id is None else []
        for path in selected_paths(root, run_id):
            try:
                state = load_json(path)
                if skip_terminal and state.get("status") in TERMINAL_RUN_STATUSES:
                    continue
                outcome = reconcile_one(root, state, opener, headers, timeout)
                write_json(path, state)
                outcomes.append(outcome)
            except (JenkinsError, platform.GitLabError, OrchestratorError, OSError) as error:
                errors.append({"path": str(path), "error": str(error)})
        pruned = prune_terminal_runs(root, prune_days) if prune_days else []
        artifacts = (prune_artifacts(root, prune_days, keep_artifacts) if prune_days
                     else {"removed": [], "freed_bytes": 0, "kept": 0})
    return {"phase": 2, "runs": outcomes, "errors": errors, "retired": retired,
            "pruned": pruned, "artifacts": artifacts}


# --------------------------------------------------------------------------- #
# Optional webhook receiver (legacy: the platform signal now comes from GitLab)
# --------------------------------------------------------------------------- #

def webhook_secret(name: str) -> bytes:
    value = os.environ.get(name)
    if not value:
        raise OrchestratorError(f"Webhook secret environment variable is not set: {name}")
    return value.encode("utf-8")


def event_value(payload: dict[str, Any], direct: str, nested: str) -> Any:
    if direct in payload:
        return payload[direct]
    nested_value = payload.get("build")
    if isinstance(nested_value, dict):
        return nested_value.get(nested)
    return None


def webhook_target(payload: dict[str, Any]) -> tuple[str, int]:
    job = payload.get("job") or payload.get("job_name")
    if isinstance(job, dict):
        job = job.get("name")
    number = event_value(payload, "build_number", "number")
    if not isinstance(job, str) or not job:
        raise OrchestratorError("Webhook payload must contain job or job_name")
    if not isinstance(number, int) or number < 1:
        raise OrchestratorError("Webhook payload must contain a positive build_number")
    return job, number


def make_webhook_server(root: Path, host: str, port: int, secret_env: str,
                        timeout: int) -> ThreadingHTTPServer:
    secret = webhook_secret(secret_env)

    class WebhookHandler(BaseHTTPRequestHandler):
        server_version = "OpenHarmonyCIWebhook/1.1"

        def log_message(self, format: str, *args: Any) -> None:
            print(f"webhook {self.address_string()} {format % args}", file=sys.stderr)

        def respond(self, status: int, value: dict[str, Any]) -> None:
            body = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path != "/healthz":
                self.respond(404, {"error": "not found"})
                return
            self.respond(200, {"phase": 2, "status": "ok"})

        def do_POST(self) -> None:
            if self.path != "/jenkins":
                self.respond(404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 1_048_576:
                    raise OrchestratorError("Webhook body must be 1 to 1048576 bytes")
                body = self.rfile.read(length)
                supplied = self.headers.get("X-CI-Signature", "")
                expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
                if not hmac.compare_digest(supplied, expected):
                    self.respond(401, {"error": "invalid webhook signature"})
                    return
                payload = json.loads(body.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise OrchestratorError("Webhook payload must be a JSON object")
                webhook_target(payload)
                result = reconcile_runs(root, timeout, skip_terminal=True)
                if not result["runs"]:
                    self.respond(404, {"error": "no tracked run matched webhook", **result})
                    return
                self.respond(202, result)
            except (UnicodeDecodeError, json.JSONDecodeError, OrchestratorError) as error:
                self.respond(400, {"error": str(error)})

    return ThreadingHTTPServer((host, port), WebhookHandler)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def add_run_options(parser: argparse.ArgumentParser, *, profile_help: str) -> None:
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    parser.add_argument("--mr-iid", required=True, help="GitLab merge request iid")
    parser.add_argument("--source-sha", required=True, help="Exact MR head SHA the platform built")
    parser.add_argument("--source-branch", default="")
    parser.add_argument("--target-branch", default="")
    parser.add_argument("--base-url", default=None,
                        help="Jenkins base URL (default: JENKINS_BASE_URL or the platform host)")
    parser.add_argument("--repo-dir", required=True)
    parser.add_argument("--run-id")
    parser.add_argument(
        "--agent-sandbox", choices=("read-only", "workspace-write", "danger-full-access"),
        default="read-only",
    )
    parser.add_argument("--phase3-profile", help=profile_help)
    parser.add_argument(
        "--regression-profile", action="append", default=[],
        help="Trusted regression profile to run after OTA; repeat for multiple profiles.",
    )
    parser.add_argument("--timeout-seconds", type=int, default=20)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Rehearsal: hand the claimed build to phase 3 with --dry-run "
             "(download and validate the OTA package, never touch a device)",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    observe = subparsers.add_parser(
        "observe", help="Register a run against the platform's own MR pipeline (no trigger)")
    add_run_options(
        observe,
        profile_help="Trusted phase-3 board profile; fixes the chip whose gate build is claimed. "
                     "Requires --agent-sandbox danger-full-access.",
    )

    adopt = subparsers.add_parser(
        "adopt", help="Register and immediately claim an already-finished gate build")
    add_run_options(
        adopt,
        profile_help="Trusted phase-3 board profile; also selects the chip's gate build.",
    )

    note = subparsers.add_parser("note", help="Append one audit event to an existing run")
    note.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    note.add_argument("--run-id", required=True)
    note.add_argument("--kind", default="note")
    note.add_argument("text", help="Event detail")

    rearm = subparsers.add_parser(
        "rearm", help="Re-arm a blocked/finished run so reconcile can retry it")
    rearm.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    rearm.add_argument("--run-id", required=True)

    reconcile = subparsers.add_parser("reconcile", help="Poll GitLab and claim the gate build")
    reconcile.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    reconcile.add_argument("--run-id")
    reconcile.add_argument("--timeout-seconds", type=int, default=20)
    reconcile.add_argument("--include-terminal", action="store_true",
                           help="Also process runs that already ended (default: skip them)")
    reconcile.add_argument("--prune-days", type=int, default=0,
                           help="Archive terminal runs and reclaim their OTA packages after N days (0 disables)")
    reconcile.add_argument("--keep-artifacts", type=int, default=3,
                           help="Always keep the OTA packages of the newest N finished runs (default 3)")

    serve = subparsers.add_parser("serve", help="Optional legacy Jenkins completion webhook")
    serve.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8787)
    serve.add_argument("--webhook-secret-env", default="CI_WEBHOOK_SECRET")
    serve.add_argument("--timeout-seconds", type=int, default=20)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = state_directory(args.state_dir)
    if args.command in ("observe", "adopt"):
        if args.timeout_seconds <= 0:
            raise OrchestratorError("timeout-seconds must be positive")
        args.mode = args.command
        with state_lock(root):
            state = create_run(root, args)
        print(json.dumps({
            "phase": 2, "action": args.command, "run_id": state["run_id"],
            "status": state["status"], "chip": state["trigger"].get("chip"),
            "dry_run": bool((state.get("phase3") or {}).get("dry_run")),
            "pipeline_id": (state["trigger"].get("platform") or {}).get("pipeline_id"),
            "gate_build_url": (state["trigger"].get("platform") or {}).get("gate_build_url"),
            "build_url": (state.get("build") or {}).get("url"),
            "state": str(run_path(root, state["run_id"])),
        }, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "rearm":
        print(json.dumps({"phase": 2, "action": "rearm", **rearm_run(root, args.run_id)},
                         ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "note":
        with state_lock(root):
            path = run_path(root, args.run_id)
            state = load_json(path)
            add_event(state, args.kind, args.text)
            write_json(path, state)
        print(json.dumps({"phase": 2, "action": "note", "run_id": args.run_id},
                         ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "reconcile":
        if args.timeout_seconds <= 0 or args.prune_days < 0 or args.keep_artifacts < 0:
            raise OrchestratorError(
                "timeout-seconds must be positive; prune-days and keep-artifacts non-negative")
        print(json.dumps(
            reconcile_runs(root, args.timeout_seconds, args.run_id,
                           skip_terminal=not args.include_terminal,
                           prune_days=args.prune_days, keep_artifacts=args.keep_artifacts),
            ensure_ascii=False, sort_keys=True))
        return 0
    if args.timeout_seconds <= 0 or not 1 <= args.port <= 65535:
        raise OrchestratorError("timeout-seconds must be positive and port must be 1 to 65535")
    server = make_webhook_server(root, args.host, args.port, args.webhook_secret_env,
                                 args.timeout_seconds)
    print(json.dumps({"phase": 2, "action": "serving", "host": args.host, "port": args.port,
                      "state_dir": str(root), "legacy": True},
                     ensure_ascii=False, sort_keys=True), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (JenkinsError, platform.GitLabError, OrchestratorError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(2)
