#!/usr/bin/env python3
"""OpenHarmony 调测闭环流水线状态读写脚本。

供闭环编排 skill 的模型调用，在任意阶段把产物与事件写入状态文件，
使流水线进度跨会话持久化；/pipeline status 命令读取同一文件。

用法:
  pipeline_state.py get [--file PATH]
  pipeline_state.py set <stage> '<artifacts-json>' [--file PATH]
  pipeline_state.py note <stage> '<说明>' [--file PATH]
  pipeline_state.py tokens <stage> [--file PATH]
  pipeline_state.py reset [--file PATH]
  pipeline_state.py status [--file PATH]

--file 缺省时使用 $DSH_PIPELINE_STATE_FILE 或 ~/.dsh/pipeline-state.json。
tokens 子命令从 ~/.dsh/storages/session_projcache.json 读取当前会话的
token 累计（uncachedInput/output/cacheRead/cacheWrite），快照进状态文件的
tokenSnapshots 数组，供每个阶段结束后记录 token 用量。
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
TOKEN_CACHE = Path.home() / ".dsh" / "storages" / "session_projcache.json"

VALID_STAGES = {
    "report", "triage", "fix", "mr", "ci", "ota", "regression", "done", "reset",
}


def token_totals() -> dict | None:
    """从 DSH session_projcache 读取当前工作区最活跃会话的 token 累计。

    返回 {sessionId, uncachedInputTokens, outputTokens, cacheReadTokens, cacheWriteTokens}
    或 None（缓存文件缺失/解析失败时）。
    会话选择：先按脚本 cwd 对应的 ~/.dsh/sessions/<workspace-key>/ 目录限定候选
    （目录名是 workspace 路径的 / 替换为 -，如 /home/cx/os → --home-cx-os--），
    再取其中 liveTokenUsage.seq 最大者；workspace 目录缺失时回退全局最大 seq。
    """
    if not TOKEN_CACHE.is_file():
        return None
    try:
        data = json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    sessions = (data.get("tables") or {}).get("sessions") or {}
    if not sessions:
        return None
    # 当前工作区会话集合：沿 cwd 向上逐级找存在的 ~/.dsh/sessions/<key> 目录
    # （目录名形如 --home-cx-os--，即 "--" + workspace 路径 /→- + "--"；
    #  脚本可能从 workspace 子目录运行，故逐级上溯）
    workspace_ids = None
    for ancestor in [Path.cwd(), *Path.cwd().parents]:
        # 去掉开头的 "/"（DSH 的 workspace key 形如 --home-cx-os--）
        key = f"--{str(ancestor).lstrip('/').replace('/', '-')}--"
        sdir = Path.home() / ".dsh" / "sessions" / key
        if sdir.is_dir():
            workspace_ids = {p.name for p in sdir.iterdir()}
            break
    best_open = None
    best_any = None
    for sid, table in sessions.items():
        if workspace_ids is not None and sid not in workspace_ids:
            continue
        rows = table.get("rows") or {}
        usage = (rows.get("tokenUsage") or {}).get("val")
        if not usage:
            continue
        totals = usage.get("totals") or {}
        if not totals.get("outputTokens") and not totals.get("cacheReadTokens"):
            continue
        live = rows.get("liveTokenUsage")
        if live is not None:
            # 有活跃 live 视图 = 当前打开的会话，优先选 seq 最大者
            seq = live.get("seq", 0)
            if best_open is None or seq > best_open[0]:
                best_open = (seq, sid, totals)
        seq = usage.get("seq", 0)
        if best_any is None or seq > best_any[0]:
            best_any = (seq, sid, totals)
    best = best_open if best_open is not None else best_any
    if best is None:
        return None
    return {"sessionId": best[1], **best[2]}


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


def cmd_tokens(args: argparse.Namespace, state: dict) -> int:
    """把当前会话 token 累计快照写入 state.tokenSnapshots（含阶段标签）。"""
    totals = token_totals()
    if totals is None:
        print("warning: 未找到 session_projcache 的 token 数据，跳过快照", file=sys.stderr)
        return 0
    snapshots = state.setdefault("tokenSnapshots", [])
    snapshots.append({
        "at": datetime.now(timezone.utc).isoformat(),
        "stage": args.stage,
        **totals,
    })
    print(json.dumps(totals, ensure_ascii=False))
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

    p_tokens = sub.add_parser("tokens", help="快照当前会话 token 用量到流水线状态")
    with_file(p_tokens)
    p_tokens.add_argument("stage", help="阶段标签（如 triage）")
    p_tokens.set_defaults(handler=cmd_tokens)

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
    if rc == 0 and args.command in ("set", "note", "tokens"):
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
    snapshots = state.get("tokenSnapshots") or []
    if snapshots:
        print("token 快照（最近 3 次）:")
        for snap in snapshots[-3:]:
            print(f"  [{snap.get('stage')}] in={snap.get('uncachedInputTokens')} out={snap.get('outputTokens')} cacheRead={snap.get('cacheReadTokens')}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
