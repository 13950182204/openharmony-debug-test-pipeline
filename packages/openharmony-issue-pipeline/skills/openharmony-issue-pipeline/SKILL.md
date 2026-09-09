---
name: openharmony-issue-pipeline
description: "GitLab Issue 驱动看板流水线:抓取 Assignee 相关待解决 issue,按标签映射工作区与分支、按 Due date 分级模型档位(含图片强制视觉模型),按父插件八阶段闭环处理(第一步分析 issue Description),每阶段同步 DSH 任务看板卡片与 issue 评论。"
whenToUse: "当会话来自任务看板(运行 sweeper 或某个 issue 工作任务)、或用户要求处理 GitLab issue 流水线时使用。"
---

# GitLab Issue 驱动看板流水线(openharmony-issue-pipeline)

目标:把 GitLab 项目 `harmony/system/v9611/openharmony_V6.1` 中 Assignee=cx 的待解决 issue,
自动走完 定位 → 修复 → MR → CI → OTA → 回归 的八阶段闭环,并把进度同步到 DSH 任务看板
五列(待规划/待办/进行中/已完成/已失败)与 issue 评论。

本 skill 是父插件 `openharmony-debug-test-pipeline` 的子插件编排。父插件的六个模块 skill
(`openharmony-debug-loop` / `openharmony-test-report-triage` / `karpathy-guidelines` /
`glab-mr-submit` / `openharmony-ci-orchestrator` / `openharmony-ota-upgrade`)按名用 `skill`
工具加载,本包**不 import 父插件代码**。

## 术语与角色

- **sweeper 会话**:任务看板定时任务「GitLab 问题流水线轮询」(Host cron 默认 `*/30 * * * *`)
  创建的会话,职责 = `sync`(抓取/建卡/自动 run),**不处理 issue 本体**。
- **issue 会话**:每张 issue 卡片由 Host runner 创建的独立 DSH 会话,职责 = 处理具体 issue,
  走八阶段闭环。

## 配置与数据文件

- 配置 `~/.dsh/issue-pipeline/config.json`(`python3 {{ISSUE_SCRIPT}} init-config` 生成,勿手改后丢失注释)。
- issue 完整缓存 `~/.dsh/issue-pipeline/issues/<iid>.json`:`sync` 时写入完整 issue 元数据
  (Description 全量、标签、due date、`hasImages`、解析出的 workspace/branch/modelTier/boardTaskId)。
  **卡片描述是截断版,以缓存文件为准。**
- 运行日志 `~/.dsh/issue-pipeline/issue_pipeline.log`;每 issue 运行记录 `~/.dsh/issue-pipeline/runs/<日期>-<iid>.md`。
- 流水线状态(八阶段)照常使用父插件: `python3 {{PARENT_PLUGIN_DIR}}/scripts/pipeline_state.py set|note|tokens ... --file ~/.dsh/pipeline-state.json`(`/pipeline status` 可查询)。

## 脚本命令(所有命令都支持 --dry-run,只读不写)

```bash
python3 {{ISSUE_SCRIPT}} init-config            # 生成默认配置(已存在则跳过)
python3 {{ISSUE_SCRIPT}} sync [--no-autorun]    # 抓取 issue→与看板比对→建卡→(默认)自动 run/rerun
python3 {{ISSUE_SCRIPT}} run <taskId|iid>       # 启动一个任务执行
python3 {{ISSUE_SCRIPT}} rerun <taskId|iid>     # 重新执行一个已结束的任务
python3 {{ISSUE_SCRIPT}} move <taskId|iid> <backlog|todo|running|done|failed>
python3 {{ISSUE_SCRIPT}} stage <iid> <progress|done|failed|blocked> <一句话摘要>
python3 {{ISSUE_SCRIPT}} close <iid> [--confirm] # 人工确认后关闭 issue(默认 dry-run)
python3 {{ISSUE_SCRIPT}} status                 # 看板任务 vs issue 对照
python3 {{ISSUE_SCRIPT}} bootstrap              # 创建/校验 sweeper 卡片与 cron(已存在则跳过)
```

## 标签 → 工作区/分支映射(config.json `labelWorkspace` / `labelBranch` 可覆盖)

