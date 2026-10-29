# openharmony-debug-test-pipeline

OpenHarmony「修改-调试-测试」闭环 DSH 插件（运行时插件，bundle 聚合包形态）。

> 📖 **流程与操作手册见 [docs/操作手册.md](docs/操作手册.md)**（八阶段流程、CI 契约、状态字段、日常操作、异常速查、双宿主环境规则、能力边界）。

把五个 OpenHarmony 调测 skill 打包进一个 DSH 插件，串成一条八阶段闭环流水线，
每个 skill 同时保持独立可调用（模块化）。skill 已从 `~/.codex/skills` **拷贝进插件**
并针对 DSH 做了定制改造（见下文「DSH 定制清单」），仓库自包含，不依赖本机 Codex 环境。

## 插件包含什么

| 模块 skill | 作用 | 独立使用示例 |
|---|---|---|
| `openharmony-test-report-triage` | 测试报告分诊：失败表、源码定位、hilog 证据、根因分类、上游核查 | 「分析这份测试报告」 |
| `karpathy-guidelines` | 编码行为准则：思考先行、简单优先、外科手术式改动、目标驱动 | 「按 Karpathy 规范修这个问题」 |
| `glab-mr-submit` | GitLab MR 提交/审查/修复闭环：合规标题、标签、截图上传、六维 subagent 审查 | 「提交这个 MR」 |
| `openharmony-ci-orchestrator` | 平台 CI 观察与认领：读 GitLab 流水线、认领 Jenkins 门禁构建产物、phase-3 OTA/回归交接（**不触发构建**） | 「认领平台 CI 的构建并做 OTA」 |
| `openharmony-ota-upgrade` | OTA 升级闭环：预检、hdc 传输、updater、回连验证 | 「OTA 升级到设备」 |
| `openharmony-debug-loop` | **闭环编排**（本插件新增）：八阶段状态机串联以上模块 | 「跑一遍完整闭环」 |
| `openharmony-demo-loop` | **演示模式**：客户/评审面前 10 分钟走完八阶段，真跑与预置证据都明说，独立状态不污染真实流水线 | 「演示模式，用 mr260 跑一遍」 |

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

**一条 GitHub 地址装全**：插件页 → **添加插件**，填

```
https://github.com/13950182204/openharmony-debug-test-pipeline
```

安装后只有一个 bundle。GitLab 凭据（`@superjunier/dsh-gitlab-credentials`）自 1.5.0 起是本插件的
**内嵌子组件**：源码仍在仓库的 `packages/gitlab-credentials/`，构建产物
`packages/gitlab-credentials/lib` 随本包分发，`apply()` 把它作为子插件挂在同一棵树里，
浏览器半侧由本包的 `lib/client.js` 承担（设置页里仍有「GitLab 凭据」分节）。
**不需要**再单独安装第二个插件，也不需要改 pnpm 配置（不依赖任何传递 git 依赖）。

```bash
dsh --profile desktop --dump-config | grep -A3 oh-debug-pipeline
```

> 从 1.4.x 升级：profile 里旧的独立 `@superjunier/dsh-gitlab-credentials` 依赖与 bundles
> 选中项应当删掉（保留也不会双挂——内嵌组件有进程级单实例守卫，但两处都留着会让插件列表
> 多出一个空 bundle）。旧 `cordis.patch.yml` 里的 `- id: gitlab-credentials` 覆盖行可以留着，
> 没有对应行时会被忽略；凭据配置改到本行的 `config.credentials` 下（见下）。

离线/内网环境仍可退回本地 tarball 安装：先 `pnpm pack`，再在弹窗里填 tarball 的
Windows 绝对路径。必须用 tarball / Windows 侧目录，**不能用 `link:` 或 `file:` 指向
WSL 内的仓库**：

- pnpm 会把 UNC 规格改写成 `/wsl.localhost/...`（不存在的路径）；
- 用 Windows 符号链接/junction 指到 `\\wsl.localhost\...` 也**不可行**——`Test-Path` 为真，
  但 Windows Node 无法穿越（`existsSync` 为假），插件会加载失败。

