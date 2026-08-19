#!/usr/bin/env python3
"""Extract useful hilog snippets for a module/case from an OpenHarmony report."""

from __future__ import annotations

import argparse
import gzip
import sys
from collections import deque
from pathlib import Path


def open_text(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", errors="replace")
    return path.open("rt", errors="replace")


def find_module_dir(report_dir: Path, module: str) -> Path | None:
    direct = report_dir / "log" / module
    if direct.is_dir():
        return direct
    matches = [p for p in report_dir.rglob(module) if p.is_dir()]
    return matches[0] if matches else None


def iter_hilog_files(module_dir: Path):
    patterns = ["hilog*.gz", "hilog*", "*.log"]
    seen: set[Path] = set()
    for pattern in patterns:
        for path in sorted(module_dir.rglob(pattern)):
            if path.is_file() and path not in seen and "module_run.log" not in path.name:
                seen.add(path)
                yield path


def emit_window(path: Path, case: str, keywords: list[str], before: int, after: int) -> bool:
    found = False
    pending_after = 0
    context = deque(maxlen=before)
    with open_text(path) as fh:
        for lineno, line in enumerate(fh, 1):
            if '"itName"' in line:
                continue
            hit = case in line or any(keyword in line for keyword in keywords)
            if hit:
                if not found:
                    print(f"==> {path}")
                found = True
                if context:
                    for old_lineno, old_line in context:
                        print(f"{old_lineno}: {old_line.rstrip()}")
                    context.clear()
                print(f"{lineno}: {line.rstrip()}")
                pending_after = after
                continue
            if pending_after > 0:
                print(f"{lineno}: {line.rstrip()}")
                pending_after -= 1
                continue
            context.append((lineno, line))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract hilog snippets for a failed case.")
    parser.add_argument("report_dir", type=Path, help="Report directory")
    parser.add_argument("module", help="Module name, for example ActsVideoPlayerJsTest")
    parser.add_argument("case", help="Testcase name or distinctive search text")
    parser.add_argument("--before", type=int, default=4, help="Context lines before a hit")
    parser.add_argument("--after", type=int, default=12, help="Context lines after a hit")
    parser.add_argument(
        "--keyword",
        action="append",
        default=[],
        help="Extra keyword to search in addition to the case name",
    )
    args = parser.parse_args()

    report_dir = args.report_dir.resolve()
    if not report_dir.is_dir():
        print(f"error: not a directory: {report_dir}", file=sys.stderr)
        return 2

    module_dir = find_module_dir(report_dir, args.module)
    if module_dir is None:
        print(f"error: module directory not found for {args.module}", file=sys.stderr)
        return 1

    keywords = args.keyword
    any_hit = False
    for hilog in iter_hilog_files(module_dir):
        if emit_window(hilog, args.case, keywords, args.before, args.after):
            any_hit = True

    if not any_hit:
        print("No hilog hits found.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
