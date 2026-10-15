#!/usr/bin/env python3
"""Parse xdevice test reports (summary_report.xml / result/*.xml).

OTA 成功后"重新跑测试用例"这一步的判定依据就在这里：xdevice 每次运行都会在报告目录写
`summary_report.xml`（根 `<testsuites>` 带 failures/errors，下挂 `<testsuite>` 与 `<testcase>`），
`result/*.xml` 是分模块明细。本模块只做**只读解析与判定**，不执行任何设备动作。

判定规则（用户口径，2026-10-15）：
- 请求的测试项里**全部用例通过**才算 PASS；
- 出现任何失败用例 → FAIL；
- 一个用例都没跑成（报告缺失/无记录/被跳过） → INCONCLUSIVE，绝不谎报通过。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

# XML 与 xdevice 都把结果表达成字符串；这里只认明确的失败信号，其余按"未通过"处理。
FAILURE_VALUES = frozenset(("false", "fail", "failed", "failure", "error", "1"))
PASS_VALUES = frozenset(("true", "pass", "passed", "success", "0"))
SKIP_STATUS = frozenset(("ignored", "disabled", "skip", "skipped"))


class ReportError(RuntimeError):
    """The report directory is missing or not a usable xdevice report."""


def _failure_reason(case: ElementTree.Element) -> str:
    for child in case:
        tag = child.tag.rsplit("}", 1)[-1].lower()
        if tag in ("failure", "error") and (child.text or child.get("message")):
            return (child.get("message") or child.text or "").strip()[:300]
    return ""


def _case_state(case: ElementTree.Element) -> tuple[str, str]:
    """Return ("pass"|"fail"|"skip", reason). 未知取值一律算 fail（不放过可疑结果）。"""
    status = str(case.get("status") or "").strip().lower()
    result = str(case.get("result") or "").strip().lower()
    if status in SKIP_STATUS or result in SKIP_STATUS:
        return "skip", ""
    if result in PASS_VALUES:
        return "pass", ""
    if result in FAILURE_VALUES or status in ("fail", "failed", "error") or result == "":
        return "fail", _failure_reason(case)
    return "fail", _failure_reason(case)


def parse_summary(summary_path: Path) -> dict[str, Any]:
    """Parse one testsuites XML file into suite/case records."""
    try:
        root = ElementTree.parse(summary_path).getroot()
    except (OSError, ElementTree.ParseError) as error:
        raise ReportError(f"Unable to read xdevice summary {summary_path}: {error}") from error
    suites: dict[str, dict[str, Any]] = {}
    for suite in root.iter():
        if suite.tag.rsplit("}", 1)[-1] != "testsuite":
            continue
        name = str(suite.get("name") or "").strip()
        if not name:
            continue
        module = str(suite.get("modulename") or "").strip()
        record = suites.setdefault(name, {"name": name, "module": module, "total": 0, "passed": 0,
                                          "failed": 0, "skipped": 0, "failed_cases": [],
                                          "time": suite.get("time"),
                                          "unavailable": False, "message": ""})
        if module and not record.get("module"):
            record["module"] = module
        # xdevice 在"模块没能跑起来"时也会 exit 0：<testsuite unavailable='1' message='...'>，
        # 实测取值就是 `[Environment-0101021] Test source required 1 devices, actually 0 devices
        # were found`。这种必须算"没跑成"，绝不能因为 failures=0 就判通过。
        if str(suite.get("unavailable") or "").strip().lower() in ("1", "true", "yes"):
            record["unavailable"] = True
        message = str(suite.get("message") or "").strip()
        if message:
            record["message"] = message[:300]
        for case in suite:
            if case.tag.rsplit("}", 1)[-1] != "testcase":
                continue
            state, reason = _case_state(case)
            record["total"] += 1
            record[{"pass": "passed", "fail": "failed", "skip": "skipped"}[state]] += 1
            if state == "fail":
                record["failed_cases"].append({
                    "class": str(case.get("classname") or name),
                    "name": str(case.get("name") or ""),
                    "reason": reason,
                })
    suites_list = sorted(suites.values(), key=lambda item: item["name"])
    unavailable = [item["name"] for item in suites_list if item.get("unavailable")]
    return {
        "suites": suites_list,
        "unavailable": unavailable,
        "total": sum(item["total"] for item in suites_list),
        "passed": sum(item["passed"] for item in suites_list),
        "failed": sum(item["failed"] for item in suites_list),
        "skipped": sum(item["skipped"] for item in suites_list),
    }


def parse_result_file(path: Path) -> dict[str, Any] | None:
    """Parse one `result/<module>.xml` (root `testsuites name=<module>`, cases grouped by class).

    实测：`summary_report.xml` 里 `<testsuite name>` 是**测试类名**（如 `AACommandPrintTest`），
    模块名在 `modulename` 属性或 `result/<模块>.xml` 的文件名里。按模块核对请用 result 文件。
    """
    try:
        root = ElementTree.parse(path).getroot()
    except (OSError, ElementTree.ParseError):
        return None
    tag = root.tag.rsplit("}", 1)[-1]
    if tag == "testsuite":
        module = str(root.get("name") or path.stem)
        roots = [root]
    else:
        module = str(root.get("name") or path.stem)
        roots = [child for child in root if child.tag.rsplit("}", 1)[-1] == "testsuite"]
    failed_cases: list[dict[str, Any]] = []
    total = passed = failed = skipped = 0
    unavailable = str(root.get("unavailable") or "").strip().lower() in ("1", "true", "yes")
    message = str(root.get("message") or "").strip()[:300]
    for suite in roots:
        for case in suite:
            if case.tag.rsplit("}", 1)[-1] != "testcase":
                continue
            state, reason = _case_state(case)
            total += 1
            if state == "pass":
                passed += 1
            elif state == "skip":
                skipped += 1
            else:
                failed += 1
                failed_cases.append({"class": str(case.get("classname") or suite.get("name") or ""),
                                     "name": str(case.get("name") or ""), "reason": reason})
    return {"name": module, "module": module, "total": total, "passed": passed, "failed": failed,
            "skipped": skipped, "failed_cases": failed_cases, "unavailable": unavailable,
            "message": message, "result_file": path.name}


def find_result_files(report_dir: Path) -> list[Path]:
    result_dir = report_dir / "result"
    if not result_dir.is_dir():
        return []
    return [path for path in sorted(result_dir.glob("*.xml"))
            if path.is_file() and path.stat().st_size]


def find_reports(report_dir: Path) -> list[Path]:
    """All candidate xdevice XML reports under one report directory (recursive)."""
    if not report_dir.is_dir():
        raise ReportError(f"xdevice report directory does not exist: {report_dir}")
    candidates = [path for path in sorted(report_dir.rglob("*.xml"))
                  if path.is_file() and path.stat().st_size]
    if not candidates:
        raise ReportError(f"xdevice report directory has no XML: {report_dir}")
    return candidates


def summarize(report_dir: Path) -> dict[str, Any]:
    """Merge one xdevice report directory into module-level records.

    数据源优先级：`result/<模块>.xml`（按模块权威）> `summary_report.xml`（按 modulename 归并）。
    后者在"模块没跑起来"时是唯一线索（那里 suite 名就是模块名且带 unavailable='1'）。
    """
    merged: dict[str, dict[str, Any]] = {}
    result_files = find_result_files(report_dir)
    files: list[Path]

    def merge(record_key: str, record: dict[str, Any]) -> None:
        target = merged.setdefault(record_key, {
            "name": record["name"], "module": record.get("module") or record["name"],
            "total": 0, "passed": 0, "failed": 0, "skipped": 0, "failed_cases": [],
            "time": None, "unavailable": False, "message": "",
        })
        if record.get("unavailable"):
            target["unavailable"] = True
        if record.get("message") and not target["message"]:
            target["message"] = record["message"]
        target["total"] += record.get("total", 0)
        target["passed"] += record.get("passed", 0)
        target["failed"] += record.get("failed", 0)
        target["skipped"] += record.get("skipped", 0)
        for case in record.get("failed_cases", []):
            key = (case["class"], case["name"])
            if all((item["class"], item["name"]) != key for item in target["failed_cases"]):
                target["failed_cases"].append(case)

    if result_files:
        files = [*result_files, *(p for p in find_reports(report_dir)
                                 if p.name == "summary_report.xml")]
        for path in result_files:
            parsed = parse_result_file(path)
            if parsed:
                merge(parsed["name"], parsed)
    else:
        files = find_reports(report_dir)
    for path in files:
        if path.name == "summary_report.xml" or not result_files:
            try:
                parsed = parse_summary(path)
            except ReportError:
                continue
            for suite in parsed["suites"]:
                key = suite.get("module") or suite["name"]
                suite = dict(suite, name=key)
                merge(key, suite)
    suites = sorted(merged.values(), key=lambda item: item["name"])
    return {
        "report_dir": str(report_dir),
        "unavailable": [item["name"] for item in suites if item.get("unavailable")],
        "report_files": [path.name for path in files][:20],
        "suites": suites,
        "total": sum(item["total"] for item in suites),
        "passed": sum(item["passed"] for item in suites),
        "failed": sum(item["failed"] for item in suites),
        "skipped": sum(item["skipped"] for item in suites),
    }


def requested_modules(names: list[str]) -> list[str]:
    return [str(name).strip() for name in names if str(name).strip()]


def match_requested(names: list[str], suites: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """Split requested module names into (matched, missing) against report suite names.

    匹配规则：报告里的 suite 名等于请求名，或请求名是 suite 名前缀（xdevice 会把
    `ActsXxxTest` 报成 `ActsXxxTest`，某些套件带子后缀）。大小写不敏感。
    """
    available = {str(item["name"]).lower(): str(item["name"]) for item in suites}
    matched, missing = [], []
    for name in requested_modules(names):
        lowered = name.lower()
        if lowered in available:
            matched.append(available[lowered])
            continue
        partial = [original for key, original in available.items() if key.startswith(lowered)]
        if partial:
            matched.extend(sorted(partial))
        else:
            missing.append(name)
    return matched, missing


def verdict(report_dir: Path, modules: list[str]) -> dict[str, Any]:
    """PASS only when every requested case passed; FAIL on any failure; else INCONCLUSIVE."""
    summary = summarize(report_dir)
    matched, missing = match_requested(modules, summary["suites"])
    relevant = [item for item in summary["suites"] if item["name"] in matched]
    total = sum(item["total"] for item in relevant)
    passed = sum(item["passed"] for item in relevant)
    failed = sum(item["failed"] for item in relevant)
    failed_cases = [case for item in relevant for case in item["failed_cases"]]
    unavailable = [item["name"] for item in relevant if item.get("unavailable")]
    if missing and not matched:
        status = "INCONCLUSIVE"
    elif unavailable:
        # 模块没跑起来（设备条件不满足等）：是环境/前置问题，不是用例结论。
        status = "INCONCLUSIVE"
    elif total == 0:
        status = "INCONCLUSIVE"
    elif failed:
        status = "FAIL"
    elif missing:
        # 部分请求模块没有任何结果记录：不能算通过（没跑到 ≠ 通过）。
        status = "INCONCLUSIVE"
    else:
        status = "PASS"
    return {
        "status": status,
        "requested": requested_modules(modules),
        "matched": matched,
        "missing": missing,
        "total": total,
        "passed": passed,
        "failed": failed,
        "skipped": sum(item["skipped"] for item in relevant),
        "unavailable": unavailable,
        "unavailable_messages": [item.get("message", "") for item in relevant
                                 if item.get("unavailable") and item.get("message")][:5],
        "failed_cases": failed_cases[:50],
        "suites": relevant,
        "report_dir": str(report_dir),
        "report_files": summary["report_files"],
    }


def module_name_from_case(case: str) -> str:
    """Derive a module/test-item name from a triage case id.

    xdevice 的 `-l` 收模块名（HAP 描述符名，如 `ActsUdmfTest`），而报告里的失败项常写成
    `<模块>#<用例>` 或 `<模块>.<用例>`。这里只取第一段作为模块名；取不出来时返回空串，
    由调用方回退到 triage 阶段登记的显式模块清单。
    """
    text = str(case).strip()
    if not text:
        return ""
    for separator in ("#", ".", "::", "/"):
        if separator in text:
            head = text.split(separator, 1)[0].strip()
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{2,63}", head):
                return head
    return text if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{2,63}", text) else ""
