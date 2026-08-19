#!/usr/bin/env python3
"""Offline tests for the trusted phase-3 profile gate."""

from __future__ import annotations

import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import phase3_runner as runner  # noqa: E402
import ci_orchestrator as orchestrator  # noqa: E402


class Phase3RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = runner.read_profile("a333-2g-primary-standby")

    def test_a333_profile_has_fixed_primary_and_standby(self) -> None:
        self.assertEqual(
            [device["role"] for device in self.profile["devices"]], ["primary", "standby"]
        )
        self.assertEqual(
            self.profile["devices"][0]["serial"], "ea010e325333324247102b4ed1988ce7"
        )
        self.assertEqual(
            self.profile["devices"][1]["serial"], "ea010e325333324247102b4ed1a48c99"
        )

    def test_profile_rejects_wrong_allwinner_product(self) -> None:
        state = {
            "status": "build_succeeded",
            "trigger": {
                "job": "OpenHarmony-V6.1-AllWinner",
                "parameters": {
                    "Openharmony_Devices": "a333_dsi_800x1280",
                    "XTS_PRODUCT_NAME": "DHong-A333-Development-Board",
                    "XTS_PRODUCT_MODEL": "76A",
                    "XTS_PRODUCT_BRAND": "DNAKE",
                    "XTS_PRODUCT_MANUFACTURER": "DHong",
                },
            },
            "build": {"result": "SUCCESS", "verified_parameters": True, "url": "http://jenkins/build/1"},
        }
        with self.assertRaisesRegex(runner.Phase3Error, "Openharmony_Devices"):
            runner.assert_profile_matches(self.profile, state)

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
            "trigger": {"job": "job", "source_branch": "branch", "source_sha": "abcdef0"},
            "build": {"number": 1, "url": "http://jenkins/1"},
            "phase3": {
                "status": "failed",
                "profile": "a333-2g-primary-standby",
                "attempts": [{"role": "primary", "serial": "device", "status": "failed", "updater_evidence": "Version Check Fail"}],
            },
        }
        self.assertIn("Updater rejected the package", runner.mr_note_body(state))

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

    def test_dry_run_downloads_only_the_expected_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "fixture-update.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("version_list", "OpenHarmony 6.1.0.31\n")
            package_bytes = package.read_bytes()

            class JenkinsHandler(BaseHTTPRequestHandler):
                def log_message(self, format: str, *args: object) -> None:
                    return

                def do_GET(self) -> None:
                    if self.path.startswith("/build/29/api/json"):
                        payload = (
                            '{"artifacts":[{"relativePath":'
                            '"openharmony_V6.1/out/ota/update.zip","fileName":"update.zip"}]}'
                        ).encode()
                    elif self.path == "/build/29/artifact/openharmony_V6.1/out/ota/update.zip":
                        payload = package_bytes
                    elif self.path == "/build/29/consoleText":
                        payload = (
                            b"HEAD is now at a1b2c3d4e5f test checkout\n"
                            b"[VERSION] package softVersion: OpenHarmony 6.1.0.32\n"
                        )
                    else:
                        self.send_response(404)
                        self.end_headers()
                        return
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)

            server = ThreadingHTTPServer(("127.0.0.1", 0), JenkinsHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                build_url = f"http://127.0.0.1:{server.server_port}/build/29"
                run_id = "ci-dry-run"
                state_root = root / "state"
                runner.write_json(
                    state_root / "runs" / f"{run_id}.json",
                    {
                        "run_id": run_id,
                        "status": "build_succeeded",
                        "trigger": {
                            "job": "OpenHarmony-V6.1-AllWinner",
                            "source_sha": "a1b2c3d4e5f61111111111111111111111111111",
                            "parameters": {
                                "Openharmony_Devices": "a333_medical_dsi_800x1280",
                                "XTS_PRODUCT_NAME": "DHong-A333-Development-Board",
                                "XTS_PRODUCT_MODEL": "76A",
                                "XTS_PRODUCT_BRAND": "DNAKE",
                                "XTS_PRODUCT_MANUFACTURER": "DHong",
                            },
                        },
                        "build": {"result": "SUCCESS", "verified_parameters": True, "url": build_url},
                        "phase3": {"status": "not_started", "profile": self.profile["id"], "regression_profiles": []},
                        "events": [],
                    },
                )
                with mock.patch.object(
                    sys,
                    "argv",
                    [
                        "phase3_runner.py",
                        "--state-dir",
                        str(state_root),
                        "--run-id",
                        run_id,
                        "--profile",
                        self.profile["id"],
                        "--dry-run",
                    ],
                ):
                    self.assertEqual(runner.main(), 0)
                state = runner.load_json(state_root / "runs" / f"{run_id}.json")
                self.assertEqual(state["phase3"]["status"], "dry_run")
                self.assertTrue(Path(state["phase3"]["package"]["path"]).is_file())
                self.assertEqual(state["phase3"]["package"]["target_version_evidence"], "jenkins_console")
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()

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

    def test_gitlab_configuration_falls_back_to_private_environment_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "jenkins.env"
            config.write_text("GITLAB_HOST=gitlab.example\n", encoding="utf-8")
            with mock.patch.dict("os.environ", {}, clear=True), mock.patch.object(runner, "CONFIG_ENV", config):
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

    def test_adopted_build_is_written_before_handoff_launch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "state"
            args = type("Args", (), {
                "source_sha": "a1b2c3d4e5f6",
                "phase3_profile": self.profile["id"],
                "agent_sandbox": "danger-full-access",
                "repo_dir": directory,
                "base_url": "http://jenkins.example",
                "job": "OpenHarmony-V6.1-AllWinner",
                "build_number": 29,
                "timeout_seconds": 20,
                "branch": "cx/xts-toolchain-fix",
                "mr_iid": "168",
                "run_id": "adopted-29",
                "agent_command": "dsh",
                "regression_profile": [],
            })()
            metadata = {
                "building": False,
                "result": "SUCCESS",
                "actions": [{"parameters": [
                    {"name": "FIRMWARE_BRANCH", "value": "cx/xts-toolchain-fix"},
                    {"name": "Openharmony_Devices", "value": "a333_medical_dsi_800x1280"},
                ]}],
            }

            def assert_state_exists(state_root: Path, state: dict[str, object]) -> None:
                self.assertTrue((state_root / "runs" / "adopted-29.json").is_file())
                state["agent"]["status"] = "launched"

            with mock.patch.object(orchestrator, "build_metadata", return_value=metadata), mock.patch.object(
                orchestrator, "launch_agent", side_effect=assert_state_exists
            ), orchestrator.state_lock(root):
                state = orchestrator.create_adopted_run(root, args)
            self.assertEqual(state["status"], "build_succeeded")
            self.assertEqual(state["agent"]["status"], "launched")
            self.assertEqual(state["trigger"]["parameters"]["FIRMWARE_BRANCH"], "cx/xts-toolchain-fix")


if __name__ == "__main__":
    unittest.main()
