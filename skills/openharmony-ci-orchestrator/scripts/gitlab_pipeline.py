#!/usr/bin/env python3
"""Read-only GitLab observation layer for the platform MR gate.

平台 CI 契约（2026-10 起）：本插件的 `ci` 阶段**不再触发 Jenkins**，而是只读观察 GitLab
为 MR 自动创建的流水线，并在成功后由 ci_orchestrator 认领对应的 Jenkins 门禁构建。

只用 `glab api` 这一条只读通道（与 phase3_runner 写 MR 评论用的是同一个 CLI 与同一份登录），
不在这里实现 GitLab 鉴权与 token 存储；token 仍在 `~/.config/glab-cli/config.yml` / keyring。
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any

from orchestrator_config import configured_value, resolve_binary

DEFAULT_HOST = "192.168.11.238"
DEFAULT_PROJECT = "harmony/system/v9611/openharmony_V6.1"
# 与平台门禁配置一致：仅这两个目标分支进固件门禁，其余分支只有轻量占位任务。
GATE_TARGET_BRANCHES = ("v6.1.0.31_release", "v6.1.0.35_lts")
GATE_JOB = "openharmony-mr-gate"
GATE_BUILD_PATTERN = re.compile(r"Jenkins gate:\s*(\S+)")
SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{7,64}$")

# GitLab pipeline status -> 本插件 run 状态
PIPELINE_STATES = {
    "success": "gate_succeeded",
    "failed": "gate_failed",
    "canceled": "gate_aborted",
    "cancelled": "gate_aborted",
    "skipped": "gate_aborted",
    "manual": "gate_pending",
    "created": "gate_pending",
    "waiting_for_resource": "gate_pending",
    "preparing": "gate_pending",
    "pending": "gate_pending",
    "running": "gate_pending",
    "scheduled": "gate_pending",
}


class GitLabError(RuntimeError):
    """A user-actionable GitLab observation or configuration error."""


def glab_binary() -> str:
    binary = resolve_binary("glab", configured_value("GLAB_BIN"))
    if not binary:
        raise GitLabError(
            "glab CLI is required for platform CI observation; set GLAB_BIN or install glab"
        )
    return binary


def api(host: str, endpoint: str, *, timeout: int = 60, paginate: bool = False) -> Any:
    """Run one read-only `glab api` request and return the decoded JSON payload."""
    command = [glab_binary(), "api", "--hostname", host, endpoint]
    if paginate:
        command.extend(["--paginate", "--per-page", "100"])
    result = subprocess.run(command, text=True, capture_output=True,
                            timeout=timeout, check=False)
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip()[-500:]
        raise GitLabError(f"glab api {endpoint} failed ({result.returncode}): {detail}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise GitLabError(f"glab api {endpoint} returned invalid JSON: {error}") from error


def project_path(project: str | None = None) -> str:
    value = project or configured_value("GITLAB_PROJECT")
    if not value:
        raise GitLabError(
            "GITLAB_PROJECT is not configured; add it to "
            "~/.config/openharmony-ci-orchestrator/jenkins.env"
        )
    return value


def host_value(host: str | None = None) -> str:
    return host or configured_value("GITLAB_HOST") or DEFAULT_HOST


def merge_request(iid: int | str, host: str | None = None, project: str | None = None,
                  timeout: int = 60) -> dict[str, Any]:
    """Read one MR with its current head pipeline (id/status/sha)."""
    if not str(iid).isdigit():
        raise GitLabError(f"Invalid MR iid: {iid!r}")
    encoded = project_path(project).replace("/", "%2F")
    payload = api(host_value(host), f"projects/{encoded}/merge_requests/{iid}", timeout=timeout)
    if not isinstance(payload, dict):
        raise GitLabError("GitLab merge request response must be a JSON object")
    return payload


def pipeline(pipeline_id: int, host: str | None = None, project: str | None = None,
             timeout: int = 60) -> dict[str, Any]:
    encoded = project_path(project).replace("/", "%2F")
    payload = api(host_value(host), f"projects/{encoded}/pipelines/{int(pipeline_id)}",
                  timeout=timeout)
    if not isinstance(payload, dict):
        raise GitLabError("GitLab pipeline response must be a JSON object")
    return payload


def job_trace(job_id: int, host: str | None = None, project: str | None = None,
              timeout: int = 120) -> str:
    encoded = project_path(project).replace("/", "%2F")
    payload = api(host_value(host), f"projects/{encoded}/jobs/{int(job_id)}/trace",
                  timeout=timeout)
    return payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)


def gate_build_url(trace: str) -> str | None:
    """Extract the Jenkins gate build URL that the Runner bridge printed.

    日志行形如：`Jenkins gate: http://192.168.97.54:8080/job/OpenHarmony-V6.1-MR-Gate/50/`
    """
    match = GATE_BUILD_PATTERN.search(trace or "")
    return match.group(1) if match else None


def head_pipeline(mr: dict[str, Any]) -> dict[str, Any] | None:
    pipeline_record = mr.get("head_pipeline")
    if not isinstance(pipeline_record, dict) or pipeline_record.get("id") is None:
        return None
    return pipeline_record


def source_sha_of(mr: dict[str, Any]) -> str | None:
    value = mr.get("sha")
    return value if isinstance(value, str) and SHA_PATTERN.fullmatch(value) else None


def run_state_for(pipeline_record: dict[str, Any], *, target_branch: str) -> str:
    status = str(pipeline_record.get("status") or "").lower()
    state = PIPELINE_STATES.get(status)
    if state is None:
        raise GitLabError(f"Unknown GitLab pipeline status: {status!r}")
    if state == "gate_aborted" and target_branch not in GATE_TARGET_BRANCHES:
        # 平台对非 release 目标分支只跑轻量占位任务：没有固件验证，也不算失败。
        return "gate_not_in_scope"
    return state


def observed_platform(mr: dict[str, Any], run_state: str) -> dict[str, Any]:
    pipeline_record = head_pipeline(mr) or {}
    return {
        "gitlab_host": host_value(),
        "gitlab_project": project_path(),
        "mr_state": mr.get("state"),
        "source_branch": mr.get("source_branch"),
        "target_branch": mr.get("target_branch"),
        "mr_sha": mr.get("sha"),
        "web_url": mr.get("web_url"),
        "pipeline_id": pipeline_record.get("id"),
        "pipeline_status": pipeline_record.get("status"),
        "pipeline_sha": pipeline_record.get("sha"),
        "pipeline_web_url": pipeline_record.get("web_url"),
        "head_pipeline": bool(pipeline_record),
        "run_state": run_state,
    }
