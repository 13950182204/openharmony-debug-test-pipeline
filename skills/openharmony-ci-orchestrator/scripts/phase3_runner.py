#!/usr/bin/env python3
"""Run the trusted phase-3 A333 OTA and regression workflow for one CI run."""

from __future__ import annotations

import argparse
import codecs
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request
from xml.etree import ElementTree

from ci_orchestrator import (OrchestratorError, add_event, load_json, run_path,
                             state_directory, state_lock, write_json)
from trigger_jenkins_build import (JenkinsError, build_http_opener, credentials,
                                   request_json)


SKILL_DIR = Path(__file__).resolve().parents[1]
PROFILES_DIR = SKILL_DIR / "profiles"
PROFILE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_CAPTURE = 8_000
CONFIG_ENV = Path.home() / ".config/openharmony-ci-orchestrator/jenkins.env"
HDC_LOCAL_WARNING = re.compile(r"\[W\]\[[^\]]+\] FreeChannelContinue handle->data is nullptr")


class Phase3Error(RuntimeError):
    """A terminal safety, package, device, or regression failure."""


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def captured(value: str) -> str:
    value = value.strip()
    return value[-MAX_CAPTURE:]


def configured_value(name: str) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    try:
        for line in CONFIG_ENV.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{name}="):
                configured = line.partition("=")[2].strip()
                return configured or None
    except FileNotFoundError:
        pass
    return None


def normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def read_profile(profile_id: str) -> dict[str, Any]:
    if not PROFILE_PATTERN.fullmatch(profile_id):
        raise Phase3Error(f"Invalid profile id: {profile_id!r}")
    path = PROFILES_DIR / f"{profile_id}.json"
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise Phase3Error(f"Trusted profile does not exist: {profile_id}") from error
    except json.JSONDecodeError as error:
        raise Phase3Error(f"Trusted profile is invalid JSON: {path}") from error
    if not isinstance(profile, dict) or profile.get("id") != profile_id:
        raise Phase3Error(f"Trusted profile has an invalid id: {path}")
    return profile


def run_command(
    command: list[str], timeout: int = 60, check: bool = True, max_capture: int = MAX_CAPTURE
) -> str:
    result = subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)
    output = result.stdout.strip()
    if len(output) > max_capture:
        output = output[-max_capture:]
    if check and result.returncode:
        detail = result.stdout + ("\n" if result.stdout and result.stderr else "") + result.stderr
        if len(detail) > max_capture:
            detail = detail[-max_capture:]
        raise Phase3Error(f"Command failed ({result.returncode}): {' '.join(command[:3])}: {detail}")
    return output


def hdc(serial: str, *arguments: str, timeout: int = 60, check: bool = True) -> str:
    output = run_command(["hdc", "-t", serial, *arguments], timeout=timeout, check=check)
    return HDC_LOCAL_WARNING.sub("", output).strip()


def listed_targets() -> set[str]:
    output = run_command(["hdc", "list", "targets"], check=True)
    return {line.strip().split()[0] for line in output.splitlines() if line.strip()}


def require_target(serial: str) -> None:
    if serial not in listed_targets():
        raise Phase3Error(f"Configured target is not online in hdc list targets: {serial}")


def shell(serial: str, command: str, timeout: int = 60, check: bool = True) -> str:
    return hdc(serial, "shell", command, timeout=timeout, check=check)


def device_snapshot(device: dict[str, Any]) -> dict[str, str]:
    serial = device["serial"]
    require_target(serial)
    return {
        "product_name": shell(serial, "param get const.product.name"),
        "product_model": shell(serial, "param get const.product.model"),
        "software_version": shell(serial, "param get const.product.software.version"),
        "ohos_fullname": shell(serial, "param get const.ohos.fullname"),
        "device_tree_model": shell(serial, "cat /proc/device-tree/model"),
        "boot_completed": shell(serial, "param get bootevent.boot.completed"),
        "free_kib_line": shell(serial, "df -k /data | tail -n 1"),
        "write_updater": shell(serial, "command -v write_updater"),
    }


def require_device_identity(device: dict[str, Any], snapshot: dict[str, str]) -> None:
    for key in ("product_name", "product_model", "device_tree_model"):
        actual = snapshot.get(key, "").strip("\x00\n ")
        expected = str(device[key]).strip()
        if actual != expected:
            raise Phase3Error(
                f"{device['role']} device identity mismatch for {key}: "
                f"expected {expected!r}, got {actual!r}"
            )
    if not snapshot["write_updater"].strip():
        raise Phase3Error(f"{device['role']} device has no write_updater command")
    if snapshot["boot_completed"].strip() != "true":
        raise Phase3Error(f"{device['role']} device has not completed normal boot")