装完后在 desktop profile 的 `cordis.patch.yml` 加一行 config（**否则 skill 正文里的路径是
Windows 路径、模型在 WSL 里跑不动**；`credentials.storeFile` 让桌面实例与 WSL 侧实例共用同一份凭据）：

```yaml
- id: oh-debug-pipeline
  name: "openharmony-debug-test-pipeline"
  config:
    wslPackageRoot: /home/cx/os/openharmony-debug-test-pipeline
    stateFile: //wsl.localhost/Ubuntu-22.04/home/cx/.dsh/pipeline-state.json
    stateFileModel: /home/cx/.dsh/pipeline-state.json
    credentials:
      storeFile: //wsl.localhost/Ubuntu-22.04/home/cx/.dsh/gitlab-credentials.json
      announceToAgent: false
```

- `wslPackageRoot`：把 `{{SKILLS_DIR}}`/脚本路径渲染成 WSL 可用路径；
- `stateFile`：**宿主**侧 `/pipeline status` 的 Node 读取用，写成 `//wsl.localhost/...`；
- `stateFileModel`：**模型**执行用，必须是 WSL 视角路径（`/home/cx/...`）；
- `credentials.storeFile`：内嵌凭据组件的凭据文件（宿主可写的 WSL 视角路径）；
- `credentials.announceToAgent`：权威实例在 WSL 侧时置 `false`，避免两处都向模型自我宣告。

两者指向同一个文件（实测两侧 `stat` 与 `sha256` 一致），但**形态必须分开**：Windows 侧的
`//wsl.localhost/...` 在 WSL 里既不存在、`python3` 还会静默退回**空状态**而不报错——
把 UNC 形态写进 skill 正文会让模型看到「（未开始）」这种假状态。

未配置 `wslPackageRoot` 时插件会在 Windows 宿主上打一条 warn 提示。

### DSH 版本兼容

- **构建与类型检查基线：`0.2.1-rc.3`**（`devDependencies` 的 `@deepseek-ai/dsh-*`）。
  `package.json` 同时声明：
  - `engines.dsh`：`>=0.2.1-rc.1 <0.3.0`（DSH 安装/启动时的兼容判定）；
  - `dsh.engines.dsh`：同一范围（插件市场展示用）；
  - `dsh.compatibility.dshReleases`：登记 `0.2.1-rc.1` / `0.2.1-rc.2` / `0.2.1-rc.3`。
- **0.2.1-rc.1 的破坏性变更（已适配）**：子代理 API 由 `ctx.subagents.start(name, request)`
  改为 **`ctx.subagents.startActivation(spec)`**（`provider` / `label` / `request` / `signal` /
  `delivery` 五个顶层字段，`request` 内不再携带 `label` 与 `signal`）。
  `mr_review_six` 已改为该形状，`delivery: 'caller'` 保持“只把结果返回给调用方”的原语义。
- **类型增强陷阱**：`ctx.subagents` 的类型来自 `@deepseek-ai/dsh-subagent` 的
  `declare module '@deepseek-ai/cordis'`；该包**必须被 import 进编译程序**才会生效，
  否则 tsc 报 `Property 'subagents' does not exist on type 'Context'`。
  本仓库因此保留一个显式类型 import（`SubagentActivation`）。
- **rc.2 / rc.3 没有新的破坏性变更**：`dsh-tools` / `dsh-agent` / `dsh-subagent` /
  `dsh-skill` / `dsh-commands` 的 `lib/` 与类型声明和 rc.1 **逐字节一致**（仅
  `package.json` 里的版本号与 peer 范围不同），因此调用代码无需再改；基线升到 rc.3 后
  `typecheck` / `build` / 40 项 vitest 全绿。真正需要改的是**依赖形态**（下节）。

### 宿主 SDK 的解析：`@deepseek-ai/dsh-tools` 只做 peer

