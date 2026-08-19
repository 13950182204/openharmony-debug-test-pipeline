#!/usr/bin/env python3
"""OpenHarmony 调测闭环流水线状态读写脚本。

供闭环编排 skill 的模型调用，在任意阶段把产物与事件写入状态文件，
使流水线进度跨会话持久化；/pipeline status 命令读取同一文件。

用法:
  pipeline_state.py get [--file PATH]
  pipeline_state.py set <stage> '<artifacts-json>' [--file PATH]
  pipeline_state.py note <stage> '<说明>' [--file PATH]
  pipeline_state.py reset [--file PATH]
  pipeline_state.py status [--file PATH]

--file 缺省时使用 $DSH_PIPELINE_STATE_FILE 或 ~/.dsh/pipeline-state.json。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
DEFAULT_FILE = Path.home() / ".dsh" / "pipeline-state.json"

VALID_STAGES = {
    "report", "triage", "fix", "mr", "ci", "ota", "regression", "done", "reset",
}


def state_file(args_file: str | None) -> Path:
    if args_file:
        return Path(args_file).expanduser()
    env = os.environ.get("DSH_PIPELINE_STATE_FILE")
    return Path(env).expanduser() if env else DEFAULT_FILE


def load(path: Path) -> dict:
    if not path.is_file():
        return empty()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schemaVersion") != SCHEMA_VERSION:
            raise ValueError("schema version mismatch")
        value.setdefault("stage", None)
        value.setdefault("artifacts", {})
        value.setdefault("events", [])
        return value
    except (json.JSONDecodeError, ValueError) as error:
        # 损坏文件备份后重建，保证流水线可继续
        backup = path.with_name(f"{path.name}.bak-{int(datetime.now().timestamp())}")
        try:
            path.rename(backup)
        except OSError:
            pass
        print(f"warning: 状态文件损坏已备份为 {backup} ({error})", file=sys.stderr)
        return empty()


def empty() -> dict:
    return {"schemaVersion": SCHEMA_VERSION, "updatedAt": "", "stage": None, "artifacts": {}, "events": []}


def save(path: Path, state: dict) -> None:
    state["updatedAt"] = datetime.now(timezone.utc).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def cmd_get(state: dict) -> int:
    print(json.dumps(state, ensure_ascii=False, indent=2))
    return 0


def cmd_set(args: argparse.Namespace, state: dict) -> int:
    if args.stage not in VALID_STAGES:
        print(f"error: 非法阶段 {args.stage!r}，允许: {sorted(VALID_STAGES)}", file=sys.stderr)
        return 2
    try:
        artifacts = json.loads(args.artifacts_json)
    except json.JSONDecodeError as error:
        print(f"error: artifacts 不是合法 JSON: {error}", file=sys.stderr)
        return 2
    if not isinstance(artifacts, dict):
        print("error: artifacts 必须是 JSON 对象", file=sys.stderr)
        return 2
    state["stage"] = args.stage
    state["artifacts"].update(artifacts)
    state["events"].append({
        "at": datetime.now(timezone.utc).isoformat(),
        "stage": args.stage,
        "note": f"进入阶段 {args.stage}，记录产物 {', '.join(artifacts)}",
    })
    return 0


def cmd_note(args: argparse.Namespace, state: dict) -> int:
    state["events"].append({
        "at": datetime.now(timezone.utc).isoformat(),
        "stage": args.stage,
        "note": args.note,
    })
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def with_file(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--file", help="状态文件路径（缺省用 $DSH_PIPELINE_STATE_FILE 或 ~/.dsh/pipeline-state.json）")

    p_get = sub.add_parser("get", help="输出完整状态 JSON")
    with_file(p_get)
    p_reset = sub.add_parser("reset", help="重置流水线状态")
    with_file(p_reset)

    p_status = sub.add_parser("status", help="输出人类可读状态摘要")
    with_file(p_status)
    p_status.set_defaults(handler=cmd_status)

    p_set = sub.add_parser("set", help="设置阶段并合并产物")
    with_file(p_set)
    p_set.add_argument("stage", help="阶段名（report/triage/fix/mr/ci/ota/regression/done）")
    p_set.add_argument("artifacts_json", help="产物 JSON 对象，如 '{\"mrIid\": 168}'")
    p_set.set_defaults(handler=cmd_set)

    p_note = sub.add_parser("note", help="追加一条流水线事件")
    with_file(p_note)
    p_note.add_argument("stage", help="阶段名（用于事件标注，不改变当前阶段）")
    p_note.add_argument("note", help="事件说明")
    p_note.set_defaults(handler=cmd_note)

    args = parser.parse_args(argv)
    path = state_file(args.file)

    if args.command == "reset":
        save(path, empty())
        print(f"已重置流水线状态: {path}")
        return 0

    state = load(path)
    if args.command == "get":
        return cmd_get(state)

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 2
    rc = handler(args, state)
    if rc == 0 and args.command in ("set", "note"):
        save(path, state)
    return rc


def cmd_status(args: argparse.Namespace, state: dict) -> int:
    stage = state.get("stage")
    print(f"流水线阶段: {stage if stage else '（未开始）'}")
    artifacts = state.get("artifacts") or {}
    if artifacts:
        print("产物:")
        for key, value in artifacts.items():
            print(f"  {key}: {value}")
    events = state.get("events") or []
    if events:
        print("最近事件:")
        for event in events[-8:]:
            print(f"  [{event.get('stage')}] {event.get('note')}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
