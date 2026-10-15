#!/usr/bin/env python3
"""Offline tests for the platform-CI observation path and the trusted phase-3 gate.

覆盖三块：
1. phase-3 的 profile 契约（新门禁作业只声明 MR 身份参数）、产物模式解析、包元数据；
2. ci_orchestrator 的 observe/reconcile/认领链路（用真实 HTTP 假 Jenkins 与假 glab）；
3. 只读契约：代码里不再存在任何 Jenkins 触发调用。
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import phase3_runner as runner  # noqa: E402
import ci_orchestrator as orchestrator  # noqa: E402
import gitlab_pipeline as platform  # noqa: E402
import jenkins_api  # noqa: E402

GATE_SHA = "a1b2c3d4e5f61111111111111111111111111111"
GATE_JOB = "OpenHarmony-V6.1-MR-Gate"
CHIP_JOB = "OpenHarmony-V6.1-MR-a333"
MR_IID = "226"
TARGET_BRANCH = "v6.1.0.35_lts"
TARGET_SHA = "f" * 40
PIPELINE_ID = 416


def gate_parameters(**overrides: str) -> dict[str, str]:
    parameters = {
        "MR_IID": MR_IID,
        "SOURCE_SHA": GATE_SHA,
        "TARGET_SHA": TARGET_SHA,
        "TARGET_BRANCH": TARGET_BRANCH,
    }
    parameters.update(overrides)
    return parameters


def mr_payload(status: str = "running", sha: str = GATE_SHA,
               pipeline: bool = True) -> dict:
    payload = {
        "iid": int(MR_IID),
        "state": "opened",
        "sha": sha,
        "source_branch": "cx/mr-gate-demo",
        "target_branch": TARGET_BRANCH,
        "web_url": "http://gitlab.example/mr/226",
        "head_pipeline": {
            "id": PIPELINE_ID, "status": status, "sha": sha,
            "web_url": "http://gitlab.example/pipelines/416",
        },
    }
    if not pipeline:
        payload["head_pipeline"] = None
    return payload


class JenkinsStub:
    """A read-only Jenkins stub: build lists, build metadata, artifacts, consoleText."""

    def __init__(self, *, gate_state: str = "SUCCESS", chip_state: str = "SUCCESS",
                 chip_parameters: dict | None = None,
                 artifacts: list[str] | None = None,
                 plan_chips: list[str] | None = None,
                 gate_number: int = 50, chip_number: int = 51,
                 console: str = "HEAD is now at a1b2c3d4e5f test checkout\n"
                                "[VERSION] package softVersion: OpenHarmony 6.1.0.32\n",
                 package: bytes | None = None) -> None:
        self.gate_state = gate_state
        self.chip_state = chip_state
        self.chip_parameters = dict(chip_parameters if chip_parameters is not None
                                    else gate_parameters())
        self.artifacts = list(artifacts if artifacts is not None
                              else ["openharmony_V6.1/out/ota/update.zip"])
        self.plan_chips = list(plan_chips if plan_chips is not None else ["rk3568", "a333"])
        self.gate_number = gate_number
        self.chip_number = chip_number
        self.console = console
        self.package = package or b""
        self.server: ThreadingHTTPServer | None = None

    # -- HTTP surface ------------------------------------------------------ #
    def build_record(self, number: int, result: str, parameters: dict[str, str],
                     artifacts: list[str] | None = None) -> dict:
        return {
            "number": number,
            "building": False,
            "result": result,
            "actions": [{"parameters": [{"name": name, "value": value}
                                        for name, value in parameters.items()]}],
            "artifacts": [{"relativePath": name, "fileName": name.rsplit("/", 1)[-1]}
                          for name in (artifacts or [])],
        }

    def payload(self, path: str) -> tuple[int, bytes, str]:
        """Return (status, body, content_type)."""
        query = ""
        if "?" in path:
            path, _, query = path.partition("?")
        parts = path.strip("/").split("/")
        if len(parts) < 3 or parts[0] != "job":
            return 404, b"not found", "text/plain"
        job = parts[1]
        rest = parts[2:]
        if rest == ["api", "json"] and job in (GATE_JOB, CHIP_JOB):
            parameters = gate_parameters() if job == GATE_JOB else self.chip_parameters
            number = self.gate_number if job == GATE_JOB else self.chip_number
            result = self.gate_state if job == GATE_JOB else self.chip_state
            artifacts = ["gate-plan.json"] if job == GATE_JOB else self.artifacts
            tree = parse_qs(query).get("tree", [""])[0]
            record = self.build_record(number, result, parameters,
                                       artifacts if "relativePath" in tree else None)
            if "builds[" in tree:
                body = {"name": job, "buildable": True, "builds": [record]}
            elif "relativePath" in tree:
                body = {"artifacts": record["artifacts"]}
            else:
                body = record
            return 200, json.dumps(body).encode(), "application/json"
        if len(rest) >= 2 and rest[1] == "api" and rest[0].isdigit():
            number = int(rest[0])
            tree = parse_qs(query).get("tree", [""])[0]
            job_result = self.gate_state if job == GATE_JOB else self.chip_state
            parameters = gate_parameters() if job == GATE_JOB else self.chip_parameters
            artifacts = ["gate-plan.json"] if job == GATE_JOB else self.artifacts
            record = self.build_record(number, job_result, parameters,
                                       artifacts if "relativePath" in tree else None)
            body = {"artifacts": record["artifacts"]} if "relativePath" in tree else record
            return 200, json.dumps(body).encode(), "application/json"
        if len(rest) >= 2 and rest[1] == "consoleText":
            if job == GATE_JOB:
                plan = {"mr_iid": MR_IID, "source_sha": GATE_SHA, "target_sha": TARGET_SHA,
                        "target_branch": TARGET_BRANCH, "merge_base": TARGET_SHA,
                        "chips": self.plan_chips}
                output = "Jenkins gate plan\n" + json.dumps(plan, ensure_ascii=True) + "\n"
            else:
                output = self.console
            return 200, output.encode(), "text/plain; charset=utf-8"
        if len(rest) >= 3 and rest[1] == "artifact":
            relative = "/".join(rest[2:])
            if job == CHIP_JOB and relative in self.artifacts:
                return 200, self.package, "application/zip"
            return 404, b"no artifact", "text/plain"
        return 404, b"not found", "text/plain"

    def start(self) -> str:
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def do_GET(self) -> None:
                status, body, content_type = stub.payload(self.path)
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self._thread = thread
        return f"http://127.0.0.1:{self.server.server_port}"

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self._thread.join(timeout=5)
            self.server.server_close()
            self.server = None


GLAB_SHIM = '''#!/usr/bin/env python3
"""Test double for `glab api --hostname <host> <endpoint>`."""
import json, os, sys

args = sys.argv[1:]
if args[:1] != ["api"]:
    print("unsupported", file=sys.stderr)
    raise SystemExit(2)
endpoint = args[-1]
fixtures = json.loads(open(os.environ["GLAB_FIXTURES"], encoding="utf-8").read())
if endpoint not in fixtures:
    print(f"404 {endpoint}", file=sys.stderr)
    raise SystemExit(1)
print(json.dumps(fixtures[endpoint]))
'''


class PlatformFixture:
    """Patch GitLab observation to a deterministic, offline fake."""

    def __init__(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)
        self.shim = self.path / "glab"
        self.shim.write_text(GLAB_SHIM, encoding="utf-8")
        self.shim.chmod(self.shim.stat().st_mode | stat.S_IEXEC)
        self.fixtures_path = self.path / "fixtures.json"
        self.fixtures: dict[str, object] = {}
        self.set_mr()

    def set_mr(self, **kwargs: object) -> None:
        self.fixtures[f"projects/{platform.DEFAULT_PROJECT.replace('/', '%2F')}"
                      f"/merge_requests/{MR_IID}"] = mr_payload(**kwargs)
        self.flush()

    def set_pipeline(self, payload: dict) -> None:
        self.fixtures[f"projects/{platform.DEFAULT_PROJECT.replace('/', '%2F')}"
                      f"/pipelines/{PIPELINE_ID}"] = payload
        self.flush()

    def flush(self) -> None:
        self.fixtures_path.write_text(json.dumps(self.fixtures), encoding="utf-8")

    def environ(self) -> dict[str, str]:
        return {"GLAB_BIN": str(self.shim), "GLAB_FIXTURES": str(self.fixtures_path)}

    def close(self) -> None:
        self.directory.cleanup()


class Phase3RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = runner.read_profile("a333-2g-primary-standby")

    # -- profile contract -------------------------------------------------- #
    def test_a333_profile_has_fixed_primary_and_standby(self) -> None:
        # 设备池可扩容：前两台仍是 primary/standby 固定串号，追加设备只允许角色为 standby。
        devices = self.profile["devices"]
        self.assertGreaterEqual(len(devices), 2)
        self.assertEqual([device["role"] for device in devices[:2]], ["primary", "standby"])
        self.assertTrue(all(device["role"] == "standby" for device in devices[2:]))
        self.assertEqual(devices[0]["serial"], "ea010e325333324247102b4ed1988ce7")
        self.assertEqual(devices[1]["serial"], "ea010e325333324247102b4ed1a48c99")

    def test_profile_targets_the_platform_gate_job_and_chip(self) -> None:
        self.assertEqual(self.profile["jenkins"]["job"], CHIP_JOB)
        self.assertEqual(orchestrator.profile_chip(self.profile), "a333")
        self.assertEqual(self.profile["artifact"]["relative_path"],
                         "openharmony_V6.1/out/ota/*.zip")

    def test_profile_rejects_wrong_gate_job(self) -> None:
        state = {
            "status": "build_succeeded",
            "trigger": {
                "job": "OpenHarmony-V6.1-AllWinner",
                "parameters": {"MR_IID": MR_IID, "SOURCE_SHA": GATE_SHA},
            },
            "build": {"result": "SUCCESS", "verified_parameters": True,
                      "url": "http://jenkins/build/1"},
        }
        with self.assertRaisesRegex(runner.Phase3Error, "requires Jenkins job"):
            runner.assert_profile_matches(self.profile, state)

    def test_profile_accepts_platform_gate_build(self) -> None:
        state = {
            "status": "build_succeeded",
            "trigger": {
                "job": CHIP_JOB, "mr_iid": MR_IID, "source_sha": GATE_SHA,
                "parameters": gate_parameters(),
            },
            "build": {"result": "SUCCESS", "verified_parameters": True,
                      "url": "http://jenkins/build/1"},
        }
        runner.assert_profile_matches(self.profile, state)

    def test_profile_rejects_evidence_parameter_that_contradicts_the_board(self) -> None:
        # evidence_parameters 是"期望证据"：一旦构建参数里出现且不符，说明作业配方被改过。
        state = {
            "status": "build_succeeded",
            "trigger": {
                "job": CHIP_JOB, "mr_iid": MR_IID, "source_sha": GATE_SHA,
                "parameters": gate_parameters(Openharmony_Devices="rk3568_dsi_800x1280"),
            },
            "build": {"result": "SUCCESS", "verified_parameters": True,
                      "url": "http://jenkins/build/1"},
        }
        with self.assertRaisesRegex(runner.Phase3Error, "Openharmony_Devices"):
            runner.assert_profile_matches(self.profile, state)

    def test_profile_rejects_a_gate_build_for_another_source_sha(self) -> None:
        state = {
            "status": "build_succeeded",
            "trigger": {
                "job": CHIP_JOB, "mr_iid": MR_IID, "source_sha": GATE_SHA,
                "parameters": gate_parameters(SOURCE_SHA="0" * 40),
            },
            "build": {"result": "SUCCESS", "verified_parameters": True,
                      "url": "http://jenkins/build/1"},
        }
        with self.assertRaisesRegex(runner.Phase3Error, "was made from"):
            runner.assert_profile_matches(self.profile, state)

    # -- evidence / artifact resolution ------------------------------------ #
    def test_source_sha_requires_console_checkout_match(self) -> None:
        state = {"trigger": {"source_sha": "0123456"}, "build": {"url": "http://jenkins/build/1"}}
        response = mock.MagicMock()
        response.read.side_effect = [b"HEAD is now at abcdef0 other\n", b""]
        response.__enter__.return_value = response
        with mock.patch.object(runner, "build_http_opener") as opener, mock.patch.object(
            runner, "credentials", return_value={}
        ):
            opener.return_value.open.return_value = response
            with self.assertRaisesRegex(runner.Phase3Error, "no checkout matching"):
                runner.verify_source_sha(state)

    def test_console_checkout_matches_the_detached_head_format(self) -> None:
        # 门禁用 `git checkout --detach <sha>`：输出是 `HEAD is now at <短sha> <subject>`。
        state = {"trigger": {"source_sha": GATE_SHA}, "build": {"url": "http://jenkins/build/1"}}
        response = mock.MagicMock()
        response.read.side_effect = [
            b"HEAD is now at a1b2c3d4e5f [RK3568] fix something\n",
            b"",
        ]
        response.__enter__.return_value = response
        with mock.patch.object(runner, "build_http_opener") as opener, mock.patch.object(
            runner, "credentials", return_value={}
        ):
            opener.return_value.open.return_value = response
            evidence = runner.verify_source_sha(state)
        self.assertEqual(evidence["verified"], "a1b2c3d4e5f")

    def test_artifact_pattern_requires_exactly_one_match(self) -> None:
        state = {"build": {"url": "http://jenkins/build/51"}}
        single = [{"relativePath": "openharmony_V6.1/out/ota/update.zip"}]
        with mock.patch.object(runner, "fetch_artifacts", return_value=single):
            self.assertTrue(
                runner.artifact_url(state, "openharmony_V6.1/out/ota/*.zip").endswith(
                    "/artifact/openharmony_V6.1/out/ota/update.zip"
                )
            )
        both = single + [{"relativePath": "openharmony_V6.1/out/ota/updater_full.zip"}]
        with mock.patch.object(runner, "fetch_artifacts", return_value=both):
            with self.assertRaisesRegex(runner.Phase3Error, "found 2"):
                runner.artifact_url(state, "openharmony_V6.1/out/ota/*.zip")
        with mock.patch.object(runner, "fetch_artifacts", return_value=[]):
            with self.assertRaisesRegex(runner.Phase3Error, "found 0"):
                runner.artifact_url(state, "openharmony_V6.1/out/ota/*.zip")

    def test_package_metadata_requires_source_and_one_target_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "update.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("updater_config/VERSION.mbn", "OpenHarmony 6.1.0.31\n")
                archive.writestr(
                    "updater_config/updater_specified_config.xml",
                    '<updater softVersion="OpenHarmony 6.1.0.32"/>',
                )
            metadata = runner.package_metadata(package)
        self.assertEqual(metadata["source_versions"], ["OpenHarmony 6.1.0.31"])
        self.assertEqual(metadata["target_version"], "OpenHarmony 6.1.0.32")

    def test_a333_source_gate_uses_the_updater_version_property(self) -> None:
        metadata = {"source_versions": ["OpenHarmony 6.1.0.31"]}
        snapshot = {
            "software_version": "1.3.0",
            "ohos_fullname": "OpenHarmony-6.1.0.31",
        }
        self.assertFalse(runner.source_version_matches(metadata, snapshot, "software_version"))
        self.assertTrue(runner.source_version_matches(metadata, snapshot, "ohos_fullname"))

    def test_mr_note_surfaces_updater_version_rejection(self) -> None:
        state = {
            "run_id": "run-1",
            "trigger": {"job": CHIP_JOB, "source_branch": "branch", "source_sha": GATE_SHA,
                        "platform": {"pipeline_id": PIPELINE_ID, "pipeline_status": "success",
                                     "gate_build_number": 50,
                                     "gate_build_url": "http://jenkins/job/gate/50"}},
            "build": {"number": 51, "url": "http://jenkins/job/chip/51"},
            "phase3": {
                "status": "failed",
                "profile": "a333-2g-primary-standby",
                "attempts": [{"role": "primary", "serial": "device", "status": "failed",
                              "updater_evidence": "Version Check Fail"}],
            },
        }
        body = runner.mr_note_body(state)
        self.assertIn("Updater rejected the package", body)
        self.assertIn("Platform pipeline", body)
        self.assertIn(str(PIPELINE_ID), body)

    def test_smoke_only_is_inconclusive(self) -> None:
        snapshot = {
            "boot_completed": "true\n",
            "product_name": "DHong-A333-Development-Board\n",
            "product_model": "76A\n",
            "device_tree_model": "sun65iw1\n",
            "software_version": "OpenHarmony 6.1.0.32\n",
            "ohos_fullname": "OpenHarmony-6.1.0.32\n",
            "free_kib_line": "",
            "write_updater": "/bin/write_updater\n",
        }
        with mock.patch.object(runner, "device_snapshot", return_value=snapshot), mock.patch.object(
            runner, "collect_updater_evidence", return_value="pass\n"
        ):
            result = runner.run_regressions(self.profile, [], self.profile["devices"][0])
        self.assertEqual(result["status"], "INCONCLUSIVE")
        self.assertEqual(result["results"][0]["status"], "PASS")

    # -- claim / retry policy --------------------------------------------- #
    def test_preflight_retry_is_limited_to_a_source_sha_gate_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "state"
            run_id = "retry-source-sha"
            runner.write_json(
                root / "runs" / f"{run_id}.json",
                {
                    "run_id": run_id,
                    "phase3": {
                        "status": "failed",
                        "profile": self.profile["id"],
                        "attempts": [],
                        "error": "OTA package must expose one target softVersion, got []",
                    },
                    "events": [],
                },
            )
            claimed = runner.claim_run(root, run_id, self.profile["id"], retry_preflight=True)
        self.assertEqual(claimed["phase3"]["status"], "running")
        self.assertEqual(claimed["phase3"]["preflight_retries"], 1)

    def test_preflight_retry_archives_non_mutating_device_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "state"
            run_id = "retry-device-identity"
            runner.write_json(
                root / "runs" / f"{run_id}.json",
                {
                    "run_id": run_id,
                    "phase3": {
                        "status": "failed",
                        "profile": self.profile["id"],
                        "preflight_retries": 4,
                        "attempts": [{"role": "primary", "error": "device identity mismatch"}],
                        "error": "device identity mismatch",
                    },
                    "events": [],
                },
            )
            claimed = runner.claim_run(root, run_id, self.profile["id"], retry_preflight=True)
        self.assertEqual(claimed["phase3"]["attempts"], [])
        self.assertEqual(claimed["phase3"]["preflight_failures"][0]["role"], "primary")
        self.assertEqual(claimed["phase3"]["preflight_retries"], 5)

    # -- helpers ----------------------------------------------------------- #
    def test_gitlab_configuration_falls_back_to_private_environment_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "jenkins.env"
            config.write_text("GITLAB_HOST=gitlab.example\n", encoding="utf-8")
            with mock.patch.dict("os.environ", {}, clear=True), mock.patch.object(
                runner, "CONFIG_ENV", config
            ):
                self.assertEqual(runner.configured_value("GITLAB_HOST"), "gitlab.example")

    def test_successful_command_output_excludes_stderr_warnings(self) -> None:
        completed = mock.Mock(returncode=0, stdout="value\n", stderr="[W] non-fatal\n")
        with mock.patch.object(runner.subprocess, "run", return_value=completed):
            self.assertEqual(runner.run_command(["hdc", "shell", "param get value"]), "value")

    def test_command_can_keep_full_output_for_a_structured_api_response(self) -> None:
        completed = mock.Mock(returncode=0, stdout="[" + "x" * 9_000 + "]", stderr="")
        with mock.patch.object(runner.subprocess, "run", return_value=completed):
            output = runner.run_command(["glab", "api", "notes"], max_capture=10_000)
        self.assertEqual(len(output), 9_002)

    def test_hdc_output_filters_only_the_known_local_warning(self) -> None:
        output = "sun65iw1[W][2026-08-09 20:50:11] FreeChannelContinue handle->data is nullptr\n"
        with mock.patch.object(runner, "run_command", return_value=output):
            self.assertEqual(runner.hdc("serial", "shell", "cat /proc/device-tree/model"), "sun65iw1")

    def test_dry_run_downloads_only_the_expected_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "fixture-update.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("version_list", "OpenHarmony 6.1.0.31\n")
                archive.writestr(
                    "updater_config/updater_specified_config.xml",
                    '<updater softVersion="OpenHarmony 6.1.0.32"/>',
                )
            stub = JenkinsStub(
                artifacts=["openharmony_V6.1/out/ota/update.zip",
                           "openharmony_V6.1/out/ota/updater_full.zip"],
                package=package.read_bytes(),
            )
            base_url = stub.start()
            try:
                run_id = "ci-dry-run"
                state_root = root / "state"
                runner.write_json(
                    state_root / "runs" / f"{run_id}.json",
                    {
                        "run_id": run_id,
                        "status": "build_succeeded",
                        "trigger": {
                            "job": CHIP_JOB,
                            "source_sha": GATE_SHA,
                            "mr_iid": MR_IID,
                            "chip": "a333",
                            "parameters": gate_parameters(),
                            "platform": {"pipeline_id": PIPELINE_ID,
                                         "pipeline_status": "success"},
                        },
                        "build": {"result": "SUCCESS", "verified_parameters": True,
                                  "url": f"{base_url}/job/{CHIP_JOB}/51"},
                        "phase3": {"status": "not_started", "profile": self.profile["id"],
                                   "regression_profiles": []},
                        "events": [],
                    },
                )
                # 只用模式声明包来源时，两个 .zip 命中会让 runner 明确拒绝而不是猜。
                with mock.patch.object(
                    sys, "argv",
                    ["phase3_runner.py", "--state-dir", str(state_root), "--run-id", run_id,
                     "--profile", self.profile["id"], "--dry-run"],
                ):
                    self.assertEqual(runner.main(), 2)
                state = runner.load_json(state_root / "runs" / f"{run_id}.json")
                self.assertIn("Expected exactly one Jenkins artifact",
                              state["phase3"]["error"])
            finally:
                stub.stop()


class OrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.platform = PlatformFixture()
        self.addCleanup(self.platform.close)
        self.stub = JenkinsStub()
        self.base_url = self.stub.start()
        self.addCleanup(self.stub.stop)
        self.root = Path(tempfile.mkdtemp())
        self.repo = Path(tempfile.mkdtemp())

    def observe_args(self, **overrides: object) -> object:
        values = {
            "state_dir": str(self.root),
            "mr_iid": MR_IID,
            "source_sha": GATE_SHA,
            "source_branch": "cx/mr-gate-demo",
            "target_branch": TARGET_BRANCH,
            "base_url": self.base_url,
            "repo_dir": str(self.repo),
            "run_id": "ci-observe",
            "agent_sandbox": "danger-full-access",
            "phase3_profile": "a333-2g-primary-standby",
            "regression_profile": [],
            "timeout_seconds": 20,
            "mode": "observe",
        }
        values.update(overrides)
        return type("Args", (), values)()

    def test_observe_records_the_platform_pipeline_without_touching_jenkins(self) -> None:
        with mock.patch.dict(os.environ, self.platform.environ(), clear=False):
            state = orchestrator.create_run(self.root, self.observe_args())
        self.assertEqual(state["status"], "gate_pending")
        self.assertEqual(state["trigger"]["platform"]["pipeline_id"], PIPELINE_ID)
        self.assertEqual(state["trigger"]["platform"]["pipeline_status"], "running")
        self.assertEqual(state["trigger"]["chip"], "a333")
        self.assertIsNone(state["build"]["result"])
        self.assertIsNone(state["trigger"]["platform"]["gate_build_url"] if
                          "gate_build_url" in state["trigger"]["platform"] else None)

    def test_observe_marks_a_failed_platform_pipeline_as_terminal(self) -> None:
        self.platform.set_mr(status="failed")
        with mock.patch.dict(os.environ, self.platform.environ(), clear=False):
            state = orchestrator.create_run(self.root, self.observe_args())
        self.assertEqual(state["status"], "gate_failed")
        self.assertIn(state["status"], orchestrator.TERMINAL_RUN_STATUSES)

    def test_observe_marks_a_branch_outside_the_gate_scope(self) -> None:
        self.platform.set_mr(status="skipped")
        self.platform.fixtures[
            f"projects/{platform.DEFAULT_PROJECT.replace('/', '%2F')}/merge_requests/{MR_IID}"
        ]["target_branch"] = "master"
        self.platform.flush()
        with mock.patch.dict(os.environ, self.platform.environ(), clear=False):
            state = orchestrator.create_run(self.root, self.observe_args(target_branch="master"))
        self.assertEqual(state["status"], "gate_not_in_scope")
        self.assertIn(state["status"], orchestrator.TERMINAL_RUN_STATUSES)

    def test_reconcile_claims_the_platform_build_and_starts_phase_3(self) -> None:
        self.platform.set_mr(status="success")
        with mock.patch.dict(os.environ, self.platform.environ(), clear=False):
            state = orchestrator.create_run(self.root, self.observe_args())
        with mock.patch.object(orchestrator, "phase3_unit_state", return_value="inactive"), \
                mock.patch.object(orchestrator, "start_phase3_unit") as starter, \
                mock.patch.dict(os.environ, self.platform.environ(), clear=False):
            result = orchestrator.reconcile_runs(self.root, 20)
        self.assertEqual(result["runs"][0]["action"], "phase3_unit_started")
        starter.assert_called_once()
        claimed = orchestrator.load_json(self.root / "runs" / "ci-observe.json")
        self.assertEqual(claimed["status"], "build_succeeded")
        self.assertEqual(claimed["build"]["number"], 51)
        self.assertEqual(claimed["build"]["chip"], "a333")
        self.assertEqual(claimed["build"]["artifacts"],
                         ["openharmony_V6.1/out/ota/update.zip"])
        self.assertEqual(claimed["trigger"]["platform"]["gate_build_number"], 50)
        self.assertEqual(claimed["trigger"]["parameters"]["SOURCE_SHA"], GATE_SHA)

    def test_reconcile_blocks_when_the_mr_advanced_after_the_gate_ran(self) -> None:
        self.platform.set_mr(status="success")  # 观察时平台流水线已成功

        with mock.patch.dict(os.environ, self.platform.environ(), clear=False):
            state = orchestrator.create_run(self.root, self.observe_args())
        self.platform.set_mr(status="success", sha="b" * 40)
        with mock.patch.dict(os.environ, self.platform.environ(), clear=False):
            result = orchestrator.reconcile_runs(self.root, 20)
        self.assertEqual(result["runs"][0]["action"], "stale_source")
        claimed = orchestrator.load_json(self.root / "runs" / "ci-observe.json")
        self.assertEqual(claimed["status"], "blocked")
        self.assertIn(claimed["status"], orchestrator.TERMINAL_RUN_STATUSES)
        del state

    def test_reconcile_blocks_when_the_gate_built_another_chip(self) -> None:
        self.platform.set_mr(status="success")
        self.stub.plan_chips = ["rk3568"]
        with mock.patch.dict(os.environ, self.platform.environ(), clear=False):
            orchestrator.create_run(self.root, self.observe_args())
        with mock.patch.dict(os.environ, self.platform.environ(), clear=False):
            result = orchestrator.reconcile_runs(self.root, 20)
        self.assertEqual(result["runs"][0]["action"], "gate_adopt_failed")
        claimed = orchestrator.load_json(self.root / "runs" / "ci-observe.json")
        self.assertIn("must verify", claimed["build"]["adopt_error"])
        self.assertIn("gate_adopt_failed", [event["kind"] for event in claimed["events"]])

    def test_reconcile_does_not_claim_a_chip_build_for_another_source_sha(self) -> None:
        self.platform.set_mr(status="success")
        self.stub.chip_parameters = gate_parameters(SOURCE_SHA="0" * 40)
        with mock.patch.dict(os.environ, self.platform.environ(), clear=False):
            orchestrator.create_run(self.root, self.observe_args())
        with mock.patch.dict(os.environ, self.platform.environ(), clear=False):
            result = orchestrator.reconcile_runs(self.root, 20)
        self.assertEqual(result["runs"][0]["action"], "gate_adopt_failed")
        claimed = orchestrator.load_json(self.root / "runs" / "ci-observe.json")
        self.assertIn("No successful", claimed["build"]["adopt_error"])

    def test_reconcile_skips_terminal_runs_and_prunes_old_ones(self) -> None:
        with mock.patch.dict(os.environ, self.platform.environ(), clear=False):
            orchestrator.create_run(self.root, self.observe_args(run_id="ci-old"))
        path = self.root / "runs" / "ci-old.json"
        state = orchestrator.load_json(path)
        state["status"] = "gate_failed"
        state["updated_at"] = "2026-01-01T00:00:00+00:00"
        orchestrator.write_json(path, state)
        result = orchestrator.reconcile_runs(self.root, 20, prune_days=7)
        self.assertEqual(result["runs"], [])
        self.assertEqual(result["pruned"], ["ci-old"])
        self.assertTrue((self.root / "runs" / "archive" / "ci-old.json").is_file())

    def test_reconcile_falls_back_to_the_chip_build_when_the_gate_build_is_gone(self) -> None:
        """汇总门禁被打扫掉时，芯片构建身份 + console 证据仍足以认领（但要把降级写进状态）。"""
        self.platform.set_mr(status="success")
        with mock.patch.dict(os.environ, self.platform.environ(), clear=False):
            orchestrator.create_run(self.root, self.observe_args())
        with mock.patch.object(orchestrator, "locate_gate_build",
                               side_effect=orchestrator.OrchestratorError("gate build rotated out")), \
                mock.patch.object(orchestrator, "phase3_unit_state", return_value="inactive"), \
                mock.patch.object(orchestrator, "start_phase3_unit"), \
                mock.patch.dict(os.environ, self.platform.environ(), clear=False):
            result = orchestrator.reconcile_runs(self.root, 20)
        self.assertEqual(result["runs"][0]["action"], "phase3_unit_started")
        claimed = orchestrator.load_json(self.root / "runs" / "ci-observe.json")
        self.assertEqual(claimed["build"]["number"], 51)
        self.assertIn("gate build rotated out",
                      claimed["trigger"]["platform"]["gate_build_lookup_error"])
        self.assertIn("gate_build_unavailable",
                      [event["kind"] for event in claimed["events"]])

    def test_end_to_end_observe_to_package_dry_run(self) -> None:
        """observe → 认领 → 包演练：整条只读链路可在离线下跑通。"""
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "gate-update.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("updater_config/VERSION.mbn", "OpenHarmony 6.1.0.31\n")
                archive.writestr(
                    "updater_config/updater_specified_config.xml",
                    '<updater softVersion="OpenHarmony 6.1.0.32"/>',
                )
            self.stub.artifacts = ["openharmony_V6.1/out/ota/update.zip"]
            self.stub.package = package.read_bytes()
            self.platform.set_mr(status="success")
            with mock.patch.dict(os.environ, self.platform.environ(), clear=False):
                orchestrator.create_run(self.root, self.observe_args())
            with mock.patch.object(orchestrator, "phase3_unit_state", return_value="inactive"), \
                    mock.patch.object(orchestrator, "start_phase3_unit"), \
                    mock.patch.dict(os.environ, self.platform.environ(), clear=False):
                orchestrator.reconcile_runs(self.root, 20)
            claimed = orchestrator.load_json(self.root / "runs" / "ci-observe.json")
            self.assertEqual(claimed["build"]["chip"], "a333")
            with mock.patch.object(
                sys, "argv",
                ["phase3_runner.py", "--state-dir", str(self.root), "--run-id", "ci-observe",
                 "--profile", "a333-2g-primary-standby", "--dry-run"],
            ):
                self.assertEqual(runner.main(), 0)
            finished = runner.load_json(self.root / "runs" / "ci-observe.json")
            self.assertEqual(finished["phase3"]["status"], "dry_run")
            self.assertEqual(finished["phase3"]["package"]["target_version"],
                             "OpenHarmony 6.1.0.32")
            self.assertTrue(Path(finished["phase3"]["package"]["path"]).is_file())

    def test_reconcile_retires_runs_from_the_removed_self_triggering_flow(self) -> None:
        # 旧时代自触发的 AllWinner/RockChip run 没有 platform 字段：收敛为终态，不再轮询。
        legacy = self.root / "runs" / "ci-legacy.json"
        orchestrator.write_json(legacy, {
            "run_id": "ci-legacy", "status": "queued", "created_at": "2026-09-01T00:00:00+00:00",
            "updated_at": "2026-09-01T00:00:00+00:00", "events": [],
            "trigger": {"job": "OpenHarmony-V6.1-AllWinner", "queue_url": "http://old/queue/item/1"},
            "build": {},
        })
        result = orchestrator.reconcile_runs(self.root, 20)
        self.assertEqual(result["retired"], ["ci-legacy"])
        self.assertEqual(result["runs"], [])
        retired = orchestrator.load_json(legacy)
        self.assertEqual(retired["status"], "blocked")
        self.assertTrue(retired["build"]["legacy_self_trigger"])

    def test_adopt_registers_and_claims_in_one_step(self) -> None:
        with mock.patch.object(orchestrator, "start_phase3_unit") as starter, \
                mock.patch.dict(os.environ, self.platform.environ(), clear=False):
            state = orchestrator.create_run(self.root, self.observe_args(mode="adopt",
                                                                        run_id="ci-adopt"))
        self.assertEqual(state["status"], "build_succeeded")
        self.assertEqual(state["build"]["number"], 51)
        self.assertEqual(state["trigger"]["platform"]["source"], "adopt")
        del starter


class ContractTests(unittest.TestCase):
    """The plugin must not trigger Jenkins builds any more."""

    def test_no_trigger_calls_remain_in_the_skill_scripts(self) -> None:
        offenders: list[str] = []
        for path in sorted(SCRIPTS.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for marker in ("buildWithParameters", "queue/cancelItem", "crumbIssuer"):
                if marker in text:
                    offenders.append(f"{path.name}:{marker}")
        self.assertEqual(offenders, [])

    def test_jenkins_api_is_read_only(self) -> None:
        for name in ("trigger_build", "resolve_queue", "crumb_headers"):
            self.assertFalse(hasattr(jenkins_api, name), name)
        self.assertTrue(hasattr(jenkins_api, "job_builds"))
        self.assertTrue(hasattr(jenkins_api, "build_console_text"))

    def test_default_jenkins_host_is_the_platform_gate(self) -> None:
        self.assertEqual(jenkins_api.DEFAULT_BASE_URL, "http://192.168.97.54:8080")

    def test_gate_build_url_from_the_runner_trace(self) -> None:
        trace = ("Running with gitlab-runner 13.1.3\n"
                 "Jenkins gate: http://192.168.97.54:8080/job/OpenHarmony-V6.1-MR-Gate/50/\n"
                 "Job succeeded\n")
        self.assertEqual(platform.gate_build_url(trace),
                         "http://192.168.97.54:8080/job/OpenHarmony-V6.1-MR-Gate/50/")

    def test_pipeline_status_mapping_covers_platform_states(self) -> None:
        cases = {
            "success": "gate_succeeded",
            "failed": "gate_failed",
            "canceled": "gate_aborted",
            "skipped": "gate_aborted",
            "running": "gate_pending",
            "pending": "gate_pending",
            "created": "gate_pending",
        }
        for status, expected in cases.items():
            self.assertEqual(platform.run_state_for({"status": status},
                                                    target_branch=TARGET_BRANCH), expected,
                             status)

    def test_scope_outside_the_release_branches_is_explicit(self) -> None:
        state = platform.run_state_for({"status": "skipped"}, target_branch="master")
        self.assertEqual(state, "gate_not_in_scope")


if __name__ == "__main__":
    unittest.main()
