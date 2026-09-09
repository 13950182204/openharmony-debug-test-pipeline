#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""issue_pipeline.py 纯逻辑单元测试(不触网、不写看板/GitLab)。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import issue_pipeline as ip  # noqa: E402


class TierTests(unittest.TestCase):
    def test_overdue_uses_urgent(self):
        tier = ip.resolve_tier("2000-01-01", ip.DEFAULT_CONFIG)
        self.assertEqual(tier["preset"], "issue-urgent")
        self.assertEqual(tier["model"], "deepseek-v4-pro")

    def test_due_in_2_days_uses_urgent(self):
        from datetime import date, timedelta
        due = (date.today() + timedelta(days=2)).isoformat()
        self.assertEqual(ip.resolve_tier(due, ip.DEFAULT_CONFIG)["name"], "urgent")

    def test_due_in_5_days_uses_normal(self):
        from datetime import date, timedelta
        due = (date.today() + timedelta(days=5)).isoformat()
        self.assertEqual(ip.resolve_tier(due, ip.DEFAULT_CONFIG)["name"], "normal")

    def test_due_in_30_days_uses_low(self):
        from datetime import date, timedelta
        due = (date.today() + timedelta(days=30)).isoformat()
        self.assertEqual(ip.resolve_tier(due, ip.DEFAULT_CONFIG)["name"], "low")

    def test_no_due_uses_low(self):
        self.assertEqual(ip.resolve_tier(None, ip.DEFAULT_CONFIG)["name"], "low")

    def test_invalid_due_uses_low(self):
        self.assertEqual(ip.resolve_tier("not-a-date", ip.DEFAULT_CONFIG)["name"], "low")

    def test_vision_override_forces_vision_model(self):
        urgent = ip.resolve_tier("2000-01-01", ip.DEFAULT_CONFIG)
        tier = ip.apply_vision_override(urgent, True, ip.DEFAULT_CONFIG)
        self.assertEqual(tier["model"], "deepseek-v4-flash-vision-exp")
        self.assertEqual(tier["preset"], "issue-normal")
        self.assertEqual(tier["baseModel"], "deepseek-v4-pro")
        self.assertTrue(tier.get("vision"))

    def test_no_images_keeps_tier(self):
        urgent = ip.resolve_tier("2000-01-01", ip.DEFAULT_CONFIG)
        tier = ip.apply_vision_override(urgent, False, ip.DEFAULT_CONFIG)
        self.assertEqual(tier, urgent)


class ImageDetectionTests(unittest.TestCase):
    def test_markdown_image(self):
        issue = {"description": "现象如下:\n![截图](/uploads/abc/1.png)\n复现步骤..."}
        self.assertTrue(ip.has_images(issue, ip.DEFAULT_CONFIG))

    def test_img_tag(self):
        issue = {"description": '<img src="/uploads/x.jpg"> 收到'}
        self.assertTrue(ip.has_images(issue, ip.DEFAULT_CONFIG))

    def test_upload_link(self):
        issue = {"description": "附件见 http://host/group/proj/uploads/12345/pic.webp"}
        self.assertTrue(ip.has_images(issue, ip.DEFAULT_CONFIG))

    def test_plain_text(self):
        issue = {"description": "rk3568 编译报错,无图片,纯文本描述"}
        self.assertFalse(ip.has_images(issue, ip.DEFAULT_CONFIG))


