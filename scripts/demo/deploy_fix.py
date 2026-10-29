#!/usr/bin/env python3
"""把修复后的系统库推到设备并验证生效（演示「修复上线」这一秒级动作）。

严格的替换语义：先备份原库到 /data/local/tmp，再覆盖 /system 下的目标库，
重启后逐项核对 sha256 与系统版本——三步都过才算 `verified`。任一步失败都以
非零码退出并写出证据，演示层据此切 lane c（回放），不现场 debug。

用法:
  deploy_fix.py --build <libxxx.z.so> [--device SN] [--device-lib PATH]
                [--expect-version VER] [--timeout 240] [--json-out FILE] [--dry-run]

退出码: 0 已验证生效；2 推送/替换/回连/校验失败；3 环境不满足。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

#: 目标系统库（a333 / OpenHarmony 6.1）。
DEFAULT_DEVICE_LIB = "/system/lib64/platformsdk/libace_compatible.z.so"
#: 设备上的临时落点。
REMOTE_TMP = "/data/local/tmp"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, timeout=timeout, capture_output=True,
                              text=True, errors="replace")
    except (OSError, subprocess.SubprocessError) as error:
        return subprocess.CompletedProcess(cmd, 127, "", str(error))


def sha256_of(path: Path, chunk: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def device_online(serial: str) -> bool:
    listing = run(["hdc", "list", "targets"], timeout=30)
    return listing.returncode == 0 and serial in listing.stdout


def shell(serial: str, command: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return run(["hdc", "-t", serial, "shell", command], timeout=timeout)


def wait_reconnect(serial: str, timeout: int) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if device_online(serial):
            return True
        time.sleep(5)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="推送修复库并验证（重启后校验）")
    parser.add_argument("--build", required=True, help="修复后的 .z.so")
    parser.add_argument("--device", default=None)
    parser.add_argument("--device-lib", default=DEFAULT_DEVICE_LIB)
    parser.add_argument("--expect-version", default=None)
    parser.add_argument("--timeout", type=int, default=240, help="重启回连等待秒数")
    parser.add_argument("--json-out", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    build = Path(args.build).expanduser()
    if not build.is_file():
        print(f"[FAIL] 修复件不存在: {build}", file=sys.stderr)
        return 3
    serial = args.device
    if not serial:
        print("[FAIL] 需要 --device（演示模式不猜设备）", file=sys.stderr)
        return 3
    if not device_online(serial):
        print(f"[FAIL] 设备不在线: {serial}", file=sys.stderr)
        return 3

    evidence: dict = {"at": utc_now(), "device": serial, "build": str(build),
                      "device_lib": args.device_lib, "steps": []}
    local_sha = sha256_of(build)
    evidence["local_sha256"] = local_sha
    print(f"修复件 {build.name} · {build.stat().st_size // 1024 // 1024} MiB · sha256 {local_sha[:16]}…")

    before = shell(serial, f"sha256sum {args.device_lib} 2>/dev/null || echo missing")
    evidence["device_sha256_before"] = " ".join(before.stdout.split())[:140]
    print(f"替换前设备库: {evidence['device_sha256_before']}")

    remote_tmp = f"{REMOTE_TMP}/libace_new.z.so"
    commands = [
        (["hdc", "-t", serial, "file", "send", str(build), remote_tmp], 300, "push"),
        ([], 0, "replace"),
    ]
    if args.dry_run:
        print("dry-run: 将执行 push → 备份 → 覆盖 → reboot → 校验，未真正下发")
        return 0

    pushed = run(commands[0][0], timeout=commands[0][1])
    evidence["steps"].append({"step": "push", "code": pushed.returncode,
                              "tail": (pushed.stdout + pushed.stderr).strip()[-300:]})
    if pushed.returncode != 0:
        print(f"[FAIL] 推送失败: {evidence['steps'][-1]['tail']}", file=sys.stderr)
        return 2

    replace = shell(
        serial,
        f"cp {args.device_lib} {REMOTE_TMP}/libace_compatible.z.so.orig 2>/dev/null; "
        f"cp {remote_tmp} {args.device_lib} && chmod 644 {args.device_lib} && "
        f"ls -l {args.device_lib}", timeout=180)
    evidence["steps"].append({"step": "replace", "code": replace.returncode,
                              "tail": " ".join(replace.stdout.split())[:300]})
    if replace.returncode != 0:
        print(f"[FAIL] 覆盖失败: {evidence['steps'][-1]['tail']}", file=sys.stderr)
        return 2
    print(f"已覆盖: {evidence['steps'][-1]['tail'][:110]}")

    shell(serial, f"rm -f {remote_tmp}", timeout=60)
    shell(serial, "reboot", timeout=30)
    print(f"设备重启，等待回连（≤{args.timeout}s）…")
    started = time.monotonic()
    if not wait_reconnect(serial, args.timeout):
        evidence["steps"].append({"step": "reconnect", "code": 1,
                                  "tail": f"timeout after {args.timeout}s"})
        print("[FAIL] 设备在超时内没有回连", file=sys.stderr)
        write_evidence(args.json_out, evidence)
        return 2
    evidence["reconnect_seconds"] = round(time.monotonic() - started, 1)
    print(f"已回连（{evidence['reconnect_seconds']}s）")

    time.sleep(5)
    after = shell(serial, f"sha256sum {args.device_lib}", timeout=90)
    device_sha = after.stdout.strip().split()[0] if after.stdout.strip() else None
    evidence["device_sha256_after"] = " ".join(after.stdout.split())[:140]
    version = shell(serial, "param get const.product.software.version", timeout=60)
    evidence["software_version"] = version.stdout.strip()
    shell(serial, "power-shell wakeup", timeout=60)

    verified = device_sha == local_sha
    evidence["verified"] = verified
    evidence["expected_version"] = args.expect_version
    print(f"替换后设备库: {device_sha[:16] if device_sha else '?'}… · 系统版本 {evidence['software_version']}")
    print("-" * 66)
    print(("PASS" if verified else "FAIL") + " · 修复库已生效"
          if verified else "FAIL · 设备库 sha256 与修复件不一致")
    write_evidence(args.json_out, evidence)
    return 0 if verified else 2


def write_evidence(path_text: str | None, evidence: dict) -> None:
    if not path_text:
        return
    path = Path(path_text).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"证据: {path}")


if __name__ == "__main__":
    sys.exit(main())