| 标签 | 工作区 | 分支 |
|---|---|---|
| RK3568 | /home/cx/os/6.1_rk3568(key: 6.1_rk3568) | 版本标签决定,无则 branchDefaults |
| A333/A537 | /home/cx/os/6.1(key: 6.1) | 同上 |
| 通用框架层修改 | /home/cx/os/6.1 | 同上 |
| 应用修复 | 芯片标签优先:RK3568→6.1_rk3568,否则 6.1 | 同上 |
| V6.1.0.31_Rlease | 芯片标签决定 | `v6.1.0.31_release` |
| V6.1.0.35_LTS | 芯片标签决定 | `V6.1.0.35_LTS` |
| XTS | 芯片标签决定 | 版本标签决定;流程仍走完整八阶段,XTS 相关按父插件 XTS 流程 |

冲突规则:芯片优先级 `RK3568 > A333/A537`;版本标签叠加决定分支;无任何平台标签 → 6.1。
分支基线 `branchDefaults = {"6.1": "v6.1.0.31_release", "6.1_rk3568": "V6.1.0.35_LTS"}`。
**注意:映射出的工作区必须已在 DSH 登记(工作区列表含对应目录),否则 sync 会报错跳过该 issue。**

## 模型档位(触发时按 due−今天 计算;图片规则优先)

| 距离 due | mode 预设(全量组合,主会话模型为部署默认) | 子代理模型(subagent 工具 provider=deepseek-official) |
|---|---|---|
| 已过期或 ≤2 天 | issue-urgent | deepseek-v4-pro |
| 3–6 天 | issue-normal | deepseek-v4-flash-vision-exp |
| ≥7 天或无 due | issue-low | deepseek-v4-flash |

> 说明:本部署中主会话模型由宿主 `agent-default-model` 固定(deepseek-v4-flash-vision-exp,
> 本身即视觉档,满足图片规则);档位差异通过**子代理模型**实现(config `modelTiering: subagents` 为
> 当前生效模式,预设级模型已被宿主拒绝——如需按会话分档,需宿主层支持,属后续增强)。

- **图片规则(优先级最高)**:issue 描述含 markdown 图片(`![](...)`/`<img>`)或附件为图片
  (`hasImages=true`)时,需要读图的子代理(report/triage/fix 相关)**必须**使用
  `deepseek-v4-flash-vision-exp`(唯一视觉模型,主会话本身即此模型),**不得给读图子代理指派
  deepseek-v4-pro**(纯文本);紧急度用子代理并行/重试补偿;纯文本子代理(如六维 MR 审查)
  仍按上表。
- 子代理模型通过 `subagent` 工具的 `model` 参数覆盖(provider 省略则默认 deepseek-official)。

## 处理流程(issue 会话)

阶段顺序与父插件八阶段一致,仅第一阶段不同。

1. **report(issue 描述输入)**:读取 `~/.dsh/issue-pipeline/issues/<iid>.json`,完整分析
   **Description**(含附件链接、图片、复现步骤、期望行为)定位问题。**不首先分析测试报告**;
   仅当 Description 明确引用测试报告路径/目录时,才按 `openharmony-test-report-triage`
   处理该报告。记录产物(问题域/模块/证据)。
2. **triage(分诊)**:加载 `openharmony-test-report-triage`(若上一步有报告)或直接基于
   Description 建立问题表:期望 vs 实际、涉及模块、复现路径;核查上游(OpenAtom)是否已有
   修复;给出修复优先级。载荷写入 pipeline_state `triage`。
3. **fix(修复)**:加载 `karpathy-guidelines` 规范:简单优先、外科手术式改动、每处修复有可
   验证判据。**工作区纪律**:在映射工作区内为本次 issue 创建独立 worktree:
   `git -C <workspace> worktree add /home/cx/os/worktrees/issue-<iid>-<slug> <分支基线>`,
   在 worktree 内建分支(命名沿用 glab-mr-submit 约定:如 `v1.1.x/v6.1.0.31_<主题>`),
   禁止直接改动主 worktree。修复后先跑最小验证(编译/单测/对应模块)。
