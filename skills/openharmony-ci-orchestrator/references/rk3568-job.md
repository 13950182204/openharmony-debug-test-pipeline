# RK3568 Jenkins job

## Endpoint

- Base URL: `http://192.168.13.121:8080`
- Job: `OpenHarmony-V6.1-RockChip`
- Build endpoint: `/job/OpenHarmony-V6.1-RockChip/buildWithParameters`
- Read-only metadata endpoint: `/job/OpenHarmony-V6.1-RockChip/api/json`

The hostname shown in Jenkins links is `jenkins-chenxin.local:8080`; use `JENKINS_BASE_URL` when that name resolves in the execution environment.

## Required phase-1 parameters

The job exposes many product defaults. The trigger helper supplies these three parameters explicitly because they select the source and build mode:

| Parameter | RK3568 validation value |
| --- | --- |
| `FIRMWARE_BRANCH` | MR source branch |
| `BUILD_MODE` | `INCREMENTAL` by default; use `FULL` only when requested |
| `FIRMWARE_TYPE` | `XTS` |

Also pass `Openharmony_Devices=rk3568_dsi_800x1280` for the RK3568 product instead of relying on a mutable Jenkins default.

## Known validation

On 2026-08-09, a read-only metadata request reported `buildable: true` and all four parameters above. Jenkins build #46 used the branch `rk3568/v6.1.0.31_OTA_prefix_change`, `BUILD_MODE=INCREMENTAL`, and `FIRMWARE_TYPE=XTS`; it archived `updater_full.zip` after a successful build.

Do not infer a future build number from this record. Capture the queue URL and assigned build URL returned by the current trigger.
