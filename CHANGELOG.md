# Changelog

本仓库（openharmony-debug-test-pipeline）的 OpenHarmony 兼容性闭环插件。版本号遵循语义化版本（SemVer）。

## [1.2.3] - 2026-10-15

**回归阶段：OTA 成功后重跑测试用例（用户口径）已打通并真机验证。**

### 新增

- `scripts/test_harness.py`：解析 xdevice 报告并判定 —— 请求的测试项**全部用例通过才 PASS**；
  任一失败 → FAIL；模块没跑起来（`unavailable='1'`，如设备条件不满足）或没有任何用例记录 →
  `INCONCLUSIVE`。数据源优先 `result/<模块>.xml`（按模块权威），退回 `summary_report.xml`
  （那里 `<testsuite name>` 是**测试类名**，模块名在 `modulename` 属性——实测踩到）。
- `phase3_runner` 新增受信任回归动作 `kind=xts-rerun`：模块清单取 profile `modules`，否则取
  流水线状态 `artifacts.failedModules` / `artifacts.modules` 的键（即"分诊出错的整块测试项"）。
- `profiles/xts-rerun-lts-f.json`：指向本地 LTS 运行器 `/mnt/d/ohos/XTS6.1/office/acts-LTS-f`。

### 关键实现细节（都由真实故障换来）

- **判定绝不能看退出码**：xdevice 对"模块根本没跑起来"同样 exit 0（报告里只有 `unavailable='1'`
  与 `message=[Environment-...]`）。已用真实报告验证会记 `INCONCLUSIVE` 而非误判通过。
- **xdevice 必须在 Windows 侧执行**：WSL 的 xdevice 连不到 hdc 服务（Windows hdc 只监听自身
  127.0.0.1:8710，WSL 为 NAT 不可达；WSL 原生 hdc 又看不到设备），且 WSL 内核无 `vhci-hcd`
  使 usbipd 路线也不通。故 profile 用 `runner_host: "windows"` + `windows_python`，
  由 WSL runner 经 interop 调用，路径用 `wslpath` 自动换算。
- **cwd 必须是运行器目录**：否则报 `[Environment-0101015] Task file does not exist`（`-c` 不替代）。
- **interop 输出容错解码**：cmd.exe 按控制台代码页输出 GBK，严格 utf-8 会让整步抛
  `UnicodeDecodeError`（已改为 `errors="replace"`）。

### 真机验证