def available_kib(df_line: str) -> int:
    fields = df_line.split()
    for index, field in enumerate(fields):
        if field.endswith("%") and index >= 1 and fields[index - 1].isdigit():
            return int(fields[index - 1])
    raise Phase3Error(f"Unable to parse /data free space from: {df_line!r}")


def package_metadata(package: Path, console_target_version: str | None = None) -> dict[str, Any]:
    source_versions: list[str] = []
    target_versions: list[str] = []
    try:
        with zipfile.ZipFile(package) as archive:
            names = set(archive.namelist())
            for name in ("updater_config/VERSION.mbn", "version_list"):
                if name in names:
                    source_versions.extend(
                        line.strip() for line in archive.read(name).decode("utf-8", "replace").splitlines()
                        if line.strip()
                    )
            for name in (
                "updater_config/updater_specified_config.xml",
                "updater_specified_config.xml",
            ):
                if name not in names:
                    continue
                root = ElementTree.fromstring(archive.read(name))
                for element in root.iter():
                    for key, value in element.attrib.items():
                        if key.rsplit("}", 1)[-1] == "softVersion" and value.strip():
                            target_versions.append(value.strip())
    except (OSError, zipfile.BadZipFile, ElementTree.ParseError) as error:
        raise Phase3Error(f"Unable to read updater package metadata: {error}") from error
    if not source_versions:
        raise Phase3Error("OTA package has no VERSION.mbn or version_list source-version whitelist")
    target_evidence = "package"
    if not target_versions and console_target_version:
        target_versions = [console_target_version]
        target_evidence = "jenkins_console"
    if len(set(target_versions)) != 1:
        raise Phase3Error(f"OTA package must expose one target softVersion, got {target_versions!r}")
    return {
        "source_versions": source_versions,
        "target_version": target_versions[0],
        "target_version_evidence": target_evidence,
        "size": package.stat().st_size,
    }


def source_version_matches(
    metadata: dict[str, Any], snapshot: dict[str, str], source_version_property: str
) -> bool:
    if source_version_property not in ("software_version", "ohos_fullname"):
        raise Phase3Error(f"Unsupported source-version property: {source_version_property!r}")
    device_versions = {normalized(snapshot[source_version_property])}
    return any(normalized(version) in device_versions for version in metadata["source_versions"])


def target_version_matches(metadata: dict[str, Any], snapshot: dict[str, str]) -> bool:
    target = normalized(metadata["target_version"])
    return target in {normalized(snapshot["software_version"]), normalized(snapshot["ohos_fullname"])}


def assert_profile_matches(profile: dict[str, Any], state: dict[str, Any]) -> None:
    trigger = state.get("trigger", {})
    jenkins = profile.get("jenkins", {})
    if trigger.get("job") != jenkins.get("job"):
        raise Phase3Error(
            f"Profile requires Jenkins job {jenkins.get('job')!r}, got {trigger.get('job')!r}"
        )
    parameters = trigger.get("parameters")
    if not isinstance(parameters, dict):
        raise Phase3Error("Run state has no verified Jenkins parameters")
    for name, accepted in jenkins.get("parameters", {}).items():
        if parameters.get(name) not in accepted:
            raise Phase3Error(
                f"Profile rejects Jenkins parameter {name}: {parameters.get(name)!r} not in {accepted!r}"
            )
    build = state.get("build", {})
    if state.get("status") != "build_succeeded" or build.get("result") != "SUCCESS":
        raise Phase3Error("Phase 3 requires a successful Jenkins build")
    if build.get("verified_parameters") is not True:
        raise Phase3Error("Phase 3 requires Jenkins parameters verified by phase 2")
    if not isinstance(build.get("url"), str):
        raise Phase3Error("Phase 3 requires a Jenkins build URL")


