#!/usr/bin/env python3
"""Summarize OpenHarmony xdevice-style test reports."""

from __future__ import annotations

import argparse
import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CaseResult:
    module: str
    classname: str
    name: str
    result: str
    message: str
    time: str

    @property
    def is_timeout(self) -> bool:
        return "timeout" in self.message.lower()

    @property
    def is_blocked_fallout(self) -> bool:
        return "ShellCommandUnresponsiveException" in self.message


@dataclass
class ModuleSummary:
    name: str
    total: int = 0
    failed: list[CaseResult] = field(default_factory=list)
    blocked: int = 0
    passed: int = 0
    module_log: Path | None = None


def find_files(root: Path, name: str) -> list[Path]:
    return sorted(path for path in root.rglob(name) if path.is_file())


def parse_xml_result(xml_path: Path) -> ModuleSummary | None:
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as exc:
        print(f"warn: failed to parse XML {xml_path}: {exc}", file=sys.stderr)
        return None

    root = tree.getroot()
    module_name = xml_path.stem
    summary = ModuleSummary(name=module_name)

    for testcase in root.iter("testcase"):
        summary.total += 1
        status = testcase.attrib.get("status", "")
        result = testcase.attrib.get("result", "")
        case = CaseResult(
            module=module_name,
            classname=testcase.attrib.get("classname", ""),
            name=testcase.attrib.get("name", ""),
            result=result,
            message=testcase.attrib.get("message", ""),
            time=testcase.attrib.get("time", ""),
        )
        if status == "blocked" or case.is_blocked_fallout:
            summary.blocked += 1
        elif result == "false":
            summary.failed.append(case)
        else:
            summary.passed += 1
    return summary


def collect_module_logs(report_dir: Path) -> dict[str, Path]:
    logs: dict[str, Path] = {}
    for path in find_files(report_dir, "module_run.log"):
        module = path.parent.name
        logs[module] = path
    return logs


def load_task_summary(report_dir: Path) -> str:
    task_logs = find_files(report_dir, "task_log.log")
    if not task_logs:
        return ""
    text = task_logs[0].read_text(errors="replace")
    lines = []
    for line in text.splitlines():
        if re.search(r"\b(total|passed|failed|blocked|modules)\b", line, re.IGNORECASE):
            lines.append(line.strip())
    return "\n".join(lines[-12:])


def format_table(modules: list[ModuleSummary]) -> str:
    header = f"{'module':40} {'total':>6} {'failed':>6} {'blocked':>7} {'timeout':>7} first_failed"
    rows = [header, "-" * len(header)]
    for module in modules:
        timeout_count = sum(1 for case in module.failed if case.is_timeout)
        first = module.failed[0].name if module.failed else ""
        rows.append(
            f"{module.name[:40]:40} {module.total:6d} {len(module.failed):6d} "
            f"{module.blocked:7d} {timeout_count:7d} {first}"
        )
    return "\n".join(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize OpenHarmony test report failures.")
    parser.add_argument("report_dir", type=Path, help="Report directory to inspect")
    parser.add_argument("--all", action="store_true", help="Show modules without failures too")
    args = parser.parse_args()

    report_dir = args.report_dir.resolve()
    if not report_dir.is_dir():
        print(f"error: not a directory: {report_dir}", file=sys.stderr)
        return 2

    xml_files = sorted((report_dir / "result").glob("*.xml"))
    if not xml_files:
        xml_files = sorted(report_dir.rglob("*.xml"))

    module_logs = collect_module_logs(report_dir)
    modules: list[ModuleSummary] = []
    for xml_path in xml_files:
        summary = parse_xml_result(xml_path)
        if summary is None:
            continue
        summary.module_log = module_logs.get(summary.name)
        if args.all or summary.failed or summary.blocked:
            modules.append(summary)

    task_summary = load_task_summary(report_dir)
    if task_summary:
        print("task summary:")
        print(task_summary)
        print()

    if not modules:
        print("No failed or blocked modules found.")
        return 0

    print(format_table(modules))
    print()
    for module in modules:
        if not module.failed:
            continue
        print(f"[{module.name}]")
        for case in module.failed:
            timeout = " timeout" if case.is_timeout else ""
            print(f"- {case.classname}#{case.name}{timeout} time={case.time} message={case.message}")
        if module.module_log:
            print(f"  module_log: {module.module_log}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
