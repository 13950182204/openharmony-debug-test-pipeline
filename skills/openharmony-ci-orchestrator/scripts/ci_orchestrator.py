#!/usr/bin/env python3
"""Persist and reconcile OpenHarmony Jenkins build runs for CI phases 2 and 3.

Commands:
  register   Store one successful phase-1 trigger result.
  reconcile  Query Jenkins for completion and launch one follow-up dsh headless session.
  serve      Accept authenticated Jenkins completion webhooks.

Phase 3 is opt-in at registration time. A successful build then launches one
restricted handoff agent which invokes the trusted phase3 runner. This module
does not itself download artifacts or write devices.
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
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request

from trigger_jenkins_build import (JenkinsError, SHA_PATTERN, build_http_opener,
                                   credentials, make_url, request_json)


DEFAULT_STATE_DIR = Path.home() / ".local/state/openharmony-ci-orchestrator"
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
TERMINAL_BUILD_STATUSES = frozenset(("build_failed", "blocked"))
MAX_EVENTS = 100


class OrchestratorError(RuntimeError):
    """A user-actionable state, webhook, or Jenkins reconciliation error."""


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


def safe_url(value: Any, expected_origin: tuple[str, str] | None = None) -> str:
    if not isinstance(value, str) or not value:
        raise OrchestratorError("Expected a non-empty Jenkins URL")
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise OrchestratorError(f"Invalid Jenkins URL: {value!r}")
    origin = (parsed.scheme, parsed.netloc)
    if expected_origin is not None and origin != expected_origin:
        raise OrchestratorError(f"Jenkins URL origin does not match the tracked job: {value!r}")
    return value.rstrip("/")


def origin_of(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    return parsed.scheme, parsed.netloc


def text_map(value: Any, name: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise OrchestratorError(f"{name} must be a JSON object")
    output: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, (str, int, float, bool)):
            raise OrchestratorError(f"{name} contains an unsupported value")
        output[key] = str(item)
    return output


def new_run_id() -> str:
    return f"ci-{int(time.time())}-{secrets.token_hex(4)}"


def load_trigger_result(input_path: str) -> dict[str, Any]:
    if input_path == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(input_path).read_text(encoding="utf-8")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise OrchestratorError("Phase-1 trigger result must be valid JSON") from error
    if not isinstance(value, dict):
        raise OrchestratorError("Phase-1 trigger result must be a JSON object")
    return value


def absolute_directory(value: str, name: str) -> str:
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise OrchestratorError(f"{name} must be an existing directory: {path}")
    return str(path)


def default_agent_command() -> str:
    # DSH 定制：交接会话由 codex CLI 改为 dsh headless（dsh --profile headless <task>）。
    # 可用 DSH_BIN 环境变量覆盖，例如指向 npx 缓存的 dsh 可执行文件。
    return os.environ.get("DSH_BIN") or shutil.which("dsh") or "dsh"


def create_run(root: Path, trigger: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    if trigger.get("phase") != 1 or trigger.get("action") != "trigger":
        raise OrchestratorError("Register only a real phase-1 trigger result, never a dry-run")
    job_url = safe_url(trigger.get("job_url"))
    job_name = trigger.get("job")
    branch = trigger.get("source_branch")
    if not isinstance(job_name, str) or not job_name:
        raise OrchestratorError("Trigger result is missing job")
    if not isinstance(branch, str) or not branch:
        raise OrchestratorError("Trigger result is missing source_branch")
    queue_url = trigger.get("queue_url")
    build_url = trigger.get("build_url")
    build_number = trigger.get("build_number")
    if queue_url is None and build_url is None and build_number is None:
        raise OrchestratorError("Trigger result has neither queue URL nor assigned build")
    origin = origin_of(job_url)
    if queue_url is not None:
        queue_url = safe_url(queue_url, origin)
    if build_url is not None:
        build_url = safe_url(build_url, origin)
    if build_number is not None and not isinstance(build_number, int):
        raise OrchestratorError("Trigger build_number must be an integer")
    run_id = args.run_id or new_run_id()
    path = run_path(root, run_id)
    if path.exists():
        raise OrchestratorError(f"Run id already exists: {run_id}")
    repo_dir = absolute_directory(args.repo_dir, "repo-dir")
    agent_command = args.agent_command or default_agent_command()
    if not agent_command.strip():
        raise OrchestratorError("agent-command must not be empty")
    if args.phase3_profile and args.agent_sandbox != "danger-full-access":
        raise OrchestratorError(
            "A phase-3 profile requires --agent-sandbox danger-full-access"
        )
    state = {
        "schema_version": 2,
        "run_id": run_id,
        "phase": 2,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "status": "queued" if queue_url else "building",
        "trigger": {
            "job": job_name,
            "job_url": job_url,
            "source_branch": branch,
            "source_sha": trigger.get("source_sha"),
            "mr_iid": trigger.get("mr_iid"),
            "parameters": text_map(trigger.get("parameters"), "trigger parameters"),
            "queue_url": queue_url,
            "build_number": build_number,
            "build_url": build_url,
        },
        "build": {
            "result": None,
            "verified_parameters": False,
        },
        "agent": {
            "status": "not_started",
            "repo_dir": repo_dir,
            "command": agent_command,
            "sandbox": args.agent_sandbox,
        },
        "phase3": {
            "status": "not_requested" if not args.phase3_profile else "not_started",
            "profile": args.phase3_profile,
            "regression_profiles": args.regression_profile,
            "attempts": [],
        },
        "events": [],
    }
    add_event(state, "registered", "Stored phase-1 trigger result")
    write_json(path, state)
    return state


def create_adopted_run(root: Path, args: argparse.Namespace) -> dict[str, Any]:
    """Adopt one already-completed Jenkins build after live API validation."""
    if not SHA_PATTERN.fullmatch(args.source_sha):
        raise OrchestratorError(f"Invalid source SHA: {args.source_sha!r}")
    if args.phase3_profile and args.agent_sandbox != "danger-full-access":
        raise OrchestratorError(
            "A phase-3 profile requires --agent-sandbox danger-full-access"
        )
    repo_dir = absolute_directory(args.repo_dir, "repo-dir")
    job_url = safe_url(make_url(args.base_url, args.job))
    build_url = expected_build_url(job_url, args.build_number)
    headers = jenkins_headers()
    metadata = build_metadata(build_http_opener(), build_url, headers, args.timeout_seconds)
    if metadata.get("building") or metadata.get("result") != "SUCCESS":
        raise OrchestratorError("Only a completed successful Jenkins build can be adopted")
    parameters = build_parameters(metadata)
    if parameters.get("FIRMWARE_BRANCH") != args.branch:
        raise OrchestratorError(
            f"Jenkins branch mismatch: expected {args.branch!r}, "
            f"got {parameters.get('FIRMWARE_BRANCH')!r}"
        )
    for path in selected_paths(root, None):
        existing = load_json(path)
        if (existing.get("trigger", {}).get("job") == args.job
                and existing.get("build", {}).get("number") == args.build_number):
            raise OrchestratorError(
                f"Jenkins build {args.job} #{args.build_number} is already tracked by "
                f"{existing.get('run_id')!r}"
            )
    run_id = args.run_id or new_run_id()
    path = run_path(root, run_id)
    if path.exists():
        raise OrchestratorError(f"Run id already exists: {run_id}")
    agent_command = args.agent_command or default_agent_command()
    if not agent_command.strip():
        raise OrchestratorError("agent-command must not be empty")
    state = {
        "schema_version": 2,
        "run_id": run_id,
        "phase": 2,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "status": "build_succeeded",
        "trigger": {
            "job": args.job,
            "job_url": job_url,
            "source_branch": args.branch,
            "source_sha": args.source_sha,
            "mr_iid": args.mr_iid,
            "parameters": parameters,
            "queue_url": None,
            "build_number": args.build_number,
            "build_url": build_url,
        },
        "build": {
            "url": build_url,
            "number": args.build_number,
            "result": "SUCCESS",
            "verified_parameters": True,
        },
        "agent": {
            "status": "not_started",
            "repo_dir": repo_dir,
            "command": agent_command,
            "sandbox": args.agent_sandbox,
        },
        "phase3": {
            "status": "not_requested" if not args.phase3_profile else "not_started",
            "profile": args.phase3_profile,
            "regression_profiles": args.regression_profile,
            "attempts": [],
        },
        "events": [],
    }
    add_event(state, "adopted", f"Adopted verified Jenkins build {args.job} #{args.build_number}")
    write_json(path, state)
    try:
        launch_agent(root, state)
    except (OSError, OrchestratorError) as error:
        state["agent"].update({"status": "launch_failed", "error": str(error)})
        add_event(state, "agent_launch_failed", str(error))
    write_json(path, state)
    return state


def jenkins_headers() -> dict[str, str]:
    headers = {"User-Agent": "openharmony-ci-orchestrator/phase2", "Accept": "application/json"}
    headers.update(credentials())
    return headers


def queue_metadata(opener: Any, queue_url: str, headers: dict[str, str], timeout: int) -> dict[str, Any]:
    request = Request(f"{queue_url.rstrip('/')}/api/json", headers=headers)
    return request_json(opener, request, timeout)


def build_metadata(opener: Any, build_url: str, headers: dict[str, str], timeout: int) -> dict[str, Any]:
    tree = "number,url,result,building,actions[parameters[name,value]]"
    request = Request(
        f"{build_url.rstrip('/')}/api/json?{urlencode({'tree': tree})}", headers=headers
    )
    return request_json(opener, request, timeout)


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


def agent_prompt(state_path: Path, state: dict[str, Any]) -> str:
    trigger = state["trigger"]
    phase3 = state.get("phase3", {})
    profile = phase3.get("profile")
    if isinstance(profile, str) and profile:
        skill_dir = Path(__file__).resolve().parents[1]
        runner = skill_dir / "scripts" / "phase3_runner.py"
        return (
            "A tracked OpenHarmony Jenkins build succeeded and has an explicitly approved "
            "phase-3 profile. Execute exactly this trusted runner once, then report its JSON "
            "result without any other device action:\n\n"
            f"{shlex.quote(sys.executable)} {shlex.quote(str(runner))} "
            f"--state-dir {shlex.quote(str(state_path.parents[1]))} "
            f"--run-id {shlex.quote(state['run_id'])} "
            f"--profile {shlex.quote(profile)}\n\n"
            "The runner enforces artifact, package, device, primary/standby, OTA, regression, "
            "and MR-evidence policy. Do not substitute package paths, serials, commands, or "
            "profiles."
        )
    return (
        "A tracked OpenHarmony Jenkins build succeeded. This is an automated phase-2 "
        "handoff session. Read the immutable run-state JSON at "
        f"{state_path}. Confirm the build, branch {trigger['source_branch']!r}, and "
        f"source SHA {trigger.get('source_sha')!r}. Phase 3 is not installed yet: do not "
        "download artifacts, touch HDC devices, perform OTA, run regression tests, modify "
        "the repository, or update GitLab. Report that the run is ready for phase 3 and stop."
    )


def launch_agent(root: Path, state: dict[str, Any]) -> None:
    agent = state["agent"]
    if agent.get("status") != "not_started":
        return
    state_path = run_path(root, state["run_id"])
    log_base = root / "agents" / state["run_id"]
    prompt_path = log_base.with_suffix(".prompt.txt")
    stdout_path = log_base.with_suffix(".stdout.log")
    stderr_path = log_base.with_suffix(".stderr.log")
    prompt = agent_prompt(state_path, state)
    prompt_path.write_text(prompt + "\n", encoding="utf-8")
    os.chmod(prompt_path, 0o600)
    command = shlex.split(agent["command"])
    if not command:
        raise OrchestratorError("Stored agent command is empty")
    executable = command[0]
    if "/" not in executable:
        resolved = shutil.which(executable)
        if not resolved:
            raise OrchestratorError(f"dsh executable is not available: {executable}")
        command[0] = resolved
    elif not Path(executable).is_file():
        raise OrchestratorError(f"dsh executable does not exist: {executable}")
    # DSH 定制：不再使用 codex exec 的 --cd/--sandbox/--json/--output-last-message。
    # dsh headless 的 workspace 根目录取进程 cwd（下方 Popen cwd=repo_dir），
    # 权限由 headless profile 的配置决定；--agent-sandbox 仅作为元数据保留在状态里。
    command.extend([
        "--profile", "headless",
        prompt,
    ])
    with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
        process = subprocess.Popen(
            command,
            cwd=agent["repo_dir"],
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
    agent.update({
        "status": "launched",
        "pid": process.pid,
        "launched_at": utc_now(),
        "prompt_path": str(prompt_path),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
    })
    add_event(state, "agent_launched", f"Started a new dsh headless session with PID {process.pid}")


def reconcile_one(
    root: Path,
    state: dict[str, Any],
    opener: Any,
    headers: dict[str, str],
    timeout: int,
    candidate_build_number: int | None = None,
) -> dict[str, Any]:
    trigger = state["trigger"]
    status = state.get("status")
    if status in TERMINAL_BUILD_STATUSES:
        return {"run_id": state["run_id"], "status": status, "action": "terminal"}
    if status == "build_succeeded":
        agent_status = state.get("agent", {}).get("status")
        if agent_status == "launched":
            return {"run_id": state["run_id"], "status": status, "action": "agent_already_launched"}
        if agent_status == "launch_failed":
            return {"run_id": state["run_id"], "status": status, "action": "agent_launch_failed"}
    origin = origin_of(trigger["job_url"])
    build_url = trigger.get("build_url")
    build_number = trigger.get("build_number")

    if build_url is None and build_number is None:
        queue_url = trigger.get("queue_url")
        if not queue_url:
            state["status"] = "blocked"
            add_event(state, "blocked", "No Jenkins queue URL or build URL is available")
            return {"run_id": state["run_id"], "status": state["status"], "action": "blocked"}
        queue = queue_metadata(opener, safe_url(queue_url, origin), headers, timeout)
        if queue.get("cancelled"):
            state["status"] = "build_failed"
            state["build"]["result"] = "CANCELLED"
            add_event(state, "build_cancelled", "Jenkins cancelled the queue item")
            return {"run_id": state["run_id"], "status": state["status"], "action": "cancelled"}
        executable = queue.get("executable") or {}
        number = executable.get("number")
        url = executable.get("url")
        if not isinstance(number, int) or not isinstance(url, str):
            state["status"] = "queued"
            add_event(state, "queue_pending", "Jenkins has not assigned a build number")
            return {"run_id": state["run_id"], "status": state["status"], "action": "queue_pending"}
        build_number = number
        build_url = safe_url(url, origin)

    if candidate_build_number is not None and build_number != candidate_build_number:
        return {"run_id": state["run_id"], "status": status, "action": "ignored_other_build"}

    if build_url is None:
        build_url = expected_build_url(trigger["job_url"], int(build_number))
    build_url = safe_url(build_url, origin)
    metadata = build_metadata(opener, build_url, headers, timeout)
    actual_parameters = build_parameters(metadata)
    matches, reason = parameters_match(trigger["parameters"], actual_parameters)
    if not matches:
        if candidate_build_number is not None and trigger.get("build_number") is None:
            return {"run_id": state["run_id"], "status": status, "action": "ignored_unmatched_event"}
        state["status"] = "blocked"
        state["build"].update({"url": build_url, "number": metadata.get("number"), "result": metadata.get("result")})
        add_event(state, "blocked", f"Jenkins build parameters do not match tracked run: {reason}")
        return {"run_id": state["run_id"], "status": state["status"], "action": "parameter_mismatch"}

    trigger["build_url"] = build_url
    trigger["build_number"] = metadata.get("number", build_number)
    state["build"].update({
        "url": build_url,
        "number": metadata.get("number", build_number),
        "result": metadata.get("result"),
        "verified_parameters": True,
    })
    if metadata.get("building") or metadata.get("result") is None:
        state["status"] = "building"
        add_event(state, "build_running", f"Jenkins build {trigger['build_number']} is still running")
        return {"run_id": state["run_id"], "status": state["status"], "action": "building"}
    if metadata.get("result") != "SUCCESS":
        state["status"] = "build_failed"
        add_event(state, "build_failed", f"Jenkins build finished with {metadata.get('result')!r}")
        return {"run_id": state["run_id"], "status": state["status"], "action": "build_failed"}

    state["status"] = "build_succeeded"
    add_event(state, "build_succeeded", f"Jenkins build {trigger['build_number']} finished successfully")
    try:
        launch_agent(root, state)
    except (OSError, OrchestratorError) as error:
        state["agent"].update({"status": "launch_failed", "error": str(error)})
        add_event(state, "agent_launch_failed", str(error))
        return {"run_id": state["run_id"], "status": state["status"], "action": "agent_launch_failed"}
    return {
        "run_id": state["run_id"],
        "status": state["status"],
        "action": "agent_launched" if state["agent"]["status"] == "launched" else "agent_already_launched",
    }


def selected_paths(root: Path, run_id: str | None) -> list[Path]:
    if run_id:
        return [run_path(root, run_id)]
    return sorted((root / "runs").glob("*.json"))


def reconcile_runs(
    root: Path,
    timeout: int,
    run_id: str | None = None,
    job: str | None = None,
    candidate_build_number: int | None = None,
) -> dict[str, Any]:
    headers = jenkins_headers()
    opener = build_http_opener()
    outcomes = []
    errors = []
    with state_lock(root):
        for path in selected_paths(root, run_id):
            try:
                state = load_json(path)
                if job is not None and state.get("trigger", {}).get("job") != job:
                    continue
                outcome = reconcile_one(
                    root, state, opener, headers, timeout, candidate_build_number
                )
                write_json(path, state)
                outcomes.append(outcome)
            except (JenkinsError, OrchestratorError, OSError) as error:
                errors.append({"path": str(path), "error": str(error)})
    return {"phase": 2, "runs": outcomes, "errors": errors}


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


def make_webhook_server(
    root: Path, host: str, port: int, secret_env: str, timeout: int
) -> ThreadingHTTPServer:
    secret = webhook_secret(secret_env)

    class WebhookHandler(BaseHTTPRequestHandler):
        server_version = "OpenHarmonyCIWebhook/1.0"

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
                job, number = webhook_target(payload)
                result = reconcile_runs(
                    root, timeout, job=job, candidate_build_number=number
                )
                if not result["runs"]:
                    self.respond(404, {"error": "no tracked run matched webhook", **result})
                    return
                self.respond(202, result)
            except (UnicodeDecodeError, json.JSONDecodeError, OrchestratorError) as error:
                self.respond(400, {"error": str(error)})

    return ThreadingHTTPServer((host, port), WebhookHandler)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    register = subparsers.add_parser("register", help="Store a phase-1 Jenkins trigger result")
    register.add_argument("--input", default="-", help="Phase-1 JSON result path, or - for stdin")
    register.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    register.add_argument("--repo-dir", required=True)
    register.add_argument("--run-id")
    register.add_argument("--agent-command")
    register.add_argument(
        "--agent-sandbox", choices=("read-only", "workspace-write", "danger-full-access"), default="read-only"
    )
    register.add_argument(
        "--phase3-profile",
        help="Trusted phase-3 board profile. Requires --agent-sandbox danger-full-access.",
    )
    register.add_argument(
        "--regression-profile", action="append", default=[],
        help="Trusted regression profile to run after OTA; repeat for multiple profiles.",
    )

    adopt = subparsers.add_parser("adopt", help="Adopt one completed Jenkins build after API validation")
    adopt.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    adopt.add_argument("--base-url", required=True)
    adopt.add_argument("--job", required=True)
    adopt.add_argument("--build-number", type=int, required=True)
    adopt.add_argument("--branch", required=True)
    adopt.add_argument("--source-sha", required=True)
    adopt.add_argument("--mr-iid", required=True)
    adopt.add_argument("--repo-dir", required=True)
    adopt.add_argument("--run-id")
    adopt.add_argument("--agent-command")
    adopt.add_argument(
        "--agent-sandbox", choices=("read-only", "workspace-write", "danger-full-access"), default="read-only"
    )
    adopt.add_argument("--phase3-profile")
    adopt.add_argument("--regression-profile", action="append", default=[])
    adopt.add_argument("--timeout-seconds", type=int, default=20)

    reconcile = subparsers.add_parser("reconcile", help="Query Jenkins and start the phase-2 agent")
    reconcile.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    reconcile.add_argument("--run-id")
    reconcile.add_argument("--timeout-seconds", type=int, default=20)

    serve = subparsers.add_parser("serve", help="Receive authenticated Jenkins completion events")
    serve.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8787)
    serve.add_argument("--webhook-secret-env", default="CI_WEBHOOK_SECRET")
    serve.add_argument("--timeout-seconds", type=int, default=20)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = state_directory(args.state_dir)
    if args.command == "register":
        with state_lock(root):
            state = create_run(root, load_trigger_result(args.input), args)
        print(json.dumps({"phase": 2, "action": "registered", "run_id": state["run_id"], "state": str(run_path(root, state["run_id"]))}, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "adopt":
        if args.build_number < 1 or args.timeout_seconds <= 0:
            raise OrchestratorError("build-number and timeout-seconds must be positive")
        with state_lock(root):
            state = create_adopted_run(root, args)
        print(json.dumps({"phase": 2, "action": "adopted", "run_id": state["run_id"], "state": str(run_path(root, state["run_id"])), "agent": state["agent"]["status"]}, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "reconcile":
        if args.timeout_seconds <= 0:
            raise OrchestratorError("timeout-seconds must be positive")
        print(json.dumps(reconcile_runs(root, args.timeout_seconds, args.run_id), ensure_ascii=False, sort_keys=True))
        return 0
    if args.timeout_seconds <= 0 or not 1 <= args.port <= 65535:
        raise OrchestratorError("timeout-seconds must be positive and port must be 1 to 65535")
    server = make_webhook_server(root, args.host, args.port, args.webhook_secret_env, args.timeout_seconds)
    print(json.dumps({"phase": 2, "action": "serving", "host": args.host, "port": args.port, "state_dir": str(root)}, ensure_ascii=False, sort_keys=True), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (JenkinsError, OrchestratorError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(2)
