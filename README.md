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

> **只能装在 WSL 侧的 dsh profile。** 本机有两套运行时，别装错：
>
> | | Windows 桌面 App | WSL 侧 dsh |
> |---|---|---|
> | 监听 | `127.0.0.1:19387`（Web GUI 外壳） | `127.0.0.1:3080`（`deepseek-dsh-web.service`） |
> | profile | `C:\Users\<user>\.dsh\profiles\desktop` | `~/.dsh/profiles/web` |
> | 角色 | 壳 + WSL 工作区桥接（`dsh-wsl-workspace`） | **真正执行 agent、加载本插件** |
>
> GUI 插件页（「添加插件」）属于桌面 App，安装目标是 **Windows 的 desktop profile**。
> 本插件的 skill 全部依赖 WSL 工具链（`python3`、`~/.dsh/pipeline-*.json`、`hdc`/`glab`/`ssh`、
> systemd 单元），装到 Windows profile 后在 Windows 会话里跑不通，因此**不要**用那个弹窗装本插件。
> WSL 侧也不需要弹窗：用下面的 `link:` 命令即可（这也是当前在用的形态）。

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

### DSH 版本兼容

- **构建与类型检查基线：`0.2.0-rc.2`**（`devDependencies` 的 `@deepseek-ai/dsh-*`），
  `package.json` 声明 `dsh.engines.dsh: ">=0.2.0-rc.2"`，并带
  `dsh.compatibility.dshReleases` 兼容矩阵。
- 插件不保留旧版本分支：0.1.2-rc.1 与 0.2.0-rc.2 的接触面（`defineTool`、`ctx.skills`、
  `ctx.commands`、`ctx.subagents`、`Agent`/`AgentOptions`）除 `SubagentResult.output` 由
  “可变”变为 `readonly` 外没有破坏性变更，因此**同一份代码在两个版本上都能加载**，
  只是不再为旧版本做额外适配。
- `package.json` 的 `os: ["linux"]` 是护栏：阻止误装到 Windows profile。

### 运行时依赖链接（`dsh-tools` 的 Symbol 一致性）

`@deepseek-ai/dsh-tools` 导出的调度器使用进程内 `Symbol` 标识；插件必须与**正在运行的
DSH** 使用同一份模块实例，不能只满足“版本号相同”。`postinstall` / `link-runtime-deps`
会解析活动运行时（`~/.dsh/profiles/node_modules` → `~/.local/bin/dsh` wrapper 推导的
`~/.dsh/runtime/<id>` → `~/.dsh/runtime/*` 里最新的一个）并链接 `dsh-tools`：

- **声明版本 == 活动运行时版本** → 链接到活动运行时的 `dsh-tools`（Symbol 一致）；
- **两者不同** → 跳过重链，保持 pnpm 解析结果（保证类型检查基线不被另一世代的
  peer 类型污染），并打印提示；
- **解析不到活动运行时** → 打印警告并 `exit 0`，**不打断 `pnpm install`**。

升级 DSH 后的推荐顺序：

```bash
dsh --profile web --dump-config     # 让共享运行时树先按新 runtime 生成
pnpm install                        # 版本对齐后 postinstall 自动重链
pnpm run link-runtime-deps          # 需要时手动补
pnpm build
systemctl --user restart deepseek-dsh-web.service
```

排障：若插件启动报 `MODULE_NOT_FOUND`，先看 `readlink -f node_modules/@deepseek-ai/dsh-tools`
是否指向活动运行时的同一份文件；再确认 profile 的 `dsh.profile.bundles` 里有
`openharmony-debug-test-pipeline`。不要在 `/pipeline` 后面放自然语言；该命令只接受
`status` 或 `reset`，实际闭环任务请直接作为普通对话发送。

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

模型在每个阶段结束时通过 `scripts/pipeline_state.py` 把产物（`set`）、事件（`note`）与
**token 用量快照**（`tokens <stage>`，读取 `~/.dsh/storages/session_projcache.json` 中当前
会话的 uncachedInput/output/cacheRead 累计）写入状态文件；状态文件是闭环的唯一事实来源，
跨会话可续。每个阶段还把流程细节（证据路径、关键日志行、根因分析）追加写入
`~/.dsh/pipeline-runs/<日期>-<报告名>.md` 运行日志，供后续优化插件复盘。

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
