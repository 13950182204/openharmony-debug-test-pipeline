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
    def test_unknown_or_corrupt_state_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            for content in ('{bad', '{"schemaVersion": 2}'):
                for command in ("get", "reset", "set"):
                    path.write_text(content, encoding="utf-8")
                    args = [sys.executable, str(SCRIPTS / "pipeline_state.py"), command]
                    if command == "set":
                        args.extend(["done", "{}"])
                    result = subprocess.run([*args, "--file", str(path)], capture_output=True)
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(path.read_text(encoding="utf-8"), content)

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


class StageGateTests(unittest.TestCase):
    """阶段推进契约测试（waiver 阶段 / 越级 / done 判据 / 轮次归档）。

    背景（真实故障）：waiver 在闭环 skill 正文里是正式阶段，脚本的 VALID_STAGES 却没有它，
    模型按正文执行必然被拒；同时 done 不校验任何前提，回归跑成 INCONCLUSIVE 也能宣布闭环。
    """

    def state(self, stage=None, **artifacts) -> dict:
        return {**pipeline_state.empty(), "stage": stage, "artifacts": dict(artifacts)}

    # -- waiver 阶段 -------------------------------------------------------- #
    def test_waiver_records_must_be_a_non_empty_list(self) -> None:
        for payload in ({"waiverRecords": []}, {"waiverRecords": "x.txt"}):
            errors, _ = pipeline_state.validate_stage_artifacts("waiver", payload)
            self.assertTrue(errors, payload)
        # 完全没写 waiverRecords 属于「空转」，由阶段闸门拦，而不是形状校验。
        errors = pipeline_state.check_advance(
            self.state("triage"), "waiver", {}, force=False)
        self.assertTrue(any("waiverRecords" in error for error in errors), errors)

    def test_waiver_record_shape(self) -> None:
        errors, warnings = pipeline_state.validate_stage_artifacts(
            "waiver", {"waiverRecords": [{"txt": "a.txt", "records": "r", "cases": 3}]})
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])
        errors, _ = pipeline_state.validate_stage_artifacts(
            "waiver", {"waiverRecords": [{"cases": "3"}]})
        self.assertTrue(any("cases" in error for error in errors))

    # -- 越级 --------------------------------------------------------------- #
    def test_cannot_jump_to_ota_without_report(self) -> None:
        errors = pipeline_state.check_advance(
            self.state(), "ota", {"otaResult": "ok", "otaSerial": "s"}, force=False)
        self.assertTrue(any("report" in error for error in errors), errors)

    def test_cannot_jump_to_ci_without_mr(self) -> None:
        errors = pipeline_state.check_advance(
            self.state("triage", reportDir="/r"), "ci",
            {"jenkinsBuildNumber": 1, "gateBuildUrl": "u", "pipelineId": 2}, force=False)
        self.assertTrue(any("mr" in error for error in errors), errors)

    def test_force_skips_progress_checks_but_not_done_criteria(self) -> None:
        self.assertEqual(
            pipeline_state.check_advance(self.state(), "ota", {"otaResult": "ok", "otaSerial": "s"},
                                         force=True), [])
        errors = pipeline_state.check_advance(self.state("mr", mrIid=168), "done", {}, force=True)
        self.assertTrue(any("回归" in error for error in errors), errors)

    # -- done 判据 ---------------------------------------------------------- #
    def test_done_needs_regression_or_waiver(self) -> None:
        errors = pipeline_state.check_advance(self.state("regression"), "done", {}, force=False)
        self.assertTrue(any("done" in error for error in errors), errors)
        self.assertEqual(
            pipeline_state.check_advance(self.state("waiver", waiverRecords=[{"txt": "a.txt"}]),
                                         "done", {}, force=False), [])

    def test_done_after_mr_requires_passing_regression(self) -> None:
        state = self.state("mr", mrIid=168)
        errors = pipeline_state.check_advance(
            state, "done", {"regressionResult": {"status": "INCONCLUSIVE", "failed": 2, "total": 30}},
            force=False)
        self.assertTrue(any("MR" in error for error in errors), errors)

    def test_regression_conclusion_matrix(self) -> None:
        pass_cases = (
            {"status": "PASS"},
            {"verdict": "passed"},
            {"failed": 0, "total": 44},
            {"status": "INCONCLUSIVE", "smokeOnly": True},
        )
        for value in pass_cases:
            self.assertTrue(pipeline_state.regression_conclusion(value)[0], value)
        fail_cases = (
            None,
            {"status": "INCONCLUSIVE"},
            {"status": "FAIL", "failed": 2, "total": 44},
            {"failed": 0, "total": 0},
        )
        for value in fail_cases:
            self.assertFalse(pipeline_state.regression_conclusion(value)[0], value)

    # -- 轮次归档 ----------------------------------------------------------- #
    def test_new_report_dir_archives_previous_round(self) -> None:
        state = self.state("done", reportDir="/reports/r1", mrIid=168)
        pipeline_state.archive_previous_round(state, "/reports/r2")
        self.assertEqual(list(state["runs"]), ["r1"])
        self.assertEqual(state["runs"]["r1"]["artifacts"]["mrIid"], 168)
        self.assertIsNone(state["stage"])
        self.assertEqual(state["artifacts"], {})
        self.assertEqual(state["events"], [])

    def test_same_report_dir_keeps_current_round(self) -> None:
        state = self.state("triage", reportDir="/reports/r1")
        pipeline_state.archive_previous_round(state, "/reports/r1")
        self.assertNotIn("runs", state)
        self.assertEqual(state["stage"], "triage")

    def test_archive_keeps_only_recent_rounds(self) -> None:
        state = self.state("done")
        for index in range(7):
            state["artifacts"] = {"reportDir": f"/reports/r{index}"}
            pipeline_state.archive_previous_round(state, f"/reports/r{index + 1}")
        self.assertEqual(len(state["runs"]), 5)

    # -- CLI 契约 ----------------------------------------------------------- #
    def test_describe_exposes_the_contract(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "pipeline_state.py"), "describe"],
            text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        contract = json.loads(result.stdout)
        self.assertIn("waiver", contract["order"])
        self.assertEqual(contract["order"][0], "report")
        self.assertIn("waiverRecords", contract["required"]["waiver"])

    def test_cli_accepts_waiver_stage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "pipeline_state.py"), "set", "waiver",
                 json.dumps({"waiverRecords": [{"txt": "a.txt", "cases": 1}]}),
                 "--file", str(path)], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["stage"], "waiver")


if __name__ == "__main__":
    unittest.main()
