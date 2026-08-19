# Phase 3 A333 2G

The first installed phase-3 profile is `a333-2g-primary-standby`. It is deliberately narrower than the Jenkins AllWinner job:

- Job: `OpenHarmony-V6.1-AllWinner`.
- Product selector: `Openharmony_Devices=a333_medical_dsi_800x1280` with DHong/76A/DNAKE XTS product parameters.
- OTA artifact: exactly `openharmony_V6.1/out/ota/update.zip` from that build; the runner rejects `updater_full.zip`, image files, globs, missing artifacts, and duplicates.
- Primary: `ea010e325333324247102b4ed1988ce7`.
- Standby: `ea010e325333324247102b4ed1a48c99`.

The runner first verifies the registered source SHA against Jenkins' `HEAD is now at` console record. It then uses the existing `openharmony-ota-upgrade` preflight and normal full-OTA delivery (`write_updater updater` then one `reboot updater`). It verifies the package source-version whitelist against the running device, captures the artifact SHA-256, verifies transfer size, requires the same device to return, and then checks `bootevent.boot.completed`, target `softVersion`, and `updater_result=pass`.

If primary preflight, staging, updater, or reconnect fails, it records evidence and attempts the standby once. It never retries the primary and never upgrades both devices after a primary success. A regression failure after a successful upgrade is a firmware result, not an OTA failover condition.

## Register an A333 run

Use an exact source branch, SHA, and MR iid. The profile checks the submitted parameters again after Jenkins completes.

```bash
export JENKINS_BASE_URL="http://192.168.13.121:8080"
export JENKINS_JOB="OpenHarmony-V6.1-AllWinner"

python3 "{{SKILLS_DIR}}/openharmony-ci-orchestrator/scripts/trigger_jenkins_build.py" \
  --branch <mr-source-branch> \
  --source-sha <mr-source-sha> \
  --mr-iid <mr-iid> \
  --build-mode FULL \
  --firmware-type XTS \
  --parameter Openharmony_Devices=a333_medical_dsi_800x1280 \
  --parameter XTS_PRODUCT_NAME=DHong-A333-Development-Board \
  --parameter XTS_PRODUCT_MODEL=76A \
  --parameter XTS_PRODUCT_BRAND=DNAKE \
  --parameter XTS_PRODUCT_MANUFACTURER=DHong \
  --verify-job \
| python3 "{{SKILLS_DIR}}/openharmony-ci-orchestrator/scripts/ci_orchestrator.py" \
    register --repo-dir <absolute-repository-path> \
    --phase3-profile a333-2g-primary-standby \
    --regression-profile <trusted-feature-profile> \
    --agent-sandbox danger-full-access
```

Do a first package-only rehearsal with `phase3_runner.py --dry-run` against a registered successful run. It downloads and validates the exact artifact but does not access HDC or write GitLab.

## Adopt A Completed Build

Use `adopt` only to recover a successful build that predates phase-2 registration. It is not a replacement for phase 1: it validates the live Jenkins result and branch parameter, refuses a previously tracked build, and still requires phase 3 to verify the source SHA from Jenkins console before package download or OTA.

```bash
python3 "{{SKILLS_DIR}}/openharmony-ci-orchestrator/scripts/ci_orchestrator.py" \
  adopt --base-url http://192.168.13.121:8080 \
  --job OpenHarmony-V6.1-AllWinner --build-number <number> \
  --branch <mr-source-branch> --source-sha <mr-source-sha> --mr-iid <iid> \
  --repo-dir <absolute-repository-path> \
  --phase3-profile a333-2g-primary-standby \
  --agent-sandbox danger-full-access
```

## Regression profiles

Feature regression is opt-in and must use a static JSON profile in `profiles/`. The runner always performs `a333-ota-smoke-v1`, but that only proves the package booted. If no feature profile is registered, result is `INCONCLUSIVE`.

For a human-discovered defect, add a board-specific profile with a fixed implementation in `phase3_runner.py`: named action, expected device identity, deterministic assertions, and captured evidence. Do not add arbitrary shell, HDC, or UI commands to MR text or a JSON profile. XTS/HATS cases should be represented by a dedicated trusted runner action that calls their exact target/case; a UI-only validator case should use a dedicated HAP scenario action and evidence contract.

Set `GITLAB_HOST` and `GITLAB_PROJECT` in `~/.config/openharmony-ci-orchestrator/jenkins.env`. The runner creates or updates one MR note marked with the run id; an unavailable GitLab note does not alter the recorded OTA/regression outcome.
