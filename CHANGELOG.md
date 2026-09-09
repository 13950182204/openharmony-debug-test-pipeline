# Changelog

本仓库（openharmony-debug-test-pipeline）的 OpenHarmony 兼容性闭环插件。版本号遵循语义化版本（SemVer）。

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