同一台 A333（MR 226 门禁固件）上：`ActsAACommandPrintSyncTest` → `total=16 passed=16 failed=0`
→ **PASS**，报告落在 `D:\ohos\XTS6.1\office\acts-LTS-f\reports\ci\<时间戳>\`，
WSL 侧解析同一份报告得出结论。python 单测 55 → 68 项。

## [1.2.2] - 2026-10-15

端到端流程审计后的 6 项加固（全部带单测），并新增 [docs/操作手册.md](docs/操作手册.md)。
审计依据：12 个真实 run、29 次归档失败尝试、artifact 占用 8.43 GB 的实测数据。

### 修复

- **artifact 只增不删（已占 8.43 GB）**：新增 `prune_artifacts()`，按"最新 N 个 + 超过 N 天"回收
  OTA 包（`--prune-days` / `--keep-artifacts`），**进行中的 run 绝不触碰**；run 的 JSON 审计保留。
  实测一次回收释放 2.26 GB。
- **`blocked` 是死状态**：新增 `ci_orchestrator.py rearm --run-id`，把因环境问题终结的 run 拉回
  可推进状态（清 `stale_source` / `adopt_error` / 门禁查找错误），并写 `rearmed` 审计事件。
- **`stale_source` 永久污染 run**：判据从"MR 当前 head"改为"**流水线实际构建的提交**"
  （`pipeline.sha`），平台对登记提交重跑后自动解除并记 `stale_source_cleared`。
  实测旧逻辑会让"MR 改一版"永久卡死该 run。
- **离线设备消耗重试预算**：新增 `DeviceOfflineError` 与 `offline` 尝试状态；离线设备**不写入
  `attempts`**（不再污染 `no_device_write`/`retry_reason`）。池内全离线时落
  `phase3.status=waiting_for_device`（非失败），按 300s 冷却自动重试且**不消耗** `unit_restarts`。
  直接起因：真实 run 因池内 3 台离线用掉 4/5 次重试预算。
- **MR 证据评论缺关键信息**：`mr_note_body` 现输出身份差异（`identity_notes`）、
  "已在本构建上未重复刷机"（`skipped_write`）、升级后设备版本/commitid/boot 状态、离线设备清单。
- **手工 drop-in 不可复现**：`install_systemd_units.py` 现生成
  `openharmony-ci-phase3@.service.d/retry-preflight.conf`（`CI_PHASE3_RETRY_PREFLIGHT=1`），
  换机重装不再丢失 phase-3 的合法重试开关。

### 新增文档

- [docs/操作手册.md](docs/操作手册.md)：八阶段流程、平台 CI 契约、状态机与字段表、日常/恢复/
  巡检操作、异常速查表、双宿主环境规则、身份口径、能力边界与自检清单。

## [1.2.1] - 2026-10-15

真机 OTA 演练（MR 226 / A333 门禁构建 #31）暴露并修掉的问题。**两台 A333 现均运行
`OpenHarmony-6.1.0.35`，其 `const.product.firmware.commitid` 精确等于 MR head
`b0ca72ac95eeb38578e31150dc853ec92c6d358f`**；MR 226 上的 CI 证据评论已按新格式更新。

### 修复（全部由真机演练暴露）

- **双宿主 hdc 传包必然失败（致命）**：本机 `hdc` 是 Windows `hdc.exe` 的 WSL 包装器，
  会把本地路径当 Windows 路径拼成 `\\wsl.localhost\Ubuntu-22.04\home\cx\/home/cx/...`，
  传输以 `[Fail]Error opening file` 结束，整条 OTA 在投递阶段失败。现在所有本地文件参数
  （`hdc file send` / `file recv`）先经 `wslpath -w` 转换；原生 Linux hdc（无 `wslpath`）保持原路径。
  已用 1 MiB 探针双向验证（旧写法失败、新写法成功）。
- **`hdc.exe` 卡死会拖住整个单元**：经 WSL interop 调用的 Windows 进程偶发不理会 Python 侧超时
  （子进程被杀但 `/init` 仍持有 stdout 管道），单元因此无限阻塞（实测卡 13 分钟）。
  所有外部命令现包一层 `timeout -s KILL N`，由内核计时兜底。
- **重试门禁自相矛盾**：`no_device_write` 把"失败的 transfer"也算作写过设备，导致
  "修好传输路径后重跑"永远不可能；而允许重试的原因里又包含传输失败。判据改为只看
  `write_updater` / `reboot_updater`；并补充"取 console 瞬时网络失败（Connection reset）"为可重试原因。
- **身份硬门禁会误杀成功升级**：门禁包内置通用产品身份，升级后 `const.product.name` 由
  `DHong-A333-Development-Board` 变为 `OpenHarmony 3.2`。按用户口径（identity 不做硬限制，
  只要能升级且升级正确），产品名/型号/品牌/厂商降级为**证据**（记入 `attempt.identity_notes`），
  硬门禁只剩"有 `write_updater` + 正常启动完成"，"升对了没有"由
  `firmware.commitid == 源 SHA` + `updater_result=pass` + boot completed 共同证明。

### 新增

- **幂等"已在本构建上"**：设备固件 commitid 已等于本次源 SHA 时，`perform_ota` 直接返回
  `already_on_target`——不再重复刷机（避免无意义重启），仍照常完成回归与证据链。设备池里另一台
  A333 正是这种情况，本轮即以此路径通过。
- `test/tools/undefined_names.py` + `pnpm run check:names`：静态"用了但没绑定"的检查，并入
  `pnpm run test:python`。直接起因是本次重构中一次替换误删赋值行，直到设备执行才以
  `NameError` 暴露（单测覆盖不到该分支）。

### 实测证据（MR 226）

| 项 | 值 |
| --- | --- |
| 平台流水线 | `410` success → 汇总门禁 `#50` SUCCESS（`gate-plan.json` chips `[rk3568, a333]`） |
| 认领构建 | `OpenHarmony-V6.1-MR-a333 #31`（四参数逐项相符） |
| 包 | `openharmony_V6.1/out/ota/update.zip`，794,042,102 字节，SHA-256 `fb99a437…ac9a7` |
| 刷机 | `updater_result = /data/updater/updater_full.zip\|pass\|\|install_time=77.83\|` |
| 升级正确性 | 两台设备的 `const.product.firmware.commitid` = MR head SHA；`bootevent.boot.completed=true` |
| 回归 | `a333-ota-smoke-v1` PASS；无功能回归 profile ⇒ `INCONCLUSIVE` |
| MR 证据评论 | `<comments>`（带 run 标记，重复执行按 run id 幂等更新） |

