import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "create_glab_mr.py"
SPEC = importlib.util.spec_from_file_location("create_glab_mr", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


MILESTONES = [
    {"id": 3, "iid": 2, "title": "开源鸿蒙V6.1 DNAKE V1.1.4 release版本"},
    {"id": 4, "iid": 3, "title": "开源鸿蒙V6.1 DNAKE V1.2.0 release版本"},
    {"id": 5, "iid": 4, "title": "开源鸿蒙V6.1 DNAKE V1.1.0 release版本"},
    {"id": 6, "iid": 5, "title": "开源鸿蒙V6.1 DNAKE V1.3.0 release版本"},
    {"id": 7, "iid": 6, "title": "开源鸿蒙V6.1 DNAKE V1.4.0 release版本"},
]


class TitleTests(unittest.TestCase):
    def test_parses_chip_and_xts_fields(self):
        parsed = MODULE.parse_title("[修改] [A333/A537] [XTS] 修复 Display HATS 失败问题")
        self.assertEqual("修改", parsed["action"])
        self.assertEqual("A333/A537", parsed["chip"])
        self.assertTrue(parsed["xts"])
        self.assertEqual("修复 Display HATS 失败问题", parsed["summary"])

    def test_rejects_legacy_compact_xts_title(self):
        with self.assertRaisesRegex(MODULE.MrError, "spaces"):
            MODULE.parse_title("[修改][XTS] 修复 PlayerVideo 问题")

    def test_rejects_second_chip_field(self):
        with self.assertRaisesRegex(MODULE.MrError, "only one chip"):
            MODULE.parse_title("[修改] [RK3568] [A333/A537] 修改音频问题")

    def test_requires_xts_field_for_xts_paths(self):
        with self.assertRaisesRegex(MODULE.MrError, "include an \[XTS\]"):
            MODULE.validate_message("[修改] 修复音频问题", "具体:\n修复测试失败。", ["test/xts/demo.cpp"])

    def test_branch_suffix_removes_structured_fields(self):
        suffix = MODULE.normalize_branch_suffix("[优化] [RK3568] 优化 开机时长问题")
        self.assertEqual("开机时长", suffix)


class LabelTests(unittest.TestCase):
    def test_common_xts_title_derives_common_and_xts_labels(self):
        parsed = MODULE.parse_title("[修改] [XTS] 修复 PlayerVideo 问题")
        self.assertEqual({"XTS", "通用框架层修改"}, set(MODULE.resolve_labels(parsed, [])))

    def test_common_title_allows_explicit_cross_chip_labels(self):
        parsed = MODULE.parse_title("[修改] 兼容 ALSA 适配")
        labels = MODULE.resolve_labels(parsed, ["A333/A537,RK3568", "应用修复"])
        self.assertEqual({"A333/A537", "RK3568", "通用框架层修改", "应用修复"}, set(labels))

    def test_rejects_conflicting_chip_label(self):
        parsed = MODULE.parse_title("[优化] [RK3568] 优化启动时序")
        with self.assertRaisesRegex(MODULE.MrError, "conflict"):
            MODULE.resolve_labels(parsed, ["A333/A537"])


class MilestoneTests(unittest.TestCase):
    def test_matches_unique_minor_branch_version(self):
        milestone = MODULE.match_milestone_text("v1.2.x/v6.1.0.31_feature", MILESTONES)
        self.assertEqual("开源鸿蒙V6.1 DNAKE V1.2.0 release版本", milestone["title"])

    def test_exact_patch_beats_minor_family(self):
        milestone = MODULE.match_milestone_text("v1.1.4/v6.1.0.31_feature", MILESTONES)
        self.assertEqual("开源鸿蒙V6.1 DNAKE V1.1.4 release版本", milestone["title"])

    def test_ambiguous_minor_returns_none(self):
        self.assertIsNone(MODULE.match_milestone_text("v1.1.x/v6.1.0.31_feature", MILESTONES))

    def test_base_version_is_not_treated_as_release_milestone(self):
        self.assertIsNone(MODULE.match_milestone_text("v6.1.0.31_xts_audio", MILESTONES))

    def test_create_command_includes_metadata(self):
        milestone = MILESTONES[1]
        command = MODULE.mr_create_command(
            "v1.2.x/v6.1.0.31_audio",
            "v6.1.0.31_release",
            "[修改] [RK3568] 修改音频问题",
            "具体:\n修复音频问题。",
            "cx",
            ["RK3568", "应用修复"],
            milestone,
        )
        self.assertIn("--label", command)
        self.assertIn("RK3568,应用修复", command)
        self.assertIn("--milestone", command)
        self.assertIn(milestone["title"], command)


if __name__ == "__main__":
    unittest.main()
