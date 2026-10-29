"""`scripts/demo/regression_fast.py` 的纯逻辑单测（不碰设备、不起 xdevice）。"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "demo" / "regression_fast.py"


def load_module():
    spec = importlib.util.spec_from_file_location("regression_fast_under_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ConfigForSerialTest(unittest.TestCase):
    """共享配置的 `<sn>` 是固定板子，必须按本次设备覆盖。"""

    def setUp(self) -> None:
        self.module = load_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _config(self, sn: str) -> Path:
        path = self.root / "user_config.xml"
        path.write_text(
            f'<?xml version="1.0"?>\n<user_config><device type="usb-hdc">'
            f"<sn>{sn}</sn><conn_type>usb</conn_type></device></user_config>\n",
            encoding="utf-8")
        return path

    def test_copies_and_replaces_sn(self) -> None:
        config = self._config("ea010e325333324247102b4f80429c69")
        patched, note = self.module.config_for_serial(
            config, "150100424a5444345206517525298929", self.root / "work")

        self.assertNotEqual(patched, config)
        self.assertTrue(patched.is_file())
        self.assertIn("<sn>150100424a5444345206517525298929</sn>", patched.read_text(encoding="utf-8"))
        self.assertIsNotNone(note)
        self.assertIn("150100424a5444345206517525298929", note or "")
        # 共享配置保持原样：真实链路不受演示影响。
        self.assertIn("ea010e325333324247102b4f80429c69", config.read_text(encoding="utf-8"))

    def test_same_sn_needs_no_patch(self) -> None:
        serial = "150100424a5444345206517525298929"
        config = self._config(serial)
        patched, note = self.module.config_for_serial(config, serial, self.root / "work")

        self.assertEqual(patched, config)
        self.assertIsNone(note)

    def test_missing_sn_falls_back(self) -> None:
        config = self.root / "plain.xml"
        config.write_text("<user_config></user_config>\n", encoding="utf-8")
        patched, note = self.module.config_for_serial(
            config, "150100424a5444345206517525298929", self.root / "work")

        self.assertEqual(patched, config)
        self.assertIn("无 <sn>", note or "")


class RelocateSessionRootTest(unittest.TestCase):
    """Windows 侧执行时报告目录不能落在 WSL 私有路径（会变成 UNC，枚举不到设备）。"""

    def setUp(self) -> None:
        self.module = load_module()

    def test_windows_runner_relocates_wsl_private_root(self) -> None:
        profile = {
            "runner_host": "windows",
            "runner_dir": "/mnt/d/ohos/XTS6.1/office/acts",
            "session_root": "/home/cx/.dsh/demo/runs/x/evidence/regression",
        }
        note = self.module.relocate_session_root(profile)

        self.assertEqual(profile["session_root"],
                         "/mnt/d/ohos/XTS6.1/office/acts/reports/demo")
        self.assertIn("UNC", note or "")

    def test_windows_runner_keeps_drive_path(self) -> None:
        profile = {
            "runner_host": "windows",
            "runner_dir": "/mnt/d/ohos/XTS6.1/office/acts",
            "session_root": "/mnt/d/ohos/XTS6.1/office/acts/reports/demo",
        }
        self.assertIsNone(self.module.relocate_session_root(profile))
        self.assertEqual(profile["session_root"], "/mnt/d/ohos/XTS6.1/office/acts/reports/demo")

    def test_native_runner_is_not_constrained(self) -> None:
        profile = {"runner_host": "wsl", "session_root": "/tmp/whatever"}
        self.assertIsNone(self.module.relocate_session_root(profile))
        self.assertEqual(profile["session_root"], "/tmp/whatever")


if __name__ == "__main__":
    unittest.main()