## [1.2.0] - 2026-10-15

**移除自触发 Jenkins，改为依托平台自带 CI**：MR 提交后 GitLab 平台已自动跑固件门禁，
插件不再自己 POST `buildWithParameters`；Jenkins 端点从 `192.168.13.121:8080` 切到
平台门禁主机 `192.168.97.54:8080`，后续自动 OTA 与回归使用门禁构建产出的包。

### 背景（实测取证）

- 项目 `harmony/system/v9611/openharmony_V6.1` 已配外部 CI
  `config/mr-gate/gitlab-entrypoint.yml@harmony/system/v9611/jenkins-auto-build`：
  MR 事件 → job `openharmony-mr-gate`（专用 Runner `OpenHarmony MR gate bridge`，IP
  192.168.97.54）→ `bridge.py` POST `OpenHarmony-V6.1-MR-Gate`
  （`MR_IID/SOURCE_SHA/TARGET_BRANCH/GITLAB_PIPELINE_ID`）→ 门禁按 diff 路由顺序
  `build job:` `OpenHarmony-V6.1-MR-{rk3568,a333}`，归档
  `gate-plan.json` + `openharmony_V6.1/out/ota/*.zip`（`update.zip` 或 `updater_full.zip`）。
- 项目设置已是"流水线成功才允许合并""禁止跳过流水线"；目标分支白名单
  `v6.1.0.31_release`/`v6.1.0.35_lts`，其他分支只有明确标记的轻量任务。
- 编译参数（`BUILD_MODE/FIRMWARE_TYPE/FIRMWARE_BRANCH/Openharmony_Devices`）由门禁任务
  **服务端固定**，调用方只给 MR 身份四元组；因此旧插件按 `FIRMWARE_BRANCH/BUILD_MODE/
  FIRMWARE_TYPE` 触发与核对的做法整体失效。
- 旧主机仍在服务旧作业，但旧账号 token 打到新主机是 403；门禁服务账号权限限定在门禁作业。

### 移除（Breaking）

- `trigger_jenkins_build.py` → 重命名并瘦身为 **`jenkins_api.py`**（只读）：删除
  `buildWithParameters` 入队、queue 解析、crumb 获取、`--dry-run/--verify-job/--wait-seconds/
  --allow-anonymous/--parameter` 触发 CLI。代码中已无任何构建触发端点。
- `ci_orchestrator.py`：删除 `register`（只接受 phase-1 触发结果）与全部 queue/触发分支
  （含每 tick 产生噪音的 `queue_unresolved`），删除 LLM 交接会话 `launch_agent`/`agent_prompt`。
- `reconcile` 不再把"自己触发的构建"当状态源。

### 新增

- **`gitlab_pipeline.py`**：只读平台观察层（`glab api`）。读 MR 的 `head_pipeline{id,status,sha}`、
  流水线详情、job trace；从 trace 提取 `Jenkins gate: <build url>`；把平台状态映射为
  `gate_pending/gate_succeeded/gate_failed/gate_aborted/gate_not_in_scope`。
- **`orchestrator_config.py`**：`jenkins.env`（0600）读取统一入口（进程环境 > 配置文件），
  供 orchestrator / runner 共用；另提供 `GLAB_BIN` 等外部命令解析（PATH → `~/bin`/`~/.local/bin`）。
- `ci_orchestrator.py observe`：登记 run（`--mr-iid/--source-sha/--source-branch/--target-branch/
  --phase3-profile`），只读读一次平台流水线；`--phase3-profile` 的 `jenkins.job` 决定认领哪个芯片。
- `adopt_gate_build()`：按 `MR_IID/SOURCE_SHA/TARGET_BRANCH` 在
  `OpenHarmony-V6.1-MR-<chip>` 最近构建里认领 `result=SUCCESS` 的那一个，并用门禁
  `gate-plan.json`（console 打印 + 归档）交叉核对本次确实构建了该芯片；认领前**重新读 MR
  head SHA**，不同即 `blocked/stale_source`。
- `ci_orchestrator.py note`：补上文档里一直引用但缺失的审计事件入口（记录 DUT 选择等决策）。
- `reconcile --include-terminal/--prune-days N`：默认跳过终结 run，按天归档旧 run 到
  `runs/archive/`（保留审计），消除旧版每分钟重复处理十几个死 run 的 journal 噪音。
