# A333 `a333_newpines` OTA Reference

Use this reference only for the OpenHarmony 6.1 A333 board rooted at `device/board/seed/a333_newpines`.

## Package Creation

Run a completed product build first. If the change includes `boot0`, U-Boot, or another boot packaging input, run the active board pack flow before creating OTA so that `out/pack_out` and `out/kernel/bsp` are current.

Create a full OTA from the source root:

```bash
bash device/board/seed/a333_newpines/tools/make_ota_package.sh "$(pwd)" "$(pwd)/out/ota"
```

Deploy `out/ota/update.zip`. The script also emits `updater_*.zip` and can create nested recovery artifacts; do not choose a package by filename glob.

Create an incremental OTA only when `out/ota/old` is a preserved image set from the exact source version installed on the device:

```bash
bash device/board/seed/a333_newpines/tools/make_ota_package.sh \
  -s "$(pwd)/out/ota/old" "$(pwd)" "$(pwd)/out/ota"
```

## Version and Signing Rules

- `updater_config/VERSION.mbn` is the allowed source-version list.
- `updater_specified_config.xml` or `updater_specified_config_diff.xml` `softVersion` is the target version.
- Confirm the running device values before staging:

  ```bash
  hdc -t <serial> shell 'param get const.product.software.version; param get const.ohos.fullname'
  ```

- A signing-key replacement requires the updater image on the device to contain the matching verification certificate. Package regeneration alone cannot fix a certificate mismatch.

## Full OTA Delivery

```bash
TARGET=<hdc-target-serial>
PACKAGE="$(pwd)/out/ota/update.zip"

hdc -t "$TARGET" file send "$PACKAGE" /data/updater/updater_full.zip
hdc -t "$TARGET" shell 'write_updater updater /data/updater/updater_full.zip'
hdc -t "$TARGET" shell 'reboot updater'
```

For an incremental package, use `/data/updater/updater_diff.zip` as the destination. Use `write_updater updater`; do not switch this normal updater flow to `sdcard_update`.

## Result Evidence

After the board returns to the normal system, verify:

```bash
hdc -t <serial> shell 'param get bootevent.boot.completed; param get const.product.software.version; param get const.ohos.fullname'
hdc -t <serial> shell 'cat /data/updater/updater_result; cat /data/updater/log/updater_stage_log; tail -n 120 /data/updater/log/updater_log'
```

`/data/updater/updater_result` should be `pass`. On failure or repeated entry to updater, preserve these files before any recovery action. Then compare updater XML component addresses with `fstab.updater` and the actual board partition names.
