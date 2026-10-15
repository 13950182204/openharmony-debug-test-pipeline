---
name: openharmony-ci-orchestrator
description: 观察并认领平台自带的 OpenHarmony MR 门禁 CI（GitLab 流水线 → Jenkins MR-Gate），取得该次提交已构建的固件/OTA 包，并把它交给受信任的板级 profile 做 OTA 与回归。用于 MR 提交后需要依据平台 CI 结果做自动 OTA 升级与回归验证的场景。本 skill 不触发任何构建。
---

# OpenHarmony CI 编排（平台 CI 观察 + 认领）

## 交付边界

**本模块不触发 Jenkins 构建。** MR 提交/更新后，GitLab 为该项目自动创建流水线
（`openharmony-mr-gate`），该流水线通过专用 Runner 触发并等待 Jenkins 门禁构建
`OpenHarmony-V6.1-MR-Gate`；门禁再按受影响芯片顺序构建 `OpenHarmony-V6.1-MR-<chip>`
并归档固件与 OTA 包。我们要做的是：**只读观察这条平台流水线**、在成功后**认领**对应的
芯片构建、把包交给受信任的 phase-3 profile 做 OTA 与回归。

Phase 2（本模块）：注册 run（`observe`）→ 只读轮询平台流水线 → 成功后按 MR 身份认领芯片构建
→ 启动受信任的 phase-3 单元。Phase 3 执行 [scripts/phase3_runner.py](scripts/phase3_runner.py)，
它只接受 `profiles/` 下的受信任 profile。部署与排障见
[references/phase2-operations.md](references/phase2-operations.md)，A333 板级细节见
[references/phase3-a333.md](references/phase3-a333.md)。

## 工作流

1. 取得 MR 的 **iid、源分支、精确 head SHA 与目标分支**（`glab mr view <iid>` 或 `glab api`）。
   不要用含糊的分支名代替 SHA。
2. 注册 run（只读；不产生任何构建）：

   ```bash
   python3 "{{SKILLS_DIR}}/openharmony-ci-orchestrator/scripts/ci_orchestrator.py" observe \
     --mr-iid <iid> --source-sha <head-sha> --source-branch <source-branch> \
     --target-branch <target-branch> \
     --repo-dir <absolute-repository-path> \
     --phase3-profile a333-2g-primary-standby \
     --regression-profile <trusted-feature-profile> \
     --agent-sandbox danger-full-access
   ```

   `--phase3-profile` 同时决定**认领哪个芯片的构建**（取 `jenkins.job` 里的 `<chip>`）：
   需要 OTA 验证时必须显式给出。只做观察、不打算 OTA 时可以不传。
3. 平台流水线跑完（数十分钟到数小时）后推进一次；`reconcile` 也可由 systemd 定时器兜底：

   ```bash
   python3 "{{SKILLS_DIR}}/openharmony-ci-orchestrator/scripts/ci_orchestrator.py" reconcile
   ```

   `reconcile` 默认只处理未结束的 run（`--include-terminal` 强制全量），可用
   `--prune-days N` 把终结 run 归档到 `runs/archive/`。
4. 平台流水线成功 → 认领芯片构建（参数 `MR_IID/SOURCE_SHA/TARGET_BRANCH` 与 run 记录逐项相符，
   且该构建 `result=SUCCESS`）→ 记录 `build.url` 与归档清单 → 启动 phase-3 systemd 单元。
5. phase-3 单元执行受信任 runner：核对源 SHA 的 console 检出证据 → 下载 OTA 包 → 包预检 →
   设备身份与源版本预检 → OTA → 同设备回归 → 回写 MR 评论。

## 失败分流（都不触发构建）