class MappingTests(unittest.TestCase):
    def test_rk3568_label(self):
        self.assertEqual(ip.resolve_workspace_key(["RK3568"], ip.DEFAULT_CONFIG), "6.1_rk3568")

    def test_a333_label(self):
        self.assertEqual(ip.resolve_workspace_key(["A333/A537"], ip.DEFAULT_CONFIG), "6.1")

    def test_chip_priority(self):
        self.assertEqual(
            ip.resolve_workspace_key(["A333/A537", "RK3568"], ip.DEFAULT_CONFIG), "6.1_rk3568"
        )

    def test_framework_label(self):
        self.assertEqual(ip.resolve_workspace_key(["通用框架层修改"], ip.DEFAULT_CONFIG), "6.1")

    def test_no_platform_label(self):
        self.assertIsNone(ip.resolve_workspace_key(["XTS"], ip.DEFAULT_CONFIG))

    def test_branch_version_label(self):
        self.assertEqual(
            ip.resolve_branch(["RK3568", "V6.1.0.31_Rlease"], "6.1_rk3568", ip.DEFAULT_CONFIG),
            "v6.1.0.31_release",
        )
        self.assertEqual(
            ip.resolve_branch(["V6.1.0.35_LTS"], "6.1", ip.DEFAULT_CONFIG), "V6.1.0.35_LTS"
        )

    def test_branch_default(self):
        self.assertEqual(
            ip.resolve_branch(["RK3568"], "6.1_rk3568", ip.DEFAULT_CONFIG), "V6.1.0.35_LTS"
        )
        self.assertEqual(ip.resolve_branch([], "6.1", ip.DEFAULT_CONFIG), "v6.1.0.31_release")


class MarkerTests(unittest.TestCase):
    def test_parse_iid(self):
        self.assertEqual(ip.parse_iid_from_description("issue-gitlab-iid: 42\n其他"), 42)
        self.assertIsNone(ip.parse_iid_from_description("没有标记"))
        self.assertIsNone(ip.parse_iid_from_description(None))


class RenderTests(unittest.TestCase):
    def test_render_known_unknown_tokens(self):
        out = ip.render_template("iid={iid} mode={mode} keep={unknown}", {"iid": 7, "mode": "x"})
        self.assertEqual(out, "iid=7 mode=x keep={unknown}")

    def test_issue_prompt_has_core_placeholders(self):
        issue = {
            "iid": 9,
            "title": "测试",
            "web_url": "http://x/-/issues/9",
            "labels": ["RK3568"],
            "due_date": "2026-09-10",
            "assignee": {"username": "cx"},
            "description": "d",
        }
        kept = {
            "workspace_path": "/home/cx/os/6.1_rk3568",
            "workspace_key": "6.1_rk3568",
            "branch": "V6.1.0.35_LTS",
            "tier": ip.apply_vision_override(
                ip.resolve_tier("2026-09-10", ip.DEFAULT_CONFIG), False, ip.DEFAULT_CONFIG
            ),
        }
        prompt = ip.build_issue_prompt(9, issue, Path("/tmp/i9.json"), kept, "/s/ip.py", ip.DEFAULT_CONFIG)
        self.assertIn("Issue #9", prompt)
        self.assertIn("/home/cx/os/6.1_rk3568", prompt)
        self.assertIn("openharmony-issue-pipeline", prompt)
        self.assertNotIn("{", prompt.split("请加载")[0])  # 头部无未替换 token


class ConfigMergeTests(unittest.TestCase):
    def test_deep_merge_preserves_defaults(self):
        merged = ip.deep_merge(ip.DEFAULT_CONFIG, {"board": {"sweepCron": "0 8 * * *"}})
        self.assertEqual(merged["board"]["sweepCron"], "0 8 * * *")
        self.assertEqual(merged["gitlab"]["project"], ip.DEFAULT_CONFIG["gitlab"]["project"])


class BoardDescriptionTests(unittest.TestCase):
    def test_contains_marker_and_cap(self):
        desc = ip.build_board_description(
            {"iid": 3, "title": "T", "description": "x" * 30000, "labels": ["RK3568"]},
            {},
            "/p",
            "v6.1.0.31_release",
            {"name": "urgent"},
            ip.DEFAULT_CONFIG,
        )
        self.assertIn("issue-gitlab-iid: 3", desc)
        self.assertIn("截断", desc)
        self.assertLess(len(desc), 22000)


if __name__ == "__main__":
    unittest.main()
