#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GitLab Issue 驱动看板流水线 CLI(openharmony-issue-pipeline 子插件配套脚本)。

职责:
- 定期/手动从 GitLab 项目抓取 Assignee=cx 的待解决 issue;
- 按标签映射工作区/分支、按 Due date 分级模型档位(含图片强制视觉模型);
- 与 DSH 任务看板(@linxin666/dsh-client-ui-task-board)账本比对,幂等建卡/移动/执行;
- 处理会话用 stage 命令同步卡片状态与 issue 评论;人工确认后 close 关闭 issue。

安全:
- GitLab 令牌只从 ~/.dsh/gitlab-credentials.json(0600)读取,绝不打印/落日志;
- 看板 API 仅本机 loopback(127.0.0.1:3080)+ Origin 同源标记;
- 所有命令支持 --dry-run(只读,不向 GitLab/看板写任何数据)。

用法示例:
  python3 issue_pipeline.py init-config
  python3 issue_pipeline.py sync --dry-run
  python3 issue_pipeline.py sync
  python3 issue_pipeline.py stage 123 progress 已完成定位,开始修复
  python3 issue_pipeline.py close 123 --confirm
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import logging
import os
import re
import sys
import time
import urllib.parse
import uuid
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# 常量与路径
# ---------------------------------------------------------------------------

DEFAULT_GITLAB_BASE_URL = "http://192.168.11.238"
DEFAULT_GITLAB_PROJECT = "harmony/system/v9611/openharmony_V6.1"
DEFAULT_GITLAB_ASSIGNEE = "cx"
DEFAULT_BOARD_BASE_URL = "http://127.0.0.1:3080"

DSH_HOME = Path(os.environ.get("DSH_HOME", Path.home() / ".dsh"))
PIPELINE_HOME = DSH_HOME / "issue-pipeline"
CONFIG_FILE = PIPELINE_HOME / "config.json"
ISSUES_DIR = PIPELINE_HOME / "issues"
RUNS_DIR = PIPELINE_HOME / "runs"
LOG_FILE = PIPELINE_HOME / "issue_pipeline.log"
CREDS_FILE = DSH_HOME / "gitlab-credentials.json"
WORKSPACE_FILE = DSH_HOME / "storages" / "workspace.json"

BOARD_ORIGIN_HEADER = "Origin"
TASK_STATUSES = ("backlog", "todo", "running", "done", "failed")
STAGE_TO_STATUS = {"done": "done", "failed": "failed", "blocked": "todo"}

# 看板任务描述中的稳定标记行(sync 幂等依据)
IID_MARKER_RE = re.compile(r"issue-gitlab-iid:\s*(\d+)")
SWEEPER_MARKER = "issue-pipeline-sweeper: 1"

LOG = logging.getLogger("issue-pipeline")

# ---------------------------------------------------------------------------
# 默认配置
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "gitlab": {
        "baseUrl": DEFAULT_GITLAB_BASE_URL,
        "project": DEFAULT_GITLAB_PROJECT,
        "assignee": DEFAULT_GITLAB_ASSIGNEE,
        "perPage": 100,
    },
    "board": {
        "baseUrl": DEFAULT_BOARD_BASE_URL,
        "sweepCron": "*/30 * * * *",
        "sweepTaskTitle": "GitLab 问题流水线轮询",
    },
    # 工作区 key → 目录(必须已在 DSH 工作区列表登记)
    "workspaces": {
        "6.1": "/home/cx/os/6.1",
        "6.1_rk3568": "/home/cx/os/6.1_rk3568",
    },
    "branchDefaults": {
        "6.1": "v6.1.0.31_release",
        "6.1_rk3568": "V6.1.0.35_LTS",
    },
    # 标签 → 工作区 key;None 表示不决定工作区(由芯片标签决定)
    "labelWorkspace": {
        "RK3568": "6.1_rk3568",
        "A333/A537": "6.1",
        "通用框架层修改": "6.1",
        "应用修复": "6.1",
        "V6.1.0.31_Rlease": None,
        "V6.1.0.35_LTS": None,
        "XTS": None,
    },
    # 标签 → 分支
    "labelBranch": {
        "V6.1.0.31_Rlease": "v6.1.0.31_release",
        "V6.1.0.35_LTS": "V6.1.0.35_LTS",
    },
    "chipPriority": ["RK3568", "A333/A537"],
    # 档位按 maxDays 升序匹配(<=);maxDays 为 None 是兜底档(无 due 或更远)。
    # 主会话模型由宿主 agent-default-model 固定(预设级模型已被宿主拒绝),档位通过
    # subagentModel(子代理 model override)实现;preset 为可用的标准全量组合预设。
    "modelTiers": [
        {"maxDays": 2, "preset": "issue-urgent", "model": "deepseek-v4-pro",
         "subagentModel": "deepseek-v4-pro", "name": "urgent"},
        {"maxDays": 6, "preset": "issue-normal", "model": "deepseek-v4-flash-vision-exp",
         "subagentModel": "deepseek-v4-flash-vision-exp", "name": "normal"},
        {"maxDays": None, "preset": "issue-low", "model": "deepseek-v4-flash",
         "subagentModel": "deepseek-v4-flash", "name": "low"},
    ],
    "vision": {
        # el 描述含图片时强制视觉模型(唯一视觉档)
        "model": "deepseek-v4-flash-vision-exp",
        "preset": "issue-normal",
        "detectDescriptionImages": True,
        "detectDescriptionImgTag": True,
        "detectAttachments": True,
    },
    "modelTiering": "subagents",  # 生效模式:presets 被宿主拒绝后降级为子代理分档(见 SKILL.md)
    "autoRun": True,
    "commentPerStage": True,
    "closeOnDone": False,
    "requireApproval": False,
    "autoRetry": {"enabled": True, "afterHours": 6, "maxAttempts": 3},
    "descriptionCapBytes": 20480,
    "workspaceRegistryFile": str(WORKSPACE_FILE),
    "issuePromptTemplate": "",
    "sweeperPromptTemplate": "",
}

