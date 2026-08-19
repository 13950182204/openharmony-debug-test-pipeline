# A333 `a333_newpines` OTA 参考

本参考仅用于根在 `device/board/seed/a333_newpines` 的 OpenHarmony 6.1 A333 板。

## 打包

先完成产品构建。改动包含 `boot0`、U-Boot 或其他启动打包输入时，先运行生效中的板级打包流程，保证 `out/pack_out` 与 `out/kernel/bsp` 是最新的。

从源码根创建全量 OTA：

```bash
bash device/board/seed/a333_newpines/tools/make_ota_package.sh "$(pwd)" "$(pwd)/out/ota"
```

部署 `out/ota/update.zip`。脚本还会产出 `updater_*.zip`，并可能生成嵌套的 recovery 产物；不要用文件名通配选择包。

只有 `out/ota/old` 是设备上安装的精确源版本所保留的镜像集时，才创建增量 OTA：

```bash
bash device/board/seed/a333_newpines/tools/make_ota_package.sh \
  -s "$(pwd)/out/ota/old" "$(pwd)" "$(pwd)/out/ota"
```

## 版本与签名规则

- `updater_config/VERSION.mbn` 是允许的源版本列表。
- `updater_specified_config.xml` 或 `updater_specified_config_diff.xml` 的 `softVersion` 是目标版本。
- 投递前确认设备运行值：

  ```bash
  hdc -t <serial> shell 'param get const.product.software.version; param get const.ohos.fullname'
  ```

- 更换签名密钥要求设备上的 updater 镜像包含匹配的验证证书。仅重新打包无法修复证书不匹配。

## 全量 OTA 投递

```bash
TARGET=<hdc-target-serial>
PACKAGE="$(pwd)/out/ota/update.zip"

hdc -t "$TARGET" file send "$PACKAGE" /data/updater/updater_full.zip
hdc -t "$TARGET" shell 'write_updater updater /data/updater/updater_full.zip'
hdc -t "$TARGET" shell 'reboot updater'
```

增量包使用 `/data/updater/updater_diff.zip` 作为目标。使用 `write_updater updater`；不要把这个常规 updater 流程切换成 `sdcard_update`。

## 结果证据

板卡回到正常系统后验证：

```bash
hdc -t <serial> shell 'param get bootevent.boot.completed; param get const.product.software.version; param get const.ohos.fullname'
hdc -t <serial> shell 'cat /data/updater/updater_result; cat /data/updater/log/updater_stage_log; tail -n 120 /data/updater/log/updater_log'
```

`/data/updater/updater_result` 应为 `pass`。失败或反复进入 updater 时，先保全这些文件再执行任何恢复动作。然后对比 updater XML 组件地址与 `fstab.updater` 及实际板级分区名。