- `phase3_runner.py`：新增 `resolved_artifact_patterns()` 与 `select_artifact_name()`：
  按 profile 的 `artifact.preferred` **有序候选**逐个尝试，每个候选必须恰好命中一个文件
  （0 命中退下一个候选，>1 命中立刻报错并列出实际归档）；profile 校验新增
  `required_parameters` 与 `evidence_parameters` 语义；MR 证据评论新增平台流水线与门禁构建行；
  "产物候选解析失败"纳入可重试原因（未写设备，修正候选后可重试）。
- `observe` / `adopt --dry-run`：把整条链路登记为**包演练**，由 `phase3_unit_entry.py` 读
  `phase3.dry_run` 转发给 runner 的 `--dry-run`（下载并校验真包，绝不访问设备）。
- `install_systemd_units.py`：`jenkins.env` 模板改为新口径（`JENKINS_BASE_URL=http://192.168.97.54:8080`、
  `GITLAB_HOST/GITLAB_PROJECT/GLAB_BIN`）；单元显式 `Environment=PATH=~/bin:...`（systemd 最小
  PATH 找不到 glab）；reconcile 单元带 `--prune-days`；`--with-webhook` 才启用 legacy webhook。

### 修复（其中两条由本轮真实演练暴露）

- **手动 CLI 读不到凭据**：`jenkins.env` 原先只由 systemd 的 `EnvironmentFile=` 注入，
  人在会话里直接跑 `ci_orchestrator.py` 时 `credentials()` 只读进程环境 → 一律 403。
  现在凭据按"进程环境 > `jenkins.env`"解析（`configured_value`）。
- **产物名歧义**：门禁芯片作业同时归档 `update.zip` 与 `updater_full.zip`（实测 a333 #31），
  原先"通配命中恰好一个"的规则在执行真实取包时必然失败；改为有序候选（`update.zip` 优先，
  `updater_full.zip` 兜底），任一候选命中多个仍拒绝。
- 认领/下载遇到 401/403 时，错误文本改为可操作提示（指出要改 `jenkins.env` 的哪几项），
  不再只是一段登录页 HTML 截断。
- `assert_profile_matches` 把产品参数从"硬门禁"改为"期望证据"（门禁不再下发它们），
  同时新增门禁身份对账：构建参数的 `SOURCE_SHA`/`MR_IID` 必须等于注册值。
- 产物模式解析修正：旧代码按精确路径取包，门禁归档名可能是 `update.zip` 或
  `updater_full.zip`，旧实现会直接判失败。

### Profile

- `a333-2g-primary-standby`：`jenkins.job` → `OpenHarmony-V6.1-MR-a333`；
  `required_parameters=[MR_IID,SOURCE_SHA]`；`Openharmony_Devices`/`XTS_PRODUCT_*` 移入
  `evidence_parameters`（出现即核对，缺失由设备身份校验兜底）；
  `artifact.relative_path` → `openharmony_V6.1/out/ota/*.zip`。

### 测试与验证

- python 单测 15 → 38 项：新增平台观察、认领、`stale_source`、芯片不匹配、多包拒绝、
  gate 构建被清理时的芯片构建兜底、旧自触发 run 的退役收敛、prune/terminal 跳过、以及
  "代码里不存在构建触发端点"的契约断言；测试用真实 HTTP 假 Jenkins + 假 `glab`
  （`GLAB_BIN` 指向临时 shim），不联网、不碰设备。
- **真实取包演练通过**（97.54 只读服务账号已配入 `jenkins.env`）：MR 226 →
  GitLab pipeline `410`（success）→ 认领汇总门禁 `#50`（SUCCESS，`gate-plan.json` chips
  `[rk3568, a333]`）→ 认领芯片构建 `OpenHarmony-V6.1-MR-a333 #31`（四参数逐项相符）→
  下载真实 `openharmony_V6.1/out/ota/update.zip`：**794,042,102 字节**，
  SHA-256 `fb99a437…ac9a7`，包内目标版本 `OpenHarmony 6.1.0.32`，
  源版本白名单 `['OpenHarmony 6.1.0.31', '1.3.0']`（**含设备当前 `1.3.0`**，即这台 DUT 现在可升级），
  console 检出证据 `HEAD is now at a1b2c3d4e5f` ⇒ `source_sha.verified=b0ca72ac95e`；
  全程无 HDC 动作。

