#!/usr/bin/env python3
"""Install user-level webhook and hourly reconciliation systemd units."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path


DEFAULT_STATE_DIR = Path.home() / ".local/state/openharmony-ci-orchestrator"
DEFAULT_CONFIG_DIR = Path.home() / ".config/openharmony-ci-orchestrator"


def unit_arg(value: str) -> str:
    return shlex.quote(value.replace("%", "%%"))


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
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 1 <= args.listen_port <= 65535:
        raise SystemExit("listen-port must be 1 to 65535")
    skill_dir = Path(__file__).resolve().parents[1]
    orchestrator = skill_dir / "scripts/ci_orchestrator.py"
    state_dir = Path(args.state_dir).expanduser().resolve()
    config_dir = Path(args.config_dir).expanduser().resolve()
    unit_dir = Path.home() / ".config/systemd/user"
    env_file = config_dir / "jenkins.env"
    unit_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not env_file.exists():
        env_file.write_text(
            "# Required for the webhook service. Keep this file private.\n"
            "CI_WEBHOOK_SECRET=replace-with-a-random-secret\n"
            "# Add JENKINS_USER and JENKINS_API_TOKEN if Jenkins API reads require auth.\n"
            "# Optional phase-3 MR note target. glab authentication remains in the user keyring.\n"
            "# GITLAB_HOST=192.168.11.238\n"
            "# GITLAB_PROJECT=harmony/system/v9611/openharmony_V6.1\n",
            encoding="utf-8",
        )
        os.chmod(env_file, 0o600)
    command_prefix = f"{unit_arg(sys.executable)} {unit_arg(str(orchestrator))}"
    common = f"EnvironmentFile=-{unit_arg(str(env_file))}\n"
    webhook = (
        "[Unit]\nDescription=OpenHarmony CI Jenkins webhook receiver\n\n"
        "[Service]\nType=simple\nRestart=on-failure\nRestartSec=10\n"
        f"{common}"
        f"ExecStart={command_prefix} serve --state-dir {unit_arg(str(state_dir))} "
        f"--host {unit_arg(args.listen_host)} --port {args.listen_port}\n\n"
        "[Install]\nWantedBy=default.target\n"
    )
    reconcile = (
        "[Unit]\nDescription=OpenHarmony CI Jenkins reconciliation\n\n"
        "[Service]\nType=oneshot\n"
        f"{common}"
        f"ExecStart={command_prefix} reconcile --state-dir {unit_arg(str(state_dir))}\n"
    )
    timer = (
        "[Unit]\nDescription=Hourly OpenHarmony CI Jenkins reconciliation\n\n"
        "[Timer]\nOnCalendar=hourly\nPersistent=true\nRandomizedDelaySec=5m\n"
        "Unit=openharmony-ci-reconcile.service\n\n[Install]\nWantedBy=timers.target\n"
    )
    write_if_changed(unit_dir / "openharmony-ci-webhook.service", webhook)
    write_if_changed(unit_dir / "openharmony-ci-reconcile.service", reconcile)
    write_if_changed(unit_dir / "openharmony-ci-reconcile.timer", timer)
    if args.enable:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", "openharmony-ci-webhook.service"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", "openharmony-ci-reconcile.timer"], check=True)
    print("units_dir=" + str(unit_dir))
    print("environment_file=" + str(env_file))
    print("state_dir=" + str(state_dir))
    if not args.enable:
        print("next=fill jenkins.env, then rerun with --enable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
