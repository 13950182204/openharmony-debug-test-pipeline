#!/usr/bin/env python3
"""演示模式状态机的形状校验测试。

背景：演示是**现场**跑的，状态机出错没有第二次机会——最贵的错误是「阶段越序」和
「把本地替身说成真跑」。这里固化三条硬规则：
1. 八阶段严格顺序推进，跳阶段必须显式 --force；
2. 每阶段必须带产物来源三态之一（live / local-staged / recorded）；
3. 演示状态只落在 demo 命名空间，绝不碰真实流水线状态文件。
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

SCRIPTS = Path(__file__).resolve().parents[1]
REPO = SCRIPTS.parent
_spec = importlib.util.spec_from_file_location("democtl", SCRIPTS / "demo" / "democtl.py")
democtl = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(democtl)


class DemoStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self._demo_dir = democtl.DEMO_DIR
        self._cases_dir = democtl.CASES_DIR
        democtl.DEMO_DIR = self.root / "demo"
        democtl.CASES_DIR = REPO / "scripts" / "demo" / "cases"
        self.report = self.root / "report"
        (self.report / "result").mkdir(parents=True)
        (self.report / "result" / "ActsX.xml").write_text("<x/>", encoding="utf-8")

    def tearDown(self) -> None:
        democtl.DEMO_DIR = self._demo_dir
        democtl.CASES_DIR = self._cases_dir
        self.temp.cleanup()

    def start(self) -> dict:
        democtl.cmd_start(SimpleNamespace(case="mr260", report=str(self.report), lane="a"))
        return democtl.load_state()

    def advance(self, name: str, **kwargs) -> int:
        args = SimpleNamespace(name=name, status="done", provenance=None, note=None,
                               evidence=None, degraded=False, force=False)
        for key, value in kwargs.items():
            setattr(args, key, value)
        return democtl.cmd_stage(args)

    def test_start_seeds_eight_pending_stages_in_order(self) -> None:
        state = self.start()
        self.assertEqual(list(state["stages"]), list(democtl.STAGES))
        self.assertTrue(all(record["status"] == "pending" for record in state["stages"].values()))
        self.assertEqual(state["stage"], "report")
        self.assertTrue(Path(state["runDir"], "evidence").is_dir())
        self.assertEqual(Path(state["reportDir"]), self.report)

    def test_report_override_is_recorded(self) -> None:
        state = self.start()
        self.assertEqual(Path(state["reportDir"]), self.report)

    def test_stage_out_of_order_is_rejected(self) -> None:
        self.start()
        with self.assertRaises(SystemExit) as caught:
            self.advance("fix")
        self.assertIn("阶段越序", str(caught.exception))

    def test_stage_out_of_order_allowed_with_force(self) -> None:
        self.start()
        self.advance("fix", force=True)
        state = democtl.load_state()
        self.assertEqual(state["stages"]["fix"]["status"], "done")
        # 未完成的 report 仍在等待，不会被“顺手”标成完成
        self.assertEqual(state["stages"]["report"]["status"], "pending")

    def test_provenance_defaults_to_live_and_finishes_stage(self) -> None:
        self.start()
        self.advance("report", note="648/644/4")
        state = democtl.load_state()
        record = state["stages"]["report"]
        self.assertEqual(record["provenance"], "live")
        self.assertIsNotNone(record["startedAt"])
        self.assertIsNotNone(record["finishedAt"])
        self.assertEqual(record["notes"], ["648/644/4"])
        self.assertEqual(state["stage"], "triage")

    def test_provenance_rejects_unknown_value(self) -> None:
        self.start()
        with self.assertRaises(SystemExit) as caught:
            self.advance("report", provenance="fake-live")
        self.assertIn("未知产物来源", str(caught.exception))

    def test_full_walk_reaches_done(self) -> None:
        self.start()
        for stage in democtl.STAGES:
            self.advance(stage)
        state = democtl.load_state()
        self.assertEqual(democtl.next_stage(state), None)
        self.assertTrue(all(record["status"] == "done" for record in state["stages"].values()))

    def test_evidence_must_exist(self) -> None:
        self.start()
        with self.assertRaises(SystemExit):
            self.advance("report", evidence=str(self.root / "nope.json"))

    def test_degraded_latch_is_visible_in_status(self) -> None:
        self.start()
        self.advance("report", degraded=True)
        state = democtl.load_state()
        self.assertTrue(state["degraded"])
        self.assertIn("DEGRADED", democtl.render_status(state))

    def test_status_card_shows_sources_and_stage_rows(self) -> None:
        self.start()
        self.advance("report", provenance="live")
        self.advance("triage", provenance="recorded")
        card = democtl.render_status(democtl.load_state())
        for stage in democtl.STAGES:
            self.assertIn(stage, card)
        self.assertIn("recorded 1", card)

    def test_reset_needs_confirmation(self) -> None:
        self.start()
        self.assertEqual(democtl.cmd_reset(SimpleNamespace(yes=False)), 2)
        self.assertTrue(democtl.state_path().is_file())
        self.assertEqual(democtl.cmd_reset(SimpleNamespace(yes=True)), 0)
        self.assertFalse(democtl.state_path().is_file())

    def test_state_never_touches_real_pipeline_file(self) -> None:
        real = Path.home() / ".dsh" / "pipeline-state.json"
        before = real.stat().st_mtime if real.is_file() else None
        self.start()
        self.advance("report")
        after = real.stat().st_mtime if real.is_file() else None
        self.assertEqual(before, after)
        self.assertTrue(str(democtl.state_path()).startswith(str(self.root)))


class CaseLoadingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._cases_dir = democtl.CASES_DIR

    def tearDown(self) -> None:
        democtl.CASES_DIR = self._cases_dir

    def test_mr260_case_is_complete(self) -> None:
        case = democtl.load_case("mr260")
        for key in ("report", "triage", "fix", "mr", "gate", "ota", "regression", "cues"):
            self.assertIn(key, case)
        self.assertEqual(case["report"]["expect"]["failed"], 4)
        self.assertTrue(case["cues"], "cues 是现场口播的唯一来源，不能为空")
        for stage in democtl.STAGES:
            self.assertIn(stage, case["cues"], f"缺少 {stage} 阶段的口播")

    def test_unknown_case_fails_loudly(self) -> None:
        with self.assertRaises(SystemExit):
            democtl.load_case("no-such-case")


class PreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self._demo_dir = democtl.DEMO_DIR
        self._cases_dir = democtl.CASES_DIR
        democtl.DEMO_DIR = self.root / "demo"
        democtl.CASES_DIR = self.root / "cases"
        democtl.CASES_DIR.mkdir(parents=True)

    def tearDown(self) -> None:
        democtl.DEMO_DIR = self._demo_dir
        democtl.CASES_DIR = self._cases_dir
        self.temp.cleanup()

    def write_case(self, report: Path) -> None:
        payload = json.loads((REPO / "scripts" / "demo" / "cases" / "mr260.json")
                             .read_text(encoding="utf-8"))
        payload["report"]["path"] = str(report)
        (democtl.CASES_DIR / "mr260.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_missing_report_is_a_hard_failure(self) -> None:
        self.write_case(self.root / "gone")
        results, failures = democtl.preflight("mr260")
        levels = {label: level for level, label, _ in results}
        self.assertEqual(levels["报告目录"], democtl.FAIL)
        self.assertGreaterEqual(failures, 1)

    def test_present_report_passes(self) -> None:
        report = self.root / "report"
        (report / "result").mkdir(parents=True)
        (report / "result" / "ActsX.xml").write_text("<x/>", encoding="utf-8")
        self.write_case(report)
        results, _ = democtl.preflight("mr260")
        levels = {label: level for level, label, _ in results}
        self.assertEqual(levels["报告目录"], democtl.PASS)


if __name__ == "__main__":
    unittest.main()
