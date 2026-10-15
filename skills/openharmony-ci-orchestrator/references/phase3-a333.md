# Phase 3 A333 2G

首个已安装的 phase-3 profile 是 `a333-2g-primary-standby`。它刻意比平台门禁作业更窄：

- 作业：`OpenHarmony-V6.1-MR-a333`（平台门禁按受影响芯片生成；A333 与 A537 共用 AllWinner 配方）。
- 必需门禁参数：`MR_IID`、`SOURCE_SHA`（还必须与注册值一致）；目标分支由门禁服务端固定为
  `v6.1.0.31_release` / `v6.1.0.35_lts` 之一，并由 `TARGET_BRANCH` 复核。
- 产品参数（`Openharmony_Devices=a333_dsi_800x1280`、`XTS_PRODUCT_*`）由门禁服务端固定、
  不再作为构建参数下发；profile 把它们放在 `evidence_parameters`：**出现即核对，缺失则由设备身份
  校验兜底**。
- OTA 产物：该次门禁构建归档的 `openharmony_V6.1/out/ota/*.zip`，要求**恰好一个命中**
  （`update.zip` 或 `updater_full.zip` 由板级打包流程决定）；runner 拒绝命中多个、缺失产物
  与非 OTA 目录下的包。
- 主设备：`ea010e325333324247102b4ed1988ce7`。
- 备机：`ea010e325333324247102b4ed1a48c99`。

## 目标设备选择规则（用户规定，2026-09-10 起执行）

OTA **只需选择当前在线且可升级的 DUT**，不要因为 profile 里的固定串号缺席而阻塞：

1. 先 `hdc list targets` 枚举当前在线设备；
2. 优先 profile 池内在线设备（primary 优先，其次 standby）；
3. 池内无在线设备时，任选一台在线、且通过**产品身份校验**
   （`const.product.name/model/brand/manufacturer` 与 profile 期望一致）与**包预检**
   （源版本兼容、`/data` 容量、`write_updater` 可用）的同型号 DUT；
4. 把最终选中的 serial 与选择理由写入 CI run 状态（`ci_orchestrator.py note`）与运行日志；
5. 离线设备不等待、不反复探测；OTA 完成后在**同一台**设备上做回归。

### "可升级"如何判定（2026-09-10 实测补充）

`可升级` 不是"在线"就够，**源版本必须在包的允许域内**：

- 包内 `version_list`（ZIP 成员之一）来自板级
  `device/board/seed/a333_newpines/updater/config/updater_config/VERSION.mbn`
  （由 `base/update/packaging_tools/utils.py` 的 `VERSION_MBN_PATH` 追加进包）。
- runner 用 profile 的 `artifact.source_version_property`（当前为 `software_version`）与
  `version_list` 做精确比对；不匹配即 `source version is not allowed by package`（在写设备前失败）。
- **选定 DUT 前先核对**：`param get const.product.software.version` 是否出现在包内 `version_list`；
  不出现则该机对本包**不可升级**，换机或按需扩展 `VERSION.mbn`（属产品/板级配置变更，需用户决策）。
- 实测示例：包内 `version_list` = `OpenHarmony 6.1.0.31`，而在线 DUT 为 `OpenHarmony 6.1.0.35`
  → 被拒；`const.product.software.version` 记为 `1.3.0` 的 DHong 样机同样不在该域内。
- 该失败**未写设备**，属可重试的纯预检失败：`phase3_runner.py --retry-preflight` 的允许原因里
  已包含"设备不在线"与"源版本不在允许域"两类（上限 5 次）。
- **`source_version_property` 必须选对属性**：包内 `version_list`（VERSION.mbn）写的是**哪个属性**的值，
  profile 就得用同一个属性去比。设备侧 updater 的实际判定见下条实测。
- **实测（2026-09-10，DHong A333）**：`VERSION.mbn` 只有 `OpenHarmony 6.1.0.31`，设备
  `const.product.software.version = 1.3.0`、`const.ohos.fullname = OpenHarmony-6.1.0.31`。
  - 用 `ohos_fullname` 比 → runner 放行，但**设备侧仍拒绝**：updater 日志
    `updater_preprocess.cpp 90: current version:1.3.0` → `updater_main.cpp 334: Version Check Fail!`
    → `/data/updater/updater_result = fail|status=1|mode=0`（传输已成功，设备随后正常回系统）。
  - 结论：**updater 用 `const.product.software.version` 做精确匹配**，故 profile 必须保持
    `source_version_property: software_version`；要让这类整机可升级，需要把其产品版本（如 `1.3.0`）
    加入 `VERSION.mbn` 后重新打包（与 CHANGELOG 1.0.0 记录的是同一类问题）。