# 单值标量替换(只替换 {key} 形式的 token,未知 token 保留原样)
TOKEN_RE = re.compile(r"\{([a-zA-Z0-9_]+)\}")

DEFAULT_ISSUE_PROMPT = """# GitLab Issue #{iid} 自动处理任务
- 标题: {title}
- 链接: {url}
- 标签: {labels}  |  Due: {dueDate}  |  Assignee: {assignee}
- 完整材料: {cacheFile}(含完整 Description 与 hasImages 标记;看板卡片描述为截断版,以缓存文件为准)

请加载 skill openharmony-issue-pipeline 并严格按其「处理流程」执行本任务:
- 第一步(report):仅分析 issue Description(含附件/复现步骤)定位问题;只有 Description 明确引用测试报告路径时才按 openharmony-test-report-triage 处理报告
- 工作区/分支: {workspacePath}(workspace={workspaceKey}), 基线分支 {branch};在该仓库内创建独立 worktree: /home/cx/os/worktrees/issue-{iid}-<slug>,禁止直接改动主 worktree
- 模型档位: {tierName}(mode={modePreset});档位通过子代理实现:provider=deepseek-official model={subagentModel};主会话为部署默认(视觉模型,可读图);含图片的归因分析必须用视觉模型,参考缓存 hasImages
- 八阶段闭环(每阶段结束调用 `python3 {script} stage {iid} <progress|done|failed|blocked> <一句话摘要>` 同步看板与 issue 评论): report → triage → fix → mr → ci → ota → regression → done
- {authorization}
- 回归通过: 调用 `python3 {script} stage {iid} done <回归结论>`;完成后 issue 评论「修复完成,等待人工确认关闭」,不要自动关闭 issue(closeOnDone 保持 false)
- 失败/阻塞: `python3 {script} stage {iid} failed <原因>` 或 `stage {iid} blocked <设备缺失/CI未就绪说明>`(卡片回待办,等待下轮重试)
- 收尾: 用 todo_write 收尾,运行记录写入 ~/.dsh/issue-pipeline/runs/<日期>-{iid}.md"""

DEFAULT_SWEEPER_PROMPT = """你是 GitLab 问题流水线轮询器(任务看板定时任务,无需等待用户授权)。
1) 运行 `python3 {script} sync`(每轮自动建卡并 run/rerun;首次可先 `python3 {script} init-config`)
2) 检查输出:若出现 令牌失效/403/工作区未登记 等错误,在最终回复中明确向用户报告
3) 不要在本会话中处理具体 issue 本体;issue 处理由其独立会话承担
4) 最终回复给出本轮摘要:新增卡片数、启动执行数、重试数、错误数"""

# ---------------------------------------------------------------------------
# 配置读写
# ---------------------------------------------------------------------------


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"配置文件不存在: {CONFIG_FILE}(先执行 init-config)")
    try:
        user = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"配置 JSON 解析失败: {CONFIG_FILE}: {exc}") from exc
    return deep_merge(DEFAULT_CONFIG, user)


def cmd_init_config(args, config: dict | None = None) -> int:
    if CONFIG_FILE.exists():
        print(f"配置已存在,跳过: {CONFIG_FILE}")
        return 0
    PIPELINE_HOME.mkdir(parents=True, exist_ok=True)
    ISSUES_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"配置已生成: {CONFIG_FILE}(可在其中覆盖 标签映射/档位表/轮询策略)")
    return 0


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------


def setup_logging() -> None:
    PIPELINE_HOME.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    )
    LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)
    # 令牌等敏感信息绝不进入日志:调用点自行脱敏


# ---------------------------------------------------------------------------
# 凭据与 HTTP
# ---------------------------------------------------------------------------