DSH 0.2.1 起在 profile 上装了**模块解析拦截层**：profile 内插件（含 `link:` 安装）解析
`@deepseek-ai/*` 时会回落到**安装目录**（`app.asar`）里的宿主副本；`link:` 安装的插件
只要在 `peerDependencies` 里声明了包名，即便其仓库 `node_modules` 里有同名包，也会优先
路由到宿主副本。因此本插件：

- 把 `@deepseek-ai/dsh-tools` 声明为 **`peerDependencies`**（`^0.2.1-rc.1`），运行时由宿主提供；
- 只在 `devDependencies` 保留精确的 `0.2.1-rc.3` 供类型检查与构建；
- **没有** `postinstall` / `link-runtime-deps`，也**不往 profile 里装任何宿主 SDK**。

为什么不能把 `dsh-tools` 装进 profile（1.3.0 的旧做法）：profile 里一旦出现
`node_modules/@deepseek-ai/dsh-tools`，解析就优先落到这个副本——连 `dsh-base` 里的
`tools` 插件行本身都会加载它。两份模块实例的调度器 `Symbol` 不同，`tools` 服务无法
激活，启动直接失败（实测 0.2.1-rc.3：17 个插件挂在 `Missing services: tools`，
`1 required plugin did not activate`）。**“版本号相同”不够——模块实例必须相同。**

安装/升级 DSH 后的推荐顺序：

```bash
pnpm install          # 只装 devDependencies 的 rc.3，用于构建
pnpm build            # 产出 lib/（安装 tarball 时用的就是它）
dsh --profile desktop --dump-config   # 确认 bundles/patch 组合无误
```

排障：

- 先确认 profile 里**没有**宿主 SDK 副本：
  PowerShell：`Test-Path "$env:USERPROFILE\.dsh\profiles\desktop\node_modules\@deepseek-ai\dsh-tools"`
  应为 `False`；若为 `True`，删掉并重装（`pnpm remove @deepseek-ai/dsh-tools`）。
- 再确认 profile 的 `dsh.profile.bundles` 里有 `openharmony-debug-test-pipeline`。
- 不要在 `/pipeline` 后面放自然语言；该命令只接受 `status` 或 `reset`，实际闭环任务
  请直接作为普通对话发送。

## 配置

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `stateFile` | `~/.dsh/pipeline-state.json` | 流水线状态文件（**宿主** Node 读取用） |
| `stateFileModel` | 同 `stateFile` | 同上文件的**模型**执行路径；Windows 宿主上必须给成 WSL 视角路径 |
| `wslPackageRoot` | 空 | 插件包的 WSL 路径；Windows 宿主上必填，否则 skill 正文是 Windows 路径 |
| `reviewLadder` | 两档（pro/flash） | 六维审查子代理的模型档位表 |
| `credentials` | 见下 | 内嵌 GitLab 凭据子组件的配置：`enabled` / `announceToAgent` / `storeFile` |
| `demoStateFile` | `~/.dsh/demo/state.json` | 演示模式状态文件（宿主读）；与 `stateFile` 完全独立 |
| `demoStateFileModel` | 同 `demoStateFile` | 演示模式状态文件（模型口径），理由同 `stateFileModel` |

可在 profile 的 `cordis.patch.yml` 覆盖该行 config，或在 web GUI
Settings → 插件配置 中调整。

## 使用

- **完整闭环**：对 agent 说「跑一遍完整闭环 + 测试报告路径」，编排 skill 会按状态机推进，
  每个阶段结束向你汇报并等待授权（创建 MR / OTA 为真实动作，必须授权）。
- **单独一个模块**：直接说「提交这个 MR / 分析这份报告 / 认领平台 CI 的构建并 OTA / OTA 升级」，
  只加载对应 skill，不影响其他模块。
- **CI 阶段不触发构建**：MR 提交后 GitLab 平台自带 CI（`openharmony-mr-gate` → Jenkins
  `OpenHarmony-V6.1-MR-Gate` → `OpenHarmony-V6.1-MR-<芯片>`）自动跑门禁并归档固件/OTA；
  本插件只**只读观察**并**按 MR 身份认领**那次构建的产物，用于后续自动 OTA 与回归。
