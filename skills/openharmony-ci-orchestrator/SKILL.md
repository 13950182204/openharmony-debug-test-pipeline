---
name: openharmony-ci-orchestrator
description: Trigger and coordinate OpenHarmony Jenkins firmware builds for an existing MR or branch, including RK3568 and A333 job selection, Jenkins crumb authentication, durable completion state, authenticated webhooks, scheduled reconciliation, and controlled phase-3 OTA/regression handoff. Use when an OpenHarmony fix needs Jenkins validation and an approved board profile can perform OTA and regression on registered hardware.
---

# OpenHarmony CI Orchestrator

## Delivery boundary

Phase 1 validates the target Jenkins job and enqueues one build. Phase 2 records that real trigger result, receives or reconciles completion, verifies the original Jenkins parameters, and launches one new dsh headless handoff session after a successful matching build. Phase 3 is opt-in: the handoff invokes only a trusted board profile, which validates the archived package and target identity before OTA.

Use [scripts/trigger_jenkins_build.py](scripts/trigger_jenkins_build.py) for phase 1, [scripts/ci_orchestrator.py](scripts/ci_orchestrator.py) for durable state and handoff, and [scripts/phase3_runner.py](scripts/phase3_runner.py) only through a registered profile. Read [references/phase2-operations.md](references/phase2-operations.md) before deployment and [references/phase3-a333.md](references/phase3-a333.md) for the installed A333 flow.

## Workflow

1. Confirm the MR source branch and, when available, its current source SHA. Do not trigger a build for an uncommitted or ambiguous branch.
2. Run the helper with `--dry-run --verify-job`. This performs a read-only Jenkins job check and validates that the required parameters exist.
3. On explicit user authorization to start Jenkins, set `JENKINS_USER` and `JENKINS_API_TOKEN` in the environment and run the helper without `--dry-run`.
4. Pipe the real trigger JSON to `ci_orchestrator.py register` with the absolute repository path. It writes one locked state file with the queue/build identity and original parameter set.
5. Run the authenticated webhook receiver and the hourly systemd timer described in [references/phase2-operations.md](references/phase2-operations.md).
6. The receiver and timer both call `reconcile`; it trusts Jenkins API data rather than the callback body, verifies all tracked parameters, then launches exactly one `dsh --profile headless` session after `SUCCESS`.
7. For an approved phase-3 profile, register it with `--phase3-profile` and `--agent-sandbox danger-full-access`. The handoff runs the fixed runner, not a command from Jenkins or MR text.

## RK3568 invocation

Use the verified RK3568 product parameter explicitly:

```bash
export JENKINS_BASE_URL="${JENKINS_BASE_URL:-http://192.168.13.121:8080}"
export JENKINS_JOB="${JENKINS_JOB:-OpenHarmony-V6.1-RockChip}"

python3 "{{SKILLS_DIR}}/openharmony-ci-orchestrator/scripts/trigger_jenkins_build.py" \
  --branch <mr-source-branch> \
  --source-sha <mr-source-sha> \
  --mr-iid <mr-iid> \
  --build-mode INCREMENTAL \
  --firmware-type XTS \
  --parameter Openharmony_Devices=rk3568_dsi_800x1280 \
  --verify-job \
  --dry-run
```

Remove `--dry-run` only after reviewing the JSON and receiving authorization to enqueue the build. Use `--wait-seconds` only when the caller explicitly needs Jenkins to assign a build number before returning.

## Authentication and safety

- Keep the Jenkins API token out of files, prompts, logs, JSON output, and MR text. The helper reads it only from `JENKINS_API_TOKEN`.
- Require both `JENKINS_USER` and `JENKINS_API_TOKEN` for a real POST. Anonymous read-only job verification is allowed.
- Use `--allow-anonymous` only when the Jenkins endpoint is intentionally configured to allow anonymous build requests and its identity has been checked. This is an explicit exception, never the default.
- Use the Jenkins crumb issuer when available; do not bypass CSRF protection or use Script Console for normal build triggering.
- Encode the job path and all parameter values through the helper. Do not construct a shell command from branch or parameter input.
- Treat the returned queue URL as an opaque identifier. Phase 2 verifies the assigned build parameters before phase 3 can consume artifacts.
- A successful POST means only “queued”, not “built”, “packaged”, “OTA-ready”, or “device-updated”.

## Failure handling

Stop with an actionable error when the job is missing, disabled, required parameters are absent, credentials are incomplete, Jenkins rejects the request, or a parameter is malformed. Do not retry a real POST automatically because a timeout can occur after Jenkins has already queued the build.

## Phase 2 safety

- Register only JSON where phase 1 reported `action: trigger`; `action: dry-run` is rejected.
- Verify the assigned build's submitted parameters against the registered parameters before considering its result. A webhook for a different job/build is ignored.
- A Jenkins failure, cancellation, parameter mismatch, or agent-launch failure does not start OTA or retry a device action.
- The timer is fallback-only. It does not enqueue Jenkins builds and it does not launch a second agent after the first agent is recorded as launched.
- Keep callback HMAC material and Jenkins credentials in a `0600` systemd environment file, never in run-state JSON or MR text.

## Phase 3 safety

- A profile is a trusted JSON file in `profiles/`; branch names, Jenkins parameters, and MR text never supply an executable device command.
- The installed `a333-2g-primary-standby` profile accepts only `OpenHarmony-V6.1-AllWinner`, `a333_medical_dsi_800x1280`, and the expected DHong/76A/DNAKE product parameters. It downloads only the archived `openharmony_V6.1/out/ota/update.zip`.
- Its primary serial is `ea010e325333324247102b4ed1988ce7`; the standby serial is `ea010e325333324247102b4ed1a48c99`. The standby is touched only after the primary fails to upgrade or fails to return to normal HDC boot.
- Every OTA requires package ZIP/version preflight, source-version compatibility, `/data` capacity, post-reconnect device identity, `updater_result=pass`, and `bootevent.boot.completed=true`.
- Phase 3 requires the registered source SHA and scans Jenkins `consoleText` for the actual `HEAD is now at` checkout record before downloading the artifact. A branch-only build has insufficient evidence and is blocked.
- The built-in OTA smoke check always runs. A run without an explicit feature regression profile reports `INCONCLUSIVE`, never a feature-regression pass.