## [1.1.14] - 2026-10-14

把 `scripts/desktop-reinstall.mjs` 升级为**可反复一键恢复**：desktop profile 在本机被重置过
多次（每次都会丢掉 `dsh.profile.bundles`、依赖，以及 `cordis.patch.yml` 里的插件配置行），
旧版助手有两处在重置面前会失效。

### 修复 / 增强

- **版本不再写死**（关键）：旧版把 tarball 文件名钉在 `1.1.10` / `0.1.1`，仓库升到 1.1.13 /
  0.1.2 后助手会去找一个不存在的文件并直接 `exit 1`。现在从各包自己的 `package.json`
  现读版本，并派生 tarball 名（scope 按 pnpm 规则用 `-` 连接）。
- **tarball 缺失就地重打包**：`~/.dsh/plugins` 会被 profile 重置连带删除（本机已发生多次），
  旧版只报"缺少 tarball"让人手工打。现在缺失即用 DSH 自带 pnpm 现场 `pack`，
  一条命令从零恢复。
- **补回插件配置行**：profile 重置会连 `cordis.patch.yml` 里 `oh-debug-pipeline` 与
  `gitlab-credentials` 的 config 一起丢掉，而这两行是"skill 正文路径可用"的前提
  （宿主 Windows / 执行 WSL：`wslPackageRoot`、`stateFile`、`stateFileModel`、`storeFile`、
  `announceToAgent`）。现在缺失即按默认值补回、写前先备份，已存在则不改动（尊重用户改过的值）。
- 安装后自检改为**比对仓库版本与实际安装版本**，不一致即非零退出（旧版只打印实际版本）。

### 验证（本轮实测）

- 干跑零写盘（无备份产生、不创建 plugins 目录、不改 patch）。
- 正式跑：重打包两个 tarball → 登记 2 插件 + 4 宿主 SDK 包 → 补 2 条配置行 →
  `RESOLVE_OK`（插件所需 9 个依赖全部可解析）→ 副本矩阵 26 包 / 0 嵌套副本 / 仅 4 种版本
  （`0.2.0-rc.2` 统一）。
- 两插件版本与仓库一致：`openharmony-debug-test-pipeline@1.1.13`、
  `@superjunier/dsh-gitlab-credentials@0.1.2`；6 个 OpenHarmony skill 与
  `mr_review_six`、`gitlab_cred_status` 工具已在新注册代际中生效。

## [1.1.13] - 2026-10-14

修复「用 GitHub 仓库地址安装插件」不可用的问题（实测发现两个独立原因）。

### 诊断

用 GUI 的「添加插件」填 `https://github.com/13950182204/openharmony-debug-test-pipeline`
安装后，profile 里出现 `openharmony-debug-test-pipeline = github:13950182204/...`，但：

1. **主包装了不工作**：安装目录里**没有 `lib/`**，而 `main` 指向 `lib/index.js`。
   原因是 `lib/` 是 `tsc` 产物且被 `.gitignore` 忽略，**git 安装不会构建**——
   拿到的是一份「入口指向不存在文件」的包，加载不出任何代码，且失败是静默的。
2. **子包不会跟着装**：`packages/gitlab-credentials` 在 pnpm workspace 里，根
   `package.json` **没有把它声明为依赖**，所以任何包管理器都不会顺带安装它；
   且它**未发布到 npm**（`npm view` 404），因此只能单独用 tarball 或 git 子目录安装。

### 为什么不能靠 `prepare` 解决（实测三重不可行）

在临时 git 仓库上原样复现：

- 仓库里 `.gitignore` 掉 `lib/` + `prepare: node build.mjs` → 安装被 pnpm 拦下：
  `ERR_PNPM_GIT_DEP_PREPARE_NOT_ALLOWED`（git 宿主的包必须显式进 `allowBuilds`）；
- 按提示把该包写进 `allowBuilds` 后仍失败：pnpm 接着跑 **`npm install`** 去装构建依赖，
  而安装环境里通常没有 npm → `ERR_PNPM_PREPARE_PACKAGE: npm-install`；
- 且 `allowBuilds` 的键**带 commit 哈希**，每次提交都要改。

### 修复

