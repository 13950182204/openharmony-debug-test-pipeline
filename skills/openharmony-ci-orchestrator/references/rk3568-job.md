# RK3568 Jenkins 作业

## 端点

- Base URL: `http://192.168.13.121:8080`
- 作业: `OpenHarmony-V6.1-RockChip`
- 构建端点: `/job/OpenHarmony-V6.1-RockChip/buildWithParameters`
- 只读元数据端点: `/job/OpenHarmony-V6.1-RockChip/api/json`

Jenkins 链接中显示的主机名是 `jenkins-chenxin.local:8080`；执行环境中该名字可解析时使用 `JENKINS_BASE_URL`。

## Phase-1 必需参数

该作业暴露大量产品默认值。触发辅助脚本显式提供以下三个参数，因为它们选择源码与构建模式：

| 参数 | RK3568 验证值 |
| --- | --- |
| `FIRMWARE_BRANCH` | MR 源分支 |
| `BUILD_MODE` | 默认 `INCREMENTAL`；仅被要求时才用 `FULL` |
| `FIRMWARE_TYPE` | `XTS` |

同时为 RK3568 产品传入 `Openharmony_Devices=rk3568_dsi_800x1280`，不要依赖可变的 Jenkins 默认值。

## 已知验证记录

2026-08-09 的一次只读元数据请求报告 `buildable: true` 且以上四个参数齐全。Jenkins 构建 #46 使用分支 `rk3568/v6.1.0.31_OTA_prefix_change`、`BUILD_MODE=INCREMENTAL`、`FIRMWARE_TYPE=XTS`；构建成功后归档了 `updater_full.zip`。

不要从这条记录推断未来的构建号。捕获当前触发返回的 queue URL 与分配的构建 URL。
