#!/usr/bin/env python3
"""verify_waiver_layout.py 的规则测试（R1-R8）。

全部用例都在临时目录里造合成豁免资料，不触碰真实豁免目录；每个断言对应一条硬规则：
一 Testsuite 一 txt/一 zip（R2）、三节非空（R3）、evidence 命名与引用（R4）、平台字段（R5）、
records 一一对应（R6）、模块/套件不得混用（R8）、--allow-legacy 只降级命名类问题。
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("verify_waiver_layout", SCRIPTS / "verify_waiver_layout.py")
verify = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(verify)

REASON = "该失败用例由测试包断言与当前产品实现不一致导致，属于测试侧问题；" * 4


def build_txt(module: str, suite: str, cases: list[str], *, with_evidence: bool = True) -> str:
    lines = [
        "1. 失败用例",
        "设备：76AM-HS26A / serial 123456",
        "测试报告：report-1/Acts/ActsAceEtsModuleTest/result.xml",
    ]
    if with_evidence:
        lines.append("- evidence/" + suite + "-report.txt：报告中本套件失败用例原文摘录")
    for case in cases:
        lines.append("- " + module + "#" + suite + "#" + case + "（报告耗时 1.000s）")
    lines += ["", "2. 失败原因", "源码 " + module + "/test.cpp:42 的断言与产品实现不一致，实测返回 0 期望 1。", "", "3. 申请豁免理由", REASON]
    return "\n".join(lines) + "\n"


def add_suite(module_dir: Path, module: str, suite: str, cases: list[str], *, zip_it: bool = True) -> None:
    txt = module_dir / (suite + ".txt")
    txt.write_text(build_txt(module, suite, cases), encoding="utf-8")
    evidence = module_dir / "evidence"
    evidence.mkdir(exist_ok=True)
    (evidence / (suite + "-report.txt")).write_text("失败原文\n", encoding="utf-8")
    if zip_it:
        archive = module_dir / (suite + "-豁免材料.zip")
        with zipfile.ZipFile(archive, "w") as zf:
            zf.write(txt, arcname=txt.name)
            zf.write(evidence / (suite + "-report.txt"), arcname="evidence/" + suite + "-report.txt")


def add_record(module_dir: Path, module: str, suite: str, cases: list[str]) -> dict:
    return {
        "assessmentNumber": "",
        "testCategory": "ACTS",
        "moduleName": module,
        "testsuite": suite,
        "testcases": cases,
        "caseCount": len(cases),
        "waiverReason": REASON,
        "attachmentPath": str(module_dir / (suite + "-豁免材料.zip")),
        "attachmentTxt": str(module_dir / (suite + ".txt")),
    }


class WaiverLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "Acts"
        self.module = "ActsFooTest"
        self.module_dir = self.root / self.module
        self.module_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_main(self, *extra: str) -> tuple[int, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(io.StringIO()):
            code = verify.main(["--exemption-dir", str(self.root), *extra])
        return code, buffer.getvalue()

    def write_records(self, records: list[dict], name: str = "waiver-records-test.json") -> str:
        path = Path(self._tmp.name) / name
        path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
        return str(path)

    # -- 合规基线 ----------------------------------------------------------- #
    def test_compliant_directory_passes_with_expected_summary(self) -> None:
        add_suite(self.module_dir, self.module, "barTest", ["caseOne", "caseTwo"])
        add_suite(self.module_dir, self.module, "bazTest", ["caseAlpha"])
        records = [
            add_record(self.module_dir, self.module, "barTest", ["caseOne", "caseTwo"]),
            add_record(self.module_dir, self.module, "bazTest", ["caseAlpha"]),
        ]
        code, output = self.run_main("--records", self.write_records(records))
        self.assertEqual(code, 0, output)
        self.assertIn("汇总：1 模块 / 2 套件 / 3 用例 / 0 问题", output)

    # -- R3 三节 ------------------------------------------------------------ #
    def test_missing_reason_section_is_an_error(self) -> None:
        add_suite(self.module_dir, self.module, "barTest", ["caseOne"])
        txt = self.module_dir / "barTest.txt"
        txt.write_text(txt.read_text(encoding="utf-8").split("3. 申请豁免理由")[0], encoding="utf-8")
        code, output = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("第 3 节", output)

    def test_empty_evidence_section_is_an_error(self) -> None:
        add_suite(self.module_dir, self.module, "barTest", ["caseOne"])
        txt = self.module_dir / "barTest.txt"
        text = txt.read_text(encoding="utf-8")
        head, _, _ = text.partition("2. 失败原因")
        txt.write_text(head + "2. 失败原因\n\n3. 申请豁免理由\n" + REASON, encoding="utf-8")
        code, output = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("第 2 节", output)

    # -- R8 拆分强制 -------------------------------------------------------- #
    def test_mixed_module_and_suite_is_rejected(self) -> None:
        add_suite(self.module_dir, self.module, "barTest", ["caseOne"])
        txt = self.module_dir / "barTest.txt"
        txt.write_text(
            txt.read_text(encoding="utf-8").replace(
                "- " + self.module + "#barTest#caseOne",
                "- OtherModule#otherSuite#caseOne",
            ),
            encoding="utf-8",
        )
        code, output = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("模块 ≠ 目录名", output)

    def test_case_suite_must_match_file_name(self) -> None:
        add_suite(self.module_dir, self.module, "barTest", ["caseOne"])
        txt = self.module_dir / "barTest.txt"
        txt.write_text(
            txt.read_text(encoding="utf-8").replace(
                "- " + self.module + "#barTest#caseOne",
                "- " + self.module + "#otherSuite#caseOne",
            ),
            encoding="utf-8",
        )
        code, output = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("套件 ≠ 文件名", output)

    # -- R4 佐证 ------------------------------------------------------------ #
    def test_missing_evidence_file_is_an_error(self) -> None:
        add_suite(self.module_dir, self.module, "barTest", ["caseOne"])
        (self.module_dir / "evidence" / "barTest-report.txt").unlink()
        code, output = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("引用的佐证不存在", output)

    def test_evidence_without_suite_prefix_is_legacy_only(self) -> None:
        add_suite(self.module_dir, self.module, "barTest", ["caseOne"])
        evidence = self.module_dir / "evidence"
        (evidence / "barTest-report.txt").rename(evidence / "report.txt")
        txt = self.module_dir / "barTest.txt"
        txt.write_text(txt.read_text(encoding="utf-8").replace("evidence/barTest-report.txt", "evidence/report.txt"), encoding="utf-8")
        archive = self.module_dir / ("barTest-豁免材料.zip")
        with zipfile.ZipFile(archive, "w") as zf:
            zf.write(txt, arcname=txt.name)
            zf.write(evidence / "report.txt", arcname="evidence/report.txt")
        code, _ = self.run_main()
        self.assertEqual(code, 1)
        code, _ = self.run_main("--allow-legacy")
        self.assertEqual(code, 0)

    # -- R2/R4 zip --------------------------------------------------------- #
    def test_missing_zip_is_error_strict_and_warning_in_legacy(self) -> None:
        add_suite(self.module_dir, self.module, "barTest", ["caseOne"])
        add_suite(self.module_dir, self.module, "bazTest", ["caseAlpha"])
        (self.module_dir / "bazTest-豁免材料.zip").unlink()
        code, output = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("缺少成对附件", output)
        code, output = self.run_main("--allow-legacy")
        self.assertEqual(code, 0, output)

    def test_zip_must_contain_its_txt_and_evidence(self) -> None:
        add_suite(self.module_dir, self.module, "barTest", ["caseOne"])
        archive = self.module_dir / "barTest-豁免材料.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("other.txt", "x")
        code, output = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("zip 内缺少本套件 txt", output)
        self.assertIn("evidence/", output)

    # -- R6 records --------------------------------------------------------- #
    def test_records_must_cover_every_suite(self) -> None:
        add_suite(self.module_dir, self.module, "barTest", ["caseOne"])
        code, output = self.run_main("--records", self.write_records([]))
        self.assertEqual(code, 1)
        self.assertIn("缺少平台豁免申请记录", output)

    def test_records_case_count_must_match_txt(self) -> None:
        add_suite(self.module_dir, self.module, "barTest", ["caseOne", "caseTwo"])
        records = [add_record(self.module_dir, self.module, "barTest", ["caseOne"])]
        code, output = self.run_main("--records", self.write_records(records))
        self.assertEqual(code, 1)
        self.assertIn("用例数不一致", output)

    def test_records_attachment_path_must_match_directory(self) -> None:
        add_suite(self.module_dir, self.module, "barTest", ["caseOne"])
        record = add_record(self.module_dir, self.module, "barTest", ["caseOne"])
        record["attachmentPath"] = str(self.module_dir / "barTest-wrong.zip")
        code, output = self.run_main("--records", self.write_records([record]))
        self.assertEqual(code, 1)
        self.assertIn("attachmentPath 与目录不一致", output)

    # -- 退出码 2 ----------------------------------------------------------- #
    def test_missing_directory_returns_usage_error(self) -> None:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = verify.main(["--exemption-dir", str(Path(self._tmp.name) / "nope")])
        self.assertEqual(code, 2)

    def test_broken_records_json_returns_usage_error(self) -> None:
        add_suite(self.module_dir, self.module, "barTest", ["caseOne"])
        path = Path(self._tmp.name) / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = verify.main(["--exemption-dir", str(self.root), "--records", str(path)])
        self.assertEqual(code, 2)

    # -- 路径口径 ----------------------------------------------------------- #
    def test_windows_and_mnt_paths_share_identity(self) -> None:
        self.assertEqual(verify.path_identity(r"D:\ohos\XTS6.1\Acts"), verify.path_identity("/mnt/d/ohos/XTS6.1/Acts"))
        self.assertEqual(verify.path_identity(r"D:\ohos\XTS6.1\Acts"), "d:/ohos/xts6.1/acts")

    def test_windows_style_exemption_dir_is_accepted(self) -> None:
        # 只有能在 /mnt 下建目录的宿主才具备 Windows 路径映射，这里只验证候选展开逻辑本身。
        candidates = [str(item) for item in verify._candidate_paths(r"D:\ohos\Acts")]
        self.assertIn("/mnt/d/ohos/Acts", candidates)
        self.assertIn(str(self.root), [str(item) for item in verify._candidate_paths(str(self.root))])


if __name__ == "__main__":
    unittest.main()