- **把两个分发入口的构建产物入库**：`lib/`（主包，18 文件 ~100K）与
  `packages/gitlab-credentials/lib/`（子包，4 文件 ~56K），并在 `.gitignore` 里只为
  这两个路径开例外（`packages/openharmony-issue-pipeline` 用包级 `.gitignore` 继续排除）。
  这样 GitHub URL 安装拿到的包**直接可用**，无需构建、无需 allowBuilds。
- 代价与约束（已写进 `.gitignore` 注释）：改 `src` 后必须重建并连同产物一起提交，
  否则仓库里是旧代码；主包 `pnpm run build`，子包
  `pnpm --dir packages/gitlab-credentials run build`。

### 验证

- 入口齐备：`lib/index.js`、`lib/types/index.d.ts`、子包 `lib/index.js` + `lib/client.js`；
  重建后 `git status lib/` 无 diff（产物与 src 同步）。
- vitest 40、python 15+15 全过。

### 补充安装指引

`openharmony-debug-test-pipeline` 与 `@superjunier/dsh-gitlab-credentials` 必须**各装一次**：
子包不会被主包带装。推荐用 `scripts/desktop-reinstall.mjs`（tarball 方式，含 PATH 与副本自检），
或用 GUI 分别填两个 tarball 路径。

## [1.1.12] - 2026-10-14

**定位并本地修复** `Unknown agent preset: wsl-standard` 的根因（DSH Desktop 上 WSL 变体预设
生成失败），并把该修复固化进重装助手。

### 诊断

- 该错误与 persona 冲突属同一族，均由 `dsh-wsl-workspace` 的**变体生成**失败引发；两条错误文案
  分别来自 `dsh-agent-preset-registry`（`Unknown agent preset`）与
  `dsh-system-prompt`/`dsh-subagent`（`deployment:persona-prefix`），与本仓库两个插件无关
  （两者产物中 `persona`/`agentPresets`/`wsl-standard` 零匹配）。
- **根因（实测）**：`dsh-wsl-workspace` 启动期直接 import 的宿主 SDK 包
  `@deepseek-ai/{dsh-fs,dsh-fs-local,dsh-shell,dsh-tool-fs-search}` 在 desktop profile 下
  **无法解析**（`Cannot find module`）——DSH Desktop 把宿主打在 `app.asar` 里，Node 沿文件系统
  向上找裸包名走不进归档。插件 CHANGELOG 中"Desktop 把宿主打在归档包里…这条路永远走不到宿主
  里面"即指此；失败被 `catch` 吞掉，界面上只表现为 `Unknown agent preset: wsl-standard`。

### 修复

- 将上述 4 个包以**与 runtime asar 相同的版本（0.2.0-rc.2）**装进 desktop profile：不引入第二个
  版本，仅把归档里的那一份在 profile 侧变得可解析。修复后插件所需的 9 个依赖（含
  `@deepseek-ai/schemastery` 与 `js-yaml`）全部可解析。
- `scripts/desktop-reinstall.mjs`：新增 `HOST_SDK` 段随插件一起安装，并新增**依赖可解析性自检**
  （用 `createRequire(插件入口)` 逐个 resolve，失败即报 `RESOLVE_FAIL`）；同时修掉干跑仍会写
  `package.json.bak-desktop-reinstall` 的缺陷（dry-run 现在零写盘）。

### 验证

- 副本矩阵自检：`@deepseek-ai` **26 个包、0 个嵌套副本**，除 `cordis@4.0.4`/`cosmokit@1.8.5`/
  `schemastery@3.18.4` 外**全部 0.2.0-rc.2**（与 runtime 的 asar 副本同版本）。
- 插件侧：`mr_review_six`、`gitlab_cred_status` 工具与 6 个 OpenHarmony skill 已在重启后的桌面
  会话中注册（新注册代际生效）。
- 生效边界：WSL 变体预设由插件在启动期生成，**需重启 DSH Desktop 后确认**模式选择器里是否出现
  `WSL · …` 条目。

## [1.1.11] - 2026-10-13

新增 Windows 侧重装助手（`scripts/desktop-reinstall.mjs`），并补齐本版本条目（发版时漏记）。

### 新增 / 增强

- **`scripts/desktop-reinstall.mjs`**：在 Windows 上一键把插件与宿主 SDK 依赖装回 desktop profile，
  内置三项手工易错点的防护——显式补 PATH（**只补 node 的 bin 目录**，绝不加 Node 根目录，否则
  `bash.exe`/`wsl.exe` 会顶掉 WSL 的 bash 而打断会话）、安装后强制副本矩阵自检（每个核心包
  只允许一个版本、禁止嵌套副本）、校验 `dsh.profile.bundles` 含两个插件。