注意：runner 仍按 profile 做全部安全校验（源 SHA 与 console 的 `HEAD is now at` 核对、
产物路径白名单、身份与版本预检）。放宽的只是"选哪台"这一项，不是校验强度。
若池内设备长期离线，考虑为其新增一个以在线 DUT 串号入池的 profile（改 profile 属受信任配置变更，需用户确认）。

runner 先把已注册的源 SHA 与芯片构建 console 的 `HEAD is now at` 记录核对（门禁用
`git checkout --detach <sha>`，输出是短 SHA + 提交标题）。然后使用既有的 `openharmony-ota-upgrade`
预检与常规全量 OTA 投递（`write_updater updater` 后一次 `reboot updater`）。它把包源版本白名单
与运行设备核对，捕获产物 SHA-256，验证传输大小，要求同一台设备回归，然后检查
`bootevent.boot.completed`、目标 `softVersion` 与 `updater_result=pass`。

主设备预检、投递、updater 或重连失败时，记录证据并尝试备机一次。它从不重试主设备，也绝不在主设备成功后升级两台设备。成功升级后的回归失败是固件结果，不是 OTA 故障转移条件。

## 注册 A333 运行（观察平台 CI，不触发构建）

使用精确的源分支、SHA 与 MR iid。门禁完成后 profile 会再次核对提交的 MR 身份参数；
门禁实际构建了哪些芯片由 `gate-plan.json` 交叉核对。

```bash
export JENKINS_BASE_URL="http://192.168.97.54:8080"

python3 "{{SKILLS_DIR}}/openharmony-ci-orchestrator/scripts/ci_orchestrator.py" \
  observe --mr-iid <mr-iid> \
  --source-sha <mr-head-sha> \
  --source-branch <mr-source-branch> \
  --target-branch v6.1.0.35_lts \
  --repo-dir <absolute-repository-path> \
  --phase3-profile a333-2g-primary-standby \
  --regression-profile <trusted-feature-profile> \
  --agent-sandbox danger-full-access
```

先对已认领的成功运行用 `phase3_runner.py --dry-run` 做一次仅包的排练。它下载并校验精确产物，
但不访问 HDC，也不写 GitLab。

## 认领已完成的平台构建

`adopt` 用于恢复"平台门禁早已跑完、本插件当时没登记"的场景：它一次性登记并把那次运行认领下来
（仍逐项核对 `MR_IID/SOURCE_SHA/TARGET_BRANCH`，且仍要求 phase 3 在包下载或 OTA 前从芯片构建
console 验证源 SHA）。它不是"再触发一次构建"的替代品——本插件不会触发构建。

```bash
python3 "{{SKILLS_DIR}}/openharmony-ci-orchestrator/scripts/ci_orchestrator.py" \
  adopt --mr-iid <iid> --source-sha <mr-head-sha> \
  --target-branch v6.1.0.35_lts \
  --repo-dir <absolute-repository-path> \
  --phase3-profile a333-2g-primary-standby \
  --agent-sandbox danger-full-access
```

## 回归 profile

功能回归为可选，必须使用 `profiles/` 中的静态 JSON profile。runner 始终执行 `a333-ota-smoke-v1`，但它只能证明包能启动。未注册功能 profile 时，结果为 `INCONCLUSIVE`。

对人工发现的缺陷，在 `phase3_runner.py` 中添加带固定实现的板级 profile：具名动作、预期设备身份、确定性断言与捕获证据。不要把任意 shell、HDC 或 UI 命令加进 MR 文本或 JSON profile。XTS/HATS 用例应由调用其精确 target/用例的专用受信任 runner 动作表示；仅 UI 的验证用例应使用专用 HAP 场景动作与证据契约。

在 `~/.config/openharmony-ci-orchestrator/jenkins.env` 中设置 `GITLAB_HOST` 与 `GITLAB_PROJECT`。runner 创建或更新一条带 run id 标记的 MR 评论；GitLab 评论不可用不改变已记录的 OTA/回归结果。
