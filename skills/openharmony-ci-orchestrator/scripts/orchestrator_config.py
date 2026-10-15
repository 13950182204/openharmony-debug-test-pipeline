#!/usr/bin/env python3
"""Shared configuration resolution for the CI orchestrator and phase-3 runner.

`~/.config/openharmony-ci-orchestrator/jenkins.env`（0600）是本插件唯一的部署侧配置来源：
systemd 单元用 `EnvironmentFile=` 注入，人工/模型会话执行脚本时由本模块读取。两处口径必须
一致，因此读取逻辑集中在这里，避免各脚本各写一份。

约定优先级：**进程环境变量 > 配置文件**。凭据只从这两处读取，绝不写入 run 状态或 MR 文本。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

CONFIG_ENV = Path.home() / ".config/openharmony-ci-orchestrator/jenkins.env"

# 常见落点（PATH 之外）——systemd 单元的最小 PATH 常看不到 ~/bin。
BINARY_SEARCH_DIRS = ("bin", ".local/bin")


def configured_value(name: str, config_path: Path | None = None) -> str | None:
    """Return one setting from the process environment or the private config file."""
    value = os.environ.get(name)
    if value:
        return value
    path = config_path or CONFIG_ENV
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[len("export "):]
            if line.startswith(f"{name}="):
                configured = line.partition("=")[2].strip().strip('"').strip("'")
                return configured or None
    except FileNotFoundError:
        pass
    return None


def resolve_binary(name: str, configured: str | None = None) -> str | None:
    """Resolve an external CLI via an explicit setting, PATH, then common home dirs."""
    candidate = configured or os.environ.get(f"{name.upper()}_BIN")
    if candidate:
        return candidate if Path(candidate).is_file() else None
    found = shutil.which(name)
    if found:
        return found
    for directory in BINARY_SEARCH_DIRS:
        path = Path.home() / directory / name
        if path.is_file():
            return str(path)
    return None
