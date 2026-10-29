#!/usr/bin/env python3
"""演示模式（demo mode）状态机：预检、开演、按阶段推进、状态卡、清单。

演示模式的意义：把八阶段闭环（report → triage → fix → mr → ci → ota →
regression → done）压进 10 分钟，并且**不污染真实流水线状态**。所有演示数据
落在独立命名空间（默认 ~/.dsh/demo/），真实状态文件
（~/.dsh/pipeline-state.json）与 ci-orchestrator 状态目录只读不写。

产物三态（每个阶段必须显式声明其一，状态卡与口播用同一套词）：
  live          现场真跑（命令正在跑）
  local-staged  本地准备/替代（例如用本地出包替代 40 分钟平台门禁）
  recorded      历史回放（真实跑过的结果，现场只展示）

用法:
  democtl.py preflight  [--case mr260]
  democtl.py start      --case mr260 [--report PATH] [--lane a|b|c]
  democtl.py stage      NAME [--provenance live|local-staged|recorded]
                        [--note TEXT] [--evidence PATH] [--degraded]
  democtl.py next       [--case mr260]
  democtl.py status     [--json]
  democtl.py checklist  [--case mr260]
  democtl.py snapshot   --mr [--case mr260]
  democtl.py reset      [--yes]

退出码: 0 成功；1 预检有 FAIL 或阶段越序；2 前置数据缺失（case/状态文件）。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
DEMO_DIR = Path(os.environ.get("DSH_DEMO_DIR", Path.home() / ".dsh" / "demo"))
CASES_DIR = Path(__file__).resolve().parent / "cases"

#: 八阶段固定顺序。任何「跳阶段」都是 bug，不是灵活性。
STAGES = ("report", "triage", "fix", "mr", "ci", "ota", "regression", "done")
#: 阶段产物来源三态。
PROVENANCE = ("live", "local-staged", "recorded")
#: 三条演示线：a=10 分钟快线（默认），b=全链加演，c=无设备/无网络兜底。
LANES = ("a", "b", "c")


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d-%H-%M-%S")


def state_path() -> Path:
    return DEMO_DIR / "state.json"


def run_dir_path(run_id: str) -> Path:
    return DEMO_DIR / "runs" / run_id


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                     prefix=".tmp-", delete=False) as handle:
        handle.write(text)
        temp = Path(handle.name)
    os.replace(temp, path)


def write_json(path: Path, payload: dict) -> None:
    atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run(cmd: list[str], timeout: int = 60, cwd: str | None = None) -> subprocess.CompletedProcess:
    """执行外部命令；永不抛异常，调用方按 returncode 判断。"""
    try:
        return subprocess.run(cmd, cwd=cwd, timeout=timeout, capture_output=True,
                              text=True, errors="replace")
    except (OSError, subprocess.SubprocessError) as error:  # 设备/工具缺失是常态
        return subprocess.CompletedProcess(cmd, 127, "", str(error))


# --------------------------------------------------------------------------- #
# 用例文件
# --------------------------------------------------------------------------- #
def load_case(case_id: str) -> dict:
    path = CASES_DIR / f"{case_id}.json"
    if not path.is_file():
        raise SystemExit(f"用例文件不存在: {path}")
    case = load_json(path)
    missing = [key for key in ("id", "report", "triage", "fix", "mr", "gate",
                               "ota", "regression") if key not in case]
    if missing:
        raise SystemExit(f"用例 {case_id} 缺少字段: {', '.join(missing)}")
    return case


def default_case_id() -> str:
    state = load_state(required=False)
    if state:
        return state["case"]
    names = sorted(path.stem for path in CASES_DIR.glob("*.json"))
    if not names:
        raise SystemExit(f"{CASES_DIR} 下没有任何用例文件")
    return names[0]


# --------------------------------------------------------------------------- #
# 状态文件
# --------------------------------------------------------------------------- #
def load_state(required: bool = True) -> dict | None:
    path = state_path()
    if not path.is_file():
        if required:
            raise SystemExit(f"演示未开始（缺 {path}）；先跑: democtl.py start --case mr260")
        return None
    return load_json(path)


def new_stage(stage: str) -> dict:
    return {"status": "pending", "provenance": None, "startedAt": None,
            "finishedAt": None, "notes": [], "evidence": []}


def save_state(state: dict) -> None:
    state["updatedAt"] = utc_now()
    write_json(state_path(), state)


def elapsed_seconds(state: dict) -> int:
    started = state.get("startedAt")
    if not started:
        return 0
    try:
        begin = datetime.strptime(started, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return 0
    return int((datetime.now(timezone.utc) - begin).total_seconds())


def stage_seconds(record: dict) -> int | None:
    if not (record.get("startedAt") and record.get("finishedAt")):
        return None
    try:
        begin = datetime.strptime(record["startedAt"], "%Y-%m-%dT%H:%M:%SZ")
        end = datetime.strptime(record["finishedAt"], "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return int((end - begin).total_seconds())


def fmt_seconds(seconds: int | None) -> str:
    if seconds is None:
        return "  -  "
    return f"{seconds // 60}:{seconds % 60:02d}"


# --------------------------------------------------------------------------- #
# 预检
# --------------------------------------------------------------------------- #
PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


def _check_report(case: dict, report_override: str | None) -> tuple[str, str, str]:
    raw = report_override or case["report"]["path"]
    report = Path(raw).expanduser()
    if not report.is_dir():
        return FAIL, "报告目录", f"不存在: {report}"
    result = report / "result"
    if not result.is_dir() or not list(result.glob("*.xml")):
        return WARN, "报告目录", f"存在但没有 result/*.xml: {report}"
    expect = case["report"].get("expect") or {}
    if expect:
        return PASS, "报告目录", f"{report.name}（期望 {expect.get('total')} 例 / 失败 {expect.get('failed')}）"
    return PASS, "报告目录", str(report)


def _check_fix(case: dict) -> tuple[str, str, str]:
    patch = Path(case["fix"]["patch"]).expanduser()
    build = Path(case["fix"]["build"]).expanduser()
    problems = [str(path) for path in (patch, build) if not path.is_file()]
    if problems:
        return FAIL, "修复产物", f"缺失: {', '.join(problems)}"
    return PASS, "修复产物", f"{patch.name} + {build.name}（{build.stat().st_size // 1024 // 1024} MiB）"


def _check_gate_package(case: dict) -> tuple[str, str, str]:
    package = Path(case["gate"]["package"]).expanduser()
    if not package.is_file():
        return FAIL, "OTA 包", f"不存在: {package}"
    if not zipfile.is_zipfile(package):
        return FAIL, "OTA 包", f"不是合法 ZIP: {package}"
    size = package.stat().st_size / 1024 / 1024
    return PASS, "OTA 包", f"{package.name}（{size:.0f} MiB，{case['gate'].get('version', '?')}）"


def _check_gate_script(case: dict) -> tuple[str, str, str]:
    script = Path(__file__).resolve().parent.parent.parent / \
        "skills/openharmony-ota-upgrade/scripts/ota_preflight.sh"
    if not script.is_file():
        return FAIL, "OTA 预检脚本", f"缺失: {script}"
    return PASS, "OTA 预检脚本", script.name


def _check_device(case: dict) -> tuple[str, str, str]:
    serial = case["fix"].get("device")
    if not serial:
        return WARN, "目标设备", "用例未指定设备（lane c 兜底）"
    listing = run(["hdc", "list", "targets"], timeout=30)
    if listing.returncode != 0:
        return WARN, "目标设备", "hdc 不可用（lane c 兜底）"
    if serial not in listing.stdout:
        return WARN, "目标设备", f"{serial} 不在线（lane c 兜底）"
    probe = run(["hdc", "-t", serial, "shell",
                 "param get const.product.software.version; "
                 "sha256sum /system/lib64/platformsdk/libace_compatible.z.so"],
                timeout=45)
    text = " ".join(probe.stdout.split())
    return PASS, "目标设备", f"{serial} · {text[:110]}"


def _check_runner(case: dict) -> tuple[str, str, str]:
    regression = case["regression"]
    runner_dir = Path(regression["runner_dir"]).expanduser()
    if not runner_dir.is_dir():
        return FAIL, "回归运行器", f"不存在: {runner_dir}"
    config = runner_dir / "config/user_config.xml"
    module_json = runner_dir / "testcases" / f"{regression['module']}.json"
    problems = [str(path) for path in (config, module_json) if not path.is_file()]
    if problems:
        return FAIL, "回归运行器", f"缺失: {', '.join(problems)}"
    return PASS, "回归运行器", f"{runner_dir.name} · {regression['module']}"


def _check_gitlab(case: dict) -> tuple[str, str, str]:
    """MR 段能否读实时数据；失败不算硬故障，只是要切快照。"""
    host = (case["mr"].get("host") or "").strip()
    project = case["mr"]["project"].replace("/", "%2F")
    auth = run(["glab", "api", "--hostname", host, "user"] if host else ["glab", "api", "user"],
               timeout=45)
    if auth.returncode != 0:
        detail = (auth.stderr or auth.stdout or "").strip().splitlines()
        reason = detail[0] if detail else "unknown"
        if "401" in reason or "revoked" in reason.lower() or "Unauthorized" in reason:
            return WARN, "GitLab", f"令牌失效 → MR 段用快照（{reason[:70]}）"
        return WARN, "GitLab", f"只读探测失败 → MR 段用快照（{reason[:70]}）"
    probe = run(["glab", "api", "--hostname", host, f"projects/{project}"] if host
                else ["glab", "api", f"projects/{project}"], timeout=45)
    if probe.returncode != 0:
        detail = (probe.stderr or probe.stdout or "").strip().splitlines()
        return WARN, "GitLab", f"项目不可见 → MR 段用快照（{(detail[0] if detail else '?')[:70]}）"
    return PASS, "GitLab", "glab 可读（令牌有效、项目可见）"


def _check_disk(case: dict) -> tuple[str, str, str]:
    target = Path(case["gate"]["package"]).expanduser().parent
    try:
        usage = shutil.disk_usage(target)
    except OSError as error:
        return WARN, "磁盘余量", str(error)
    free_gib = usage.free / 1024 / 1024 / 1024
    return (PASS if free_gib >= 2 else WARN), "磁盘余量", f"{free_gib:.1f} GiB 可用（{target}）"


CHECKS = (_check_report, _check_fix, _check_gate_package, _check_gate_script,
          _check_device, _check_runner, _check_gitlab, _check_disk)


def preflight(case_id: str, report_override: str | None = None) -> tuple[list[tuple[str, str, str]], int]:
    case = load_case(case_id)
    results = []
    for check in CHECKS:
        if check is _check_report:
            results.append(check(case, report_override))
        else:
            results.append(check(case))
    failures = sum(1 for level, _, _ in results if level == FAIL)
    return results, failures


def render_preflight(results: list[tuple[str, str, str]]) -> str:
    icons = {PASS: "[PASS]", WARN: "[WARN]", FAIL: "[FAIL]"}
    lines = ["演示模式预检", "-" * 66]
    for level, label, detail in results:
        lines.append(f"{icons[level]:<7} {label:<12} {detail}")
    fails = sum(1 for level, _, _ in results if level == FAIL)
    warns = sum(1 for level, _, _ in results if level == WARN)
    lines.append("-" * 66)
    lines.append(f"结论: {fails} FAIL / {warns} WARN / {len(results) - fails - warns} PASS"
                 + ("" if fails == 0 else "  ← 先修 FAIL 再开演"))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------------- #
def cmd_preflight(args: argparse.Namespace) -> int:
    results, failures = preflight(args.case, args.report)
    print(render_preflight(results))
    return 1 if failures else 0


def cmd_start(args: argparse.Namespace) -> int:
    case = load_case(args.case)
    report = args.report or case["report"]["path"]
    if not Path(report).expanduser().is_dir():
        raise SystemExit(f"报告目录不存在: {report}")

    run_id = local_stamp()
    run_dir = run_dir_path(run_id)
    (run_dir / "evidence").mkdir(parents=True, exist_ok=True)

    state = {
        "schemaVersion": SCHEMA_VERSION,
        "case": case["id"],
        "title": case.get("title", case["id"]),
        "lane": args.lane,
        "stage": STAGES[0],
        "degraded": False,
        "startedAt": utc_now(),
        "updatedAt": utc_now(),
        "runId": run_id,
        "runDir": str(run_dir),
        "reportDir": str(Path(report).expanduser()),
        "stages": {stage: new_stage(stage) for stage in STAGES},
        "artifacts": {},
        "events": [],
    }
    save_state(state)
    write_json(run_dir / "case.json", case)

    print(f"演示已开始: {case['id']} · lane {args.lane} · run {run_id}")
    print(f"报告: {state['reportDir']}")
    print(f"产物: {run_dir}")
    print(f"下一步: democtl.py next")
    return 0


def next_stage(state: dict) -> str | None:
    for stage in STAGES:
        if state["stages"][stage]["status"] != "done":
            return stage
    return None


def cmd_stage(args: argparse.Namespace) -> int:
    state = load_state()
    stage = args.name
    if stage not in STAGES:
        raise SystemExit(f"未知阶段 {stage}；支持: {' | '.join(STAGES)}")
    if args.provenance and args.provenance not in PROVENANCE:
        raise SystemExit(f"未知产物来源 {args.provenance}；支持: {' | '.join(PROVENANCE)}")

    expected = next_stage(state)
    current = state["stages"][stage]
    if stage != expected and current["status"] != "active" and not args.force:
        raise SystemExit(
            f"阶段越序：当前应推进 {expected}，收到 {stage}（确实要跳阶段请加 --force）")

    if args.degraded:
        state["degraded"] = True
    if args.provenance:
        current["provenance"] = args.provenance
    elif not current["provenance"]:
        current["provenance"] = "live"
    if current["status"] != "active":
        current["startedAt"] = current["startedAt"] or utc_now()
    if args.note:
        current["notes"].append(args.note)
    if args.evidence:
        evidence = Path(args.evidence).expanduser()
        if not evidence.exists():
            raise SystemExit(f"证据文件不存在: {evidence}")
        current["evidence"].append(str(evidence))
    if args.status == "done":
        current["finishedAt"] = utc_now()
    current["status"] = args.status

    state["events"].append({"at": utc_now(), "stage": stage,
                            "note": args.note or args.status})
    state["stage"] = stage if args.status != "done" else (next_stage(state) or "done")
    save_state(state)
    print(f"[{stage}] {args.status} · {current['provenance']}"
          + (f" · {fmt_seconds(stage_seconds(current))}" if stage_seconds(current) else ""))
    return 0


def cmd_next(args: argparse.Namespace) -> int:
    state = load_state()
    case = load_case(state["case"])
    stage = next_stage(state)
    if stage is None:
        print("八阶段已全部完成。")
        return 0
    cue = (case.get("cues") or {}).get(stage) or {}
    print(f"下一阶段: {stage}（lane {state['lane']}）")
    if cue.get("title"):
        print(f"  目标: {cue['title']}")
    if cue.get("budget_s"):
        print(f"  预算: {cue['budget_s']} 秒")
    if cue.get("say_mgr"):
        print(f"  管理口径: {cue['say_mgr']}")
    if cue.get("say_dev"):
        print(f"  研发口径: {cue['say_dev']}")
    if cue.get("command"):
        print(f"  命令: {cue['command']}")
    if cue.get("provenance"):
        print(f"  来源标注: {cue['provenance']}（必须向听众说明）")
    return 0


def render_status(state: dict) -> str:
    total = elapsed_seconds(state)
    planned = (load_case(state["case"]).get("budgets") or {}).get("total_s", 600)
    lines = [
        f"演示模式 · {state['case']} · lane {state['lane']}"
        + (" · DEGRADED" if state.get("degraded") else ""),
        f"用时 {fmt_seconds(total)} / 预算 {fmt_seconds(planned)}"
        f"（{state['runId']}）",
        "-" * 66,
    ]
    for stage in STAGES:
        record = state["stages"][stage]
        icon = {"done": "[x]", "active": "[>]", "pending": "[ ]", "skipped": "[~]"}.get(
            record["status"], "[ ]")
        provenance = record.get("provenance") or "-"
        duration = fmt_seconds(stage_seconds(record))
        notes = ("  " + record["notes"][-1]) if record["notes"] else ""
        lines.append(f"{icon} {stage:<11} {provenance:<13} {duration}{notes}")
    counts = {key: 0 for key in PROVENANCE}
    for record in state["stages"].values():
        if record.get("provenance") in counts:
            counts[record["provenance"]] += 1
    lines.append("-" * 66)
    lines.append("产物来源: " + " / ".join(f"{k} {v}" for k, v in counts.items()))
    artifacts = state.get("artifacts") or {}
    if artifacts:
        shown = list(artifacts.items())[-4:]
        lines.append("关键产物: " + " · ".join(f"{k}={str(v)[:48]}" for k, v in shown))
    return "\n".join(lines)


def cmd_status(args: argparse.Namespace) -> int:
    state = load_state()
    if args.json:
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return 0
    print(render_status(state))
    return 0


def cmd_checklist(args: argparse.Namespace) -> int:
    results, failures = preflight(args.case)
    print("演出前 5 分钟清单")
    print("=" * 66)
    print(render_preflight(results))
    print("=" * 66)
    print("现场动作：")
    print("  1. 投屏分辨率 ≥ 1920x1080，终端字号调到后排能看清")
    print("  2. 设备解锁、亮度固定、关闭自动旋转")
    print("  3. 关闭无关通知（演示机与宿主都关）")
    print("  4. 打开 Jenkins 构建页与 MR 页面两个标签页待切换")
    print("  5. lane b/c 的兜底证据目录已就位（regression 预置结果）")
    return 1 if failures else 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    state = load_state()
    case = load_case(state["case"])
    run_dir = Path(state["runDir"])
    if not args.mr:
        raise SystemExit("当前只支持 --mr")
    project = case["mr"]["project"]
    iid = case["mr"]["iid"]
    host = case["mr"].get("host")
    api = f"projects/{project.replace('/', '%2F')}/merge_requests/{iid}"
    probe = run(["glab", "api", "--hostname", host, api] if host else ["glab", "api", api],
                timeout=60)
    payload: dict = {"at": utc_now(), "mr": case["mr"], "source": "case"}
    if probe.returncode == 0:
        try:
            live = json.loads(probe.stdout)
            payload.update({
                "source": "live",
                "title": live.get("title"),
                "state": live.get("state"),
                "sha": live.get("sha"),
                "target_branch": live.get("target_branch"),
                "web_url": live.get("web_url"),
                "detailed_merge_status": live.get("detailed_merge_status"),
            })
        except json.JSONDecodeError:
            payload["error"] = "glab api 返回非 JSON"
    else:
        payload["error"] = (probe.stderr or probe.stdout or "glab api failed").strip()[:300]
    write_json(run_dir / "evidence" / "mr-snapshot.json", payload)
    print(f"MR 快照已写入 {run_dir / 'evidence' / 'mr-snapshot.json'}（来源: {payload['source']}）")
    return 0 if payload["source"] == "live" else 2


def cmd_reset(args: argparse.Namespace) -> int:
    path = state_path()
    if not path.is_file():
        print("没有进行中的演示。")
        return 0
    if not args.yes:
        print(f"将删除 {path}（runs/ 目录保留）。确认请加 --yes")
        return 2
    path.unlink()
    print(f"已删除 {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="演示模式状态机")
    sub = parser.add_subparsers(dest="command", required=True)

    pre = sub.add_parser("preflight", help="演出前预检")
    pre.add_argument("--case", default=None)
    pre.add_argument("--report", default=None)
    pre.set_defaults(func=cmd_preflight)

    start = sub.add_parser("start", help="开始一次演示")
    start.add_argument("--case", required=True)
    start.add_argument("--report", default=None)
    start.add_argument("--lane", default="a", choices=LANES)
    start.set_defaults(func=cmd_start)

    stage = sub.add_parser("stage", help="推进/记录一个阶段")
    stage.add_argument("name")
    stage.add_argument("--status", default="done", choices=("active", "done", "skipped"))
    stage.add_argument("--provenance", default=None)
    stage.add_argument("--note", default=None)
    stage.add_argument("--evidence", default=None)
    stage.add_argument("--degraded", action="store_true")
    stage.add_argument("--force", action="store_true")
    stage.set_defaults(func=cmd_stage)

    nxt = sub.add_parser("next", help="打印下一阶段的口播与命令")
    nxt.add_argument("--case", default=None)
    nxt.set_defaults(func=cmd_next)

    status = sub.add_parser("status", help="状态卡")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)

    checklist = sub.add_parser("checklist", help="演出前 5 分钟清单")
    checklist.add_argument("--case", default=None)
    checklist.set_defaults(func=cmd_checklist)

    snapshot = sub.add_parser("snapshot", help="把实时事实录成快照（断网时用）")
    snapshot.add_argument("--mr", action="store_true")
    snapshot.set_defaults(func=cmd_snapshot)

    reset = sub.add_parser("reset", help="清掉进行中的演示状态")
    reset.add_argument("--yes", action="store_true")
    reset.set_defaults(func=cmd_reset)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "case", None) is None and args.command in ("preflight", "checklist"):
        args.case = default_case_id()
    elif getattr(args, "case", None) is None and args.command == "next":
        args.case = default_case_id()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
