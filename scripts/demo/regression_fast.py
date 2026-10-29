#!/usr/bin/env python3
"""演示用快速回归：在指定设备上真跑**一个** XTS 模块，并按报告判定 PASS/FAIL。

复用受信任的 phase-3 回归实现（`phase3_runner.run_xts_rerun`），不复制命令拼装、
不绕过 `test_harness.verdict` 的「全通过才算 PASS」规则。差异只有三处，全部显式：

1. 只跑一个模块（演示要 1–3 分钟，不要 5400 秒的整轮）；
2. runner_dir / session_root 由演示层覆盖（真实 profile 的 `acts-LTS-f` 已被
   改名为 `acts`，见 docs/演示模式.md 的说明）；
3. 不注册 ci-orchestrator 的 run，不写它的状态目录——演示不污染真实链路。

用法:
  regression_fast.py --module ActsAACommandPrintSyncTest \
                     [--serial SN] [--runner-dir /mnt/d/ohos/XTS6.1/office/acts] \
                     [--session-root DIR] [--timeout 900] [--json-out FILE]
                     [--dry-command]

退出码: 0 PASS；2 FAIL/INCONCLUSIVE；3 环境不满足（模块缺失等）。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CI_SCRIPTS = REPO / "skills/openharmony-ci-orchestrator/scripts"
DEFAULT_PROFILE = REPO / "skills/openharmony-ci-orchestrator/profiles/xts-rerun-lts-f.json"
#: 运行器目录的真实位置（`office/acts-LTS-f` 已被改名为 `office/acts`）。
DEFAULT_RUNNER_DIR = "/mnt/d/ohos/XTS6.1/office/acts"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_runner():
    """按文件路径加载 phase3_runner，并让它能 import 同目录的兄弟模块。"""
    if not CI_SCRIPTS.is_dir():
        raise SystemExit(f"ci-orchestrator scripts 缺失: {CI_SCRIPTS}")
    sys.path.insert(0, str(CI_SCRIPTS))
    spec = importlib.util.spec_from_file_location("phase3_runner",
                                                  CI_SCRIPTS / "phase3_runner.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def build_profile(args: argparse.Namespace) -> dict:
    profile_path = Path(args.profile).expanduser()
    if not profile_path.is_file():
        raise SystemExit(f"回归 profile 不存在: {profile_path}")
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    profile = dict(profile)
    profile["kind"] = "xts-rerun"
    profile["runner_dir"] = str(Path(args.runner_dir).expanduser())
    profile["session_root"] = str(Path(args.session_root).expanduser())
    profile["modules"] = [item for item in args.module.split(",") if item]
    if args.timeout:
        profile["timeout_seconds"] = args.timeout
    return profile


def relocate_session_root(profile: dict) -> str | None:
    """Windows 侧执行时，把不可见的 session_root 改到运行器目录下。

    实测（2026-10-29）：`--session-root` 指向 `/tmp` 或 `/home`（含 `~/.dsh/demo/runs/...`）
    时，`wslpath -w` 会转成 `\\\\wsl.localhost\\...` UNC 路径，xdevice 从 UNC 读配置后
    枚举到 0 台设备：`[Environment-0101021] Test source required 1 devices, actually 0 devices were found`。
    演示证据仍可留在 `~/.dsh/demo/`（由 `--json-out` 写），报告本体落到运行器目录下，
    两端口径都能看到。
    """
    if str(profile.get("runner_host") or "").lower() != "windows":
        return None
    session_root = str(profile.get("session_root") or "")
    if session_root.startswith("/mnt/"):
        return None
    fallback = Path(str(profile["runner_dir"])).expanduser() / "reports" / "demo"
    profile["session_root"] = str(fallback)
    return (f"session_root 由 {session_root} 改到 {fallback}"
            "（Windows 侧 xdevice 读不了 UNC 路径，会枚举到 0 台设备）")


def config_for_serial(config: Path, serial: str, workdir: Path) -> tuple[Path, str | None]:
    """让 xdevice 的 `<sn>` 与本次设备一致。

    共享的 `config/user_config.xml` 里 `<sn>` 是别的板子（实测：1.4.0 的 ea010e…），
    xdevice 会按它过滤设备，于是 `-sn <别的 serial>` 直接得到「0 devices」。
    这里把配置复制一份、只替换 `<sn>`，共享配置保持原样（真实链路不受演示影响）。
    """
    text = config.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"<sn>([^<]*)</sn>", text)
    if not match:
        return config, "config 无 <sn> 字段，按原配置执行"
    current = match.group(1).strip()
    if current == serial:
        return config, None
    workdir.mkdir(parents=True, exist_ok=True)
    patched = workdir / f"user_config.{serial}.xml"
    patched.write_text(re.sub(r"<sn>[^<]*</sn>", f"<sn>{serial}</sn>", text, count=1),
                       encoding="utf-8")
    return patched, f"config <sn> 由 {current or '(空)'} 覆盖为 {serial}"


def main() -> int:
    parser = argparse.ArgumentParser(description="演示用快速回归（单模块真跑）")
    parser.add_argument("--module", required=True, help="单模块名，多个用逗号分隔")
    parser.add_argument("--serial", default=None, help="设备 serial；缺省取 profile 的 primary")
    parser.add_argument("--profile", default=str(DEFAULT_PROFILE))
    parser.add_argument("--runner-dir", default=DEFAULT_RUNNER_DIR)
    parser.add_argument("--session-root", default=None,
                        help="报告输出目录；缺省放到 demo 运行目录外的 reports/demo")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--json-out", default=None)
    parser.add_argument("--dry-command", action="store_true", help="只打印将要执行的命令")
    args = parser.parse_args()

    runner = load_runner()
    if args.session_root is None:
        args.session_root = str(Path(args.runner_dir).expanduser() / "reports" / "demo")
    profile = build_profile(args)
    relocated = relocate_session_root(profile)

    serial = args.serial
    if not serial:
        devices = profile.get("devices") or []
        if not devices:
            raise SystemExit("profile 未声明设备且没有 --serial")
        serial = devices[0]["serial"]

    runner_dir = Path(profile["runner_dir"])
    config = runner_dir / str(profile.get("config") or "config/user_config.xml")
    if not runner_dir.is_dir():
        print(f"[FAIL] runner_dir 不存在: {runner_dir}", file=sys.stderr)
        return 3
    if not config.is_file():
        print(f"[FAIL] xdevice config 不存在: {config}", file=sys.stderr)
        return 3

    config, override_note = config_for_serial(config, serial, Path(profile["session_root"]))
    # 关键：run_xts_rerun 从 profile["config"] 取配置。只改本地变量等于没改——
    # 实测漏这一步时 xdevice 仍用共享配置（<sn> 是别的板子）+ `-sn <本机>`，
    # 结果还是 `0 devices were found`。绝对路径经 posix 拼接后仍是它自己。
    profile["config"] = str(config)

    if args.dry_command:
        modules = profile["modules"]
        command = runner.build_xts_command(
            profile, runner_dir, config,
            Path(profile["session_root"]) / "DRYRUN", modules, serial)
        print(" ".join(command))
        return 0

    print(f"回归开始 {utc_now()} · {profile['modules']} · 设备 {serial} · {runner_dir.name}")
    if override_note:
        print(f"注意: {override_note}")
    if relocated:
        print(f"注意: {relocated}")
    started = time.monotonic()
    entry = runner.run_xts_rerun(profile, serial, None)
    entry["demo"] = {"elapsed_seconds": round(time.monotonic() - started, 1),
                     "session_root": profile["session_root"],
                     "runner_dir": profile["runner_dir"],
                     "config": str(config),
                     "config_override": override_note}

    status = entry.get("status")
    verdict = entry.get("verdict") or {}
    print("-" * 66)
    print(f"结果: {status} · {verdict.get('passed', '?')}/{verdict.get('total', '?')} 通过"
          f" · {entry['demo']['elapsed_seconds']}s")
    if verdict.get("failed_cases"):
        print("失败用例: " + ", ".join(map(str, verdict["failed_cases"][:8])))
    if entry.get("report_dir"):
        print(f"报告: {entry['report_dir']}")
    error = (entry.get("evidence") or {}).get("error")
    if error:
        print(f"原因: {error}")
    tail = (entry.get("runner_tail") or "").strip()
    if tail:
        print("-" * 66)
        print(tail[-600:])

    if args.json_out:
        out = Path(args.json_out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(entry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"证据: {out}")

    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