def gitlab_token(config: dict) -> str:
    host = urllib.parse.urlparse(config["gitlab"]["baseUrl"]).hostname
    try:
        data = json.loads(CREDS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"读取 GitLab 凭据失败: {CREDS_FILE}({exc})") from exc
    hosts = data.get("hosts", {})
    for key, entry in hosts.items():
        if not isinstance(entry, dict):
            continue
        if host in (key, entry.get("host"), entry.get("apiHost")):
            token = entry.get("token")
            if token:
                return token
    raise RuntimeError(
        f"未找到主机 {host} 的 GitLab 令牌;请在 DSH 设置 → GitLab 凭据 中保存"
    )


class GitLabAPI:
    def __init__(self, config: dict):
        self.base = config["gitlab"]["baseUrl"].rstrip("/")
        self.project = config["gitlab"]["project"]
        self.token = gitlab_token(config)
        self.timeout = 15

    def _headers(self) -> dict:
        return {"PRIVATE-TOKEN": self.token}

    def _url(self, path: str) -> str:
        return f"{self.base}/api/v4/projects/{urllib.parse.quote(self.project, safe='')}{path}"

    def opened_issues(self, assignee: str, per_page: int) -> list[dict]:
        issues: list[dict] = []
        page = 1
        while True:
            resp = requests.get(
                self._url("/issues"),
                params={
                    "state": "opened",
                    "assignee_username": assignee,
                    "per_page": per_page,
                    "page": page,
                },
                headers=self._headers(),
                timeout=self.timeout,
            )
            resp.raise_for_status()
            batch = resp.json()
            issues.extend(batch)
            if len(batch) < per_page:
                break
            page += 1
            if page > 50:  # 防溢出
                break
        return issues

    def comment(self, iid: int, body: str) -> None:
        resp = requests.post(
            self._url(f"/issues/{iid}/notes"),
            json={"body": body},
            headers=self._headers(),
            timeout=self.timeout,
        )
        resp.raise_for_status()

    def close(self, iid: int) -> None:
        resp = requests.put(
            self._url(f"/issues/{iid}"),
            json={"state_event": "close"},
            headers=self._headers(),
            timeout=self.timeout,
        )
        resp.raise_for_status()


class BoardAPI:
    def __init__(self, config: dict):
        self.base = config["board"]["baseUrl"].rstrip("/")
        self.origin = self.base
        self.timeout = 15

    def _headers(self) -> dict:
        return {BOARD_ORIGIN_HEADER: self.origin, "Content-Type": "application/json"}

    def _get(self, path: str):
        resp = requests.get(f"{self.base}{path}", headers=self._headers(), timeout=self.timeout)
        if resp.status_code == 403:
            raise RuntimeError(
                f"任务看板 403(同源栅栏): 确认 DSH web 本机运行于 {self.base}"
            )
        resp.raise_for_status()
        return resp.json()

    def state(self) -> dict:
        return self._get("/api/task-board/state")

    def action(self, kind: str, payload: dict) -> dict:
        action = {"kind": kind}
        action.update(payload)
        envelope = {"requestId": uuid.uuid4().hex, "action": action}
        resp = requests.post(
            f"{self.base}/api/task-board/action",
            json=envelope,
            headers=self._headers(),
            timeout=self.timeout,
        )
        if resp.status_code == 403:
            raise RuntimeError(
                f"任务看板 403(同源栅栏): 确认 DSH web 本机运行于 {self.base}"
            )
        resp.raise_for_status()
        return resp.json()

    def create_task(self, task_id: str, input_: dict) -> dict:
        return self.action("create", {"id": task_id, "input": input_})

    def move(self, task_id: str, status: str) -> dict:
        return self.action("move", {"taskId": task_id, "status": status})

    def run(self, task_id: str) -> dict:
        return self.action("run", {"taskId": task_id})

    def rerun(self, task_id: str) -> dict:
        return self.action("rerun", {"taskId": task_id})

    def set_schedule(self, task_id: str, cron: str) -> dict:
        return self.action(
            "set-schedule", {"taskId": task_id, "patch": {"enabled": True, "cron": cron}}
        )

    def archive(self, task_id: str) -> dict:
        return self.action("archive", {"taskId": task_id})

    def delete(self, task_id: str) -> dict:
        return self.action("delete", {"taskId": task_id})


# ---------------------------------------------------------------------------
# 纯逻辑(可单测)
# ---------------------------------------------------------------------------

IMAGE_MARKDOWN_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
IMAGE_HTML_RE = re.compile(r"<img[^>]*>", re.IGNORECASE)
IMAGE_UPLOAD_RE = re.compile(
    r"/uploads/[^ )]+\.(?:png|jpe?g|gif|webp|bmp|svg|heic)", re.IGNORECASE
)


def has_images(issue: dict, config: dict) -> bool:
    vision = config.get("vision", {})
    desc = issue.get("description") or ""
    if vision.get("detectDescriptionImages", True) and IMAGE_MARKDOWN_RE.search(desc):
        return True
    if vision.get("detectDescriptionImgTag", True) and IMAGE_HTML_RE.search(desc):
        return True
    if vision.get("detectAttachments", True) and IMAGE_UPLOAD_RE.search(desc):
        return True
    return False


