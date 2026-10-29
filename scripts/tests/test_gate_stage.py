#!/usr/bin/env python3
"""演示门禁（gate_stage.py）产物契约测试。

要点：演示最容易被质疑的就是「是不是演的」。契约里 `mode=demo-local` 必须
无条件写死，且 sha256 以本地计算为准、与预检输出交叉比对——这里固化这两条，
再加一条「符号链接指向真包、不复制 1 GiB」。
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("gate_stage", SCRIPTS / "demo" / "gate_stage.py")
gate_stage = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(gate_stage)


class ParsePreflightTests(unittest.TestCase):
    SAMPLE = """[PACKAGE] /tmp/update.zip
[SIZE]    1039951283 bytes
[SHA256]  6adac43f0838710798f9390b9af8621609af7081e7e446fa693bb9a81aaf7d8e
[DEVICE]  Reserve at least 1120 MiB free under /data before staging
[CHECK]   Validating ZIP integrity...
No errors detected in compressed data of /tmp/update.zip.
[ZIP]     4 members
[VERSION] OpenHarmony 6.1.0.35
[PASS]    Local package preflight passed
[DEMO] preflight_wall_seconds=19.0
"""

    def test_fields_are_extracted(self) -> None:
        fields = gate_stage.parse_preflight(self.SAMPLE)
        self.assertEqual(fields["size"], "1039951283")
        self.assertEqual(fields["version"], "OpenHarmony 6.1.0.35")
        self.assertEqual(fields["members"], "4")
        self.assertTrue(fields["passed"])
        self.assertTrue(fields["verify_ok"])
        self.assertEqual(fields["wall_seconds"], 19.0)

    def test_missing_fields_stay_none(self) -> None:
        fields = gate_stage.parse_preflight("garbage output")
        self.assertIsNone(fields["sha256"])
        self.assertFalse(fields["passed"])


class ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.package = self.root / "update.zip"
        with zipfile.ZipFile(self.package, "w") as archive:
            archive.writestr("version_list", "OpenHarmony 6.1.0.35\n")
        self.out = self.root / "gate"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_gate(self, *extra: str) -> int:
        argv = ["gate_stage.py", "--package", str(self.package), "--out", str(self.out),
                "--mr-iid", "260", "--source-sha", "deadbeef", *extra]
        with mock.patch.object(sys, "argv", argv):
            return gate_stage.main()

    def test_contract_is_labelled_demo_local(self) -> None:
        self.assertEqual(self.run_gate("--skip-preflight"), 0)
        build = json.loads((self.out / "build.json").read_text(encoding="utf-8"))
        self.assertEqual(build["mode"], "demo-local")
        self.assertEqual(build["mr_iid"], "260")
        self.assertEqual(build["source_sha"], "deadbeef")
        self.assertEqual(build["artifact"]["size"], self.package.stat().st_size)
        self.assertEqual(build["artifact"]["members"], ["version_list"])
        self.assertIn("jenkins_url", build["reference"])

    def test_artifact_is_a_symlink_not_a_copy(self) -> None:
        self.run_gate("--skip-preflight")
        link = self.out / "artifacts" / "update.zip"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), self.package.resolve())

    def test_metrics_tsv_lists_checks(self) -> None:
        self.run_gate("--skip-preflight")
        text = (self.out / "mr-metrics.tsv").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("item\tvalue\tstatus"))
        self.assertIn("sha256_local", text)
        self.assertIn("zip_integrity", text)

    def test_missing_package_fails(self) -> None:
        with mock.patch.object(sys, "argv",
                               ["gate_stage.py", "--package", str(self.root / "nope.zip"),
                                "--out", str(self.out)]):
            self.assertEqual(gate_stage.main(), 1)

    def test_non_zip_package_fails(self) -> None:
        fake = self.root / "fake.zip"
        fake.write_bytes(b"not a zip")
        with mock.patch.object(sys, "argv",
                               ["gate_stage.py", "--package", str(fake),
                                "--out", str(self.out), "--skip-preflight"]):
            self.assertEqual(gate_stage.main(), 1)
        build = json.loads((self.out / "build.json").read_text(encoding="utf-8"))
        statuses = {row["item"]: row["status"] for row in build["checks"]}
        self.assertEqual(statuses["zip_integrity"], "FAIL")

    def test_preflight_sha_mismatch_is_a_failure(self) -> None:
        canned = ("[SIZE] 123\n[SHA256] " + "0" * 64 + "\n[ZIP] 1 members\n"
                  "[VERSION] x\n[PASS] ok\nNo errors detected\n")
        with mock.patch.object(gate_stage, "run_preflight", return_value=(0, canned)):
            self.assertEqual(self.run_gate(), 1)
        build = json.loads((self.out / "build.json").read_text(encoding="utf-8"))
        statuses = {row["item"]: row["status"] for row in build["checks"]}
        self.assertEqual(statuses["sha256_preflight_match"], "FAIL")


if __name__ == "__main__":
    unittest.main()