def verify_source_sha(state: dict[str, Any]) -> dict[str, str]:
    expected = state.get("trigger", {}).get("source_sha")
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{7,64}", expected):
        raise Phase3Error("Phase 3 requires a registered source SHA")
    build_url = state["build"]["url"].rstrip("/")
    request = Request(
        f"{build_url}/consoleText",
        headers={"User-Agent": "openharmony-ci-orchestrator/phase3", **credentials()},
    )
    pattern = re.compile(r"HEAD (?:is now at|现在位于)\s+([0-9a-fA-F]{7,64})")
    package_target_pattern = re.compile(r"\[VERSION\]\s+package softVersion:\s*(.+)$")
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    remainder = ""
    matches: list[str] = []
    package_targets: list[str] = []

    def examine(line: str) -> None:
        matches.extend(match.group(1) for match in pattern.finditer(line))
        target = package_target_pattern.search(line)
        if target and target.group(1).strip():
            package_targets.append(target.group(1).strip())
    try:
        with build_http_opener().open(request, timeout=120) as response:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                text = remainder + decoder.decode(chunk)
                lines = text.split("\n")
                remainder = lines.pop()
                for line in lines:
                    examine(line)
            examine(remainder + decoder.decode(b"", final=True))
    except OSError as error:
        raise Phase3Error(f"Unable to read Jenkins console for source-SHA verification: {error}") from error
    expected_lower = expected.lower()
    matched = next(
        (
            commit for commit in matches
            if commit.lower().startswith(expected_lower) or expected_lower.startswith(commit.lower())
        ),
        None,
    )
    if matched is None:
        raise Phase3Error(
            f"Jenkins console has no checkout matching registered source SHA {expected}; found {matches!r}"
        )
    targets = sorted(set(package_targets))
    if len(targets) > 1:
        raise Phase3Error(f"Jenkins console has conflicting OTA target versions: {targets!r}")
    evidence = {"requested": expected, "verified": matched}
    if targets:
        evidence["package_target_version"] = targets[0]
    return evidence


def fetch_artifacts(state: dict[str, Any]) -> list[dict[str, Any]]:
    build_url = state["build"]["url"].rstrip("/")
    tree = "artifacts[relativePath,fileName]"
    request = Request(
        f"{build_url}/api/json?{urlencode({'tree': tree})}",
        headers={"Accept": "application/json", "User-Agent": "openharmony-ci-orchestrator/phase3", **credentials()},
    )
    payload = request_json(build_http_opener(), request, 30)
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        raise Phase3Error("Jenkins build response has no artifact list")
    return [item for item in artifacts if isinstance(item, dict)]


def artifact_url(state: dict[str, Any], relative_path: str) -> str:
    if relative_path.startswith("/") or ".." in Path(relative_path).parts:
        raise Phase3Error(f"Unsafe artifact path in trusted profile: {relative_path!r}")
    artifacts = fetch_artifacts(state)
    matches = [item for item in artifacts if item.get("relativePath") == relative_path]
    if len(matches) != 1:
        raise Phase3Error(
            f"Expected exactly one Jenkins artifact {relative_path!r}, found {len(matches)}"
        )
    return f"{state['build']['url'].rstrip('/')}/artifact/{quote(relative_path, safe='/')}"


def download_artifact(state: dict[str, Any], relative_path: str, destination: Path) -> Path:
    url = artifact_url(state, relative_path)
    if destination.is_file() and destination.stat().st_size and zipfile.is_zipfile(destination):
        return destination
    request = Request(url, headers={"User-Agent": "openharmony-ci-orchestrator/phase3", **credentials()})
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with build_http_opener().open(request, timeout=120) as response:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".artifact-", delete=False) as temp:
            temp_path = Path(temp.name)
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                temp.write(chunk)
    os.chmod(temp_path, 0o600)
    os.replace(temp_path, destination)
    if not destination.is_file() or not destination.stat().st_size:
        raise Phase3Error("Downloaded Jenkins OTA artifact is empty")
    return destination


def local_preflight(package: Path) -> str:
    # DSH 定制：OTA 脚本已随插件 vendored，按相对路径从本文件解析，
    # 不再依赖 ~/.codex 目录。本文件位于 <pkg>/skills/openharmony-ci-orchestrator/scripts/，
    # ota 脚本位于 <pkg>/skills/openharmony-ota-upgrade/scripts/。
    script = Path(__file__).resolve().parents[2] / "openharmony-ota-upgrade/scripts/ota_preflight.sh"
    if not script.is_file():
        raise Phase3Error(f"OTA preflight script is unavailable: {script}")
    return run_command([str(script), str(package)], timeout=300)


def collect_updater_evidence(serial: str) -> str:
    return shell(
        serial,
        "cat /data/updater/updater_result 2>/dev/null; "
        "cat /data/updater/log/updater_stage_log 2>/dev/null; "
        "tail -n 120 /data/updater/log/updater_log 2>/dev/null",
        timeout=60,
        check=False,
    )


