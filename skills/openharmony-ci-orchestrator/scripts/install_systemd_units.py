#!/usr/bin/env python3
"""Install the user-level reconciliation timer (and the optional legacy webhook unit).

平台 CI 契约（2026-10 起）：门禁构建由 GitLab 流水线自动触发，本插件只观察 + 认领。
reconcile 定时器因此只需 GitLab 与 Jenkins 的**只读**访问：GitLab 走 glab（登录在
`~/.config/glab-cli/config.yml`），Jenkins 只读拿构建参数与归档产物。
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path


DEFAULT_STATE_DIR = Path.home() / ".local/state/openharmony-ci-orchestrator"
DEFAULT_CONFIG_DIR = Path.home() / ".config/openharmony-ci-orchestrator"

ENV_TEMPLATE = """# OpenHarmony CI orchestrator 配置（0600）。凭据只放这里，不进 run 状态/MR 文本。
# 平台门禁 Jenkins（只读：构建参数核对 + 归档产物下载）
JENKINS_BASE_URL=http://192.168.97.54:8080
# 成对提供；只给一个会被 credentials() 判为异或错误
# JENKINS_USER=
# JENKINS_API_TOKEN=
# 平台 GitLab（glab 已登录时这两项用于定位项目）
GITLAB_HOST=192.168.11.238
GITLAB_PROJECT=harmony/system/v9611/openharmony_V6.1
# 可选：glab 可执行文件（systemd 单元的最小 PATH 里通常找不到 ~/bin）
# GLAB_BIN=/home/cx/bin/glab
# 可选（仅 legacy webhook 单元需要）
# CI_WEBHOOK_SECRET=replace-with-a-random-secret
"""


def unit_arg(value: str) -> str:
    return shlex.quote(value.replace("%", "%%"))


def service_path() -> str:
    """PATH for units: keep the user's ~/bin (glab) and ~/.local/bin ahead of defaults."""
    entries = [str(Path.home() / "bin"), str(Path.home() / ".local/bin"),
               "/usr/local/bin", "/usr/bin", "/bin"]
    seen: list[str] = []
    for entry in entries:
        if entry not in seen:
            seen.append(entry)
    return ":".join(seen)


def write_if_changed(path: Path, content: str) -> None:
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o644)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    parser.add_argument("--config-dir", default=str(DEFAULT_CONFIG_DIR))
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=8787)
    parser.add_argument("--enable", action="store_true", help="Reload, enable, and start units after writing them")
    parser.add_argument("--reconcile-timer", default="hourly",
        help="systemd OnCalendar/OnUnitActiveSec expression for the reconcile timer (default: hourly)")
    parser.add_argument("--with-webhook", action="store_true",
        help="Also enable the legacy Jenkins webhook receiver (platform CI signals via GitLab)")
    parser.add_argument("--prune-days", type=int, default=14,
        help="Archive terminal runs older than N days on each reconcile tick (default: 14)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 1 <= args.listen_port <= 65535:
        raise SystemExit("listen-port must be 1 to 65535")
    if args.prune_days < 0:
        raise SystemExit("prune-days must be non-negative")
    import re
    if not re.fullmatch(r"[A-Za-z0-9:*/-]+", args.reconcile_timer):
        raise SystemExit("reconcile-timer must be a plain systemd OnCalendar expression (letters/digits/*:/ -)")
    skill_dir = Path(__file__).resolve().parents[1]
    orchestrator = skill_dir / "scripts/ci_orchestrator.py"
    state_dir = Path(args.state_dir).expanduser().resolve()
    config_dir = Path(args.config_dir).expanduser().resolve()
    unit_dir = Path.home() / ".config/systemd/user"
    env_file = config_dir / "jenkins.env"
    unit_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not env_file.exists():
        env_file.write_text(ENV_TEMPLATE, encoding="utf-8")
        os.chmod(env_file, 0o600)
    command_prefix = f"{unit_arg(sys.executable)} {unit_arg(str(orchestrator))}"
    common = f"EnvironmentFile=-{unit_arg(str(env_file))}\nEnvironment=PATH={unit_arg(service_path())}\n"
    webhook = (
        "[Unit]\nDescription=OpenHarmony CI Jenkins webhook receiver (legacy)\n\n"
        "[Service]\nType=simple\nRestart=on-failure\nRestartSec=10\n"
        f"{common}"
        f"ExecStart={command_prefix} serve --state-dir {unit_arg(str(state_dir))} "
        f"--host {unit_arg(args.listen_host)} --port {args.listen_port}\n\n"
        "[Install]\nWantedBy=default.target\n"
    )
    reconcile = (
        "[Unit]\nDescription=OpenHarmony platform-CI reconciliation\n\n"
        # 交接 agent 由 reconcile 以 start_new_session 启动后即脱离本单元；
        # Type=oneshot 默认 KillMode=control-group 会在单元结束时把该 cgroup 内
        # 所有进程一起杀掉（agent 零输出、瞬间消失，构建成功也不会有下一步动作）。
        # KillMode=process 只结束主进程，保留已派生的交接 agent。
        "[Service]\nType=oneshot\nKillMode=process\n"
        f"{common}"
        f"ExecStart={command_prefix} reconcile --state-dir {unit_arg(str(state_dir))} "
        f"--prune-days {args.prune_days}\n"
    )
    timer = (
        "[Unit]\nDescription=OpenHarmony platform-CI reconciliation\n\n"
        "[Timer]\nOnCalendar=" + args.reconcile_timer + "\nPersistent=true\nRandomizedDelaySec=1m\n"
        "Unit=openharmony-ci-reconcile.service\n\n[Install]\nWantedBy=timers.target\n"
    )
    write_if_changed(unit_dir / "openharmony-ci-webhook.service", webhook)
    write_if_changed(unit_dir / "openharmony-ci-reconcile.service", reconcile)
    write_if_changed(unit_dir / "openharmony-ci-reconcile.timer", timer)
    if args.enable:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", "openharmony-ci-reconcile.timer"], check=True)
        if args.with_webhook:
            subprocess.run(["systemctl", "--user", "enable", "--now", "openharmony-ci-webhook.service"], check=True)
        else:
            # 平台信号来自 GitLab，webhook 只保留单元文件便于回滚。
            subprocess.run(["systemctl", "--user", "disable", "--now", "openharmony-ci-webhook.service"],
                           check=False)
    print("units_dir=" + str(unit_dir))
    print("environment_file=" + str(env_file))
    print("state_dir=" + str(state_dir))
    print("service_path=" + service_path())
    if not args.enable:
        print("next=fill jenkins.env, then rerun with --enable")
    elif not args.with_webhook:
        print("note=legacy webhook receiver disabled (platform CI signals via GitLab)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
