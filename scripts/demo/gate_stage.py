#!/usr/bin/env python3
"""演示快速门禁（demo gate）：把本地已构建的 OTA 包变成「门禁产物契约」。

真实链路里，门禁是 GitLab 流水线 → Jenkins `OpenHarmony-V6.1-MR-Gate` →
芯片作业 `OpenHarmony-V6.1-MR-<chip>`，全流程 40 分钟以上。演示只有 10 分钟，
所以这里做的是**同形状的产物契约**：包校验 + OTA 预检 + 版本/sha256/清单落盘，
并固定标记 `mode = demo-local`——绝不伪造 Jenkins 构建号，也不访问 Jenkins。

产物（--out 目录）：
  build.json      构建契约：source_sha / artifact{relativePath,size,sha256,version}
                  / reference{jenkins_url,build_no} / mode=demo-local
  mr-metrics.tsv  预检逐项结果（制表符分隔，可直接贴到汇报）
  artifacts/update.zip   指向真实包的符号链接（不复制 1 GiB）

用法:
  gate_stage.py --package /home/cx/os/v6.1/out/ota_final/update.zip \
                --mr-iid 260 --source-sha ddde621d74487a7828883270da8f8096773a7909 \
                --out <demo run>/gate [--reference-url URL] [--skip-preflight]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

#: 门禁产物契约的模式标记。本地替代必须可辨认，不能伪装成平台构建。
MODE = "demo-local"
#: 真实门禁的固定入口（只作为对照链接展示，不访问）。
DEFAULT_REFERENCE = "http://192.168.97.54:8080/job/OpenHarmony-V6.1-MR-a333/31"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_of(path: Path, chunk: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def preflight_script() -> Path:
    # 本文件位于 <repo>/scripts/demo/，OTA 预检脚本在 skills/openharmony-ota-upgrade/scripts/。
    return Path(__file__).resolve().parents[2] / \
        "skills/openharmony-ota-upgrade/scripts/ota_preflight.sh"


def run_preflight(package: Path) -> tuple[int, str]:
    script = preflight_script()
    if not script.is_file():
        return 127, f"OTA preflight script missing: {script}"
    started = time.monotonic()
    try:
        result = subprocess.run(["bash", str(script), str(package)], timeout=600,
                                capture_output=True, text=True, errors="replace")
    except (OSError, subprocess.SubprocessError) as error:
        return 127, f"OTA preflight could not run: {error}"
    output = (result.stdout or "") + (result.stderr or "")
    output += f"\n[DEMO] preflight_wall_seconds={time.monotonic() - started:.1f}\n"
    return result.returncode, output


def parse_preflight(output: str) -> dict:
    """从 ota_preflight.sh 的固定格式里取字段；缺失就是 None，不猜。"""
    fields: dict[str, str] = {}
    patterns = {
        "size": r"^\[SIZE\]\s+(\d+)",
        "sha256": r"^\[SHA256\]\s+([0-9a-fA-F]{64})",
        "members": r"^\[ZIP\]\s+(\d+)",
        "version": r"^\[VERSION\]\s+(.+?)\s*$",
        "device_reserve": r"^\[DEVICE\]\s+(.+?)\s*$",
    }
    for name, pattern in patterns.items():
        match = re.search(pattern, output, re.MULTILINE)
        fields[name] = match.group(1) if match else None
    fields["passed"] = "[PASS]" in output
    fields["verify_ok"] = "No errors detected" in output
    wall = re.search(r"\[DEMO\] preflight_wall_seconds=([0-9.]+)", output)
    fields["wall_seconds"] = float(wall.group(1)) if wall else None
    return fields


def zip_members(package: Path) -> list[str]:
    try:
        with zipfile.ZipFile(package) as archive:
            return archive.namelist()
    except (OSError, zipfile.BadZipFile):
        return []


def write_tsv(path: Path, rows: list[tuple[str, str, str]]) -> None:
    lines = ["item\tvalue\tstatus"]
    lines.extend(f"{name}\t{value}\t{status}" for name, value, status in rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="演示快速门禁（本地包 → 门禁产物契约）")
    parser.add_argument("--package", required=True, help="本地已构建的 OTA 包（update.zip）")
    parser.add_argument("--out", required=True, help="契约输出目录（通常是 <demo run>/gate）")
    parser.add_argument("--mr-iid", default=None)
    parser.add_argument("--source-sha", default=None)
    parser.add_argument("--reference-url", default=DEFAULT_REFERENCE,
                        help="真实门禁入口，仅作对照链接")
    parser.add_argument("--skip-preflight", action="store_true")
    args = parser.parse_args()

    package = Path(args.package).expanduser()
    out = Path(args.out).expanduser()
    if not package.is_file():
        print(f"[FAIL] 包不存在: {package}", file=sys.stderr)
        return 1

    started = time.monotonic()
    rows: list[tuple[str, str, str]] = []
    failures = 0

    rows.append(("package", package.name, "PASS"))
    size = package.stat().st_size
    rows.append(("size_bytes", str(size), "PASS"))

    if not zipfile.is_zipfile(package):
        rows.append(("zip_integrity", "not a zip", "FAIL"))
        failures += 1
    else:
        rows.append(("zip_integrity", "ok", "PASS"))

    digest = sha256_of(package)
    rows.append(("sha256_local", digest, "PASS"))

    fields: dict = {}
    if args.skip_preflight:
        rows.append(("ota_preflight", "skipped", "WARN"))
    else:
        code, output = run_preflight(package)
        fields = parse_preflight(output)
        out.mkdir(parents=True, exist_ok=True)
        (out / "preflight.log").write_text(output, encoding="utf-8")
        status = "PASS" if code == 0 and fields.get("passed") else "FAIL"
        if status == "FAIL":
            failures += 1
        rows.append(("ota_preflight", "passed" if status == "PASS" else "failed", status))
        if fields.get("sha256"):
            same = fields["sha256"].lower() == digest.lower()
            rows.append(("sha256_preflight_match", "yes" if same else "no",
                         "PASS" if same else "FAIL"))
            if not same:
                failures += 1
        for name in ("size", "members", "version", "device_reserve", "wall_seconds"):
            if fields.get(name) is not None:
                rows.append((f"preflight_{name}", str(fields[name]), "PASS"))

    members = zip_members(package)
    if members:
        rows.append(("zip_members", ",".join(members[:8]), "PASS"))

    artifacts = out / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    link = artifacts / "update.zip"
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(package)

    elapsed = time.monotonic() - started
    build = {
        "schemaVersion": 1,
        "mode": MODE,
        "at": utc_now(),
        "elapsed_seconds": round(elapsed, 1),
        "mr_iid": args.mr_iid,
        "source_sha": args.source_sha,
        "artifact": {
            "relativePath": f"{package.parent.name}/{package.name}",
            "path": str(package),
            "size": size,
            "sha256": digest,
            "version": fields.get("version") or None,
            "members": members,
        },
        "reference": {"jenkins_url": args.reference_url, "note": "真实门禁入口，仅对照展示"},
        "checks": [{"item": name, "value": value, "status": status} for name, value, status in rows],
    }
    build_path = out / "build.json"
    build_path.parent.mkdir(parents=True, exist_ok=True)
    build_path.write_text(json.dumps(build, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_tsv(out / "mr-metrics.tsv",
              [(name, str(value)[:160], status) for name, value, status in rows])

    print("演示门禁（demo-local）")
    print("-" * 66)
    for name, value, status in rows:
        print(f"{status:<5} {name:<22} {str(value)[:36]}")
    print("-" * 66)
    print(f"用时 {elapsed:.1f}s · 契约 {build_path}")
    print(f"产物 {link} -> {package}")
    print(f"真实门禁对照: {args.reference_url}")
    print("声明: 本段是本地出包 + 门禁产物契约替代，不是平台 Jenkins 构建。")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