def days_to_due(due_date: str | None) -> int | None:
    if not due_date:
        return None
    try:
        due = dt.date.fromisoformat(due_date)
    except ValueError:
        return None
    return (due - dt.date.today()).days


def resolve_tier(due_date: str | None, config: dict) -> dict:
    """按 maxDays 升序匹配;无 due 或超出所有上限 → maxDays 为 None 的兜底档。"""
    days = days_to_due(due_date)
    tiers = sorted(
        config["modelTiers"],
        key=lambda t: (t["maxDays"] is None, t["maxDays"] if t["maxDays"] is not None else 0),
    )
    for tier in tiers:
        if tier.get("maxDays") is None and days is None:
            return tier
        if tier.get("maxDays") is not None and days is not None and days <= tier["maxDays"]:
            return tier
    for tier in tiers:  # 全部带 maxDays 且都不匹配 → 兜底最后一档
        if tier.get("maxDays") is None:
            return tier
    return tiers[-1]


def apply_vision_override(tier: dict, images: bool, config: dict) -> dict:
    """has_images → 强制视觉模型/预设;原 tier 的 model/preset 保留在 *Base 字段。"""
    if not images:
        return tier
    vision = config.get("vision", {})
    return {
        **tier,
        "basePreset": tier.get("preset"),
        "baseModel": tier.get("model"),
        "baseSubagentModel": tier.get("subagentModel"),
        "preset": vision.get("preset", "issue-normal"),
        "model": vision.get("model", "deepseek-v4-flash-vision-exp"),
        "subagentModel": vision.get("model", "deepseek-v4-flash-vision-exp"),
        "name": f"{tier.get('name', '?')}+vision",
        "vision": True,
    }


def resolve_workspace_key(labels: list[str], config: dict) -> str | None:
    """芯片标签决定工作区 key;无芯片标签 → 版本/框架类标签的映射;再兜底 None。"""
    for chip in config.get("chipPriority", []):
        if chip in labels:
            key = config.get("labelWorkspace", {}).get(chip)
            if key:
                return key
    for label in labels:
        key = config.get("labelWorkspace", {}).get(label)
        if key:
            return key
    return None


def resolve_branch(labels: list[str], workspace_key: str, config: dict) -> str:
    for label in labels:
        branch = config.get("labelBranch", {}).get(label)
        if branch:
            return branch
    return config.get("branchDefaults", {}).get(workspace_key) or "master"


def load_workspace_registry(config: dict) -> dict[str, str]:
    """{真实路径: workspaceId},来自 DSH 工作区注册表。"""
    path = Path(config.get("workspaceRegistryFile", WORKSPACE_FILE)).expanduser()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    result: dict[str, str] = {}
    for wid, entry in (data.get("tables", {}).get("workspaces", {}) or {}).items():
        wp = entry.get("path")
        if not wp:
            continue
        try:
            result[os.path.realpath(wp)] = wid
        except OSError:
            result[wp] = wid
    return result


def resolve_workspace_id(workspace_path: str, registry: dict[str, str]) -> str | None:
    try:
        real = os.path.realpath(workspace_path)
    except OSError:
        real = workspace_path
    return registry.get(real) or registry.get(workspace_path)


def parse_iid_from_description(description: str) -> int | None:
    match = IID_MARKER_RE.search(description or "")
    return int(match.group(1)) if match else None


def render_template(template: str, values: dict) -> str:
    def repl(match: re.Match) -> str:
        key = match.group(1)
        if key not in values:
            return match.group(0)  # 未知 token 保留原样,便于发现模板缺变量
        value = values[key]
        return "" if value is None else str(value)

    return TOKEN_RE.sub(repl, template)


def shorten(text: str, cap_bytes: int) -> str:
    data = text.encode("utf-8")
    if len(data) <= cap_bytes:
        return text
    return data[:cap_bytes].decode("utf-8", errors="ignore") + "\n…(截断,完整内容见缓存文件)"


def build_board_description(issue: dict, kept: dict, workspace_path: str, branch: str,
                            tier: dict, config: dict) -> str:
    labels = ",".join(issue.get("labels") or [])
    lines = [
        f"issue-gitlab-iid: {issue['iid']}",
        f"issue-gitlab-url: {issue.get('web_url') or issue.get('_url') or ''}",
        f"issue-gitlab-title: {issue.get('title') or ''}",
        f"issue-gitlab-labels: {labels}",
        f"issue-gitlab-due: {issue.get('due_date') or ''}",
        f"issue-gitlab-assignee: {(issue.get('assignee') or {}).get('username') or ''}",
        f"issue-pipeline-workspace: {workspace_path}",
        f"issue-pipeline-branch: {branch}",
        f"issue-pipeline-tier: {tier.get('name')}",
        f"issue-pipeline-has-images: {'true' if tier.get('vision') else 'false'}",
        "",
        f"## {issue.get('title') or ''}",
        "",
    ]
    body = issue.get("description") or ""
    body = shorten(body, config.get("descriptionCapBytes", 20480))
    lines.append("### Description:\n")
    lines.append(body)
    return "\n".join(lines)


