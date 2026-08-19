---
name: openharmony-ota-upgrade
description: Package, deliver, trigger, monitor, and verify OpenHarmony full or incremental OTA upgrades. Use for OpenHarmony board OTA work involving update.zip, updater packages, HDC, write_updater, reboot updater, post-upgrade boot verification, OTA signature or version failures, or creating an auditable OTA result.
---

# OpenHarmony OTA Upgrade

Run OTA work as a closed loop: identify the source and target versions, create one explicit package, validate it locally, stage it to one explicit device, trigger updater, wait for the device to return, and record the result. Do not treat a successful `hdc file send` or `write_updater` return as an upgrade success.

## Safety Rules

- Use a full OTA unless the user explicitly requires an incremental package and supplies a verified source-image baseline.
- Run `hdc list targets` before every device-changing step. When zero or multiple targets exist, stop and ask for a target serial. Use `hdc -t <serial>` thereafter.
- Capture the current device version before staging. Do not overwrite `/data/updater/` with an unverified package.
- Do not use factory reset, erase userdata, or repeated updater reboots as recovery actions. Collect updater state and logs first.
- Treat device disconnect immediately after `reboot updater` as expected. Escalate only after a bounded reconnect wait and log collection.

## Workflow

1. Identify the board, package mode, source version, target version, package path, and target serial. Read the board's OTA script and updater XML before packaging.
2. Complete the product build and any board pack flow needed for bootloader or boot image changes. Confirm that every image named by the updater XML exists and is fresh.
3. Build exactly one package. For the A333 `a333_newpines` flow, read [references/a333-newpines.md](references/a333-newpines.md).
4. Run the local preflight script before touching the device:

   ```bash
   "{{SKILLS_DIR}}/openharmony-ota-upgrade/scripts/ota_preflight.sh" <ota-package.zip>
   ```

   Resolve every failure before staging. Record the printed SHA-256 and package size.
5. Perform device preflight with the selected serial. Compare the running version against the intended source version, verify `/data` has at least the package size plus the script's reported margin, and confirm `write_updater` exists.
6. Transfer the package to a mode-specific, explicit destination. Compare the remote file size with the local size before running `write_updater`.
7. Run `write_updater updater <remote-package>` and, only after it returns successfully, run `reboot updater`. Do not issue additional write or reboot commands while the updater is running.
8. Poll for the same device to return. Once online, verify `bootevent.boot.completed`, target version properties, and the product's critical function. Read updater result and logs even on success.
9. Report the closed-loop evidence: source version, target version, package mode/path/SHA-256, target serial, transfer size match, updater result, boot completion, and any remaining risk.

## Standard Device Commands

Replace all placeholders. Run the read-only commands before the state-changing commands.

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

For an incremental package, set `REMOTE=/data/updater/updater_diff.zip`; keep the same `write_updater updater` command.

After reconnecting, use:

```bash
hdc -t "$TARGET" shell 'param get bootevent.boot.completed; param get const.product.software.version; param get const.ohos.fullname'
hdc -t "$TARGET" shell 'cat /data/updater/updater_result 2>/dev/null; cat /data/updater/log/updater_stage_log 2>/dev/null; tail -n 120 /data/updater/log/updater_log 2>/dev/null'
```

## Failure Routing

- Package preflight fails: rebuild or regenerate the package. Do not transfer it.
- Signature or package-load failure: compare the package signing certificate with the certificate embedded in the updater baseline. A new key requires a matching updater image on the device.
- Version rejection: verify that the package source-version whitelist describes the running version, while the package XML target version describes the new version.
- Repeated updater boot or no normal boot: stop retries, collect `/data/updater/updater_result`, `updater_stage_log`, `updater_log`, and the active updater XML/fstab mapping. Check for stale updater commands only after preserving the evidence.
- Image or partition failure: compare the updater XML component names and partition addresses with the device updater `fstab` and the actual board partition layout.

## Resources

- [scripts/ota_preflight.sh](scripts/ota_preflight.sh): Validate a local OTA ZIP and print a deployment manifest.
- [references/a333-newpines.md](references/a333-newpines.md): Board-specific A333 full and incremental package commands, version semantics, and updater evidence paths.
