#!/usr/bin/env python3
"""Run the trusted phase-3 A333 OTA and regression workflow for one CI run."""

from __future__ import annotations

import argparse
import codecs
import json
import os
import re
import shutil
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

from ci_orchestrator import (add_event, load_json, run_path, state_directory,
                             state_lock, write_json)
from jenkins_api import (JenkinsError, build_http_opener, credentials, request_json)
from orchestrator_config import CONFIG_ENV
from orchestrator_config import configured_value as _configured_value


SKILL_DIR = Path(__file__).resolve().parents[1]
PROFILES_DIR = SKILL_DIR / "profiles"
PROFILE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_CAPTURE = 8_000
# profile 未声明 artifact.relative_path 时使用的兜底模式（门禁归档的 OTA 目录）。
DEFAULT_ARTIFACT_PATTERN = "openharmony_V6.1/out/ota/*.zip"
HDC_LOCAL_WARNING = re.compile(r"\[W\]\[[^\]]+\] FreeChannelContinue handle->data is nullptr")


class Phase3Error(RuntimeError):
    """A terminal safety, package, device, or regression failure."""


class DeviceOfflineError(Phase3Error):
    """目标设备当前不在 hdc 列表里。

    与"真失败"分开统计：设备池里常有多台离线，若把"不在线"也算作失败尝试，会迅速耗尽
    `preflight_retries`（实测一次真实 run 因此用掉 4/5 次预算）。离线只表示"本轮没有可用设备"，
    不消耗重试预算，等下一个 tick 再看。
    """


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def captured(value: str) -> str:
    value = value.strip()
    return value[-MAX_CAPTURE:]


def configured_value(name: str) -> str | None:
    """One setting from the process environment or the private orchestrator config."""
    return _configured_value(name, CONFIG_ENV)


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


def resolved_artifact_patterns(profile: dict[str, Any]) -> list[str]:
    """Ordered OTA artifact candidates this profile accepts.

    `artifact.preferred` 给有序候选（常用：`update.zip` → `updater_full.zip`），
    `artifact.relative_path` 作为兜底（通配时要求目录内恰好一个 .zip）。
    """
    artifact = profile.get("artifact") or {}
    candidates: list[str] = []
    preferred = artifact.get("preferred")
    if isinstance(preferred, list):
        candidates.extend(item for item in preferred if isinstance(item, str) and item)
    fallback = artifact.get("relative_path") or DEFAULT_ARTIFACT_PATTERN
    if not isinstance(fallback, str) or not fallback:
        raise Phase3Error("Trusted profile has an invalid artifact.relative_path")
    candidates.append(fallback)
    return list(dict.fromkeys(candidates))


def run_command(
    command: list[str], timeout: int = 60, check: bool = True, max_capture: int = MAX_CAPTURE
) -> str:
    result = subprocess.run(_bounded(command, timeout), text=True, capture_output=True,
                            timeout=timeout + 15, check=False)
    output = result.stdout.strip()
    if len(output) > max_capture:
        output = output[-max_capture:]
    if check and result.returncode:
        detail = result.stdout + ("\n" if result.stdout and result.stderr else "") + result.stderr
        if len(detail) > max_capture:
            detail = detail[-max_capture:]
        raise Phase3Error(f"Command failed ({result.returncode}): {' '.join(command[:3])}: {detail}")
    return output


def _bounded(command: list[str], timeout: int) -> list[str]:
    """Wrap a command in an external hard-kill timer when `timeout(1)` is available.

    实测（2026-10-15，真实 OTA 后的回归阶段）：经由 WSL interop 调用的 Windows `hdc.exe`
    偶发不理会 Python 侧超时——`subprocess.run(timeout=...)` 已杀掉直接子进程，但 interop 的
    `/init` 仍持有 stdout 管道，父进程在 `communicate()` 上**无限阻塞**，整条 phase-3 单元
    就此卡住（本次卡了 13 分钟仍未返回，最终靠人工重启单元继续）。`timeout -s KILL N` 由内核
    计时，对这类进程更可靠；因此所有外部命令都先包一层，并给 Python 侧再留 15s 余量。
    """
    guard = shutil.which("timeout")
    if not guard or not command:
        return command
    return [guard, "-s", "KILL", str(max(1, int(timeout))), *command]


def hdc(serial: str, *arguments: str, timeout: int = 60, check: bool = True) -> str:
    output = run_command(["hdc", "-t", serial, *arguments], timeout=timeout, check=check)
    return HDC_LOCAL_WARNING.sub("", output).strip()


