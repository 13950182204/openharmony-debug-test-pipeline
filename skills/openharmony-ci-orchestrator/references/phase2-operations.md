# Phase 2 operations

## State flow

1. Trigger Jenkins with `trigger_jenkins_build.py` and save its JSON output.
2. Register that real trigger result with `ci_orchestrator.py register`.
3. Jenkins calls `POST /jenkins` with an HMAC signature after the build finishes.
4. The webhook receiver fetches the Jenkins build API and verifies all originally submitted parameters before recording success or failure.
5. A successful matching build launches exactly one new `dsh --profile headless` session. The hourly timer repeats the same reconciliation path if Jenkins cannot reach the webhook receiver.

Without a phase-3 profile, the agent handoff remains read-only. A registration that explicitly names a trusted phase-3 profile and uses the `danger-full-access` sandbox instead invokes the fixed phase-3 runner after a verified successful build. The run state uses `queued`, `building`, `build_succeeded`, `build_failed`, and `blocked`; phase-3 results live independently under `phase3.status` so a failed OTA can never be confused with a Jenkins failure.

## Register a run

Use a pipe so only a real, successful phase-1 trigger can create a state file:

```bash
python3 "{{SKILLS_DIR}}/openharmony-ci-orchestrator/scripts/trigger_jenkins_build.py" \
  --branch <source-branch> --source-sha <source-sha> --mr-iid <iid> \
  --parameter Openharmony_Devices=rk3568_dsi_800x1280 \
  --wait-seconds 30 \
| python3 "{{SKILLS_DIR}}/openharmony-ci-orchestrator/scripts/ci_orchestrator.py" \
  register --repo-dir <absolute-repository-path>
```

`JENKINS_USER` and `JENKINS_API_TOKEN` must be present for the real trigger. The registration command rejects a phase-1 dry-run result.

## Webhook contract

Set `CI_WEBHOOK_SECRET` only in the systemd environment file. Jenkins must send a JSON body in either form:

```json
{"job_name":"OpenHarmony-V6.1-RockChip","build_number":46}
```

```json
{"job":"OpenHarmony-V6.1-RockChip","build":{"number":46}}
```

Add `X-CI-Signature: sha256=<hex-hmac-sha256-of-raw-body>`. The receiver never trusts a webhook's claimed result. It reads Jenkins' build API, then validates the tracked branch and parameters before changing state.

Bind to `127.0.0.1` by default. To receive a remote Jenkins callback, bind an explicit reachable address or place a reverse proxy in front of it. Keep HMAC verification enabled in either case.

## Install the fallback timer

Run once on the host that has Jenkins network access and will later control the target RK3568:

```bash
python3 "{{SKILLS_DIR}}/openharmony-ci-orchestrator/scripts/install_systemd_units.py"
```

Fill `~/.config/openharmony-ci-orchestrator/jenkins.env` with a random `CI_WEBHOOK_SECRET` and Jenkins credentials when required. Then enable the service and hourly timer:

```bash
python3 "{{SKILLS_DIR}}/openharmony-ci-orchestrator/scripts/install_systemd_units.py" \
  --listen-host <reachable-host-or-ip> --enable
```

The user service requires the machine to remain on. `loginctl enable-linger <user>` makes it survive logout; verify it with `loginctl show-user <user> -p Linger`.

## Inspect and recover

```bash
python3 "{{SKILLS_DIR}}/openharmony-ci-orchestrator/scripts/ci_orchestrator.py" reconcile
systemctl --user status openharmony-ci-webhook.service openharmony-ci-reconcile.timer
ls -1 ~/.local/state/openharmony-ci-orchestrator/runs/
```

Do not delete an active state file to retry. Inspect its `events` and Jenkins build URL first. A failed or blocked build never launches the follow-up agent.

The state files use atomic replacement and a process lock. A duplicate webhook, a timer tick, or a webhook for another queued run cannot launch a second phase-3 agent.

## Phase 3 registration

Only use this for a profile which has been reviewed and whose exact device pool is available on the host. The runner rejects all unspecified package paths and HDC serials:

```bash
... trigger_jenkins_build.py <verified A333 arguments> \
| python3 "{{SKILLS_DIR}}/openharmony-ci-orchestrator/scripts/ci_orchestrator.py" \
    register --repo-dir <absolute-repository-path> \
    --phase3-profile a333-2g-primary-standby \
    --regression-profile <trusted-feature-profile> \
    --agent-sandbox danger-full-access
```

Read [phase3-a333.md](phase3-a333.md) before enabling this path. Set `GITLAB_HOST` and `GITLAB_PROJECT` in the same systemd environment file to receive the idempotent MR evidence note.