def build_issue_prompt(iid: int, issue: dict, issue_cache: Path, resolved: dict,
                       script_path: str, config: dict) -> str:
    tier = resolved["tier"]
    template = config.get("issuePromptTemplate") or DEFAULT_ISSUE_PROMPT
    labels = ",".join(issue.get("labels") or [])
    authorization = (
        "审批纪律: requireApproval=true → 每个真实动作(MR 创建/Jenkins 触发/OTA)前停下,"
        "把下步计划写入 issue 评论并等待用户确认后再继续"
        if config.get("requireApproval")
        else "审批纪律: 部署为无人值守(approval 关闭 + danger-full-access),"
        "真实动作按 dry-run 先行、设备唯一性校验等纪律连续执行,不逐阶段等待人工确认"
    )
    values = {
        "iid": iid,
        "title": issue.get("title") or "",
        "url": issue.get("web_url") or "",
        "labels": labels,
        "dueDate": issue.get("due_date") or "无",
        "assignee": (issue.get("assignee") or {}).get("username") or "",
        "cacheFile": str(issue_cache),
        "workspacePath": resolved["workspace_path"],
        "workspaceKey": resolved["workspace_key"],
        "branch": resolved["branch"],
        "tierName": tier.get("name"),
        "model": tier.get("model"),
        "modePreset": tier.get("preset"),
        "subagentModel": tier.get("subagentModel"),
        "authorization": authorization,
        "script": script_path,
    }
    return render_template(template, values)


def task_executions(task: dict) -> list[dict]:
    return task.get("executions") or []


def task_running(task: dict) -> bool:
    return any(e.get("endedAt") is None for e in task_executions(task))


def task_last_ended_ms(task: dict) -> int | None:
    ended = [e["endedAt"] for e in task_executions(task) if e.get("endedAt")]
    return max(ended) if ended else None


# ---------------------------------------------------------------------------
# 命令实现
# ---------------------------------------------------------------------------

def find_task_by_iid(state: dict, iid: int) -> dict | None:
    for task in state.get("tasks", []):
        if parse_iid_from_description(task.get("description") or "") == iid:
            return task
    return None


def find_task_by_sweeper(state: dict, config: dict) -> dict | None:
    for task in state.get("tasks", []):
        if task.get("title") == config["board"]["sweepTaskTitle"]:
            return task
        if SWEEPER_MARKER in (task.get("description") or ""):
            return task
    return None


