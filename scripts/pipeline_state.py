#!/usr/bin/env python3
"""OpenHarmony 调测闭环流水线状态读写脚本。

供闭环编排 skill 的模型调用，在任意阶段把产物与事件写入状态文件，
使流水线进度跨会话持久化；/pipeline status 命令读取同一文件。

用法:
  pipeline_state.py get [--file PATH]
  pipeline_state.py set <stage> '<artifacts-json>' [--force] [--file PATH]
  pipeline_state.py describe
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
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
DEFAULT_FILE = Path.home() / ".dsh" / "pipeline-state.json"
TOKEN_CACHE = Path.home() / ".dsh" / "storages" / "session_projcache.json"

VALID_STAGES = {
    "report", "triage", "fix", "waiver", "mr", "ci", "ota", "regression", "done", "reset",
}

# 阶段推进契约：脚本与 skill 正文共用这一份，避免「正文写了、脚本不认」。waiver 曾如此：
# 闭环 skill 正文 3b 要求把结论写成 stage=waiver，而 VALID_STAGES 里没有它，执行必被拒。
STAGE_ORDER = ("report", "triage", "fix", "waiver", "mr", "ci", "ota", "regression", "done")
# 分支：triage 之后 fix 与 waiver 是**两条腿**（同一轮里允许两条腿都有条目，
# 阶段指针只记录最前一次推进，条目归属由 artifacts 表达）。
BRANCH_STAGES = ("fix", "waiver")
# 回退白名单：回归失败回 fix、重新分诊回 triage、换报告回 report；其余回退需 --force。
BACKTRACKS = {
    "triage": STAGE_ORDER,
    "report": STAGE_ORDER,
    "fix": ("mr", "ci", "ota", "regression", "done"),
}
# 每个阶段必须登记的核心产物（任一即可）：缺了说明这个阶段是空转。
STAGE_REQUIRED = {
    "report": ("reportDir",),
    "waiver": ("waiverRecords",),
    "mr": ("mrIid",),
    "ci": ("jenkinsBuildNumber", "gateBuildUrl", "pipelineId"),
    "ota": ("otaResult", "otaSerial"),
    "regression": ("regressionResult",),
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
        raise ValueError(f"状态文件无法读取，已保留原文件 {path}: {error}") from error


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


MODULE_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{2,63}$")


def looks_like_test_class(name: str) -> bool:
    """启发式：小写开头的驼峰（如 dmsJsUnitTest）通常是测试类名而非 HAP 模块名。"""
    return bool(name) and name[0].islower()


def validate_stage_artifacts(stage: str, artifacts: dict) -> tuple[list[str], list[str]]:
    """校验一个阶段写入的产物形状，返回 (errors, warnings)。

    为什么需要：`set` 原先接受任意 JSON，于是出现过 `failedCases: 3`（数量而非清单）与
    "模块名写成测试类名" 这类**静默**不一致——两端各自演化、直到运行期才暴露。
    本函数只校验结构与命名空间边界（不做业务正确性判断）：错误阻塞写入，警告提示。
    """
    errors: list[str] = []
    warnings: list[str] = []
    if stage == "waiver":
        records = artifacts.get("waiverRecords")
        if records is not None:
            if not isinstance(records, list) or not records:
                errors.append("waiverRecords 必须是非空清单，每项形如 "
                              '{"txt": "<套件>.txt", "zip": "<套件>-豁免材料.zip", '
                              '"records": "<同目录申请记录>", "cases": <覆盖用例数>}')
            else:
                for item in records:
                    if not isinstance(item, dict):
                        errors.append(f"waiverRecords 每项应为对象，收到 {type(item).__name__}")
                        continue
                    cases = item.get("cases")
                    if cases is not None and not isinstance(cases, int):
                        errors.append("waiverRecords[*].cases 必须是整数（覆盖用例数）")
                    for key in ("txt", "records"):
                        if not item.get(key):
                            warnings.append(
                                f"waiverRecords 项缺 {key}（建议写全 txt / zip / records / cases）")
        return errors, warnings
    if stage == "report":
        report_dir = artifacts.get("reportDir")
        if report_dir is not None and (not isinstance(report_dir, str) or not report_dir):
            errors.append(f"reportDir 必须是非空字符串，收到 {report_dir!r}")
        return errors, warnings
    if stage != "triage":
        return errors, warnings

    failed_cases = artifacts.get("failedCases")
    if failed_cases is not None:
        if not isinstance(failed_cases, list):
            errors.append(
                f"failedCases 必须是清单（如 [\'ActsUdmfTest#case_0100\']），"
                f"收到 {type(failed_cases).__name__}={failed_cases!r}；"
                f"只想记数量时请写 modules.<模块>.failed"
            )
        else:
            for item in failed_cases:
                if not isinstance(item, str) or "#" not in item:
                    errors.append(f"failedCases 每项应为 '<模块名>#<用例名>'，收到 {item!r}")

    failed_modules = artifacts.get("failedModules")
    if failed_modules is not None:
        if not isinstance(failed_modules, list):
            errors.append(f"failedModules 必须是模块名清单，收到 {type(failed_modules).__name__}")
        else:
            for name in failed_modules:
                if not isinstance(name, str) or not MODULE_NAME_PATTERN.match(name):
                    errors.append(f"failedModules 含非法模块名: {name!r}")
                elif looks_like_test_class(name):
                    warnings.append(f"failedModules 里的 {name!r} 小写开头，"
                                    "看起来是测试类名（报告 <testsuite name>）；xdevice -l 要的是模块名")

    modules = artifacts.get("modules")
    if modules is not None:
        if not isinstance(modules, dict):
            errors.append("modules 必须是 {模块名: {tests, failed, cases}} 对象，"
                          f"收到 {type(modules).__name__}")
        else:
            for name, info in modules.items():
                if not isinstance(name, str) or not MODULE_NAME_PATTERN.match(name):
                    errors.append(f"modules 的键不是合法模块名: {name!r}")
                    continue
                if looks_like_test_class(name):
                    warnings.append(f"modules 的键 {name!r} 小写开头，看起来是测试类名")
                if not isinstance(info, dict):
                    errors.append(f"modules[{name!r}] 必须是对象（tests/failed/cases）")
                    continue
                if "failed" in info and not isinstance(info["failed"], int):
                    errors.append(f"modules[{name!r}].failed 必须是整数")
                if "cases" in info and not isinstance(info["cases"], list):
                    errors.append(f"modules[{name!r}].cases 必须是清单")

    if failed_modules is None and modules is None:
        warnings.append(
            "未登记 failedModules/modules：回归阶段将没有可重跑的测试项（会以 INCONCLUSIVE 结束）；"
            "请写入 failedModules（xdevice -l 用的模块名）"
        )
    return errors, warnings


def stage_index(stage: str) -> int:
    return STAGE_ORDER.index(stage) if stage in STAGE_ORDER else -1


def regression_conclusion(value: object) -> tuple[bool, str]:
    """回归结论能否判为「通过」。

    phase-3 的状态口径是 PASS / FAIL / INCONCLUSIVE（openharmony-ci-orchestrator 的
    test_harness.verdict）；INCONCLUSIVE 不算通过，除非本轮显式声明 smokeOnly。
    """
    if not isinstance(value, dict):
        return False, "未登记 regressionResult"
    status = str(value.get("status") or value.get("verdict") or "").upper()
    if status in ("PASS", "PASSED", "OK"):
        return True, status
    failed, total = value.get("failed"), value.get("total")
    if isinstance(failed, int) and failed == 0 and isinstance(total, int) and total > 0:
        return True, "failed=0"
    if status == "INCONCLUSIVE" and value.get("smokeOnly") is True:
        return True, "smoke-only"
    return False, f"status={status or '未写'}"


def check_advance(state: dict, stage: str, artifacts: dict, force: bool) -> list[str]:
    """阶段闸门：空转、越级/乱回退、done 判据。返回阻塞性错误（force 只能越过前两类）。"""
    merged = {**(state.get("artifacts") or {}), **artifacts}
    current = state.get("stage")
    errors: list[str] = []
    if stage == "done":
        ok_regression, why = regression_conclusion(merged.get("regressionResult"))
        if not merged.get("waiverRecords") and not ok_regression:
            errors.append(
                "done 需要「回归通过」或「豁免记录」之一；"
                f"当前回归结论：{why}。只能跑 smoke 时写 "
                'regressionResult {"status": "INCONCLUSIVE", "smokeOnly": true} 明示降级')
        if merged.get("mrIid") and not ok_regression:
            errors.append(
                "本轮走过 MR（fix→mr 分支），done 必须带通过的 regressionResult；回归没通过不能宣布闭环")
    if force:
        return errors
    required = STAGE_REQUIRED.get(stage)
    if required and not any(merged.get(key) for key in required):
        errors.append(f"{stage} 阶段缺少核心产物（{' / '.join(required)}）；"
                      "只想记事件请用 note，不要用 set 推进")
    # report/triage/waiver 都允许在空状态上开（续跑可能从任一条腿接上）；
    # mr 及之后才要求前序主线阶段已登记，防止「没做 fix 就直接宣布 CI 通过」。
    if stage != "done" and stage_index(stage) > stage_index("waiver"):
        # 前序主线阶段必须登记过核心产物，不能一步跳到 ota/regression 假装做过；
        # fix 与 waiver 是并行的两条腿，任一登记即可，故不参与该校验。
        for earlier in STAGE_ORDER[:stage_index(stage)]:
            keys = STAGE_REQUIRED.get(earlier)
            if not keys or earlier in BRANCH_STAGES:
                continue
            if not any(merged.get(key) for key in keys):
                errors.append(f"不能跳到 {stage}：前序阶段 {earlier} 未登记"
                              f"（{' / '.join(keys)}）")
                break
    if current in STAGE_ORDER:
        if stage_index(stage) < stage_index(current) and current not in BACKTRACKS.get(stage, ()):
            errors.append(f"不能从 {current} 回退到 {stage}"
                          f"（允许的回退目标：{sorted(BACKTRACKS)}）；确需如此请加 --force")
    return errors


def archive_previous_round(state: dict, new_report_dir: str) -> None:
    """换了一份报告：上一轮的 stage/产物/事件归档进 runs[]，避免两轮产物串在一起。

    原先状态只有一个扁平 artifacts 袋、`set` 直接 update 合并——第二轮开始后仍能看到
    第一轮的 mrIid / otaSerial，误判「已经做过」。归档后当前轮从干净状态重来。
    """
    old = (state.get("artifacts") or {}).get("reportDir")
    if not old or old == new_report_dir:
        return
    run_id = Path(str(old)).name or "previous"
    runs = state.setdefault("runs", {})
    runs[run_id] = {
        "archivedAt": datetime.now(timezone.utc).isoformat(),
        "stage": state.get("stage"),
        "reportDir": old,
        "artifacts": state.get("artifacts") or {},
    }
    for stale in list(runs)[:-5]:
        runs.pop(stale, None)
    state["stage"] = None
    state["artifacts"] = {}
    state["events"] = []
    print(f"note: 上一轮（{run_id}）已归档到 runs[]，当前轮从空状态开始", file=sys.stderr)


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
    errors, warnings = validate_stage_artifacts(args.stage, artifacts)
    report_dir = artifacts.get("reportDir")
    if (args.stage == "report" and not args.force and isinstance(report_dir, str) and report_dir
            and (report_dir.startswith("/") or report_dir.startswith("~"))
            and not Path(report_dir).expanduser().is_dir()):
        # 只校验本机可判定的 POSIX 形态；Windows 盘符 / UNC 形态交给调用方。
        errors.append(f"reportDir 不存在或不是目录: {report_dir}"
                      "（路径失效后流水线无法续跑；确认后可用 --force 越过）")
    errors.extend(check_advance(state, args.stage, artifacts, args.force))
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        print("error: 阶段推进被拒，未写入（契约见 `pipeline_state.py describe`）", file=sys.stderr)
        return 2
    if args.stage == "report" and isinstance(report_dir, str):
        archive_previous_round(state, report_dir)
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

    p_describe = sub.add_parser("describe", help="输出阶段推进契约（JSON，供 skill/文档派生）")

    p_set = sub.add_parser("set", help="设置阶段并合并产物")
    with_file(p_set)
    p_set.add_argument("stage", help="阶段名（report/triage/fix/waiver/mr/ci/ota/regression/done）")
    p_set.add_argument("artifacts_json", help="产物 JSON 对象，如 '{\"mrIid\": 168}'")
    p_set.add_argument("--force", action="store_true",
                       help="越过空转/回退校验（done 判据不可越过）")
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

    if args.command == "describe":
        return cmd_describe()

    path = state_file(args.file)

    if args.command == "reset":
        try:
            load(path)
        except (ValueError, OSError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        save(path, empty())
        print(f"已重置流水线状态: {path}")
        return 0

    try:
        state = load(path)
    except (ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
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


def cmd_describe() -> int:
    """输出阶段推进契约。skill 正文、操作手册、README 的流程图都从这一份派生。

    为什么单独做成子命令：原先「阶段顺序/必需产物/终态判据」散落在 skill 正文、
    操作手册和脚本三处，改一处忘两处——waiver 就是这么裂开的（正文有、脚本没有）。
    """
    contract = {
        "schemaVersion": SCHEMA_VERSION,
        "order": list(STAGE_ORDER),
        "branches": list(BRANCH_STAGES),
        "backtracks": {stage: list(targets) for stage, targets in BACKTRACKS.items()},
        "required": {stage: list(keys) for stage, keys in STAGE_REQUIRED.items()},
        "doneRequires": {
            "anyOf": ["waiverRecords", "regressionResult"],
            "regressionPass": ["status=PASS", "failed=0 且 total>0", "status=INCONCLUSIVE 且 smokeOnly=true"],
            "withMr": "有 mrIid 时 regressionResult 必须判为通过",
        },
    }
    print(json.dumps(contract, ensure_ascii=False, indent=2))
    return 0


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
