#!/usr/bin/env python3
"""phase-3 systemd 单元的确定性入口（不含任何 LLM/agent 逻辑）。

职责只有两件：
1. 读出该 run 在状态文件里登记的 phase-3 profile（受信任配置的来源是状态文件，而不是单元参数）；
2. exec 受信任的 phase3_runner.py。

之所以要这一层：systemd 模板单元的 %i 只能传一个字符串（run id），而 runner 还需要 profile；
同时 exec 而不是 subprocess，保证信号/退出码/超时语义都归单元本身管。

用法（由 openharmony-ci-phase3@.service 调用）：
    phase3_unit_entry.py <run-id>

环境变量：
    CI_STATE_DIR                 状态目录，默认 ~/.local/state/openharmony-ci-orchestrator
    CI_PHASE3_RETRY_PREFLIGHT=1  允许 runner 走 --retry-preflight（仅"未写设备"的预检失败可重试）
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

DEFAULT_STATE_DIR = os.path.join(
    os.environ.get("HOME", "/home/cx"), ".local/state/openharmony-ci-orchestrator"
)


def fail(message: str, code: int = 2) -> "NoReturn":  # noqa: F821
    print(f"phase3_unit_entry: {message}", file=sys.stderr)
    raise SystemExit(code)


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1]:
        fail("usage: phase3_unit_entry.py <run-id>")
    run_id = sys.argv[1]

    state_dir = pathlib.Path(os.environ.get("CI_STATE_DIR", DEFAULT_STATE_DIR))
    state_path = state_dir / "runs" / f"{run_id}.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"run state not found: {state_path}")
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read run state {state_path}: {error}")

    profile = (state.get("phase3") or {}).get("profile")
    if not isinstance(profile, str) or not profile.strip():
        fail(f"run {run_id} has no phase3.profile registered; refusing to run phase 3")
    profile = profile.strip()

    scripts_dir = pathlib.Path(__file__).resolve().parent
    runner = scripts_dir / "phase3_runner.py"
    if not runner.is_file():
        fail(f"trusted runner not found next to this entry point: {runner}")

    argv = [
        sys.executable,
        str(runner),
        "--state-dir", str(state_dir),
        "--run-id", run_id,
        "--profile", profile,
    ]
    if os.environ.get("CI_PHASE3_RETRY_PREFLIGHT") == "1":
        argv.append("--retry-preflight")

    print(f"phase3_unit_entry: exec {runner.name} for run {run_id} profile {profile}", flush=True)
    os.execv(sys.executable, argv)
    return 0  # pragma: no cover - execv 不返回


if __name__ == "__main__":
    raise SystemExit(main())
