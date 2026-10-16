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
import re
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
import test_harness  # noqa: E402

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

    def test_artifact_candidates_prefer_update_zip_then_fall_back(self) -> None:
        """实测现场：芯片构建同时归档 update.zip 与 updater_full.zip，必须按序选中唯一一个。"""
        candidates = runner.resolved_artifact_patterns(self.profile)
        self.assertEqual(candidates[0], "openharmony_V6.1/out/ota/update.zip")
        both = ["openharmony_V6.1/out/ota/update.zip",
                "openharmony_V6.1/out/ota/updater_full.zip"]
        self.assertEqual(runner.select_artifact_name(both, candidates),
                         "openharmony_V6.1/out/ota/update.zip")
        # update.zip 缺席时退到 updater_full.zip
        self.assertEqual(runner.select_artifact_name(both[1:], candidates),
                         "openharmony_V6.1/out/ota/updater_full.zip")
        # 通配候选命中多个 → 拒绝（不猜）
        with self.assertRaisesRegex(runner.Phase3Error, "matched 2 archived files"):
            runner.select_artifact_name(both, ["openharmony_V6.1/out/ota/*.zip"])
        # 全部候选都不命中 → 报错并列出实际归档
        with self.assertRaisesRegex(runner.Phase3Error, "No archived OTA package matched"):
            runner.select_artifact_name(["gate-plan.json"], candidates)

    def test_artifact_url_requires_exactly_one_match_for_a_single_pattern(self) -> None:
        state = {"build": {"url": "http://jenkins/build/51"}}
        single = [{"relativePath": "openharmony_V6.1/out/ota/update.zip"}]
        with mock.patch.object(runner, "fetch_artifacts", return_value=single):
            self.assertTrue(
                runner.artifact_url(state, "openharmony_V6.1/out/ota/update.zip").endswith(
                    "/artifact/openharmony_V6.1/out/ota/update.zip"
                )
            )
        with mock.patch.object(runner, "fetch_artifacts", return_value=[]):
            with self.assertRaisesRegex(runner.Phase3Error, "No archived OTA package matched"):
                runner.artifact_url(state, "openharmony_V6.1/out/ota/update.zip")

    def test_hdc_file_transfer_translates_paths_for_a_windows_client(self) -> None:
        """双宿主现场：Windows 侧 hdc 会把本地路径当 Windows 路径，必须先用 wslpath 转换。"""
        converted = mock.Mock(returncode=0, stdout="\\\\wsl.localhost\\Ubuntu-22.04\\home\\cx\\a.zip\n", stderr="")
        with mock.patch.object(runner.subprocess, "run", return_value=converted):
            self.assertEqual(runner.wsl_host_path("/home/cx/a.zip"),
                             "\\\\wsl.localhost\\Ubuntu-22.04\\home\\cx\\a.zip")
        # 原生 Linux hdc：wslpath 不可用/失败时保持原路径，绝不改写
        with mock.patch.object(runner.subprocess, "run", side_effect=FileNotFoundError()):
            self.assertEqual(runner.wsl_host_path("/home/cx/a.zip"), "/home/cx/a.zip")
        failed = mock.Mock(returncode=1, stdout="", stderr="wslpath: not found")
        with mock.patch.object(runner.subprocess, "run", return_value=failed):
            self.assertEqual(runner.wsl_host_path("/home/cx/a.zip"), "/home/cx/a.zip")

    def test_transfer_size_mismatch_message_stays_retryable(self) -> None:
        """传输未落地属"未写设备"的预检失败：换路径/换连接后重试要能通过 claim 门禁。"""
        message = ("standby OTA transfer size mismatch: "
                   '"/bin/sh: can\'t open /data/updater/updater_full.zip: '
                   'No such file or directory"')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "state"
            run_id = "retry-transfer"
            runner.write_json(root / "runs" / f"{run_id}.json", {
                "run_id": run_id,
                "phase3": {"status": "failed", "profile": self.profile["id"], "attempts": [],
                           "error": message},
                "events": [],
            })
            claimed = runner.claim_run(root, run_id, self.profile["id"], retry_preflight=True)
        self.assertEqual(claimed["phase3"]["status"], "running")
        self.assertEqual(claimed["phase3"]["preflight_retries"], 1)

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

    def test_device_already_on_target_is_not_reflashed(self) -> None:
        """设备已在本构建固件上（幂等重跑/收尾）：不重新刷机，直接作为已升级候选。"""
        device = self.profile["devices"][0]
        snapshot = {
            "product_name": "OpenHarmony 3.2\n", "product_model": "ohos\n",
            "software_version": "OpenHarmony 6.1.0.35\n",
            "ohos_fullname": "OpenHarmony-6.1.0.35\n",
            "firmware_commitid": GATE_SHA + "\n",
            "device_tree_model": "sun65iw1\n",
            "boot_completed": "true\n", "free_kib_line": "x 1 1 999999 1% /data",
            "write_updater": "/bin/write_updater\n",
        }
        with mock.patch.object(runner, "device_snapshot", return_value=snapshot), \
                mock.patch.object(runner, "collect_updater_evidence", return_value="|pass|"), \
                mock.patch.object(runner, "require_target"):
            attempt = runner.perform_ota(device, Path("/tmp/p.zip"),
                                        {"size": 1, "target_version": "OpenHarmony 6.1.0.32"},
                                        self.profile, GATE_SHA)
        self.assertEqual(attempt["status"], "already_on_target")
        self.assertNotIn("write_updater", attempt)
        self.assertEqual(attempt["skipped_write"], "device already runs the registered source SHA")

    def test_source_whitelist_accepts_the_documented_target_version(self) -> None:
        """包内白名单可能只列旧版本；包自身声明的目标版本同样说明"本机可升级"。"""
        metadata = {"source_versions": ["OpenHarmony 6.1.0.31", "1.3.0"],
                    "target_version": "OpenHarmony 6.1.0.32"}
        on_target = {"software_version": "OpenHarmony 6.1.0.35"}
        self.assertFalse(runner.source_version_matches(metadata, on_target, "software_version"))

    def test_offline_devices_do_not_consume_the_retry_budget(self) -> None:
        """设备池常有多台离线：离线只表示"本轮无可用设备"，不写 attempts。"""
        device = self.profile["devices"][0]
        with mock.patch.object(runner, "require_target",
                               side_effect=runner.DeviceOfflineError("not online")):
            attempt = runner.perform_ota(device, Path("/tmp/p.zip"),
                                         {"size": 1, "target_version": "x"},
                                         self.profile, GATE_SHA)
        self.assertEqual(attempt["status"], "offline")
        self.assertNotIn("write_updater", attempt)

    def test_device_identity_is_evidence_not_a_gate(self) -> None:
        """用户口径：identity 不做硬限制，只要能升级且升级正确。"""
        device = self.profile["devices"][0]
        # 门禁当前产出的通用身份（实测 OTA 后形态）必须被接受
        generic = {
            "product_name": "OpenHarmony 3.2\n", "product_model": "ohos\n",
            "product_brand": "default\n", "product_manufacturer": "default\n",
            "device_tree_model": "sun65iw1\n", "boot_completed": "true\n",
            "write_updater": "/bin/write_updater\n",
        }
        runner.require_device_ready(device, generic)  # 不抛异常
        notes = runner.device_identity_mismatches(device, generic)
        self.assertTrue(any("product_name" in note for note in notes))
        self.assertTrue(any("product_model" in note for note in notes))

    def test_device_ready_still_requires_an_upgradable_target(self) -> None:
        device = self.profile["devices"][0]
        no_writer = {"product_name": "x", "product_model": "y", "device_tree_model": "z",
                     "boot_completed": "true\n", "write_updater": ""}
        with self.assertRaisesRegex(runner.Phase3Error, "no write_updater"):
            runner.require_device_ready(device, no_writer)
        not_booted = dict(no_writer, write_updater="/bin/write_updater", boot_completed="false\n")
        with self.assertRaisesRegex(runner.Phase3Error, "has not completed normal boot"):
            runner.require_device_ready(device, not_booted)

    def test_correctness_is_proven_by_the_firmware_commit_id(self) -> None:
        """升级正确的硬证据：固件 commitid 等于本次构建的源 SHA。"""
        snapshot = {"firmware_commitid": GATE_SHA.upper() + "\n"}
        self.assertTrue(runner.target_verified_by_commitid(GATE_SHA, snapshot))
        self.assertFalse(runner.target_verified_by_commitid("0" * 40, snapshot))

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

    def test_failed_transfer_is_retryable_but_a_written_device_is_not(self) -> None:
        """传输失败=设备未改动，可重试；一旦执行过 write_updater 就不再自动重试。"""
        def claim(attempt: dict, run_id: str) -> object:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "state"
                runner.write_json(root / "runs" / f"{run_id}.json", {
                    "run_id": run_id,
                    "phase3": {"status": "failed", "profile": self.profile["id"],
                               "attempts": [attempt],
                               "error": f"{attempt.get('role', 'standby')} OTA transfer size mismatch"},
                    "events": [],
                })
                return runner.claim_run(root, run_id, self.profile["id"], retry_preflight=True)

        retried = claim({"role": "standby", "transfer": "[Fail]Error opening file"}, "retry-transfer")
        self.assertEqual(retried["phase3"]["status"], "running")

        with self.assertRaisesRegex(runner.Phase3Error, "already been claimed"):
            claim({"role": "standby", "transfer": "ok", "write_updater": "done"}, "retry-written")

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
                # 只归档 updater_full.zip：验证候选顺序会退到第二个候选
                artifacts=["openharmony_V6.1/out/ota/updater_full.zip"],
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
                # 只归档 updater_full.zip 时按候选顺序退到它，并且必须走 --dry-run。
                with mock.patch.object(
                    sys, "argv",
                    ["phase3_runner.py", "--state-dir", str(state_root), "--run-id", run_id,
                     "--profile", self.profile["id"], "--dry-run"],
                ):
                    self.assertEqual(runner.main(), 0)
                state = runner.load_json(state_root / "runs" / f"{run_id}.json")
                self.assertEqual(state["phase3"]["status"], "dry_run")
                self.assertEqual(state["phase3"]["artifact_resolved"],
                                 "openharmony_V6.1/out/ota/updater_full.zip")
                self.assertEqual(state["phase3"]["package"]["target_version"],
                                 "OpenHarmony 6.1.0.32")
                return
                state = runner.load_json(state_root / "runs" / f"{run_id}.json")
                self.assertIn("No archived OTA package matched", state["phase3"]["error"])
            finally:
                stub.stop()


