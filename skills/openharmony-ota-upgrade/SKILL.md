---
name: openharmony-ota-upgrade
description: 打包、投递、触发、监控并验证 OpenHarmony 全量或增量 OTA 升级。用于 OpenHarmony 板级 OTA 工作，涉及 update.zip、updater 包、HDC、write_updater、reboot updater、升级后启动验证、OTA 签名或版本失败，或产出可审计的 OTA 结果。
---

# OpenHarmony OTA 升级

把 OTA 工作当作闭环执行：确认源版本与目标版本，制作一个明确的包，本地校验，投递到一台明确的设备，触发 updater，等待设备回归，并记录结果。不要把 `hdc file send` 或 `write_updater` 的成功返回当作升级成功。

## 安全规则

- 除非用户明确要求增量包并提供经过验证的源镜像基线，否则使用全量 OTA。
- 每个会改变设备的步骤前运行 `hdc list targets`。零个或多个目标时，停下并索要目标 serial。此后使用 `hdc -t <serial>`。
- 投递前捕获当前设备版本。不要用未验证的包覆盖 `/data/updater/`。
- 不要用恢复出厂设置、擦除 userdata 或反复 updater 重启作为恢复手段。先收集 updater 状态与日志。
- 把 `reboot updater` 后立即出现的设备断开视为预期。只有在有界重连等待与日志收集之后才升级处理。

## 工作流

1. 确认板型、包模式、源版本、目标版本、包路径与目标 serial。打包前阅读该板的 OTA 脚本与 updater XML。
2. 完成产品构建与 bootloader 或 boot 镜像改动所需的板级打包流程。确认 updater XML 点名的每个镜像都存在且新鲜。
3. 只制作一个包。对 A333 `a333_newpines` 流程，阅读 [references/a333-newpines.md](references/a333-newpines.md)。
4. 触碰设备前运行本地预检脚本：

   ```bash
   "{{SKILLS_DIR}}/openharmony-ota-upgrade/scripts/ota_preflight.sh" <ota-package.zip>
   ```

   解决所有失败后再投递。记录打印的 SHA-256 与包大小。
5. 用选定的 serial 做设备预检。把运行版本与预期源版本对比，确认 `/data` 至少有包大小加脚本报告的余量，并确认 `write_updater` 存在。
6. 把包传输到按模式区分、明确的目标路径。运行 `write_updater` 前对比远端文件大小与本地大小。
7. 运行 `write_updater updater <remote-package>`，只有它成功返回后才运行 `reboot updater`。updater 运行期间不要发出额外的 write 或 reboot 命令。
8. 轮询同一台设备回归。上线后验证 `bootevent.boot.completed`、目标版本属性与产品关键功能。即使成功也读取 updater 结果与日志。
9. 报告闭环证据：源版本、目标版本、包模式/路径/SHA-256、目标 serial、传输大小匹配、updater 结果、启动完成情况与任何残余风险。

## 标准设备命令

替换所有占位符。先运行只读命令，再运行改状态的命令。

```bash
TARGET=<hdc-target-serial>
PACKAGE=<absolute-path-to-ota-package.zip>
REMOTE=/data/updater/updater_full.zip

hdc list targets
hdc -t "$TARGET" shell 'param get const.product.software.version; param get const.ohos.fullname'
hdc -t "$TARGET" shell 'df -k /data; ls -ld /data/updater; which write_updater'

stat -c '%s %n' "$PACKAGE"
hdc -t "$TARGET" file send "$PACKAGE" "$REMOTE"
hdc -t "$TARGET" shell "ls -l $REMOTE"

hdc -t "$TARGET" shell "write_updater updater $REMOTE"
hdc -t "$TARGET" shell 'reboot updater'
```

增量包设置 `REMOTE=/data/updater/updater_diff.zip`；`write_updater updater` 命令保持不变。

重连后使用：

```bash
hdc -t "$TARGET" shell 'param get bootevent.boot.completed; param get const.product.software.version; param get const.ohos.fullname'
hdc -t "$TARGET" shell 'cat /data/updater/updater_result 2>/dev/null; cat /data/updater/log/updater_stage_log 2>/dev/null; tail -n 120 /data/updater/log/updater_log 2>/dev/null'
```

## 失败路由

- 包预检失败：重建或重新生成包。不要传输。
- 签名或包加载失败：把包签名证书与 updater 基线内置证书对比。新密钥需要设备上有匹配的 updater 镜像。
- 版本被拒：确认包的源版本白名单描述的是运行版本，而包 XML 的目标版本描述的是新版本。
- 反复进入 updater 或无法正常启动：停止重试，收集 `/data/updater/updater_result`、`updater_stage_log`、`updater_log` 与生效中的 updater XML/fstab 映射。只有在保全证据之后才检查过期的 updater 命令。
- 镜像或分区失败：对比 updater XML 组件名与分区地址、设备 updater `fstab` 以及实际板级分区布局。

## 资源

- [scripts/ota_preflight.sh](scripts/ota_preflight.sh)：校验本地 OTA ZIP 并打印部署清单。
- [references/a333-newpines.md](references/a333-newpines.md)：A333 板级全量与增量包命令、版本语义与 updater 证据路径。