def cmd_sync(args, config: dict, script_path: str) -> int:
    errors: list[str] = []
    created: list[dict] = []
    runs: list[tuple[str, str]] = []  # (taskId, mode: run|rerun)

    log(f"sync: 拉取 issue 与看板状态...")
    gitlab = GitLabAPI(config)
    board = BoardAPI(config)
    try:
        issues = gitlab.opened_issues(config["gitlab"]["assignee"], config["gitlab"]["perPage"])
    except requests.HTTPError as exc:
        msg = f"GitLab 拉取失败: {exc}(令牌可能失效,请在设置页重新保存凭据)"
        log(f"sync ERROR: {msg}")
        print(f"❌ {msg}")
        return 1
    state = board.state()

    opened_iids = {issue["iid"] for issue in issues}
    existing = {}
    for task in state.get("tasks", []):
        iid = parse_iid_from_description(task.get("description") or "")
        if iid is not None:
            existing[iid] = task

    registry = load_workspace_registry(config)
    PIPELINE_HOME.mkdir(parents=True, exist_ok=True)
    ISSUES_DIR.mkdir(parents=True, exist_ok=True)

    for issue in issues:
        iid = issue["iid"]
        cache_file = ISSUES_DIR / f"{iid}.json"
        labels = issue.get("labels") or []
        images = has_images(issue, config)
        tier = apply_vision_override(resolve_tier(issue.get("due_date"), config), images, config)
        workspace_key = resolve_workspace_key(labels, config)
        if not workspace_key:
            errors.append(f"#{iid}: 未匹配到工作区标签({labels}),跳过建卡")
            continue
        workspace_path = config["workspaces"].get(workspace_key)
        if not workspace_path:
            errors.append(f"#{iid}: 配置缺少工作区 key {workspace_key} 的路径,跳过")
            continue
        workspace_id = resolve_workspace_id(workspace_path, registry)
        if not workspace_id:
            errors.append(
                f"#{iid}: 工作区 {workspace_path} 未在 DSH 登记(工作区列表),跳过建卡;"
                "请先在 GUI 中登记该目录"
            )
            continue
        branch = resolve_branch(labels, workspace_key, config)

        kept = {
            "iid": iid,
            "workspace_key": workspace_key,
            "workspace_path": workspace_path,
            "workspace_id": workspace_id,
            "branch": branch,
            "tier": tier,
            "hasImages": images,
            "labels": labels,
            "dueDate": issue.get("due_date"),
            "url": issue.get("web_url"),
        }
        cache_file.write_text(
            json.dumps({"issue": issue, "pipeline": kept}, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )

        if iid in existing:
            continue

        prompt = build_issue_prompt(iid, issue, cache_file, kept, script_path, config)
        task_input = {
            "title": (issue.get("title") or f"Issue #{iid}")[:120],
            "description": build_board_description(issue, kept, workspace_path, branch, tier, config),
            "prompt": prompt,
            "workspaceId": workspace_id,
            "mode": tier.get("preset"),
            "permission": "danger-full-access",
        }
        if args.dry_run:
            created.append({"iid": iid, "task_id": f"(dry-run) uuid5:{iid}", "dry": True})
            log(f"sync DRY-RUN: 将创建卡片 # {iid}")
            continue
        task_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"issue:{iid}"))
        try:
            board.create_task(task_id, task_input)
            board.move(task_id, "todo")
            created.append({"iid": iid, "task_id": task_id, "dry": False})
            log(f"sync: 创建卡片 {task_id} (issue #{iid})")
        except RuntimeError as exc:
            errors.append(f"#{iid}: 建卡失败 {exc}")

    # issue 已被人工关闭(或不在 opened 列表)→ 卡片转档(遵循看板状态机:
    # 手工 move 仅允许 非running→待办/待规划;归档仅限 已完成/已失败;运行中的卡片
    # 由 runner 负责结算,本轮跳过,结算后的下一轮 sync 再归档)
    for iid, task in existing.items():
        if iid in opened_iids or task_running(task):
            continue
        status = task.get("status")
        if status in ("done", "failed"):
            if args.dry_run:
                log(f"sync DRY-RUN: issue #{iid} 已不在待处理列表,将归档卡片")
                continue
            try:
                board.archive(task["id"])
                log(f"sync: issue #{iid} 已关闭,卡片已归档")
            except RuntimeError as exc:
                errors.append(f"#{iid}: 归档失败 {exc}")
        elif status in ("todo", "backlog"):
            if args.dry_run:
                log(f"sync DRY-RUN: issue #{iid} 已不在待处理列表,将删除从未运行的卡片")
                continue
            try:
                board.delete(task["id"])
                log(f"sync: issue #{iid} 已关闭,删除从未运行的卡片 {task['id']}")
            except RuntimeError as exc:
                errors.append(f"#{iid}: 删除失败 {exc}")

    # 重试策略:待办/已失败 + issue 仍打开 + 距上次执行 ≥ afterHours + 尝试 < maxAttempts
    retry_targets: list[tuple[dict, dict]] = []
    retry = config.get("autoRetry", {})
    if retry.get("enabled", True):
        now_ms = int(time.time() * 1000)
        for iid, task in existing.items():
            if iid not in opened_iids or task_running(task):
                continue
            if task.get("status") not in ("todo", "failed"):
                continue
            attempts = len(task_executions(task))
            if attempts >= int(retry.get("maxAttempts", 3)):
                continue
            last = task_last_ended_ms(task)
            if last is not None and (now_ms - last) < int(retry.get("afterHours", 6)) * 3600_000:
                continue
            retry_targets.append((task, issues_by_iid(issues, iid)))

    if args.dry_run:
        for t, _ in retry_targets:
            log(f"sync DRY-RUN: 将 rerun 卡片 {t['id']}")
        print(f"sync(dry-run): 新卡片 {len(created)} 张,重试候选 {len(retry_targets)} 个,错误 {len(errors)} 条")
        for c in created:
            print(f"  + issue #{c['iid']}")
        for t, _ in retry_targets:
            print(f"  ↻ rerun {t['id']}")
        for e in errors:
            print(f"  ⚠ {e}")
        return 0

    auto = config.get("autoRun", True)
    if auto:
        for c in created:
            try:
                board.run(c["task_id"])
                runs.append((c["task_id"], "run"))
                log(f"sync: run {c['task_id']}")
            except RuntimeError as exc:
                errors.append(f"#{c['iid']}: run 失败 {exc}")
        for task, issue in retry_targets:
            try:
                board.rerun(task["id"])
                runs.append((task["id"], "rerun"))
                log(f"sync: rerun {task['id']}")
            except RuntimeError as exc:
                errors.append(f"#{task['id']}: rerun 失败 {exc}")
        for e in sorted(errors, key=str):
            print(f"  ⚠ {e}")

    print(f"sync: opened={len(opened_iids)} 新卡片={len(created)} 执行={len(runs)} 错误={len(errors)}")
    for c in created:
        print(f"  + issue #{c['iid']} → 卡片 {c['task_id']}", "(仅创建,未自动执行)" if not auto else "→ 已 run")
    for task_id, mode in runs:
        print(f"  ▶ {mode} {task_id}")
    return 0 if not errors else 2


