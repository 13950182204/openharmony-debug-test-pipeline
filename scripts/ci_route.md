# OpenHarmony CI 路线（固化记录）：平台门禁观察 + 认领

更新日期：2026-10-15。适用仓库：`openharmony-debug-test-pipeline` + 本机（WSL Ubuntu 22.04，
systemd PID1）部署的轮询与 phase-3 执行。

> **本文件取代 2026-08-26 版的自触发路线。** 决定（用户，2026-10-15）：MR 提交后 GitLab 平台
> 自带 CI 已覆盖固件验证，插件**不再自己触发 Jenkins**；后续 OTA 升级与回归使用新平台
> `http://192.168.97.54:8080/` 的门禁构建产出的包。

## 1. 平台门禁链路（实测，2026-10-15）

```
MR 提交/更新
  └─ GitLab 项目 harmony/system/v9611/openharmony_V6.1
       外部 CI: config/mr-gate/gitlab-entrypoint.yml@harmony/system/v9611/jenkins-auto-build
       └─ job openharmony-mr-gate（Runner: "OpenHarmony MR gate bridge", IP 192.168.97.54）
            └─ bridge.py POST job/OpenHarmony-V6.1-MR-Gate/buildWithParameters
                 MR_IID / SOURCE_SHA / TARGET_BRANCH / GITLAB_PIPELINE_ID
                 └─ Jenkins(192.168.97.54:8080) 顺序 build:
                      OpenHarmony-V6.1-MR-rk3568 / -a333（按 diff 路由；consoleText 打印
                      "Jenkins gate: <build url>"）
                      └─ 归档 gate-plan.json + openharmony_V6.1/out/ota/*.zip
```

- 目标分支白名单：`v6.1.0.31_release`、`v6.1.0.35_lts`；其他目标分支只有
  `not-in-firmware-gate-scope` 轻量任务（**没有固件验证**）。
- 项目设置：`only_allow_merge_if_pipeline_succeeds=true`、禁止跳过流水线。
- 门禁保留策略：构建 30 / 产物 10（产物 7 天）——过期后只能让平台重跑流水线。

## 2. 本插件侧（零-token 轮询，仍成立）

```
GitLab API (glab, 只读)                    Jenkins (192.168.97.54, 只读)
   │ GET merge_requests/<iid> → head_pipeline  │ GET /job/<j>/api/json, /<n>/api/json,
   │                                            │     /<n>/consoleText, /<n>/artifact/<path>
   ▼                                            ▼
~/.local/state/openharmony-ci-orchestrator/runs/<run_id>.json
   ▲
   │ systemd: openharmony-ci-reconcile.timer → ci_orchestrator.py reconcile
   │          （只处理未终结的 run；--prune-days 归档旧 run）
   └── 认领成功后启动 openharmony-ci-phase3@<run-id>.service（受信任 runner：下载包/预检/OTA/回归）
```

token 消耗点唯一：阶段推进时由模型调用一次 `reconcile`（或由定时器零 token 推动），
构建成功后才启动 phase-3 单元。

已部署单元（本机）：
- `~/.config/systemd/user/openharmony-ci-reconcile.timer` 与 `.service`（含显式 PATH）
- `~/.config/systemd/user/openharmony-ci-phase3@.service`（KillMode=process）
- `~/.config/systemd/user/openharmony-ci-webhook.service`（**legacy/停用**：平台信号来自 GitLab）
- 配置：`~/.config/openharmony-ci-orchestrator/jenkins.env`（0600）

## 3. 与旧路线（2026-08-26）的差异

| 项 | 旧 | 新 |
|---|---|---|
| Jenkins 主机 | `192.168.13.121:8080`（自引用 `jenkins-chenxin.local`） | `192.168.97.54:8080`（返回自身 origin，无需白名单） |
| 触发方 | 插件 `trigger_jenkins_build.py`（buildWithParameters + queue 解析 + crumb） | **平台** GitLab Runner bridge（插件零触发；触发代码已删除） |
| 作业 | `OpenHarmony-V6.1-{AllWinner,RockChip,XTS}` | `OpenHarmony-V6.1-MR-Gate` + `OpenHarmony-V6.1-MR-{rk3568,a333}` |
| 参数 | 调用方给 `FIRMWARE_BRANCH/BUILD_MODE/FIRMWARE_TYPE/Openharmony_Devices` | 调用方只给 `MR_IID/SOURCE_SHA/TARGET_BRANCH`；编译参数服务端固定 |
| 产物路径 | `openharmony_V6.1/out/ota/update.zip`（精确） | `openharmony_V6.1/out/ota/*.zip`（恰好一个命中） |
| 完成通知 | Jenkins webhook（从未真正启用）+ 5 分钟轮询 | GitLab 流水线状态（轮询 head_pipeline） |
| 失败语义 | 构建失败即终态 | 平台流水线 failed/canceled/skipped + `gate_not_in_scope` 明确区分 |

## 4. 已知坑与处置

1. **凭据是按主机独立的**：旧主机账号 token 打到新主机是 403（会看到登录页 HTML）。
   插件现在把 401/403 统一改写为"请在 jenkins.env 里核对 JENKINS_BASE_URL 与
   JENKINS_USER/JENKINS_API_TOKEN"的可操作错误。
2. **MR 在门禁运行中被更新**：认领前会重新读 MR head SHA；与注册值不同即 `blocked`
   （`stale_source`），要求平台对新提交重跑流水线，绝不沿用旧流水线的成功。
3. **陈旧 run 噪音**：`reconcile` 默认跳过已终结的 run，`--prune-days` 把旧 run 移进
   `runs/archive/`（保留审计）。这解决了旧版每分钟重复处理十几个死 run 的 journal 噪音。
4. **`phase3_runner.py note` 与状态字段**：`stage` 事件用
   `ci_orchestrator.py note --run-id <id> "<说明>"` 追加，用于记录 DUT 选择等关键决策。
5. **systemd 最小 PATH**：单元现在显式带 `Environment=PATH=<~/bin>:...`，否则 `glab`
   找不到；也可用 `GLAB_BIN` 指定绝对路径。

## 5. 待办/记录

- [ ] 在 `jenkins.env` 里换成 97.54 的只读账号/token 后，跑一次真实 MR 的
      `observe → reconcile → phase3_runner --dry-run`，确认能下载到门禁包（当前只到
      "凭据被拒 + 可操作提示"这一步）。
- [ ] `OpenHarmony-V6.1-MR-a537` 作业已被生成，但门禁路由表只产出 rk3568/a333；
      若将来接入 A537，需要新增对应 profile 与设备池。
- [ ] XTS-only 变更的回测路线（不 OTA）：门禁目前只保证固件/OTA 非空，回测仍需独立 runner 动作。
