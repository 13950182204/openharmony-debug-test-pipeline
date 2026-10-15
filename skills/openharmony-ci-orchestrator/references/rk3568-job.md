# RK3568 平台门禁作业

## 端点（只读）

- Base URL: `http://192.168.97.54:8080`（`JENKINS_BASE_URL`）
- 芯片作业: `OpenHarmony-V6.1-MR-rk3568`
- 汇总门禁: `OpenHarmony-V6.1-MR-Gate`
- 只读元数据端点: `/job/<作业名>/api/json`
- 只读构建端点: `/job/<作业名>/<构建号>/api/json`、`.../consoleText`、`.../artifact/<路径>`

**没有构建触发端点**：本插件不再触发 Jenkins。MR 提交后由 GitLab 流水线
（`openharmony-mr-gate`）经专用 Runner 触发汇总门禁，门禁再按受影响芯片 `build job:` 出
`OpenHarmony-V6.1-MR-{rk3568,a333}`。

## 作业参数

汇总门禁：`MR_IID`、`SOURCE_SHA`、`TARGET_BRANCH`、`GITLAB_PIPELINE_ID`。
芯片作业：`MR_IID`、`SOURCE_SHA`、`TARGET_SHA`、`TARGET_BRANCH`。

编译侧参数（`BUILD_MODE=INCREMENTAL`、`FIRMWARE_TYPE=NORMAL`、`FIRMWARE_BRANCH=<目标分支>`、
`Openharmony_Devices=<chip>_dsi_800x1280`）由门禁任务**服务端固定**，不随调用方传入；
因此认领时只核对 MR 身份四元组，产品身份由设备侧校验与 `evidence_parameters` 承担。

## 归档产物

`gate-plan.json`（汇总门禁，含 `mr_iid/source_sha/target_sha/target_branch/merge_base/chips`）、
`openharmony_V6.1/out/*.img`、`openharmony_V6.1/out/ota/*.img`、`openharmony_V6.1/out/ota/*.zip`。
保留策略：构建 30 / 产物 10，产物 7 天——**过期的门禁构建无法再取包**，此时必须让平台对新提交
重跑流水线，而不是自己触发构建。

## 已知验证记录（旧主机，仅作对照）

2026-08-09 的只读元数据请求（旧主机 `192.168.13.121:8080` 的 `OpenHarmony-V6.1-RockChip`）
报告 `buildable: true` 且 `FIRMWARE_BRANCH`/`BUILD_MODE`/`FIRMWARE_TYPE`/`Openharmony_Devices`
齐全；构建 #46 归档了 `updater_full.zip`。那台主机**已不再被本插件使用**：
门禁链路固定在 `192.168.97.54`，凭据、作业名与参数契约都以门禁为准。

不要从这条记录推断未来的构建号或产物名。