def issues_by_iid(issues: list[dict], iid: int) -> dict | None:
    for issue in issues:
        if issue["iid"] == iid:
            return issue
    return None


def cmd_run_or_rerun(args, config: dict, rerun: bool) -> int:
    board = BoardAPI(config)
    task = resolve_task_ref(args.task, board.state())
    if task is None:
        print(f"未找到任务/issue: {args.task}")
        return 1
    if task_running(task):
        print(f"任务 {task['id']} 正在运行,跳过")
        return 0
    if args.dry_run:
        print(f"(dry-run) {'rerun' if rerun else 'run'} {task['id']}")
        return 0
    (board.rerun if rerun else board.run)(task["id"])
    print(f"✅ {'rerun' if rerun else 'run'} {task['id']}")
    return 0


def resolve_task_ref(ref: str, state: dict) -> dict | None:
    if re.fullmatch(r"\d+", ref):
        return find_task_by_iid(state, int(ref))
    for task in state.get("tasks", []):
        if task.get("id") == ref:
            return task
    return None


def cmd_move(args, config: dict) -> int:
    if args.status not in TASK_STATUSES:
        print(f"非法状态 {args.status},支持: {', '.join(TASK_STATUSES)}")
        return 1
    board = BoardAPI(config)
    task = resolve_task_ref(args.task, board.state())
    if task is None:
        print(f"未找到任务/issue: {args.task}")
        return 1
    if args.dry_run:
        print(f"(dry-run) move {task['id']} → {args.status}")
        return 0
    board.move(task["id"], args.status)
    print(f"✅ move {task['id']} → {args.status}")
    return 0


def cmd_stage(args, config: dict) -> int:
    stage = args.stage
    if stage not in ("progress", "done", "failed", "blocked"):
        print(f"非法阶段 {stage},支持: progress|done|failed|blocked")
        return 1
    board = BoardAPI(config)
    try:
        state = board.state()
        task = find_task_by_iid(state, args.iid)
    except Exception as board_err:
        task = None
        print(f"⚠ 看板不可用({str(board_err)[:120]}),降级执行:仅同步 issue 评论")
    if task is None and not args.dry_run and config.get("commentPerStage", True):
        gitlab = GitLabAPI(config)
        label = {"progress": "进行中", "done": "已完成", "failed": "失败", "blocked": "阻塞待重试"}[stage]
        gitlab.comment(args.iid, f"【流水线·{label}】{args.summary}")
        log(f"stage: issue #{args.iid} {stage}: {args.summary} (看板卡片缺失/不可用,已降级为仅评论)")
        print(f"✅ issue #{args.iid} 已评论(看板卡片缺失/不可用,已降级)")
        return 0
    target = STAGE_TO_STATUS.get(stage)
    changes: list[str] = []
    if args.dry_run:
        changes.append(f"(dry-run) move {task['id']} → {target}" if target else "")
        print(f"(dry-run) issue #{args.iid} stage={stage}: 卡片→{target},评论={args.summary}")
        return 0
    if target and task.get("status") != target:
        board.move(task["id"], target)
        changes.append(f"卡片→{target}")
    if config.get("commentPerStage", True):
        gitlab = GitLabAPI(config)
        label = {"progress": "进行中", "done": "已完成", "failed": "失败", "blocked": "阻塞待重试"}[stage]
        gitlab.comment(args.iid, f"【流水线·{label}】{args.summary}")
        changes.append(f"issue #{args.iid} 已评论")
    log(f"stage: issue #{args.iid} {stage}: {args.summary} ({', '.join(changes) or '无变更'})")
    print("✅ " + ", ".join(changes) if changes else "✅ 无变更")
    return 0


def cmd_close(args, config: dict) -> int:
    if not args.confirm:
        print(
            f"(dry-run) 将关闭 issue #{args.iid}: {config['gitlab']['baseUrl']}/{config['gitlab']['project']}"
            f"/-/issues/{args.iid} — 加 --confirm 实际关闭"
        )
        return 0
    gitlab = GitLabAPI(config)
    gitlab.close(args.iid)
    print(f"✅ issue #{args.iid} 已关闭")
    return 0


def cmd_status(args, config: dict) -> int:
    try:
        gitlab = GitLabAPI(config)
        issues = gitlab.opened_issues(config["gitlab"]["assignee"], config["gitlab"]["perPage"])
    except Exception as exc:  # 令牌/网络失败时仍展示看板侧
        issues = []
        print(f"⚠ GitLab 拉取失败: {exc}")
    try:
        state = BoardAPI(config).state()
    except Exception as exc:
        print(f"⚠ 看板拉取失败: {exc}")
        return 1
    tasks_by_iid: dict[int, dict] = {}
    for task in state.get("tasks", []):
        iid = parse_iid_from_description(task.get("description") or "")
        if iid:
            tasks_by_iid[iid] = task
    print(f"任务看板({len(state.get('tasks', []))} 张卡片) ↔ GitLab opened issue({len(issues)} 个)")
    print(f"{'iid':>5} {'看板状态':<8} {'issue 标题':<48} 标签")
    for issue in sorted(issues, key=lambda x: x["iid"]):
        task = tasks_by_iid.get(issue["iid"])
        status = task.get("status") if task else "(未建卡)"
        title = (issue.get("title") or "")[:46]
        labels = ",".join(issue.get("labels") or [])
        print(f"{issue['iid']:>5} {status:<8} {title:<48} {labels}")
    orphans = [iid for iid in tasks_by_iid if iid not in {i["iid"] for i in issues}]
    if orphans:
        print(f"无对应 opened issue 的卡片: {orphans}(可能已人工关闭)")
    return 0


