# Changelog

本仓库（openharmony-debug-test-pipeline）的 OpenHarmony 兼容性闭环插件。版本号遵循语义化版本（SemVer）。

## [1.1.8] - 2026-10-10

为发布到 npm 做的前置修正（含改名）。

### 变更

- **子包改名为 `@superjunier/dsh-gitlab-credentials`**：原名 `@linxin666/dsh-gitlab-credentials`
  的 scope 属他人所有，`npm publish` 必然 403。同步更新 `cordis.patch.yml`、README、以及
  WSL/桌面两套 profile 的依赖名与 bundles。**`insert` 行的 `id: gitlab-credentials` 保持不变**，
  现有 `cordis.patch.yml` 配置覆盖无需改动。
- **`tsdown.config.ts` 不再硬编码包名**：改从 `package.json` 读取。`lib/client.js` 的
  `ModuleLoader.load({ id })` 必须与包名一致，否则浏览器半身静默不注册（服务端正常、
  设置页不出现）——双写必然在改名时漏改。
- **运行期依赖从 devDependencies 移入 dependencies**（两个包都改）：
  `lib/*.js` 有裸 `import '@deepseek-ai/dsh-tools'`（主包 `mr-review-tool.js`、子包 `index.js`）
  与 `schemastery`，但此前只在 devDependencies —— 消费者装完会 `MODULE_NOT_FOUND`。
  本机未暴露是因为本地 `node_modules` 已就位。
- **两个包都加 `prepublishOnly`**：`lib/` 被 `.gitignore` 忽略，没有该钩子时一次全新克隆的
  `npm publish` 会发出**不含 lib 的空包**（而 `dsh.bundle` 指向的正是 `lib/index.js`）。
- 复核：`pnpm pack` **不剥离**生命周期脚本（早先 tarball 缺字段是因为它们打包于本次改动之前）。

### 验证

- 主包：vitest 36 通过、python 15+15 通过、tsc 0 错误；打包项 57（无 `.bak`/`.pyc`）。
- 子包：tsc 0 错误、store smoke 12 项通过；产物 `id` 与包名一致。
- WSL profile 与桌面 profile 均已切到新名，组合树与加载测试通过
  （`name = gitlab-credentials`、`apply = function`）。

## [1.1.7] - 2026-10-08

发布前敏感数据复查发现：**本机备份文件被打进了发布产物**。

### 修复

- `files` 白名单补否定模式 `!**/*.bak-*` 与 `!**/*.log`。
  `skills/openharmony-ci-orchestrator/profiles/a333-2g-primary-standby.json.bak-20260912-172525`
  虽已被 `.gitignore` 忽略（`git check-ignore` 命中），但 `files` 以 `skills` 整目录放行，
  而否定模式此前只覆盖了 `__pycache__/*.pyc`——于是该备份被包含进 1.1.4 与 1.1.6 的 tarball
  （实测 `tar tzf` 可见）。内容为**过期设备池配置（4 台 DUT 串号）**，无 token/密钥。
  修复后打包项 58 → 57，`tar tzf` 不再出现 `.bak-*`。
- 未排除 `skills/glab-mr-submit/assets/defaults.env`：它是**刻意入库的配置模板**
  （`create_glab_mr.py:16` 读取、`SKILL.md:84` 文档化），排除会让 skill 开箱即坏；
  其中仅内网地址与用户名，无凭据。

### 复查结论（当前 1.1.7 基线）

- 工作树、全 git 历史、发布产物三处均**无** GitLab token（`glpat-*`/`v_*`）、私钥块、
  `_authToken`、密码字面量。凭据一律由环境变量注入（CI 单元文件用 `# GITLAB_HOST=` 注释模板）。

## [1.1.6] - 2026-10-08

`mr_review_six` 在真实环境完全不可用（本次 MR 实测发现）。

### 修复

- **`ctx.subagents.start()` 的第一个参数传错**（`src/mr-review-tool.ts`）：该参数是
  **provider 名**（`SubagentRuntime.expectProvider()` 按它查表），原先传的是运行标签
  `mr-review-<类别>`，真实环境一律报
  `no subagent provider registered for "mr-review-security"`。
  改为传 provider 名（缺省 `fork`，可用新增配置 `subagentProvider` 覆盖为 `spawn` 等）。