| 现场 | 处置 |
|---|---|
| 平台流水线 `failed` | run 进入 `gate_failed` 终态；按流水线链接定位失败作业，修复后重提 MR 或重跑平台流水线 |
| 平台流水线 `canceled/skipped` | `gate_aborted`；目标分支不在门禁白名单时为 `gate_not_in_scope`（**没有固件验证**） |
| MR 在门禁运行中被更新 | 认领前复核发现 SHA 变化 → `blocked`（`stale_source`）；必须让平台对新提交重跑流水线 |
| 门禁构建已按保留策略清理 | `blocked`；只能重跑平台流水线，或人工提供已下载的包 |
| 未受影响芯片 | 门禁按 diff 路由；`gate-plan.json` 的 `chips` 不含本 profile 芯片时拒绝认领并说明 |
| 新主机凭据未配/失效 | 明确提示 `JENKINS_BASE_URL` 与 `JENKINS_USER/JENKINS_API_TOKEN` 需换成门禁主机的那一对 |

**绝不**在这些情形下回退成自己触发 Jenkins 构建——那会绕过平台门禁的授权与产物一致性。

## 认证与安全

- 只需要**只读**访问：GitLab 走已登录的 `glab`（token 在 `~/.config/glab-cli/config.yml`
  或 keyring），Jenkins 只读拿构建参数、consoleText 与归档产物。
- Jenkins 凭据只从 `~/.config/openharmony-ci-orchestrator/jenkins.env`（0600）或进程环境读取，
  不写入 run 状态、日志、MR 文本。
- 认领是**有据可查**的：`MR_IID/SOURCE_SHA/TARGET_BRANCH` 必须与注册值一致；不一致一律 `blocked`。
- 平台门禁对同一 MR 只跑一个汇总构建（`disableConcurrentBuilds()`），认领不需要抢锁。
- 状态文件使用原子替换与进程锁；同一 run 只有一个 phase-3 单元运行（`KillMode=process`、
  重启上限 3 次）。

## Phase 3 安全

- profile 是 `profiles/` 下的受信任 JSON；分支名、门禁参数与 MR 文本永远不会提供可执行的设备命令。
- 已安装的 `a333-2g-primary-standby` profile 只接受门禁作业 `OpenHarmony-V6.1-MR-a333`，
  且只下载门禁归档的 `openharmony_V6.1/out/ota/*.zip`（要求恰好一个命中）。
- 其主设备 serial 是 `ea010e325333324247102b4ed1988ce7`；备机 serial 是
  `ea010e325333324247102b4ed1a48c99`。仅当主设备升级失败或未能恢复正常 HDC 启动后，才动备机。
- **OTA 目标选择（用户规定，2026-09-10 起执行）**：OTA 只需选择**当前在线且可升级的 DUT**，
  不要因为固定串号缺席而卡住——先 `hdc list targets` 枚举在线设备；优先 profile 池内在线设备
  （primary 优先，其次 standby）；池内无在线设备时，任选一台在线且通过产品身份校验
  （`const.product.name/model/brand/manufacturer`）与包预检（源版本兼容、`/data` 容量、
  `write_updater` 可用）的同型号 DUT；把最终选中的 serial 与理由写入 run 状态
  （`ci_orchestrator.py note --run-id <id> "<理由>"`）与运行日志；离线设备不等待、不反复探测；
  OTA 完成后在同一台设备上做回归。
- 每次 OTA 都需要包 ZIP/版本预检、源版本兼容、`/data` 容量、重连后的设备身份、
  `updater_result=pass` 与 `bootevent.boot.completed=true`。
- Phase 3 要求已注册的源 SHA，并在下载产物前扫描芯片构建 `consoleText` 中实际的
  `HEAD is now at <sha>` 检出记录。仅分支的构建证据不足，会被拦截。
- 内置 OTA 冒烟检查始终运行。没有显式功能回归 profile 的运行报告 `INCONCLUSIVE`，
  绝不报告功能回归通过。
- **回归重跑测试项**（用户口径，2026-10-15）：OTA 成功后用 `--regression-profile xts-rerun-lts-f`
  重跑分诊报告里出错的整块测试项（模块级，取自流水线状态 `artifacts.failedModules`）；
  判定只看报告（**xdevice 对没跑起来的模块也 exit 0**）：全部通过 → PASS，任一失败 → FAIL，
  `unavailable`/无记录 → `INCONCLUSIVE`。执行宿主需要有可达的 hdc 服务。