class XtsRerunTests(unittest.TestCase):
    """OTA 成功后重跑测试用例：报告解析、模块匹配与判定规则。"""

    def write_report(self, root: Path, name: str, cases: list[tuple[str, str, bool]]) -> Path:
        suites: dict[str, list[tuple[str, str, bool]]] = {}
        for module, case, ok in cases:
            suites.setdefault(module, []).append((case, module, ok))
        parts = ["<testsuites name='summary_report'>"]
        for module, items in suites.items():
            failed = sum(1 for _, _, ok in items if not ok)
            parts.append(f"<testsuite name='{module}' failures='{failed}'>")
            for case, classname, ok in items:
                parts.append(f"<testcase name='{case}' classname='{classname}' "
                             f"result='{'true' if ok else 'false'}' status='run'/>")
            parts.append("</testsuite>")
        parts.append("</testsuites>")
        directory = root / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "summary_report.xml").write_text("".join(parts), encoding="utf-8")
        return directory

    def test_verdict_passes_only_when_every_case_passed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            good = self.write_report(root, "pass", [
                ("ActsUdmfTest", "case_a", True), ("ActsUdmfTest", "case_b", True)])
            self.assertEqual(test_harness.verdict(good, ["ActsUdmfTest"])["status"], "PASS")

            bad = self.write_report(root, "fail", [
                ("ActsUdmfTest", "case_a", True), ("ActsUdmfTest", "case_b", False)])
            report = test_harness.verdict(bad, ["ActsUdmfTest"])
            self.assertEqual(report["status"], "FAIL")
            self.assertEqual(report["failed"], 1)
            self.assertEqual(report["failed_cases"][0]["name"], "case_b")

    def test_verdict_is_inconclusive_when_nothing_ran(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            empty = self.write_report(root, "empty", [])
            self.assertEqual(test_harness.verdict(empty, ["ActsUdmfTest"])["status"], "INCONCLUSIVE")
            report = test_harness.verdict(empty, ["ActsNotBuiltTest"])
            self.assertEqual(report["status"], "INCONCLUSIVE")
            self.assertEqual(report["missing"], ["ActsNotBuiltTest"])

    def test_partial_coverage_is_not_reported_as_a_pass(self) -> None:
        """请求了两个模块但只跑到一个：没跑到的不能算通过。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_dir = self.write_report(root, "partial", [("ActsAaaTest", "c1", True)])
            report = test_harness.verdict(report_dir, ["ActsAaaTest", "ActsBbbTest"])
            self.assertEqual(report["status"], "INCONCLUSIVE")
            self.assertEqual(report["missing"], ["ActsBbbTest"])

    def test_unavailable_module_is_inconclusive_not_a_pass(self) -> None:
        """真实现场：xdevice 对"模块没跑起来"也 exit 0，报告里是 unavailable='1'。

        实测取值：`[Environment-0101021] Test source required 1 devices, actually 0 devices
        were found`。若只数 failures，这种"根本没跑"会被误判成通过。
        """
        directory = Path(tempfile.mkdtemp())
        (directory / "summary_report.xml").write_text(
            "<testsuites name='summary_report' failures='0' unavailable='1'>"
            "<testsuite name='ActsAACommandPrintSyncTest' failures='0' tests='0' unavailable='1' "
            "message='[Environment-0101021] Test source required 1 devices, actually 0 devices were found'/>"
            "</testsuites>", encoding="utf-8")
        report = test_harness.verdict(directory, ["ActsAACommandPrintSyncTest"])
        self.assertEqual(report["status"], "INCONCLUSIVE")
        self.assertEqual(report["unavailable"], ["ActsAACommandPrintSyncTest"])
        self.assertIn("0 devices", report["unavailable_messages"][0])

    def test_module_name_matching_is_tolerant_of_suite_suffixes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report_dir = self.write_report(Path(directory), "suffix", [
                ("ActsUdmfTest-stage", "c1", True)])
            report = test_harness.verdict(report_dir, ["ActsUdmfTest"])
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["matched"], ["ActsUdmfTest-stage"])

    def test_case_result_false_forms_are_all_treated_as_failure(self) -> None:
        directory = Path(tempfile.mkdtemp())
        (directory / "summary_report.xml").write_text(
            "<testsuites><testsuite name='M'>"
            "<testcase name='a' classname='M' result='FALSE'/>"
            "<testcase name='b' classname='M' result='fail'/>"
            "<testcase name='c' classname='M' status='error'/>"
            "</testsuite></testsuites>", encoding="utf-8")
        report = test_harness.summarize(directory)
        self.assertEqual(report["failed"], 3)

    def test_modules_to_rerun_prefers_explicit_profile_then_triage_state(self) -> None:
        state = {"artifacts": {"failedModules": ["ActsUdmfTest", "ActsBmsTest"],
                               "modules": {"ActsIgnoredTest": {"failed": 1}}}}
        explicit = {"modules": ["ActsExplicitTest"]}
        self.assertEqual(runner.modules_to_rerun(explicit, state), ["ActsExplicitTest"])
        self.assertEqual(runner.modules_to_rerun({}, state),
                         ["ActsUdmfTest", "ActsBmsTest", "ActsIgnoredTest"])
        self.assertEqual(runner.modules_to_rerun({}, {"artifacts": {}}), [])

    def test_unknown_module_names_are_caught_before_running(self) -> None:
        """消费方命名空间校验：`-l` 只认运行器 testcases/*.json 的模块名。

        真实误用现场：把报告里的**测试类名**（`DmsFwkFaTest`）当成模块名（`DctsDmsFaTest`）。
        这类错误以前只能等 xdevice 跑完才暴露（甚至表现为 unavailable），现在执行前就拦下，
        并把消费方能用的模块名样例摆出来。
        """
        with tempfile.TemporaryDirectory() as directory:
            runner_dir = Path(directory) / "dcts-LTS-f"
            (runner_dir / "testcases").mkdir(parents=True)
            (runner_dir / "config").mkdir()
            (runner_dir / "config" / "user_config.xml").write_text("<user_config/>", encoding="utf-8")
            for name in ("DctsDmsFaTest", "DctsDmsFwkStageTest"):
                (runner_dir / "testcases" / f"{name}.json").write_text("{}", encoding="utf-8")
            self.assertEqual(test_harness.available_modules(runner_dir),
                             {"DctsDmsFaTest", "DctsDmsFwkStageTest"})
            self.assertEqual(test_harness.unknown_modules(runner_dir, ["DctsDmsFaTest"]), [])
            self.assertEqual(test_harness.unknown_modules(runner_dir, ["DmsFwkFaTest"]),
                             ["DmsFwkFaTest"])
            result = runner.run_xts_rerun(
                {"id": "x", "kind": "xts-rerun", "runner_dir": str(runner_dir),
                 "modules": ["DmsFwkFaTest"]}, "SN", {"artifacts": {}})
            self.assertEqual(result["status"], "INCONCLUSIVE")
            self.assertIn("not present in the local runner", result["evidence"]["error"])
            self.assertIn("DctsDmsFaTest", result["evidence"]["known_modules_sample"])

    def test_module_name_derived_from_a_case_id(self) -> None:
        self.assertEqual(test_harness.module_name_from_case("ActsUdmfTest#case_0100"),
                         "ActsUdmfTest")
        self.assertEqual(test_harness.module_name_from_case("ActsUdmfTest"), "ActsUdmfTest")
        self.assertEqual(test_harness.module_name_from_case(""), "")

    def test_run_xts_rerun_reports_inconclusive_without_modules(self) -> None:
        regression = {"id": "xts-rerun-lts-f", "kind": "xts-rerun",
                      "runner_dir": "/mnt/d/ohos/XTS6.1/office/acts-LTS-f"}
        result = runner.run_xts_rerun(regression, "serial", {"artifacts": {}})
        self.assertEqual(result["status"], "INCONCLUSIVE")
        self.assertIn("no module to rerun", result["evidence"]["error"])

    def test_windows_runner_translates_paths_and_uses_the_runner_dir_as_cwd(self) -> None:
        """真机实测结论：xdevice 必须在 Windows 侧跑，且 cwd 必须是运行器目录。

        两点都由真实故障换来：①WSL 侧跑得到 `0 devices`（连不到 Windows 的 hdc 服务）；
        ②cwd 不是运行器目录时报 `Task file does not exist, file name: acts.json`。
        顺带：interop 调用的 cmd.exe 输出 GBK 字节，必须容错解码，否则整步抛 UnicodeDecodeError。
        """
        with tempfile.TemporaryDirectory() as directory:
            runner_dir = Path(directory) / "acts-LTS-f"
            (runner_dir / "config").mkdir(parents=True)
            # 真实 profile 指向 user_config.xml（任务文件 acts.json 由 xdevice 在 cwd/config 下自寻）
            (runner_dir / "config" / "user_config.xml").write_text("<user_config/>", encoding="utf-8")
            (runner_dir / "config" / "acts.json").write_text("{}", encoding="utf-8")
            (runner_dir / "testcases").mkdir()
            (runner_dir / "resource").mkdir()
            regression = {"id": "xts-rerun-lts-f", "kind": "xts-rerun",
                          "runner_dir": str(runner_dir), "modules": ["ActsXTest"],
                          "runner_host": "windows",
                          "windows_python": "C:\\Python38\\python.exe",
                          "session_root": str(runner_dir / "reports")}
            captured: dict[str, object] = {}

            def fake_run(command, timeout=60, check=True, max_capture=8000, cwd=None,
                         decode_errors="strict"):
                captured.update(command=command, cwd=cwd, decode_errors=decode_errors)
                return "xdevice ok"

            with mock.patch.object(runner, "run_command", side_effect=fake_run):
                result = runner.run_xts_rerun(regression, "SN123", {"artifacts": {}})
            self.assertEqual(captured["decode_errors"], "replace")
            self.assertEqual(captured["cwd"], str(runner_dir))
            command = captured["command"]
            self.assertEqual(command[0], "/mnt/c/Windows/System32/cmd.exe")
            self.assertEqual(command[1], "/c")
            self.assertIn("-m", command)
            self.assertIn("xdevice", command)
            self.assertTrue(any(part.endswith("python.exe") for part in
                                str(command[2]).split()), str(command)[:200])
            self.assertIn("SN123", command)
            self.assertIn("-l", command)
            # 假 xdevice 没写任何报告：没记录 ≠ 通过，但也不能说用例失败 → INCONCLUSIVE
            self.assertEqual(result["status"], "INCONCLUSIVE")

    def test_windows_path_translation(self) -> None:
        self.assertEqual(runner.windows_path("/mnt/d/ohos/x"), "D:\\ohos\\x")
        self.assertEqual(runner.windows_path("/mnt/c/Windows/Temp"), "C:\\Windows\\Temp")
        # 已经是 Windows 形式时原样返回（幂等）
        self.assertEqual(runner.windows_path("D:\\already\\windows"), "D:\\already\\windows")
        # 非 /mnt 的绝对路径交给 wslpath 处理（同一台机器上与 hdc 传参用的是同一条规则）
        converted = runner.windows_path("/tmp/x")
        self.assertTrue(re.match(r"^([a-zA-Z]:\\\\|\\\\wsl)", converted), converted)

    def test_run_xts_rerun_fails_closed_when_the_runner_is_missing(self) -> None:
        regression = {"id": "xts-rerun-lts-f", "kind": "xts-rerun",
                      "runner_dir": "/nonexistent/runner", "modules": ["ActsXTest"]}
        result = runner.run_xts_rerun(regression, "serial", {"artifacts": {}})
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("runner_dir does not exist", result["evidence"]["error"])

    def test_run_xts_rerun_judges_by_the_real_report_directory(self) -> None:
        """端到端（离线）：假的 xdevice 只写报告，判定必须来自报告而不是退出码。"""
        with tempfile.TemporaryDirectory() as directory:
            runner_dir = Path(directory) / "acts-LTS-f"
            (runner_dir / "config").mkdir(parents=True)
            (runner_dir / "config" / "user_config.xml").write_text("<user_config/>", encoding="utf-8")
            (runner_dir / "config" / "acts.json").write_text("{}", encoding="utf-8")
            (runner_dir / "testcases").mkdir()
            (runner_dir / "resource").mkdir()
            session_root = Path(directory) / "xts-runs"

            def fake_run(command, timeout=60, check=True, max_capture=8000, cwd=None,
                         decode_errors="strict"):
                # 模拟 xdevice：把报告写进 -rp 指定的目录后"退出码 0"
                report_dir = Path(command[command.index("-rp") + 1])
                report_dir.mkdir(parents=True, exist_ok=True)
                (report_dir / "summary_report.xml").write_text(
                    "<testsuites><testsuite name='ActsUdmfTest' failures='1'>"
                    "<testcase name='ok' classname='ActsUdmfTest' result='true'/>"
                    "<testcase name='bad' classname='ActsUdmfTest' result='false'/>"
                    "</testsuite></testsuites>", encoding="utf-8")
                return "xdevice finished"

            regression = {"id": "xts-rerun-lts-f", "kind": "xts-rerun",
                          "runner_dir": str(runner_dir), "modules": ["ActsUdmfTest"],
                          "session_root": str(session_root)}
            with mock.patch.object(runner, "run_command", side_effect=fake_run):
                result = runner.run_xts_rerun(regression, "serial-1", {"artifacts": {}})
            self.assertEqual(result["status"], "FAIL")
            self.assertEqual(result["verdict"]["total"], 2)
            self.assertIn("-sn", result["command"])
            self.assertIn("serial-1", result["command"])
            self.assertEqual(result["evidence"]["failed_cases"][0]["name"], "bad")


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
            "dry_run": False,
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

    def test_dry_run_handoff_is_recorded_and_forwarded_by_the_unit_entry(self) -> None:
        """演练模式：observe --dry-run 要落进状态，单元入口据此给 runner 加 --dry-run。"""
        self.platform.set_mr(status="success")
        with mock.patch.dict(os.environ, self.platform.environ(), clear=False):
            state = orchestrator.create_run(self.root, self.observe_args(dry_run=True))
        self.assertTrue(state["phase3"]["dry_run"])
        entry = Path(__file__).resolve().parents[1] / "scripts" / "phase3_unit_entry.py"
        source = entry.read_text(encoding="utf-8")
        self.assertIn('(state.get("phase3") or {}).get("dry_run")', source)
        self.assertIn('argv.append("--dry-run")', source)

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

    # -- 审计修复项（2026-10-15）------------------------------------------ #
    def test_prune_artifacts_keeps_recent_and_reclaims_old(self) -> None:
        """包只增不删会吃满磁盘：按"最新 N 个 + 超过 N 天"回收。"""
        artifacts = self.root / "artifacts"
        old = artifacts / "ci-old"
        recent = artifacts / "ci-recent"
        for name, size in (("ci-old", 4096), ("ci-recent", 2048)):
            (artifacts / name).mkdir(parents=True)
            (artifacts / name / "update.zip").write_bytes(b"x" * size)
        orchestrator.write_json(self.root / "runs" / "ci-old.json", {
            "run_id": "ci-old", "status": "blocked",
            "updated_at": "2026-01-01T00:00:00+00:00", "events": [],
        })
        orchestrator.write_json(self.root / "runs" / "ci-recent.json", {
            "run_id": "ci-recent", "status": "blocked",
            "updated_at": "2099-01-01T00:00:00+00:00", "events": [],
        })
        report = orchestrator.prune_artifacts(self.root, days=30, keep=1)
        self.assertEqual(report["removed"], ["ci-old"])
        self.assertGreater(report["freed_bytes"], 0)
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())

    def test_prune_artifacts_never_touches_an_active_run(self) -> None:
        artifacts = self.root / "artifacts" / "ci-active"
        artifacts.mkdir(parents=True)
        (artifacts / "update.zip").write_bytes(b"x" * 16)
        orchestrator.write_json(self.root / "runs" / "ci-active.json", {
            "run_id": "ci-active", "status": "gate_pending",
            "updated_at": "2026-01-01T00:00:00+00:00", "events": [],
        })
        report = orchestrator.prune_artifacts(self.root, days=1, keep=0)
        self.assertEqual(report["removed"], [])
        self.assertTrue(artifacts.is_dir())

    def test_rearm_clears_stale_and_restores_a_retryable_state(self) -> None:
        """blocked 是终焉状态：必须能显式重新武装，否则修好环境也永远不会再试。"""
        path = self.root / "runs" / "ci-blocked.json"
        orchestrator.write_json(path, {
            "run_id": "ci-blocked", "status": "blocked",
            "trigger": {"source_sha": GATE_SHA, "platform": {
                "pipeline_status": "success", "stale_source": {"recorded_source_sha": GATE_SHA},
                "gate_build_lookup_error": "rotated out"}},
            "build": {"adopt_error": "no matching build"},
            "phase3": {"status": "not_started", "device_wait_at": "2026-01-01T00:00:00+00:00"},
            "events": [],
        })
        result = orchestrator.rearm_run(self.root, "ci-blocked")
        self.assertEqual(result["from"], "blocked")
        self.assertEqual(result["to"], "gate_succeeded")
        state = orchestrator.load_json(path)
        self.assertNotIn("stale_source", state["trigger"]["platform"])
        self.assertNotIn("adopt_error", state["build"])
        self.assertNotIn("gate_build_lookup_error", state["trigger"]["platform"])
        self.assertIn("rearmed", [event["kind"] for event in state["events"]])

    def test_stale_source_is_cleared_when_the_platform_rebuilds_the_registered_commit(self) -> None:
        """MR 前进后平台对新提交重跑 → 流水线 SHA 回到登记值，应当自动解除 stale。"""
        state = {"trigger": {"source_sha": GATE_SHA, "platform": {}}}
        # 流水线验证的是别的提交 → stale
        self.assertTrue(orchestrator.run_is_stale(
            state, {"sha": "b" * 40}, {"sha": "c" * 40}))
        # 平台对登记提交重跑 → 不再 stale
        self.assertFalse(orchestrator.run_is_stale(
            state, {"sha": "b" * 40}, {"sha": GATE_SHA}))
        # 还没有流水线时按 MR head 兜底
        self.assertTrue(orchestrator.run_is_stale(state, {"sha": "b" * 40}, None))
        self.assertFalse(orchestrator.run_is_stale(state, {"sha": GATE_SHA}, None))

    def test_stamp_platform_clears_a_resolved_stale_marker(self) -> None:
        state = {"trigger": {"source_sha": GATE_SHA, "platform": {"stale_source": {"x": 1}}}}
        orchestrator.stamp_platform(state, mr_payload(), "gate_succeeded", stale=False)
        self.assertNotIn("stale_source", state["trigger"]["platform"])
        orchestrator.stamp_platform(state, mr_payload(), "gate_succeeded", stale=True)
        self.assertIn("stale_source", state["trigger"]["platform"])

    def test_mr_note_reports_identity_notes_and_skipped_writes(self) -> None:
        state = {
            "run_id": "run-2",
            "trigger": {"job": CHIP_JOB, "source_branch": "b", "source_sha": GATE_SHA,
                        "platform": {"pipeline_id": PIPELINE_ID, "pipeline_status": "success"}},
            "build": {"number": 31, "url": "http://jenkins/job/chip/31"},
            "phase3": {
                "status": "inconclusive", "profile": "a333-2g-primary-standby",
                "offline_devices": ["ea010e325333324247102b4ed1988ce7"],
                "attempts": [{
                    "role": "standby", "serial": "150100424a5444345206517525298929",
                    "status": "already_on_target",
                    "skipped_write": "device already runs the registered source SHA",
                    "identity_notes": ["product_name: expected 'DHong-A333-Development-Board', got 'OpenHarmony 3.2'"],
                    "after": {"software_version": "OpenHarmony 6.1.0.35",
                              "firmware_commitid": GATE_SHA, "boot_completed": "true"},
                }],
            },
        }
        body = runner.mr_note_body(state)
        self.assertIn("Not reflashed", body)
        self.assertIn("Identity note", body)
        self.assertIn("Offline at run time", body)
        self.assertIn(GATE_SHA[:12], body)

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