- 新增 `Config.subagentProvider`（schemastery 已声明，否则被剥离）。

### 验证

- 新增 `test/mr-review-tool.test.ts` 4 项：provider 名是 `fork` 而非 `mr-review-*`、
  可被配置覆盖、每类别各起一个子代理且共用同一 provider、请求带父代理/取消信号。
  此前该调用**无任何测试覆盖**，故编译通过与其余单测全绿都发现不了此缺陷。
- vitest 32 → 36 通过。

## [1.1.4] - 2026-10-08

打包刷新（无功能改动）：桌面 profile 原先引用的是 `1.1.2` tarball，而该包**早于**
「状态文件宿主/模型两口径」修复——若按 README 重装会静默回退到含假状态缺陷的版本。
本次重新打到 `1.1.4` 并更新 profile 引用，使"装到的"与"仓库里的"一致。

### 验证

- `tsc --noEmit` 0 错误；vitest 32 通过；python 套件 15+14 通过。
- 桌面 profile 的 spec 指向 `openharmony-debug-test-pipeline-1.1.4.tgz`，
  安装副本含 `stateFileModel` 与 `wslPackageRoot` 接线。

## [1.1.3] - 2026-10-08

让 `@superjunier/dsh-gitlab-credentials`（仓库内 `packages/gitlab-credentials`）也能在
**DSH Desktop（:19387）** 里编辑——用户要求后续在 desktop 维护该插件。

### 新增 / 增强

- **`Config.storeFile`**：凭据文件路径可覆盖（缺省仍为 `~/.dsh/gitlab-credentials.json`）。
  `CredentialStore` 本就支持 `filePath` 注入（原先仅测试用），本次接线到配置并在
  schemastery 中声明（否则会被剥离）。
- 桌面形态配置指向 WSL 那份文件，避免"桌面写 C:\Users\...\.dsh、WSL 读 /home/cx/.dsh"
  两份凭据分叉；同时 `announceToAgent: false`，权威实例仍是 WSL 侧，避免双实例向模型自我宣告。
- 子包 README 增加「DSH Desktop 形态」段（配置样板、打包/安装命令、Windows 侧行为差异）。

### 验证

- 子包 smoke test 12 项全过（新增 3 项：缺省路径、嵌套 `filePath` 落盘与 0600）。
- 桌面侧实测：`//wsl.localhost/.../gitlab-credentials.json` 可读，`hosts = 192.168.11.238`、
  `user = cx`；插件工具 `gitlab_cred_status` 已在桌面会话注册，设置页出现「GitLab 凭据」。
- 三份构建产物（仓库源 / WSL 侧 link / 桌面侧安装副本）sha 一致（`c7b086492ef6`），
  WSL 侧未配 `storeFile` 时回落缺省路径，行为不变。

### 已知行为差异（Windows 侧）

- 保存可用（校验走 HTTP）；`glab` 同步报 `glab binary not found`，但为 best-effort，
  令牌照常入库，仅界面提示同步失败；glab CLI 会话仍由 WSL 侧维护。

## [1.1.2] - 2026-10-08

支持「插件在 Windows 侧加载、命令在 WSL 侧执行」的 DSH Desktop 形态（与 `dsh-wsl-workspace`
同一模型），并修正一处会挡住该形态的护栏与一处打包缺陷。

### 新增 / 增强

