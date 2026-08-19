# openharmony-debug-test-pipeline

OpenHarmony「修改-调试-测试」闭环 DSH 插件（运行时插件，bundle 聚合包形态）。

把五个 OpenHarmony 调测 skill 打包进一个 DSH 插件，串成一条八阶段闭环流水线，
每个 skill 同时保持独立可调用（模块化）。skill 已从 `~/.codex/skills` **拷贝进插件**
并针对 DSH 做了定制改造（见下文「DSH 定制清单」），仓库自包含，不依赖本机 Codex 环境。

## 插件包含什么

| 模块 skill | 作用 | 独立使用示例 |
|---|---|---|
| `openharmony-test-report-triage` | 测试报告分诊：失败表、源码定位、hilog 证据、根因分类、上游核查 | 「分析这份测试报告」 |
| `karpathy-guidelines` | 编码行为准则：思考先行、简单优先、外科手术式改动、目标驱动 | 「按 Karpathy 规范修这个问题」 |
| `glab-mr-submit` | GitLab MR 提交/审查/修复闭环：合规标题、标签、截图上传、六维 subagent 审查 | 「提交这个 MR」 |
| `openharmony-ci-orchestrator` | Jenkins 构建编排：作业校验、触发、持久状态、webhook/定时交接 | 「触发 Jenkins 构建」 |
| `openharmony-ota-upgrade` | OTA 升级闭环：预检、hdc 传输、updater、回连验证 | 「OTA 升级到设备」 |
| `openharmony-debug-loop` | **闭环编排**（本插件新增）：八阶段状态机串联以上模块 | 「跑一遍完整闭环」 |

闭环状态机：

```
report → triage → fix → mr → ci → ota → regression → done
  测试报告  分诊    修复   MR   构建   升级     回归        （失败回到 fix）
```

## 安装

```bash
# 在本仓库目录构建后，用 link: 装进 web profile
pnpm build
dsh plugin --profile web add link:/home/cx/os/openharmony-debug-test-pipeline
```

安装命令会自动把本包追加进 profile 的 `dsh.profile.bundles` 层栈。
**重启 dsh web 后生效**。验证：

```bash
dsh --profile web --dump-config | grep -A3 oh-debug-pipeline
```

> 注意：若 profile 的 `cordis.patch.yml` 里已手工 mount 过同名行，先移除，
> 避免插件双实例。

## 配置

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `stateFile` | `~/.dsh/pipeline-state.json` | 流水线状态文件（跨会话持久化闭环进度） |

可在 profile 的 `cordis.patch.yml` 覆盖该行 config，或在 web GUI
Settings → 插件配置 中调整。

## 使用

- **完整闭环**：对 agent 说「跑一遍完整闭环 + 测试报告路径」，编排 skill 会按状态机推进，
  每个阶段结束向你汇报并等待授权（触发 Jenkins / 创建 MR / OTA 均为真实动作，必须授权）。
- **单独一个模块**：直接说「提交这个 MR / 分析这份报告 / 触发 Jenkins / OTA 升级」，
  只加载对应 skill，不影响其他模块。
- **流水线状态**：`/pipeline status` 查询当前阶段与产物；`/pipeline reset` 重置。

模型在每个阶段结束时通过 `scripts/pipeline_state.py`（`set`/`note`）把产物写入状态文件，
状态文件是闭环的唯一事实来源，跨会话可续。

## 开发

```bash
pnpm install
pnpm build          # tsc 产出 lib/（typescript 5.7 rewriteRelativeImportExtensions）
pnpm test           # vitest：frontmatter 解析 / 占位符替换 / 状态文件
pnpm test:python    # vendored skill 自带的 python 单测（ci_orchestrator / glab）
```

## DSH 定制清单（相对 ~/.codex/skills 原版的改造）

| 改造 | 位置 | 说明 |
|---|---|---|
| 路径占位符化 | 各 `SKILL.md` 与 `references/*.md` | `~/.codex/skills/...` / `${CODEX_HOME:-$HOME/.codex}/skills/...` → `{{SKILLS_DIR}}/...`，由插件加载时替换为 vendored 目录绝对路径 |
| 交接机制改造 | `skills/openharmony-ci-orchestrator/scripts/ci_orchestrator.py` | `codex exec --cd --sandbox --json --output-last-message` → `dsh --profile headless <prompt>`（cwd 由进程接管；sandbox 改由 headless profile 配置决定；日志落 stdout/stderr 文件） |
| OTA 脚本路径 | `.../scripts/phase3_runner.py` | `Path.home()/".codex/..."` → 相对本文件解析（`parents[2]/openharmony-ota-upgrade/...`） |
| 测试断言同步 | `.../tests/test_phase3_runner.py` | `agent_command: "codex"` → `"dsh"` |
| 措辞适配 | 各 SKILL.md | 「Codex handoff / codex exec / explorer Subagent / codex/ 分支前缀」→ dsh headless / 审查 Subagent / 本地分支惯例 |
| 中文本地化 | 全部 SKILL.md 与 references/*.md | 说明性文字翻译为中文（与你 MR/提交/问题的中文工作流一致）；代码块、命令、flag、路径、URL 逐字节保留不变 |
| 剔除 | 各 skill 的 `agents/` 目录 | codex 专用 subagent 定义，DSH 不消费 |
| 新增 | `scripts/pipeline_state.py` | 流水线状态读写脚本（get/set/note/reset/status） |

## 与 ~/.codex/skills 的同步

vendored 后，原 codex skill 的后续更新需要手动同步进本仓库：
覆盖对应 `skills/<name>/` 下的文件后，重新执行上述「路径占位符化」与
「交接机制」定制（改动点集中，见定制清单）。

## 边界与安全

- 真实动作（Jenkins 触发、MR 创建、OTA 升级）必须用户授权，dry-run 先行；
- 设备操作前必须 `hdc list targets` 确认唯一目标；
- 状态文件损坏时自动备份为 `.bak-<时间戳>` 并重建；
- Jenkins / GitLab 凭据只从环境变量读取，不写入状态文件或 MR 文本。