- README 增加「Windows 侧重装」段落，含上述 PATH 陷阱说明。

### 背景

DSH Desktop 自带 pnpm 派生子进程时**不会把 node 放进子环境 PATH**，两包 `postinstall` 里的
`node …` 一律报 `'node' 不是内部或外部命令`（实测）；这是环境前提，脚本自身无法自愈，
故由宿主侧助手显式提供。

## [1.1.10] - 2026-10-13

重装两个插件（desktop profile）时暴露的两个真异常——都属于「先修复再安装」的范畴。

### 修复

- **`link-runtime-dsh-tools.mjs` 的版本守卫在 1.1.9 后整体失效**（`scripts/`）
  1.1.9 把 `@deepseek-ai/dsh-tools` 从 devDependencies 挪到 dependencies，而脚本仍只读
  devDependencies → 读到 `undefined` → 守卫失效、**无条件**把依赖树链到活动运行时。
  实测后果：仓库里 0.1.2-rc.1 的 `dsh-tools` 与 0.2.0-rc.2 的 `dsh-session`/`dsh-agent`
  混用，`pnpm run typecheck` 崩在 `Property '[BRAND]' is missing`。
  现在同时读 `dependencies` 与 `devDependencies`。
- **陈旧运行时链接不再被"跳过"而是被清理**（同上）
  原清理判据要求 `realpath(target) === realpath(source)`；声明版本与运行时不一致时旧链接
  指向**另一个** runtime 位置，判据恒 false，于是"跳过重链"把错误世代留在原地，`pnpm install`
  也不会修它。改为识别**任何** realpath 落在 `~/.dsh/` 下的链接并移除，交回 pnpm。
- **两包 postinstall 在 Windows 上必然失败**（`package.json` ×2）
  pnpm 执行生命周期脚本时派生的环境 PATH 里既没有 `node` 也没有 `pnpm`（实测 `'node' 不是
  内部或外部命令`），因此 `node scripts/...` 这类外层按名字调用必然失败。改为
  `node -e "...execFileSync(process.execPath,...)"`，内层不再依赖 PATH。
  注意：本次安装**插件本身是装好的**（27 个包全部落盘），仅这两个钩子失败，属非致命。

### 验证（按知识库《DSH-rc1工具调度器与Web profile重复模块故障》的验收口径）

- desktop profile 的 hoisted 树里每个 `@deepseek-ai` 核心包**只有一个版本**、全部 `0.2.0-rc.2`，
  与 runtime 的 asar 副本同版本；无嵌套副本。
- 两个插件都解析到**同一份** `@deepseek-ai/dsh-tools`（profile 顶层），且二者只用其纯函数
  `defineTool`——`defineTool` 经查不引用 `TOOL_RUNTIME_SCHEDULER`、无模块级状态，因此
  "同版本但不同模块 URL"不会造成 scheduler Symbol 身份冲突（该故障需模块自己把 Symbol
  交给另一个副本的 registry，本插件不做此事）。
- vitest 36 → 40（新增 `test/link-runtime-guard.test.ts` 4 项锁住上述守卫）；
  python 15+15；子包 smoke 全过。

## [1.1.9] - 2026-10-10

**修复一个发布阻断缺陷**：子包发布后会 `npm install` 直接失败。

### 修复

- 子包 `postinstall` 指向 `scripts/link-runtime-deps.mjs`，但 `files` 白名单不含 `scripts/`
  （该脚本是仓库内的开发期 vendoring，不应发布）——消费者安装时 postinstall 找不到文件，
  `npm error command sh -c node scripts/link-runtime-deps.mjs` 致 **npm install 以 1 退出**。
  改为内联判断：文件存在才执行，缺失即静默跳过（仓库内开发行为不变，实测仍完成 vendoring）。
- 子包版本 0.1.0 → 0.1.1。

### 验证（真实消费者场景）

以打包产物为输入，在**干净目录**用 npm 安装（不继承仓库依赖）：

```
npm install ./pipeline.tgz ./superjunier-dsh-gitlab-credentials-0.1.1.tgz
→ added 28 packages, EXIT=0
```

两个插件均可加载（`apply = function`），依赖 `@deepseek-ai/dsh-tools` / `schemastery` /
`js-yaml` 由声明自动带入，客户端半身 `id` 与包名一致。

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