- **流水线状态**：`/pipeline status` 查询当前阶段与产物；`/pipeline reset` 重置。
- **演示模式**（对外演示专用，`~/.dsh/demo/` 独立状态，**绝不写真实流水线状态**）：
  `python3 scripts/demo/democtl.py preflight --case mr260` 做 8 项环境预检；
  `start --case mr260 --lane a` 起一条可跑通八阶段的演示线；`/demo status` 看进度与
  产物来源（● live / ◐ local-staged / ○ recorded）。完整 runbook 见
  `docs/演示模式.md`，脚本说明见 `scripts/demo/README.md`，配套 skill
  `openharmony-demo-loop`。

模型在每个阶段结束时通过 `scripts/pipeline_state.py` 把产物（`set`）、事件（`note`）与
**token 用量快照**（`tokens <stage>`，读取 `~/.dsh/storages/session_projcache.json` 中当前
会话的 uncachedInput/output/cacheRead 累计）写入状态文件；状态文件是闭环的唯一事实来源，
跨会话可续。每个阶段还把流程细节（证据路径、关键日志行、根因分析）追加写入
`~/.dsh/pipeline-runs/<日期>-<报告名>.md` 运行日志，供后续优化插件复盘。

## 开发

```bash
pnpm install
pnpm build          # tsc 产出 lib/（TypeScript 5.9，rewriteRelativeImportExtensions）
pnpm test           # vitest：frontmatter 解析 / 占位符替换 / 状态文件 / provider 接线
pnpm test:python    # python 单测：pipeline_state 契约校验 / ci_orchestrator / glab
```

### Windows 侧重装（DSH Desktop）

在 Windows 上跑（Node 20+ 任意）：`node scripts/desktop-reinstall.mjs [--profile desktop] [--dry-run]`

它做四件手工容易做错的事：

1. **显式补 PATH**：DSH Desktop 自带 pnpm 派生子进程时不会把 `node` 放进子环境 PATH，
   打包/安装阶段可能报 `'node' 不是内部或外部命令`。只补 `…/dependencies/node/bin`——
   **绝不把 Node 根目录加进 PATH**：该目录同时含 `bash.exe`/`wsl.exe`，进了 PATH 会顶掉
   WSL 的 bash 与 wsl.exe，而 Windows 侧 bash.exe 对 `\\wsl.localhost\…` 路径无法工作，
   会连带打断会话的 bash 工具。
2. **绝不把宿主 SDK 装进 profile**：以 `--config.auto-install-peers=false` 安装，安装后审计
   `profile/node_modules/@deepseek-ai`；出现 `dsh-tools`/`dsh-agent`/`dsh-llm`/… 即失败退出。
   rc.3 的解析拦截层会把插件对宿主 SDK 的 import 路由到 `app.asar` 里的副本（见上文
   「宿主 SDK 的解析」）；profile 副本反而会覆盖它、让 `tools` 服务起不来。
3. **校正 `dsh.profile.bundles`**：依赖装了但没进 bundles 就不会加载；脚本会补齐并打印清单。
4. **组合校验**：安装后跑 `dsh --profile <profile> --dump-config`，bundles/patch 结构有错
   会直接失败；不再用 `createRequire` 探测宿主 SDK（那正是 rc.1 时代的过时做法）。

安装完成后**重启 DSH Desktop** 生效。

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

- 真实动作（MR 创建、OTA 升级）必须用户授权，dry-run 先行；**不触发任何 Jenkins 构建**（门禁由平台 CI 驱动，插件只读观察 + 认领）；
- 设备操作前必须 `hdc list targets` 确认唯一目标；
- 状态文件损坏时自动备份为 `.bak-<时间戳>` 并重建；
- Jenkins / GitLab 凭据只从 `~/.config/openharmony-ci-orchestrator/jenkins.env`（0600）或进程环境读取，不写入状态文件或 MR 文本。
