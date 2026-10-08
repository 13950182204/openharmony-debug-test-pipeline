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

本插件支持**两种部署形态**，同一份源码：

| | A. WSL 侧 dsh（原生） | B. DSH Desktop（宿主 Windows、执行 WSL） |
|---|---|---|
| 插件加载 | WSL 侧 Node | **Windows 侧 Node**（desktop profile） |
| 命令执行 | WSL（`bash` 就是 WSL） | WSL（`bash` 工具转发进发行版） |
| 适用 | `dsh web`（`:3080`）、headless、交接会话 | 桌面 App 的 GUI 会话（`:19387`） |
| 路径口径 | 宿主路径即模型路径，**无需配置** | 需 `wslPackageRoot` + `stateFile`（见下） |

> 两个运行时容易混淆，先认清分工：
> **桌面 App 能跑 agent loop**（系统提示、skill 目录、工具注册都在 Windows Node 里），
> 而 `bash`/`read`/`write` 由 `dsh-wsl-workspace` 转发进 WSL 执行。所以「脚本必须跑在 WSL」
> 与「插件在 Windows 侧加载」并不冲突——**Linux 工具链不构成移植障碍**，因为脚本从来不经过
> 这里的 Node，而是由模型在会话 bash 里调用。

### A. 装到 WSL 侧（原生，最简单）

```bash
pnpm build
dsh plugin --profile web add link:/home/cx/os/openharmony-debug-test-pipeline
```

**重启 dsh web 后生效**。验证：

```bash
dsh --profile web --dump-config | grep -A3 oh-debug-pipeline
```

> 注意：若 profile 的 `cordis.patch.yml` 里已手工 mount 过同名行，先移除，
> 避免插件双实例。

### B. 装到 DSH Desktop（GUI 会话里可用）

GUI 的插件页查的是 **npm registry**，本包未发布，所以按包名搜不到（会提示「未找到相关插件」）。
用**本地路径**安装：先 `pnpm pack` 出 tarball，再在弹窗里填 tarball 的 Windows 路径。

必须用 tarball / Windows 侧目录，**不能用 `link:` 或 `file:` 指向 WSL 内的仓库**：

- pnpm 会把 UNC 规格改写成 `/wsl.localhost/...`（不存在的路径）；
- 用 Windows 符号链接/junction 指到 `\\wsl.localhost\...` 也**不可行**——`Test-Path` 为真，
  但 Windows Node 无法穿越（`existsSync` 为假），插件会加载失败。

装完后在 desktop profile 的 `cordis.patch.yml` 加一行 config（**否则 skill 正文里的路径是
Windows 路径、模型在 WSL 里跑不动**）：

```yaml
- id: oh-debug-pipeline
  name: "openharmony-debug-test-pipeline"
  config:
    wslPackageRoot: /home/cx/os/openharmony-debug-test-pipeline
    stateFile: //wsl.localhost/Ubuntu-22.04/home/cx/.dsh/pipeline-state.json
    stateFileModel: /home/cx/.dsh/pipeline-state.json
```

- `wslPackageRoot`：把 `{{SKILLS_DIR}}`/脚本路径渲染成 WSL 可用路径；
- `stateFile`：**宿主**侧 `/pipeline status` 的 Node 读取用，写成 `//wsl.localhost/...`；
- `stateFileModel`：**模型**执行用，必须是 WSL 视角路径（`/home/cx/...`）。

两者指向同一个文件（实测两侧 `stat` 与 `sha256` 一致），但**形态必须分开**：Windows 侧的
`//wsl.localhost/...` 在 WSL 里既不存在、`python3` 还会静默退回**空状态**而不报错——
把 UNC 形态写进 skill 正文会让模型看到「（未开始）」这种假状态。

未配置 `wslPackageRoot` 时插件会在 Windows 宿主上打一条 warn 提示。

### DSH 版本兼容

- **构建与类型检查基线：`0.2.0-rc.2`**（`devDependencies` 的 `@deepseek-ai/dsh-*`），
  `package.json` 声明 `dsh.engines.dsh: ">=0.2.0-rc.2"`，并带
  `dsh.compatibility.dshReleases` 兼容矩阵。
- 插件不保留旧版本分支：0.1.2-rc.1 与 0.2.0-rc.2 的接触面（`defineTool`、`ctx.skills`、
  `ctx.commands`、`ctx.subagents`、`Agent`/`AgentOptions`）除 `SubagentResult.output` 由
  “可变”变为 `readonly` 外没有破坏性变更，因此**同一份代码在两个版本上都能加载**，
  只是不再为旧版本做额外适配。


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
| `stateFile` | `~/.dsh/pipeline-state.json` | 流水线状态文件（**宿主** Node 读取用） |
| `stateFileModel` | 同 `stateFile` | 同上文件的**模型**执行路径；Windows 宿主上必须给成 WSL 视角路径 |
| `wslPackageRoot` | 空 | 插件包的 WSL 路径；Windows 宿主上必填，否则 skill 正文是 Windows 路径 |
| `reviewLadder` | 两档（pro/flash） | 六维审查子代理的模型档位表 |

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