def perform_ota(device: dict[str, Any], package: Path, metadata: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    serial = device["serial"]
    attempt: dict[str, Any] = {"role": device["role"], "serial": serial, "started_at": utc_now()}
    try:
        before = device_snapshot(device)
        require_device_identity(device, before)
        source_version_property = profile["artifact"].get("source_version_property")
        if not isinstance(source_version_property, str):
            raise Phase3Error("Trusted profile has no source-version property")
        if not source_version_matches(metadata, before, source_version_property):
            raise Phase3Error(
                f"{device['role']} source version is not allowed by package: "
                f"{source_version_property}={before[source_version_property]!r}"
            )
        required_kib = (metadata["size"] + 1023) // 1024 + 128 * 1024
        if available_kib(before["free_kib_line"]) < required_kib:
            raise Phase3Error(f"{device['role']} has insufficient free /data space for OTA")
        remote_path = profile["artifact"]["remote_path"]
        require_target(serial)
        attempt["transfer"] = hdc(serial, "file", "send", str(package), remote_path, timeout=600)
        remote_size = shell(serial, f"wc -c < {remote_path}")
        digits = re.search(r"\d+", remote_size)
        if not digits or int(digits.group()) != metadata["size"]:
            raise Phase3Error(f"{device['role']} OTA transfer size mismatch: {remote_size!r}")
        require_target(serial)
        attempt["write_updater"] = shell(serial, f"write_updater updater {remote_path}", timeout=120)
        require_target(serial)
        attempt["reboot_updater"] = shell(serial, "reboot updater", timeout=30, check=False)

        deadline = time.monotonic() + int(profile["reconnect_timeout_seconds"])
        while time.monotonic() < deadline:
            if serial in listed_targets():
                try:
                    after = device_snapshot(device)
                    if after["boot_completed"].strip() == "true":
                        require_device_identity(device, after)
                        evidence = collect_updater_evidence(serial)
                        if re.search(
                            r"Version Check Fail|verify updater params fail|update not succ",
                            evidence,
                            re.IGNORECASE,
                        ):
                            raise Phase3Error("Updater rejected the OTA package after returning to normal boot")
                        if not target_version_matches(metadata, after):
                            time.sleep(int(profile["poll_interval_seconds"]))
                            continue
                        if not re.search(r"(^|\n)pass(?:\n|$)", evidence, re.IGNORECASE):
                            raise Phase3Error("Updater result is not pass after device returned")
                        attempt.update({"status": "passed", "finished_at": utc_now(), "before": before, "after": after, "updater_evidence": evidence})
                        return attempt
                except Phase3Error:
                    pass
            time.sleep(int(profile["poll_interval_seconds"]))
        raise Phase3Error(f"{device['role']} did not return to completed target boot before timeout")
    except (OSError, subprocess.SubprocessError, Phase3Error) as error:
        attempt.update({"status": "failed", "finished_at": utc_now(), "error": str(error)})
        if serial in listed_targets():
            attempt["updater_evidence"] = collect_updater_evidence(serial)
        return attempt


def run_regressions(profile: dict[str, Any], regression_ids: list[str], device: dict[str, Any]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    smoke = read_profile("a333-ota-smoke-v1")
    requested = ["a333-ota-smoke-v1", *regression_ids]
    for profile_id in dict.fromkeys(requested):
        regression = smoke if profile_id == smoke["id"] else read_profile(profile_id)
        if profile["id"] not in regression.get("boards", []):
            raise Phase3Error(f"Regression profile {profile_id} is not allowed for {profile['id']}")
        if regression.get("kind") != "ota-smoke":
            raise Phase3Error(f"Regression profile {profile_id} has no trusted runner implementation")
        serial = device["serial"]
        snapshot = device_snapshot(device)
        evidence = collect_updater_evidence(serial)
        passed = snapshot["boot_completed"].strip() == "true" and bool(
            re.search(r"(^|\n)pass(?:\n|$)", evidence, re.IGNORECASE)
        )
        results.append({
            "id": profile_id,
            "status": "PASS" if passed else "FAIL",
            "device": serial,
            "evidence": {"boot_completed": snapshot["boot_completed"], "updater_result": captured(evidence)},
        })
    declared = [result for result in results if result["id"] != "a333-ota-smoke-v1"]
    if any(result["status"] == "FAIL" for result in results):
        status = "FAIL"
    elif not declared:
        status = "INCONCLUSIVE"
    else:
        status = "PASS"
    return {"status": status, "results": results}


def claim_run(root: Path, run_id: str, profile_id: str, retry_preflight: bool) -> dict[str, Any]:
    with state_lock(root):
        path = run_path(root, run_id)
        state = load_json(path)
        phase3 = state.get("phase3", {})
        if phase3.get("profile") != profile_id:
            raise Phase3Error(f"Run is not registered for phase-3 profile {profile_id!r}")
        status = phase3.get("status")
        if status != "not_started":
            error = str(phase3.get("error", ""))
            attempts = phase3.get("attempts", [])
            attempt_errors = " ".join(
                str(attempt.get("error", "")) for attempt in attempts if isinstance(attempt, dict)
            )
            retry_reason = f"{error} {attempt_errors}"
            no_device_write = isinstance(attempts, list) and all(
                isinstance(attempt, dict)
                and not any(key in attempt for key in ("transfer", "write_updater", "reboot_updater"))
                for attempt in attempts
            )
            if not (
                retry_preflight
                and status == "failed"
                and no_device_write
                and int(phase3.get("preflight_retries", 0)) < 5
                and (
                    "Jenkins console has no checkout matching" in retry_reason
                    or "OTA package must expose one target softVersion" in retry_reason
                    or "device identity mismatch" in retry_reason
                )
            ):
                raise Phase3Error(f"Phase 3 has already been claimed with status {status!r}")
            phase3["preflight_retries"] = int(phase3.get("preflight_retries", 0)) + 1
            if attempts:
                phase3.setdefault("preflight_failures", []).extend(attempts)
                phase3["attempts"] = []
            phase3.pop("error", None)
        phase3.update({"status": "running", "started_at": utc_now()})
        add_event(state, "phase3_started", f"Claimed trusted profile {profile_id}")
        write_json(path, state)
        return state


def finish_run(root: Path, run_id: str, phase3_update: dict[str, Any]) -> dict[str, Any]:
    with state_lock(root):
        path = run_path(root, run_id)
        state = load_json(path)
        state.setdefault("phase3", {}).update(phase3_update)
        add_event(
            state,
            "phase3_finished",
            f"Phase 3 finished with {state['phase3'].get('status', 'unknown')}",
        )
        write_json(path, state)
        return state


def mr_note_body(state: dict[str, Any]) -> str:
    phase3 = state.get("phase3", {})
    build = state.get("build", {})
    trigger = state.get("trigger", {})
    lines = [
        f"<!-- openharmony-ci-phase3:{state['run_id']} -->",
        "### OpenHarmony CI Phase 3",
        f"- Result: `{phase3.get('status', 'unknown')}`",
        f"- Jenkins: [{trigger.get('job')} #{build.get('number')}]({build.get('url')})",
        f"- Branch: `{trigger.get('source_branch')}`",
        f"- Source SHA: `{phase3.get('source_sha', {}).get('verified', trigger.get('source_sha'))}`",
        f"- Profile: `{phase3.get('profile')}`",
    ]
    package = phase3.get("package", {})
    if package:
        lines.append(f"- OTA: `{package.get('artifact')}`, SHA-256 `{package.get('sha256')}`")
    for attempt in phase3.get("attempts", []):
        lines.append(f"- {attempt.get('role')} `{attempt.get('serial')}`: `{attempt.get('status')}`")
        evidence = str(attempt.get("updater_evidence", ""))
        if "Version Check Fail" in evidence:
            lines.append("  - Updater rejected the package at version-list precheck (`Version Check Fail`).")
        elif attempt.get("error"):
            lines.append(f"  - Failure: `{attempt.get('error')}`")
    regression = phase3.get("regression")
    if isinstance(regression, dict):
        lines.append(f"- Regression: `{regression.get('status')}`")
        for result in regression.get("results", []):
            lines.append(f"  - `{result.get('id')}`: `{result.get('status')}`")
    return "\n".join(lines)


def write_mr_note(state: dict[str, Any]) -> dict[str, str]:
    mr_iid = state.get("trigger", {}).get("mr_iid")
    host = configured_value("GITLAB_HOST")
    project = configured_value("GITLAB_PROJECT")
    if not mr_iid or not host or not project:
        return {"status": "not_configured"}
    if not str(mr_iid).isdigit():
        return {"status": "skipped", "detail": "MR iid is invalid"}
    marker = f"<!-- openharmony-ci-phase3:{state['run_id']} -->"
    endpoint = f"projects/{quote(project, safe='')}/merge_requests/{mr_iid}/notes"
    listed = run_command(
        ["glab", "api", "--hostname", host, f"{endpoint}?per_page=100"],
        timeout=60,
        max_capture=2_000_000,
    )
    try:
        notes = json.loads(listed)
    except json.JSONDecodeError as error:
        raise Phase3Error(f"GitLab notes response is invalid JSON: {error}") from error
    body = mr_note_body(state)
    matching = next((note for note in notes if marker in str(note.get("body", ""))), None)
    if matching is None:
        run_command(["glab", "api", "--hostname", host, "-X", "POST", endpoint, "-f", f"body={body}"], timeout=60)
        return {"status": "created"}
    note_id = matching.get("id")
    if not isinstance(note_id, int):
        raise Phase3Error("Existing phase-3 GitLab note has no numeric id")
    run_command(["glab", "api", "--hostname", host, "-X", "PUT", f"{endpoint}/{note_id}", "-f", f"body={body}"], timeout=60)
    return {"status": "updated"}


def record_mr_note(root: Path, run_id: str, state: dict[str, Any]) -> dict[str, Any]:
    try:
        note = write_mr_note(state)
    except (OSError, Phase3Error) as error:
        note = {"status": "failed", "detail": str(error)}
    return finish_run(root, run_id, {"mr_note": note})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", default=os.path.expanduser("~/.local/state/openharmony-ci-orchestrator"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--dry-run", action="store_true", help="Download and validate the OTA package without HDC or GitLab writes")
    parser.add_argument(
        "--retry-preflight", action="store_true",
        help="Retry only a source-SHA preflight failure that made no device attempt.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = state_directory(args.state_dir)
    profile = read_profile(args.profile)
    state = claim_run(root, args.run_id, args.profile, args.retry_preflight)
    try:
        assert_profile_matches(profile, state)
        source_sha = verify_source_sha(state)
        artifact = profile["artifact"]["relative_path"]
        package = download_artifact(state, artifact, root / "artifacts" / args.run_id / "update.zip")
        preflight = local_preflight(package)
        metadata = package_metadata(package, source_sha.get("package_target_version"))
        sha_line = next((line for line in preflight.splitlines() if line.startswith("[SHA256]")), None)
        if sha_line is None:
            raise Phase3Error("OTA preflight did not produce a SHA-256")
        package_state = {
            "artifact": artifact,
            "path": str(package),
            "size": metadata["size"],
            "sha256": sha_line.split()[-1],
            "source_versions": metadata["source_versions"],
            "target_version": metadata["target_version"],
            "target_version_evidence": metadata["target_version_evidence"],
            "preflight": preflight,
        }
        if args.dry_run:
            result = finish_run(root, args.run_id, {"status": "dry_run", "finished_at": utc_now(), "source_sha": source_sha, "package": package_state})
            print(json.dumps({"phase": 3, "run_id": args.run_id, "status": "dry_run", "state": str(run_path(root, args.run_id))}, ensure_ascii=False))
            return 0
        attempts = []
        successful_device = None
        for device in profile["devices"]:
            attempt = perform_ota(device, package, metadata, profile)
            attempts.append(attempt)
            if attempt["status"] == "passed":
                successful_device = device
                break
        if successful_device is None:
            errors = "; ".join(str(attempt.get("error", "unknown OTA failure")) for attempt in attempts)
            result = finish_run(root, args.run_id, {"status": "failed", "finished_at": utc_now(), "error": errors, "source_sha": source_sha, "package": package_state, "attempts": attempts})
        else:
            regression = run_regressions(profile, state["phase3"].get("regression_profiles", []), successful_device)
            status = "passed" if regression["status"] == "PASS" else regression["status"].lower()
            result = finish_run(root, args.run_id, {"status": status, "finished_at": utc_now(), "source_sha": source_sha, "package": package_state, "attempts": attempts, "regression": regression})
        result = record_mr_note(root, args.run_id, result)
        print(json.dumps({"phase": 3, "run_id": args.run_id, "status": result["phase3"]["status"], "state": str(run_path(root, args.run_id))}, ensure_ascii=False))
        return 0 if result["phase3"]["status"] in ("passed", "inconclusive") else 2
    except (JenkinsError, OSError, Phase3Error, StopIteration) as error:
        result = finish_run(root, args.run_id, {"status": "failed", "finished_at": utc_now(), "error": str(error)})
        record_mr_note(root, args.run_id, result)
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