4. **mr(提交)**:加载 `glab-mr-submit`,用其 `create_glab_mr.py` 生成合规标题/标签/记录,
   target 分支 = 映射的版本分支(无版本标签则用 branchDefaults);先 --dry-run 再执行;
   创建后按六维并行 subagent 审查,确认的 P0-P2 修复在同一 MR 追加提交并复审。
5. **ci(构建)**:加载 `openharmony-ci-orchestrator`,--dry-run --verify-job 校验后触发,
   `ci_orchestrator.py register` 登记;构建完成由 webhook/定时 reconcile 交接或本会话轮询
   (**issue 会话即流水线会话:尽量在本会话内等待与继续,避免 headless 交接**,交接只作为
   会话结束后的兜底)。
6. **ota(升级)**:加载 `openharmony-ota-upgrade`:ota_preflight → `hdc list targets` 确认
   **唯一设备** → 预检 → 传输比对 → write_updater → reboot updater → 回连验证目标版本。
   无唯一设备时**禁止操作**,调用 `stage <iid> blocked <设备缺失说明>`,卡片转待办,等下一轮重试。
7. **regression(回归)**:升级后重跑 triage 阶段对应的测试集(或 CI phase-3 回归 profile),
   与问题表逐条对比;通过则 `stage <iid> done <回归结论>`,失败则回到 fix(父插件闭环的
   done→fix 回路,在原 MR 追加提交,重走 ci→ota→regression)。
8. **done**:汇总闭环记录(MR 链接、构建号、OTA 摘要、回归结论)写入运行记录
   `~/.dsh/issue-pipeline/runs/<日期>-<iid>.md`,并用 `todo_write` 收尾可见计划。

每个阶段结束(含失败):`python3 {{ISSUE_SCRIPT}} stage <iid> <stage> <一句话摘要>` +
`pipeline_state.py set <stage> ...` + 追加运行记录。stage 语义:
`progress`=已进入新阶段(卡片保持进行中 + issue 评论)、`done`=回归通过(卡片→已完成)、
`failed`=阶段失败终止(卡片→已失败)、`blocked`=需外部条件(设备缺失/CI 未就绪,卡片→待办,
sweeper 下轮按 autoRetry 重试)。

## 无人值守纪律

- 本流水线由任务看板自动执行。部署授权策略(danger-full-access、审批关闭)下,真实动作
  (MR 创建 / Jenkins 触发 / OTA)各阶段**连续执行**,不逐阶段等待人工确认;但保留:
  dry-run 先行、`hdc list targets` 唯一设备校验、GitLab 令牌绝不打印/落日志(GitLab API
  凭据由脚本从 `~/.dsh/gitlab-credentials.json` 读取)。
- 若配置 `requireApproval: true`,每个真实动作前停下,把下步计划写入 issue 评论并告知用户,
  等用户确认(看板 rerun 或会话指令)后继续。
- **回归通过后不自动关闭 issue**:卡片→已完成,issue 评论「修复完成,等待人工确认关闭」;
  用户确认后由人工或会话指令执行 `python3 {{ISSUE_SCRIPT}} close <iid> --confirm`。

## 失败与重试

- 阶段失败:卡片→已失败 + issue 评论原因 + 运行记录;
- 设备缺失/CI 未就绪:`blocked` → 卡片回待办 + 评论原因;
- sweeper 每轮 sync 对「卡片在待办/已失败 + issue 仍打开 + 上次执行结束 ≥ autoRetry.afterHours(6h)
  + 执行次数 < maxAttempts(3)」的任务自动 rerun;
- 令牌失效:sync 报错卡片不动,提示用户在设置页重新保存 GitLab 凭据;
- 看板 HTTP 403:检查 DSH web 是否本机运行(127.0.0.1:3080),脚本报错不静默。

## 与父插件的关系

- 本 skill 只引用父插件 skill 名与 `{{PARENT_PLUGIN_DIR}}` 下的脚本,不 import 其代码;
- 父插件未安装时流水线阶段 skill 无法加载,表现为阶段失败(有明确报错);
- 工具 `mr_review_six`(父插件提供)可用于 mr 阶段的六维审查一键执行。