def wsl_host_path(path: str | Path) -> str:
    """Translate a local file path for a Windows hdc client, otherwise return it unchanged.

    双宿主现场（本机）：`~/.local/bin/hdc` 是 `hdc.exe` 的 WSL 包装器（Windows 侧持有 USB），
    它把本地路径按 Windows 语义处理，于是 `hdc file send /home/cx/x.zip` 会被拼成
    `\\\\wsl.localhost\\Ubuntu-22.04\\home\\cx\\/home/cx/x.zip` 并以
    `[Fail]Error opening file: no such file or directory` 失败（实测一次真实 OTA 在传输阶段
    整机失败）。原生 Linux hdc（`hdc-linux`）则必须收到原样路径。

    判定不靠猜包装器：能运行说明 `wslpath` 存在（WSL 必备工具），再把结果与转换后的形式比对——
    两者不同即说明当前 hdc 是 Windows 客户端。
    """
    text = str(path)
    if os.name != "posix":
        return text
    try:
        converted = subprocess.run(["wslpath", "-w", text], text=True,
                                   capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return text
    if converted.returncode != 0:
        return text
    windows_path = converted.stdout.strip()
    return windows_path or text


def hdc_file_send(serial: str, local: Path, remote: str, timeout: int = 600) -> str:
    return hdc(serial, "file", "send", wsl_host_path(local), remote, timeout=timeout)


def hdc_file_recv(serial: str, remote: str, local: Path, timeout: int = 120) -> str:
    return hdc(serial, "file", "recv", remote, wsl_host_path(local), timeout=timeout,
               check=False)


def listed_targets() -> set[str]:
    output = run_command(["hdc", "list", "targets"], check=True)
    return {line.strip().split()[0] for line in output.splitlines() if line.strip()}


def require_target(serial: str) -> None:
    if serial not in listed_targets():
        raise DeviceOfflineError(f"Configured target is not online in hdc list targets: {serial}")


def shell(serial: str, command: str, timeout: int = 60, check: bool = True) -> str:
    return hdc(serial, "shell", command, timeout=timeout, check=check)


def device_snapshot(device: dict[str, Any]) -> dict[str, str]:
    serial = device["serial"]
    require_target(serial)
    return {
        "product_name": shell(serial, "param get const.product.name"),
        "product_model": shell(serial, "param get const.product.model"),
        "product_brand": shell(serial, "param get const.product.brand"),
        "product_manufacturer": shell(serial, "param get const.product.manufacturer"),
        "software_version": shell(serial, "param get const.product.software.version"),
        "ohos_fullname": shell(serial, "param get const.ohos.fullname"),
        "firmware_commitid": shell(serial, "param get const.product.firmware.commitid"),
        "device_tree_model": shell(serial, "cat /proc/device-tree/model"),
        "boot_completed": shell(serial, "param get bootevent.boot.completed"),
        "free_kib_line": shell(serial, "df -k /data | tail -n 1"),
        "write_updater": shell(serial, "command -v write_updater"),
    }


def device_identity_mismatches(device: dict[str, Any], snapshot: dict[str, str]) -> list[str]:
    """记录设备身份与 profile 期望值的差异（**不作为硬门禁**）。

    用户口径（2026-10-15）：identity 不做硬限制，"只要能升级、且升级正确即可"。同一个门禁
    包可能内置通用产品身份（实测：OTA 后 `const.product.name` 从 `DHong-A333-Development-Board`
    变为通用 `OpenHarmony 3.2`）；若把产品串当硬门禁，就会把**已经成功且正确**的升级判成失败。
    差异只进证据，供人工复核。
    """
    mismatches = []
    for key in ("product_name", "product_model", "device_tree_model",
                "product_brand", "product_manufacturer"):
        expected = str(device.get(key, "")).strip()
        if not expected:
            continue
        observed = snapshot.get(key, "").strip("\x00\n ")
        if observed and observed != expected:
            mismatches.append(f"{key}: expected {expected!r}, got {observed!r}")
    return mismatches


def require_device_ready(device: dict[str, Any], snapshot: dict[str, str]) -> None:
    """硬门禁只剩"能不能升级"：设备有 write_updater 且处于正常启动完成态。

    "升级正确"由结果证据保证，而不是由镜像身份字符串保证：
    - 升级前：包内 `version_list` 决定本机能否升级（`source_version_matches`）；
    - 升级后：固件 `const.product.firmware.commitid` 必须等于本次构建的源 SHA
      （`target_verified_by_commitid`），叠加 `updater_result=pass` 与 boot completed。
    """
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


def target_verified_by_commitid(source_sha: str, snapshot: dict[str, str]) -> bool:
    """OTA 结果的最硬证据：设备上跑的固件 commitid 等于本次构建的源 SHA。

    包内 `[VERSION] package softVersion` 与设备的 `software_version`/`ohos_fullname` 不一定同源
    （实测：包为 `OpenHarmony 6.1.0.32`，设备为 `OpenHarmony-6.1.0.35` / `1.3.0`），只靠版本串比对会把
    成功的 OTA 判成失败。构建会把源 SHA 写入固件 (`const.product.firmware.commitid`)，因此优先用它判定。
    """
    if isinstance(source_sha, dict):
        source_sha = source_sha.get("verified") or source_sha.get("requested") or ""
    expected = str(source_sha or "").strip().lower()
    actual = snapshot.get("firmware_commitid", "").strip().lower()
    if not expected or not actual:
        return False
    return actual.startswith(expected[:12]) or expected.startswith(actual[:12])


def assert_profile_matches(profile: dict[str, Any], state: dict[str, Any]) -> None:
    """核对本次 run 的平台门禁契约。

    门禁作业（`OpenHarmony-V6.1-MR-<chip>`）只声明 MR 身份参数，产品选择（设备/产品名/型号/
    品牌/厂商）由服务端固定，不再出现在构建参数里。因此：
    - `required_parameters` 逐项精确匹配（缺省时用 `parameters` 的键，保持既有 profile 语义）；
    - `parameters` 里其余项作为**期望证据**记录，不阻断——真正的产品身份门禁由设备侧
      `require_device_identity()` 与包预检承担。
    """
    trigger = state.get("trigger", {})
    jenkins = profile.get("jenkins", {})
    job = trigger.get("job")
    if job != jenkins.get("job"):
        raise Phase3Error(
            f"Profile requires Jenkins job {jenkins.get('job')!r}, got {job!r}"
        )
    parameters = trigger.get("parameters")
    if not isinstance(parameters, dict):
        raise Phase3Error("Run state has no verified Jenkins parameters")
    expected = jenkins.get("parameters", {})
    evidence = jenkins.get("evidence_parameters", {})
    required_names = jenkins.get("required_parameters")
    if not isinstance(required_names, list) or not required_names:
        required_names = list(expected)
    for name in required_names:
        actual = parameters.get(name)
        accepted = expected.get(name)
        if accepted is None:
            accepted = evidence.get(name)
        if actual in (None, ""):
            raise Phase3Error(f"Profile requires Jenkins parameter {name} to be present")
        if accepted and actual not in accepted:
            raise Phase3Error(
                f"Profile rejects Jenkins parameter {name}: {actual!r} not in {accepted!r}"
            )
    # evidence_parameters 是"期望证据"：新门禁作业由服务端固定产品参数，构建 API 里不再
    # 一定出现它们；出现时不符要拦（说明作业配方被改过），缺失时交给设备侧身份校验兜底。
    for name, accepted in evidence.items():
        actual = parameters.get(name)
        if actual and accepted and actual not in accepted:
            raise Phase3Error(
                f"Profile rejects Jenkins parameter {name}: {actual!r} not in {accepted!r}"
            )
    # 门禁身份必须与注册值一致：MR 源 SHA 是后续 console/固件证据的锚点。
    source_sha = str(trigger.get("source_sha") or "")
    declared_sha = str(parameters.get("SOURCE_SHA") or "")
    if declared_sha and declared_sha != source_sha:
        raise Phase3Error(
            f"Gate build was made from {declared_sha!r}, run registered {source_sha!r}"
        )
    declared_mr = str(parameters.get("MR_IID") or "")
    if declared_mr and declared_mr != str(trigger.get("mr_iid") or ""):
        raise Phase3Error(
            f"Gate build belongs to MR {declared_mr!r}, run registered {trigger.get('mr_iid')!r}"
        )
    build = state.get("build", {})
    if state.get("status") != "build_succeeded" or build.get("result") != "SUCCESS":
        raise Phase3Error("Phase 3 requires a successful platform gate build")
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


def pattern_matches(names: list[str], pattern: str) -> list[str]:
    if pattern.startswith("/") or ".." in Path(pattern).parts:
        raise Phase3Error(f"Unsafe artifact path in trusted profile: {pattern!r}")
    if "*" in pattern or "?" in pattern:
        compiled = re.compile(
            "^" + re.escape(pattern).replace(r"\*", "[^/]*").replace(r"\?", "[^/]") + "$"
        )
        return [name for name in names if compiled.match(name)]
    return [name for name in names if name == pattern]


def select_artifact_name(names: list[str], candidates: list[str]) -> str:
    """Pick exactly one archived OTA package from an ordered candidate list.

    门禁的芯片作业会同时归档 `update.zip` 与 `updater_full.zip`（实测 a333 构建 #31），因此
    "一个通配匹配全部 .zip" 的老规则必然失败。这里按 profile 声明的**优先顺序**逐个尝试：
    - 该候选命中恰好一个 → 采用；
    - 命中多个 → 立刻报错（不猜），并把候选清单打出来；
    - 命中 0 个 → 试下一个候选；
    - 全部候选都 0 命中 → 报错并列出该构建实际归档的产物。
    """
    attempts: list[str] = []
    for candidate in candidates:
        matches = pattern_matches(names, candidate)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise Phase3Error(
                f"Artifact pattern {candidate!r} matched {len(matches)} archived files "
                f"({matches!r}); it must resolve to exactly one"
            )
        attempts.append(candidate)
    preview = ", ".join(names[:10]) or "none"
    raise Phase3Error(
        f"No archived OTA package matched any of {attempts!r}; build archives: {preview}"
    )


def artifact_url(state: dict[str, Any], relative_path: str) -> str:
    """Resolve one archived OTA package to a download URL.

    `relative_path` 可以是精确路径，也可以是受限于同一目录的通配模式；多个候选时按顺序尝试
    （见 `select_artifact_name`）。目录白名单仍由 profile 决定，候选之外的文件一律不取。
    """
    names = sorted(
        item["relativePath"] for item in fetch_artifacts(state)
        if isinstance(item.get("relativePath"), str)
    )
    resolved = select_artifact_name(names, [relative_path])
    return f"{state['build']['url'].rstrip('/')}/artifact/{quote(resolved, safe='/')}"


def download_artifact(state: dict[str, Any], candidates: list[str],
                      destination: Path) -> tuple[str, Path]:
    """Download the first candidate that resolves to exactly one archived OTA package.

    Returns (resolved relative path, local file). 解析结果由调用方写进 phase3 状态
    （本函数里的 state 在 finish_run 重新读盘时会被覆盖，不能在这里落地）。
    """
    names = sorted(
        item["relativePath"] for item in fetch_artifacts(state)
        if isinstance(item.get("relativePath"), str)
    )
    resolved = select_artifact_name(names, candidates)
    url = f"{state['build']['url'].rstrip('/')}/artifact/{quote(resolved, safe='/')}"
    if destination.is_file() and destination.stat().st_size and zipfile.is_zipfile(destination):
        return resolved, destination
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
    return resolved, destination


def local_preflight(package: Path) -> str:
    # DSH 定制：OTA 脚本已随插件 vendored，按相对路径从本文件解析，
    # 不再依赖 ~/.codex 目录。本文件位于 <pkg>/skills/openharmony-ci-orchestrator/scripts/，
    # ota 脚本位于 <pkg>/skills/openharmony-ota-upgrade/scripts/。
    script = Path(__file__).resolve().parents[2] / "openharmony-ota-upgrade/scripts/ota_preflight.sh"
    if not script.is_file():
        raise Phase3Error(f"OTA preflight script is unavailable: {script}")
    return run_command([str(script), str(package)], timeout=300)


def updater_reported_pass(evidence: str) -> bool:
    """判断 updater 是否报告 pass。

    `/data/updater/updater_result` 的真实格式是 `<包路径>|pass||install_time=141.03|`，
    `pass` 是被竖线包围的字段而不是独立一行；只认独立行的旧正则会把**成功的 OTA 判成失败**
    并空转到 reconnect 超时（实测两次）。
    """
    if re.search(r"\|pass\|", evidence, re.IGNORECASE):
        return True
    return bool(re.search(r"(^|\n)pass(?:\n|$)", evidence, re.IGNORECASE))


def collect_updater_evidence(serial: str) -> str:
    return shell(
        serial,
        "cat /data/updater/updater_result 2>/dev/null; "
        "cat /data/updater/log/updater_stage_log 2>/dev/null; "
        "tail -n 120 /data/updater/log/updater_log 2>/dev/null",
        timeout=60,
        check=False,
    )


def perform_ota(device: dict[str, Any], package: Path, metadata: dict[str, Any], profile: dict[str, Any],
                source_sha: str) -> dict[str, Any]:
    serial = device["serial"]
    attempt: dict[str, Any] = {"role": device["role"], "serial": serial, "started_at": utc_now()}
    try:
        before = device_snapshot(device)
        require_device_ready(device, before)
        identity_notes = device_identity_mismatches(device, before)
        if identity_notes:
            attempt["identity_notes"] = identity_notes
        if target_verified_by_commitid(source_sha, before):
            # 设备已经跑在本次构建的固件上（重复执行/重跑收尾）：不要再刷一遍，
            # 直接把它当作已升级的候选交给后续回归与证据链（幂等，避免无意义的重启）。
            attempt.update({
                "status": "already_on_target", "finished_at": utc_now(),
                "before": before, "after": before,
                "updater_evidence": collect_updater_evidence(serial),
                "skipped_write": "device already runs the registered source SHA",
            })
            return attempt
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
        attempt["transfer"] = hdc_file_send(serial, package, remote_path, timeout=600)
        remote_size = shell(serial, f"wc -c < {remote_path}")
        digits = re.search(r"\d+", remote_size)
        if not digits or int(digits.group()) != metadata["size"]:
            raise Phase3Error(f"{device['role']} OTA transfer size mismatch: {remote_size!r}")
        require_target(serial)
        attempt["write_updater"] = shell(serial, f"write_updater updater {remote_path}", timeout=120)
        require_target(serial)
        attempt["reboot_updater"] = shell(serial, "reboot updater", timeout=30, check=False)

        deadline = time.monotonic() + int(profile["reconnect_timeout_seconds"])
        last_blocker = "device never reappeared in hdc list targets"
        while time.monotonic() < deadline:
            if serial in listed_targets():
                try:
                    after = device_snapshot(device)
                    if after["boot_completed"].strip() == "true":
                        require_device_ready(device, after)
                        evidence = collect_updater_evidence(serial)
                        if re.search(
                            r"Version Check Fail|verify updater params fail|update not succ",
                            evidence,
                            re.IGNORECASE,
                        ):
                            raise Phase3Error("Updater rejected the OTA package after returning to normal boot")
                        # 目标判定：版本串匹配，或（更硬）设备固件 commitid 等于本次构建的源 SHA。
                        if not (
                            target_version_matches(metadata, after)
                            or target_verified_by_commitid(source_sha, after)
                        ):
                            last_blocker = (
                                "target version not matched: package softVersion="
                                f"{metadata['target_version']!r}, device software_version="
                                f"{after['software_version']!r}, ohos_fullname={after['ohos_fullname']!r}, "
                                f"firmware_commitid={after.get('firmware_commitid', '')!r}"
                            )
                            time.sleep(int(profile["poll_interval_seconds"]))
                            continue
                        if not updater_reported_pass(evidence):
                            raise Phase3Error("Updater result is not pass after device returned")
                        post_notes = device_identity_mismatches(device, after)
                        if post_notes:
                            attempt["identity_notes_after"] = post_notes
                        attempt.update({"status": "passed", "finished_at": utc_now(), "before": before, "after": after, "updater_evidence": evidence})
                        return attempt
                    last_blocker = f"device online but boot_completed={after['boot_completed']!r}"
                except Phase3Error as error:
                    last_blocker = str(error)
            time.sleep(int(profile["poll_interval_seconds"]))
        raise Phase3Error(
            f"{device['role']} did not return to completed target boot before timeout "
            f"({last_blocker})"
        )
    except DeviceOfflineError as error:
        # 设备不在线：不计入失败尝试（见 DeviceOfflineError 的说明）。
        attempt.update({"status": "offline", "finished_at": utc_now(), "error": str(error)})
        return attempt
    except (OSError, subprocess.SubprocessError, Phase3Error) as error:
        attempt.update({"status": "failed", "finished_at": utc_now(), "error": str(error)})
        if serial in listed_targets():
            attempt["updater_evidence"] = collect_updater_evidence(serial)
        return attempt


def regression_ui_landscape_control_center(
    board_profile: dict[str, Any], regression: dict[str, Any], serial: str,
) -> dict[str, Any]:
    """固定实现的功能回归:横屏(orientation=2 重启)下拉右侧控制中心,断言标题与亮度滑块可见。
    由受信任 kind 'ota-ui-landscape-control-center' 触发;不读取 MR/参数文本,仅按本函数执行。"""
    evidence: dict[str, Any] = {"actions": []}
    try:
        # 1) 切换到横屏并重启等待回连
        hdc(serial, "shell", "param set persist.sys.orientation 2", check=False)
        hdc(serial, "shell", "reboot", check=False)
        deadline = time.time() + int(board_profile.get("reconnect_timeout_seconds", 420))
        booted = False
        while time.time() < deadline:
            time.sleep(int(board_profile.get("poll_interval_seconds", 5)))
            if serial in listed_targets():
                try:
                    if hdc(serial, "shell", "param get persist.sys.orientation", check=False).strip().endswith("2"):
                        if hdc(serial, "shell", "param get bootevent.boot.completed", check=False).strip().endswith("true"):
                            booted = True
                            break
                except Phase3Error:
                    continue
        if not booted:
            return {"id": regression["id"], "status": "FAIL", "device": serial,
                    "evidence": {"error": "device did not return with orientation=2 after reboot"}}
        # 2) 右上角下拉打开控制中心
        hdc(serial, "shell", "uitest uiInput swipe 1150 2 1150 600 2000", check=False)
        time.sleep(2)
        hdc(serial, "shell", "uitest dumpLayout -p /data/local/tmp/cc_layout.json", check=False)
        with tempfile.TemporaryDirectory() as td:
            local = Path(td) / "cc_layout.json"
            hdc_file_recv(serial, "/data/local/tmp/cc_layout.json", local, timeout=60)
            layout = local.read_text(encoding="utf-8", errors="ignore") if local.exists() else ""
        texts = re.findall(r'"text"\s*:\s*"([^"]*)"', layout)
        texts += re.findall(r'"content"\s*:\s*"([^"]*)"', layout)
        joined = "\n".join(texts)
        has_title = "控制中心" in joined
        has_brightness = "亮度" in joined or "WLAN" in joined
        evidence.update({"has_title": has_title, "has_brightness": has_brightness,
                         "layout_texts": texts[:30]})
        status = "PASS" if has_title and has_brightness else "FAIL"
        return {"id": regression["id"], "status": status, "device": serial, "evidence": evidence}
    except Phase3Error as error:
        return {"id": regression["id"], "status": "FAIL", "device": serial,
                "evidence": {"error": str(error), **evidence}}


def run_regressions(profile: dict[str, Any], regression_ids: list[str], device: dict[str, Any]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    smoke = read_profile("a333-ota-smoke-v1")
    requested = ["a333-ota-smoke-v1", *regression_ids]
    for profile_id in dict.fromkeys(requested):
        regression = smoke if profile_id == smoke["id"] else read_profile(profile_id)
        if profile["id"] not in regression.get("boards", []):
            raise Phase3Error(f"Regression profile {profile_id} is not allowed for {profile['id']}")
        serial = device["serial"]
        if regression.get("kind") == "ota-ui-landscape-control-center":
            results.append(regression_ui_landscape_control_center(profile, regression, serial))
            continue
        if regression.get("kind") != "ota-smoke":
            raise Phase3Error(f"Regression profile {profile_id} has no trusted runner implementation")
        serial = device["serial"]
        snapshot = device_snapshot(device)
        evidence = collect_updater_evidence(serial)
        # 复用与 boot 等待相同的判定：updater_result 的 pass 是被竖线包围的字段（|pass|），
        # 只认独立行的旧正则会把这台已成功的 OTA 判成 FAIL（实测整轮 status=fail）。
        passed = snapshot["boot_completed"].strip() == "true" and updater_reported_pass(evidence)
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


def _process_alive(pid: Any) -> bool:
    """判断 claim 时记录的进程是否还活着（用于回收被中断的 running claim）。"""
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    try:
        os.kill(value, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def claim_run(root: Path, run_id: str, profile_id: str, retry_preflight: bool) -> dict[str, Any]:
    with state_lock(root):
        path = run_path(root, run_id)
        state = load_json(path)
        phase3 = state.get("phase3", {})
        if phase3.get("profile") != profile_id:
            raise Phase3Error(f"Run is not registered for phase-3 profile {profile_id!r}")
        status = phase3.get("status")
        # 被中断的 running claim（例如执行器被超时杀掉）：记录进程已不存在时允许直接回收，
        # 否则 run 永久卡在 running，既不能重试也不会再被 reconcile 处理。
        stale_claim = status == "running" and not _process_alive(phase3.get("pid"))
        if stale_claim:
            add_event(
                state,
                "phase3_stale_claim",
                f"Reclaiming stale running claim (recorded pid {phase3.get('pid')!r} is gone)",
            )
        if status != "not_started" and not stale_claim:
            error = str(phase3.get("error", ""))
            attempts = phase3.get("attempts", [])
            attempt_errors = " ".join(
                str(attempt.get("error", "")) for attempt in attempts if isinstance(attempt, dict)
            )
            retry_reason = f"{error} {attempt_errors}"
            # "未写设备"的判据是**有没有执行过 write_updater / reboot updater**。
            # `transfer` 键只说明尝试过投递：传输失败（大小不符/客户端读不到本地文件）时
            # `/data/updater` 里并没有可用包，设备未被改动，重试是安全的——旧判据把失败的
            # transfer 也算作"写过设备"，会让"修好传输路径后重跑"永远不可能（实测 2026-10-15）。
            no_device_write = isinstance(attempts, list) and all(
                isinstance(attempt, dict)
                and not any(key in attempt for key in ("write_updater", "reboot_updater"))
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
                    # 历史 run 里的身份门禁失败文案（该门禁已按用户口径取消，保留兼容）
                    or "device identity mismatch" in retry_reason
                    # 目标设备不在线：profile 的设备池按"当前在线且可升级的 DUT"维护，
                    # 换机/重新插拔后串号可能变化，属可重试的纯预检失败（未写设备）。
                    or "is not online in hdc list targets" in retry_reason
                    # 源版本不在包允许域内（包内 version_list 来自板级 VERSION.mbn）：
                    # 换一台该版本域内、当前在线的 DUT 后可重试，同样未写设备。
                    or "source version is not allowed by package" in retry_reason
                    # 执行环境缺/半配 Jenkins 凭据（只有一个变量会被 credentials() 判为异或错误）：
                    # 属纯环境问题且未写设备，修正 EnvironmentFile 后可重试。
                    or "JENKINS_USER and JENKINS_API_TOKEN must be set together" in retry_reason
                    # 产物候选解析失败（包被换名/门禁归档集合变化）：未写设备，修正候选后可重试。
                    or "No archived OTA package matched" in retry_reason
                    or "Expected exactly one Jenkins artifact" in retry_reason
                    # 传输未落地（远端读不到/大小不符）：传输阶段失败即未执行 write_updater，
                    # 属"未写设备"的可重试预检失败。实测（2026-10-15）：双宿主 Windows hdc 会把
                    # Linux 路径拼成 \\wsl.localhost\... 导致整机传输失败，修好路径必须能重跑。
                    or "OTA transfer size mismatch" in retry_reason
                    # 读取 Jenkins console 时的瞬时网络失败（Connection reset by peer 等）：
                    # 属"未写设备"的取证阶段失败，重试即可（实测 2026-10-15 卡住一次真实 OTA 收尾）。
                    or "Unable to read Jenkins console" in retry_reason
                    or "Connection reset by peer" in retry_reason
                    or "Jenkins connection failed" in retry_reason
                )
            ):
                raise Phase3Error(f"Phase 3 has already been claimed with status {status!r}")
            phase3["preflight_retries"] = int(phase3.get("preflight_retries", 0)) + 1
            if attempts:
                phase3.setdefault("preflight_failures", []).extend(attempts)
                phase3["attempts"] = []
            phase3.pop("error", None)
        phase3.update({"status": "running", "started_at": utc_now(), "pid": os.getpid()})
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
    platform_state = trigger.get("platform") or {}
    lines = [
        f"<!-- openharmony-ci-phase3:{state['run_id']} -->",
        "### OpenHarmony CI Phase 3",
        f"- Result: `{phase3.get('status', 'unknown')}`",
        f"- Platform pipeline: `{platform_state.get('pipeline_id')}` "
        f"({platform_state.get('pipeline_status') or platform_state.get('run_state')})"
        + (f" / gate build [{platform_state.get('gate_build_number')}]"
           f"({platform_state.get('gate_build_url')})"
           if platform_state.get("gate_build_url") else ""),
        f"- Gate build: [{trigger.get('job')} #{build.get('number')}]({build.get('url')})",
        f"- Branch: `{trigger.get('source_branch')}` -> `{platform_state.get('target_branch')}`",
        f"- Source SHA: `{phase3.get('source_sha', {}).get('verified', trigger.get('source_sha'))}`",
        f"- Profile: `{phase3.get('profile')}`",
    ]
    package = phase3.get("package", {})
    if package:
        lines.append(f"- OTA: `{package.get('artifact')}`, SHA-256 `{package.get('sha256')}`")
    for attempt in phase3.get("attempts", []):
        lines.append(f"- {attempt.get('role')} `{attempt.get('serial')}`: `{attempt.get('status')}`")
        if attempt.get("skipped_write"):
            # 已在本构建固件上：没有重复刷机，避免读者误以为又刷了一次。
            lines.append(f"  - Not reflashed: {attempt['skipped_write']}")
        for note in attempt.get("identity_notes") or []:
            lines.append(f"  - Identity note (evidence only): `{note}`")
        for note in attempt.get("identity_notes_after") or []:
            lines.append(f"  - Identity changed after OTA: `{note}`")
        if attempt.get("after"):
            after = attempt["after"]
            lines.append(f"  - Device after: `{str(after.get('software_version', '')).strip()}`"
                         f" / commit `{str(after.get('firmware_commitid', ''))[:12]}`"
                         f" / boot `{str(after.get('boot_completed', '')).strip()}`")
        evidence = str(attempt.get("updater_evidence", ""))
        if "Version Check Fail" in evidence:
            lines.append("  - Updater rejected the package at version-list precheck (`Version Check Fail`).")
        elif attempt.get("error"):
            lines.append(f"  - Failure: `{attempt.get('error')}`")
    for serial in phase3.get("offline_devices") or []:
        lines.append(f"- Offline at run time: `{serial}`")
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
        # 门禁芯片作业会同时归档 update.zip 与 updater_full.zip：按 profile 声明的优先顺序
        # 选第一个恰好命中一个文件的候选（不猜、不做模糊匹配）。
        candidates = resolved_artifact_patterns(profile)
        artifact, package = download_artifact(
            state, candidates, root / "artifacts" / args.run_id / "update.zip")
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
            result = finish_run(root, args.run_id, {
                "status": "dry_run", "finished_at": utc_now(), "source_sha": source_sha,
                "artifact_resolved": artifact, "package": package_state})
            print(json.dumps({"phase": 3, "run_id": args.run_id, "status": "dry_run", "state": str(run_path(root, args.run_id))}, ensure_ascii=False))
            return 0
        attempts = []
        offline: list[dict[str, Any]] = []
        successful_device = None
        for device in profile["devices"]:
            # verify_source_sha() 返回的是证据 dict（requested/verified/package_target_version），
            # 这里必须传真正的 SHA 字符串，否则 commitid 门禁恒不成立（实测会无限循环）。
            attempt = perform_ota(device, package, metadata, profile,
                                  source_sha.get("verified") or source_sha.get("requested"))
            if attempt["status"] == "offline":
                # 离线设备不写进 attempts（否则会污染重试判据：no_device_write 与 retry_reason
                # 都会把"不在线"当失败）。单独收集，用于判断是否"本轮无可用设备"。
                offline.append(attempt)
                continue
            attempts.append(attempt)
            if attempt["status"] in ("passed", "already_on_target"):
                # already_on_target：设备已在本构建固件上（幂等重跑），同样进入回归与证据链。
                successful_device = device
                break
        if successful_device is None and offline and not attempts:
            # 设备池里一台都不在线：是"等待设备"，不是失败；不消耗重试预算，等下个 tick 再看。
            result = finish_run(root, args.run_id, {
                "status": "waiting_for_device", "finished_at": utc_now(),
                "error": "no pool device is online in hdc list targets",
                "offline_devices": [item["serial"] for item in offline],
                "source_sha": source_sha, "artifact_resolved": artifact, "package": package_state,
            })
            print(json.dumps({"phase": 3, "run_id": args.run_id,
                              "status": result["phase3"]["status"],
                              "state": str(run_path(root, args.run_id))}, ensure_ascii=False))
            return 0
        if successful_device is None:
            errors = "; ".join(str(attempt.get("error", "unknown OTA failure")) for attempt in attempts)
            result = finish_run(root, args.run_id, {"status": "failed", "finished_at": utc_now(), "error": errors, "source_sha": source_sha, "artifact_resolved": artifact, "package": package_state, "attempts": attempts})
        else:
            regression = run_regressions(profile, state["phase3"].get("regression_profiles", []), successful_device)
            status = "passed" if regression["status"] == "PASS" else regression["status"].lower()
            result = finish_run(root, args.run_id, {"status": status, "finished_at": utc_now(), "source_sha": source_sha, "artifact_resolved": artifact, "package": package_state, "attempts": attempts, "regression": regression})
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
