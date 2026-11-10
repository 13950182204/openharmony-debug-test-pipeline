#!/usr/bin/env python3
"""把回归证据截图上传到 GitLab，并回写到 phase-3 的同一条 MR 评论。

只做两件事：上传截图（`projects/<project>/uploads`）与更新评论（同一条 marker 的 note）。
截图是证据，不参与通过判定——判定只认回归报告。

用法：

    python3 attach_regression_evidence.py --run-id <run-id> --image <png> [--image <png> ...]

证据落在 run 状态的 `phase3.evidence_images`，重复执行不会产生重复图片。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import phase3_runner as runner  # noqa: E402


def merge_evidence_images(existing: list[Any], new: list[str]) -> list[str]:
    """保序去重：同一张截图重复上传只留第一次的链接。"""
    merged = [str(item).strip() for item in existing if str(item).strip()]
    for item in new:
        link = item.strip()
        if link and link not in merged:
            merged.append(link)
    return merged


def upload_image(host: str, project: str, source: Path) -> str:
    """上传一张截图，返回 GitLab 生成的 Markdown 链接。"""
    upload_path = source
    temporary_directory = None
    try:
        if not str(source).startswith(str(Path.home()) + os.sep):
            # Snap 版 glab 读不到 /tmp 与部分挂载点：先复制到 $HOME 再上传。
            temporary_directory = tempfile.mkdtemp(prefix=".ci-evidence-", dir=Path.home())
            upload_path = Path(temporary_directory) / source.name
            shutil.copyfile(source, upload_path)
        raw = runner.run_command(
            [
                "glab", "api", "--hostname", host, "--method", "POST",
                f"projects/{quote(project, safe='')}/uploads",
                "--form", f"file=@{upload_path}",
            ],
            timeout=120,
            max_capture=2_000_000,
        )
    finally:
        if temporary_directory:
            shutil.rmtree(temporary_directory, ignore_errors=True)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise runner.Phase3Error(f"GitLab 上传返回的不是 JSON：{source}") from error
    link = str(payload.get("markdown") or "").strip()
    if not link:
        raise runner.Phase3Error(f"GitLab 上传没有返回图片链接：{source}")
    return link


def attach(state_directory: str, run_id: str, images: list[str]) -> dict[str, Any]:
    root = runner.state_directory(state_directory)
    state_path = runner.run_path(root, run_id)
    state = runner.load_json(state_path)
    host = runner.configured_value("GITLAB_HOST")
    project = runner.configured_value("GITLAB_PROJECT")
    if not host or not project:
        raise runner.Phase3Error("GITLAB_HOST / GITLAB_PROJECT 未配置，无法回写截图")

    links = []
    for item in images:
        source = Path(item).expanduser()
        if not source.is_file():
            raise runner.Phase3Error(f"证据截图不存在：{source}")
        links.append(upload_image(host, project, source))

    phase3 = state.setdefault("phase3", {})
    phase3["evidence_images"] = merge_evidence_images(list(phase3.get("evidence_images") or []), links)
    runner.write_json(state_path, state)
    note = runner.write_mr_note(state)
    return {"run_id": run_id, "images": phase3["evidence_images"], "mr_note": note}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="上传回归证据截图并回写到 phase-3 的 MR 评论。")
    parser.add_argument("--state-dir", default=os.path.expanduser("~/.local/state/openharmony-ci-orchestrator"),
                        help="run 状态根目录（默认与 ci_orchestrator 一致）")
    parser.add_argument("--run-id", required=True, help="phase-3 的 run id")
    parser.add_argument("--image", action="append", required=True, help="截图文件路径，可重复")
    args = parser.parse_args(argv)
    try:
        result = attach(args.state_dir, args.run_id, args.image)
    except (runner.Phase3Error, runner.OrchestratorError, OSError, subprocess.SubprocessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
