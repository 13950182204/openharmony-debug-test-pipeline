#!/usr/bin/env python3
"""pipeline_state.py 的产物形状校验测试。

背景（真实故障）：这个文件是「分诊 → 回归」之间的交接物，但 `set` 原先接受任意 JSON，
于是出现过 `failedCases: 3`（数量而非清单）这种**静默**不一致——两端各自演化，
直到运行期才暴露。这里固化校验规则，并确保写入方能产出消费者真正读得懂的形状。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
REPO = SCRIPTS.parent
_spec = importlib.util.spec_from_file_location("pipeline_state", SCRIPTS / "pipeline_state.py")
pipeline_state = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(pipeline_state)


class ValidateTriageArtifactsTests(unittest.TestCase):
    def validate(self, payload: dict) -> tuple[list[str], list[str]]:
        return pipeline_state.validate_stage_artifacts("triage", payload)

    # -- 阻塞性错误 --------------------------------------------------------- #
    def test_failed_cases_must_be_a_list_not_a_count(self) -> None:
        """真实历史形状：failedCases 写成了数量 3。"""
        errors, _ = self.validate({"failedCases": 3})
        self.assertTrue(errors)
        self.assertIn("必须是清单", errors[0])
        self.assertIn("modules.<模块>.failed", errors[0])

    def test_failed_cases_entries_need_module_and_case(self) -> None:
        errors, _ = self.validate({"failedCases": ["SUB_DMS_..._0500"]})
        self.assertTrue(errors)
        self.assertIn("<模块名>#<用例名>", errors[0])

    def test_failed_modules_must_be_a_list_of_valid_names(self) -> None:
        errors, _ = self.validate({"failedModules": "DctsDmsFaTest"})
        self.assertTrue(errors)
        errors, _ = self.validate({"failedModules": ["Dcts Dms FaTest"]})
        self.assertTrue(any("非法模块名" in item for item in errors))

    def test_modules_shape_is_validated(self) -> None:
        errors, _ = self.validate({"modules": ["DctsDmsFaTest"]})
        self.assertTrue(errors)
        errors, _ = self.validate({"modules": {"DctsDmsFaTest": {"failed": "1"}}})
        self.assertTrue(any("failed 必须是整数" in item for item in errors))
        errors, _ = self.validate({"modules": {"DctsDmsFaTest": {"cases": "x"}}})
        self.assertTrue(any("cases 必须是清单" in item for item in errors))

    # -- 警告（不阻塞）----------------------------------------------------- #
    def test_lowercase_camel_module_name_warns_as_likely_test_class(self) -> None:
        errors, warnings = self.validate({"failedModules": ["dmsJsUnitTest"]})
        self.assertEqual(errors, [])
        self.assertTrue(any("测试类名" in item for item in warnings))

    def test_missing_failed_modules_warns_about_the_regression_handoff(self) -> None:
        errors, warnings = self.validate({"verdict": "TEST-ENVIRONMENT"})
        self.assertEqual(errors, [])
        self.assertTrue(any("没有可重跑的测试项" in item for item in warnings))

    def test_well_formed_payload_has_no_errors_and_no_warnings(self) -> None:
        errors, warnings = self.validate({
            "failedModules": ["DctsDmsFaTest"],
            "failedCases": ["DctsDmsFaTest#SUB_DMS_StandardOs_..._0500"],
            "modules": {"DctsDmsFaTest": {"tests": 45, "failed": 1, "cases": ["SUB_DMS_..._0500"]}},
        })
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_other_stages_are_not_constrained(self) -> None:
        errors, warnings = pipeline_state.validate_stage_artifacts("mr", {"mrIid": 226})
        self.assertEqual((errors, warnings), ([], []))

    # -- 交接端到端 --------------------------------------------------------- #
    def test_producer_shape_is_consumable_by_the_regression_reader(self) -> None:
        """本仓库最重要的保证：写入方产出的形状，回归阶段的读取方能完整消费。"""
        sys.path.insert(0, str(REPO / "skills/openharmony-ci-orchestrator/scripts"))
        import phase3_runner  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            payload = {
                "failedModules": ["DctsDmsFaTest", "DctsDmsFwkStagePermissionTest"],
                "failedCases": ["DctsDmsFaTest#SUB_DMS_StandardOs_..._0500"],
                "modules": {
                    "DctsDmsFaTest": {"tests": 45, "failed": 1,
                                      "cases": ["SUB_DMS_StandardOs_..._0500"]},
                },
            }
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "pipeline_state.py"), "set", "triage",
                 json.dumps(payload), "--file", str(path)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            state = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(phase3_runner.modules_to_rerun({}, state),
                             ["DctsDmsFaTest", "DctsDmsFwkStagePermissionTest"])

    def test_cli_rejects_a_bad_shape_and_leaves_no_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "pipeline_state.py"), "set", "triage",
                 json.dumps({"failedCases": 3}), "--file", str(path)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("必须是清单", result.stderr)
            self.assertFalse(path.exists(), "形状不合约定时不应写入状态文件")


if __name__ == "__main__":
    unittest.main()