- **新增 `src/wsl-paths.ts`：模型可见路径与宿主路径分离解析**
  - 背景：DSH Desktop 直驱 agent loop 时插件在 **Windows 侧 Node** 加载，而会话 `bash` 把命令
    转发进 **WSL**。插件用 `import.meta.url`/`homedir()` 推导出的宿主路径（`\\wsl.localhost\...`、
    `C:\Users\...`）模型跑不动。
  - 新增配置 `wslPackageRoot`（把 `{{SKILLS_DIR}}`/脚本路径渲染成 WSL 路径），并把状态文件
    拆成两个口径：`stateFile`（宿主 Node 读，Windows 上写 `//wsl.localhost/...`）与
    `stateFileModel`（模型交给 `python3`，必须是 WSL 视角的 `/home/cx/...`）。
    两者指向同一文件（实测 `stat`/`sha256` 一致），但形态必须分开——实测 UNC 形态在 WSL 里
    既不存在，`python3 --file //wsl.localhost/...` 还会**静默输出空状态**（「（未开始）」）
    而不报错，写进 skill 正文会让模型看到假状态。
  - `joinModelPath` 统一输出正斜杠：Windows 侧 `node:path.join` 会产出 `/home/cx/os\skills`
    这种混合路径，正是要避免的。
  - Windows 宿主且未配置 `wslPackageRoot` 时打 warn 提示，不静默降级。
- **`scripts/link-runtime-dsh-tools.mjs` 在 Windows 宿主直接跳过**：`~/.dsh/runtime` 那套只在
  Linux/WSL 侧存在，而链接的目的（与活动运行时的 `TOOL_RUNTIME_SCHEDULER` 共用同一 Symbol）
  对 Windows 宿主不适用——命令执行不经过这里的 Node。
- 移除 `package.json` 的 `os: ["linux"]`：该护栏会阻止装进 desktop profile，与本版本支持的
  形态冲突。
- README 重写「安装」段：给出 A（WSL 原生）/B（Desktop）两种形态的对照与配置样板，并记录
  实测结论——`link:`/`file:` 指向 WSL 路径会被 pnpm 改写成不存在的 `/wsl.localhost/...`，
  junction/symlink 指向 `\\wsl.localhost\...` 也**不可穿越**（`Test-Path` 真、Node `existsSync` 假），
  因此 Desktop 侧必须用 tarball 或 Windows 侧目录安装。

### 修复

- `files` 白名单改用否定模式排除 `__pycache__/*.pyc`：`skills/` 整目录被列入白名单时会把
  Python 运行期产物一起打进 tarball（实测 8 个 `.pyc`），而 `.npmignore` 在此不生效
  （pnpm 以 `files` 为准）。

### 验证

- `tsc --noEmit`（0.2.0-rc.2 基线）0 错误；vitest 29 通过（新增 9 个路径口径用例）；
  skill 自带 python 套件 15+14 通过。
- Windows 侧实测：以 stub ctx 调用插件 `apply`，6 个 skill + `/pipeline` + `mr_review_six`
  全部注册，`宿主=win32`，正文为 WSL 路径；与 WSL 侧共用同一 `pipeline-state.json`。

## [1.1.0] - 2026-10-07

### 新增 / 增强

- **对齐 DSH `0.2.0-rc.2`**（构建与类型检查基线）
  - `devDependencies` 的 6 个 `@deepseek-ai/dsh-*` 全部升到 `0.2.0-rc.2`；`package.json` 新增
    `dsh.engines.dsh: ">=0.2.0-rc.2"` 与 `dsh.compatibility.dshReleases`（插件管理器的兼容性
    门禁只认 `>=X.Y.Z[-prerelease]` 这一种写法，已按 `MINIMUM_RANGE_PATTERN` 核对）。
  - 新增 `os: ["linux"]` 护栏：阻止本插件被误装进 Windows 桌面 profile（其 skill 全部依赖
    WSL 工具链，装在 Windows 侧无法工作）。
  - `src/mr-review-tool.ts`：DSH 0.2.0 起 `SubagentResult.output` 为 `readonly`，
    `extractText` 入参由 `ContentBlock[]` 改为浅拷贝传入（同一份代码在 0.1.2-rc.1 与
    0.2.0-rc.2 上类型检查均通过，不保留版本分支）。