def cmd_bootstrap(args, config: dict, script_path: str) -> int:
    board = BoardAPI(config)
    state = board.state()
    task = find_task_by_sweeper(state, config)
    cron = config["board"]["sweepCron"]
    if task is not None:
        if args.dry_run:
            print(f"(dry-run) sweeper 已存在: {task['id']},将 set-schedule enabled cron={cron}")
        else:
            board.set_schedule(task["id"], cron)
            print(f"✅ sweeper 已存在: {task['id']},cron={cron} 已确认/更新")
        return 0
    if args.dry_run:
        print(f"(dry-run) 将创建 sweeper 卡片「{config['board']['sweepTaskTitle']}」cron={cron}")
        return 0
    registry = load_workspace_registry(config)
    workspace_id = resolve_workspace_id(str(Path.home() / "os"), registry) or resolve_workspace_id(
        "/home/cx/os", registry
    )
    prompt = render_template(
        config.get("sweeperPromptTemplate") or DEFAULT_SWEEPER_PROMPT,
        {"script": script_path},
    )
    description = "\n".join(
        [
            SWEEPER_MARKER,
            f"指令脚本: {script_path}",
            "由 openharmony-issue-pipeline bootstrap 创建;每轮: sync → 自动建卡并 run/rerun → 汇报摘要。",
        ]
    )
    task_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "sweeper:issue-pipeline"))
    board.create_task(
        task_id,
        {
            "title": config["board"]["sweepTaskTitle"],
            "description": description,
            "prompt": prompt,
            "workspaceId": workspace_id,
            "permission": "danger-full-access",
        },
    )
    board.set_schedule(task_id, cron)
    print(f"✅ sweeper 卡片已创建: {task_id},cron={cron} — 看板 30 分钟内开始轮询")
    return 0


def log(message: str) -> None:
    LOG.info(message)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GitLab Issue 驱动看板流水线 CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init-config", help="生成默认配置 ~/.dsh/issue-pipeline/config.json")
    p.set_defaults(func=_noop)

    p = sub.add_parser("sync", help="抓取 issue→与看板比对→建卡→(默认)自动 run/rerun")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-autorun", action="store_true", dest="no_autorun")
    p.set_defaults(func=_noop)

    for name in ("run", "rerun"):
        p = sub.add_parser(name, help=f"{name} <taskId|iid>")
        p.add_argument("task")
        p.add_argument("--dry-run", action="store_true")
        p.set_defaults(func=_noop)

    p = sub.add_parser("move", help="move <taskId|iid> <status>")
    p.add_argument("task")
    p.add_argument("status")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=_noop)

    p = sub.add_parser("stage", help="stage <iid> <progress|done|failed|blocked> <摘要>")
    p.add_argument("iid", type=int)
    p.add_argument("stage")
    p.add_argument("summary")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=_noop)

    p = sub.add_parser("close", help="close <iid> [--confirm](默认 dry-run)")
    p.add_argument("iid", type=int)
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(func=_noop)

    p = sub.add_parser("status", help="输出看板任务 vs issue 清单对照(只读)")
    p.set_defaults(func=_noop)

    p = sub.add_parser("bootstrap", help="创建/校验 sweeper 卡片与 cron")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=_noop)

    args = parser.parse_args(argv)
    setup_logging()
    script_path = Path(__file__).resolve()
    try:
        config = load_config()
    except (FileNotFoundError, ValueError) as exc:
        if args.command == "init-config":
            return cmd_init_config(args)
        print(f"❌ {exc}")
        return 1

    if args.command == "init-config":
        return cmd_init_config(args)
    if args.command == "sync":
        if args.no_autorun:
            config["autoRun"] = False
        return cmd_sync(args, config, script_path)
    if args.command in ("run", "rerun"):
        return cmd_run_or_rerun(args, config, rerun=args.command == "rerun")
    if args.command == "move":
        return cmd_move(args, config)
    if args.command == "stage":
        return cmd_stage(args, config)
    if args.command == "close":
        return cmd_close(args, config)
    if args.command == "status":
        return cmd_status(args, config)
    if args.command == "bootstrap":
        return cmd_bootstrap(args, config, script_path)
    print(f"未知命令 {args.command}")
    return 1


def _noop(*_args, **_kwargs) -> int:
    return 0


if __name__ == "__main__":
    sys.exit(main())