- **`dsh-tools` 链接改为跟随活动运行时**（`scripts/link-runtime-dsh-tools.mjs`）
  - 不再拿 `devDependencies` 的版本号当期望值（升级 DSH 后会直接把 `pnpm install` 打断），
    改为解析活动运行时：`~/.dsh/profiles/node_modules` → `~/.local/bin/dsh` wrapper 推导的
    `~/.dsh/runtime/<id>` → `~/.dsh/runtime/*` 中最新且携带 `dsh-tools` 的一个。
  - 声明版本与活动运行时版本**一致**才重链（保证 `TOOL_RUNTIME_SCHEDULER` Symbol 同一份实例）；
    **不一致**时跳过重链并保持 pnpm 解析结果（避免另一世代的 `dsh-llm`/`dsh-agent` peer 类型
    污染类型检查基线，实测会报 `[BRAND] is missing`）；解析失败降级为警告且 `exit 0`，不再阻断安装。
- **README 补充双运行时说明**：桌面 App（`:19387`，插件页「添加插件」属于它）与 WSL 侧 dsh
  （`:3080`，真正加载插件）的分工，以及「本插件只能装在 WSL profile」的原因与版本链接排障步骤。
- `package.json` 的 `version` 由 `0.1.0` 校正为 `1.1.0`（`v1.0.0` tag 时漏改，与 CHANGELOG 不一致）。

### 修复

- **测试断言与既有实现对齐**（两处陈旧断言在 `pnpm test:python` 下失败）
  - `openharmony-ci-orchestrator/tests/test_phase3_runner.py`：设备池已扩容（新增两台 standby），
    原断言写死「恰好两台设备」；改为「前两台的 role/串号固定，追加设备只允许 standby」。
  - `glab-mr-submit/tests/test_create_glab_mr.py`：分支名 ASCII 硬性规则落地后，纯中文摘要应报
    `MrError` 而不再返回中文后缀；改为断言报错（并保留一条英文摘要的正向用例）。
- **OTA 预检失败的两类原因纳入可重试范围**（`openharmony-ci-orchestrator/scripts/phase3_runner.py`）
  - `claim_run` 的 `--retry-preflight` 允许原因新增：`is not online in hdc list targets`（设备换机/重新插拔后
    串号变化）与 `source version is not allowed by package`（源版本不在包的 `version_list` 域内）。
    两者都是**未写设备**的纯预检失败，仍受"no device write + 重试上限 5 次"约束。
  - `references/phase3-a333.md` 新增"可升级如何判定"：包内 `version_list` 来自板级
    `VERSION.mbn`，选 DUT 前必须核对该机版本是否在其中（实测：包为 `OpenHarmony 6.1.0.31` 域，
    在线 DUT 为 `OpenHarmony 6.1.0.35` → 不可升级）。

- **交接会话被 systemd 连带杀死**（`openharmony-ci-orchestrator`）
  - `install_systemd_units.py` 生成的 `openharmony-ci-reconcile.service` 是 `Type=oneshot`，
    默认 `KillMode=control-group`：单元结束时会杀掉 cgroup 内所有进程，包括编排器以
    `start_new_session=True` 派生的交接 agent → 现象为 **agent 秒退、stdout/stderr 均 0 字节、
    构建成功也没有下一步动作**（历史 `build_succeeded` 后无进展的第二个根因）。
  - 单元改为 `KillMode=process`；已开机器的旧单元用 drop-in 补齐（`*.service.d/killmode.conf`）。
  - `SKILL.md` 的 Phase 2 安全段补充该要求与自查方式。

- **交接 dsh 版本/凭据 schema 不匹配导致 headless 静默退出**（环境修复，写入文档）
  - `DSH_BIN` 原先指向旧安装 `~/.dsh/dsh-browser`（0.1.0-rc.6），其 `credentials-local` 要求
    `.credentials.yaml` 为扁平 key→string；而该文件是当前 runtime（0.1.2-rc.1）写的
    `version: 1` + `refs`/`records` 结构 → headless profile 加载失败、进程直接退出。
  - 交接改为指向当前 runtime 的 dsh（`~/.dsh/runtime/dsh-012rc1/node_modules/.bin/dsh`），
    `DSH_BIN` 同步；`~/.local/bin/dsh` 用 wrapper 转发（**软链无效**：node shim 按 `$0` 解析基底目录）。
  - `SKILL.md` 增补版本/凭据 schema 的排查步骤。

### 规则（用户规定）

- **源分支名一律英文（禁止中文）** —— 中文分支名不进入 CI。
  - `glab-mr-submit/SKILL.md`：分支格式段新增硬性要求与"历史中文分支如何纠正"的步骤
    （同一 commit 建英文分支 → 用英文分支新建 MR → 关闭/删除旧 MR → 如需 CI 用英文分支+同 SHA 重触发；
    旧分支上仍有构建在跑时先等它结束再删分支，或用 `state_event=close` 显式关旧 MR）。
  - `glab-mr-submit/scripts/create_glab_mr.py`：`normalize_branch_suffix()` 改为只保留 ASCII 字符
    （此前正则显式允许 `\u4e00-\u9fff`，会产出中文分支），并在最终分支名上增加
    `[0-9A-Za-z._/-]+` 校验；纯中文摘要无法推导时明确报错并要求 `--branch` 显式给英文名。

- **OTA 目标只取"当前在线且可升级的 DUT"** —— 不因 profile 固定串号缺席而阻塞。
  - `openharmony-ci-orchestrator/SKILL.md` 的 Phase 3 安全段与
    `references/phase3-a333.md`：新增目标设备选择规则（先 `hdc list targets`；优先池内在线设备，
    primary 优于 standby；池内无在线设备时任选一台在线且通过产品身份与包预检的同型号 DUT；
    选中的 serial 与理由写入 CI run 状态与运行日志；离线设备不等待；OTA 后在同一台设备回归）。
    放宽的只是"选哪台"，runner 的源 SHA/产物/身份/版本校验强度不变。

## [1.0.0] - 2026-09-09

首个发布版本。基于 OpenHarmony A/F/E XTS 闭环实测（报告 acts-LTS-f / 2026-09-08-17-42-39）的迭代成果。

### 新增 / 增强

- **OTA 预检设备版本交叉校验**（`openharmony-ota-upgrade/scripts/ota_preflight.sh`）
  - 新增 `--device-serial <serial> [--hdc <path>]`：读取设备 `const.product.software.version` 并与包 `version_list` 逐行**精确**比对。
  - 背景：updater 的 `CheckVersion`（`base/update/updater/services/updater_preprocess.cpp`）用软件版本做精确匹配；`VERSION.mbn` 若只含 OS 版本（如 `OpenHarmony 6.1.0.31`）会导致 OTA `Version Check Fail`。此校验在投递前拦截并给出修复提示（把产品版本如 `1.3.0` 加入 `VERSION.mbn` 后重新打包）。
  - 向后兼容：不带 `--device-serial` 时行为不变。

- **MR 截图黑图拦截**（`glab-mr-submit/scripts/create_glab_mr.py`）
  - `ensure_screenshot_files` 增加 `_is_blank_or_dark_image` 亮度校验（近黑/近空白即拒绝），避免提交无效黑图。
  - 仅在本地装有 Pillow 时启用；未安装则跳过，不阻断上传。

- **文档补充**
  - `openharmony-ota-upgrade/references/a333-newpines.md` 与 `SKILL.md`：补 OTA 版本域规则（产品版本 vs OS 版本）、updater 精确比较语义、`hdc.exe` 本地路径须为 Windows 路径（`D:\\...`）的说明。
  - `openharmony-ci-orchestrator/SKILL.md`：补同一 Jenkins 作业单工作区/执行器、多 MR 触发**排队串行**构建的说明。

- **并入既有功能**：`openharmony-issue-pipeline` 插件包、CI 编排对 `JENKINS_TRUSTED_ORIGINS` 的支持、A333 横屏控制中心（`a333-ui-landscape-control-center.json`）profile、`phase3_runner` 增强、`glab-mr-submit/SKILL.md` 等。

### 修复

- `ci_orchestrator.py`：允许通过 `JENKINS_TRUSTED_ORIGINS` 环境变量显式放行 Jenkins 自引用 origin（自引用地址与访问地址不一致时的 origin 校验失配）。

### 已知/注意事项

- `ota_preflight.sh` 的版本交叉校验需要 `--device-serial` 显式传入；默认仅做 ZIP 完整性/清单/版本打印。
- 截图校验依赖 Pillow；缺失时校验跳过（不影响正常上传）。
