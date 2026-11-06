#!/usr/bin/env python3
"""豁免资料目录与提交模型校验器（只读，标准库实现）。

规则来源：docs/操作手册.md 第六节「豁免资料规范（waiver）」（R1-R8）。
一个模块的一个 Testsuite 的提交物应为：

    <exemption-dir>/<模块>/<套件>.txt
    <exemption-dir>/<模块>/<套件>-豁免材料.zip
    <exemption-dir>/<模块>/evidence/<套件>-report.txt 等

本脚本只读扫描目录并交叉校验，不修改、不删除、不提交任何内容。

用法：
    python3 scripts/verify_waiver_layout.py \
        --exemption-dir '/mnt/d/ohos/.../exemption/Acts' \
        --records '/mnt/d/ohos/.../exemption/Acts/waiver-records-20261105.json'

    # 历史目录：允许非规范命名（一模块一文件、模块级 zip 等），只告警不判错
    python3 scripts/verify_waiver_layout.py --exemption-dir <dir> --allow-legacy

退出码：0 = 无问题；1 = 存在问题；2 = 参数错误（目录不存在、records 不可解析等）。
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath

EXIT_OK = 0
EXIT_PROBLEMS = 1
EXIT_USAGE = 2

ZIP_SUFFIX = "-豁免材料.zip"
SECTION_KEYWORD = {1: "失败用例", 2: "失败原因", 3: "申请豁免理由"}
FULLWIDTH_DIGITS = {"１": 1, "２": 2, "３": 3}

SECTION_RE = re.compile(
    r"^\s*(?P<num>[123１２３])\s*[.、．)）]\s*"
    r"(?P<name>失败用例|失败原因|申请豁免理由)\s*[:：]?\s*(?P<inline>.*)$"
)
# 用例的三段都以字母/下划线开头：避免把报告里的「Driver#0..#24」之类设备串误判成用例行。
CASE_RE = re.compile(
    r"(?P<module>[A-Za-z_][A-Za-z0-9_.\-]*)#"
    r"(?P<suite>[A-Za-z_][A-Za-z0-9_.\-]*)#"
    r"(?P<case>[A-Za-z_][A-Za-z0-9_.\-]*)"
)
EVIDENCE_REF_RE = re.compile(r"evidence/[^\s：:,，。；;、）)】\]\"']+")


class UsageError(Exception):
    """命令行参数或输入文件不可用（退出码 2）。"""


class Report:
    """收集问题与警告；--allow-legacy 只把「旧版命名」类问题降级为警告。"""

    def __init__(self, root: Path, allow_legacy: bool) -> None:
        self.root = root
        self.allow_legacy = allow_legacy
        self.problems: list[str] = []
        self.warnings: list[str] = []

    def add(self, where: str, message: str, legacy_ok: bool = False) -> None:
        if legacy_ok and self.allow_legacy:
            self.warnings.append(f"{where}: {message}（--allow-legacy：仅警告）")
        else:
            self.problems.append(f"{where}: {message}")


class SuiteInfo:
    """一个 <套件>.txt 的解析结果，供 records 交叉校验使用。"""

    __slots__ = ("module", "suite", "txt", "module_dir", "cases", "refs")

    def __init__(self, module: str, suite: str, txt: Path, module_dir: Path) -> None:
        self.module = module
        self.suite = suite
        self.txt = txt
        self.module_dir = module_dir
        self.cases: list[tuple[str, str, str]] = []
        self.refs: list[str] = []


def _candidate_paths(raw: str) -> list[Path]:
    """把用户给的路径展开成候选：原样、Windows 盘符 <-> /mnt/<盘符>。"""
    text = str(raw).strip().strip('"').strip("'")
    seen: list[str] = []

    def push(candidate: Path) -> None:
        if str(candidate) not in seen:
            seen.append(str(candidate))

    push(Path(text))
    slash = text.replace("\\", "/")
    drive = re.match(r"^([A-Za-z]):/(.*)$", slash)
    if drive:
        letter, rest = drive.group(1), drive.group(2)
        push(Path(f"/mnt/{letter.lower()}/{rest}"))
        push(Path(f"{letter.upper()}:{os.sep}{rest.replace('/', os.sep)}"))
    mount = re.match(r"^/mnt/([A-Za-z])/(.*)$", slash)
    if mount:
        letter, rest = mount.group(1), mount.group(2)
        push(Path(f"{letter.upper()}:{os.sep}{rest.replace('/', os.sep)}"))
        push(Path(f"/mnt/{letter.lower()}/{rest}"))
    return [Path(item) for item in seen]


def resolve_exemption_dir(raw: str) -> Path:
    candidates = _candidate_paths(raw)
    for candidate in candidates:
        try:
            if candidate.is_dir():
                return candidate.resolve()
        except OSError:
            continue
    tried = "、".join(str(item) for item in candidates)
    raise UsageError(f"豁免目录不存在或不是目录：{raw}（已尝试：{tried}）")


def resolve_records_file(raw: str) -> Path:
    candidates = _candidate_paths(raw)
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate.resolve()
        except OSError:
            continue
    tried = "、".join(str(item) for item in candidates)
    raise UsageError(f"records 文件不存在：{raw}（已尝试：{tried}）")


def path_identity(raw: str, root: Path | None = None) -> str:
    """把 Windows / /mnt/x / POSIX 路径归一成同一个大小写不敏感的比较键。"""
    text = str(raw).strip().strip('"').strip("'").replace("\\", "/")
    mount = re.match(r"^/mnt/([A-Za-z])/(.*)$", text)
    if mount:
        text = f"{mount.group(1)}:/{mount.group(2)}"
    drive = re.match(r"^([A-Za-z]):/(.*)$", text)
    if drive:
        rest = posixpath.normpath(drive.group(2))
        return f"{drive.group(1).lower()}:/{rest}".lower()
    if text.startswith("/"):
        return posixpath.normpath(text).lower()
    base = path_identity(str(root)) if root is not None else ""
    joined = posixpath.normpath(f"{base}/{text}" if base else text)
    return joined.lower()


def parse_sections(text: str) -> tuple[dict[int, tuple[str, str]], list[int], list[int]]:
    """解析三节标题，返回 {节号: (标题, 正文)}、重复节号、出现顺序。"""
    lines = text.splitlines()
    hits: list[tuple[int, int, str, str]] = []
    for index, line in enumerate(lines):
        match = SECTION_RE.match(line)
        if not match:
            continue
        token = match.group("num")
        number = int(token) if token in "123" else FULLWIDTH_DIGITS[token]
        hits.append((index, number, match.group("name"), match.group("inline").strip()))

    sections: dict[int, tuple[str, str]] = {}
    duplicates: list[int] = []
    order: list[int] = []
    for position, (index, number, name, inline) in enumerate(hits):
        end = hits[position + 1][0] if position + 1 < len(hits) else len(lines)
        body = "\n".join([inline, *lines[index + 1 : end]]).strip()
        order.append(number)
        if number in sections:
            duplicates.append(number)
        else:
            sections[number] = (name, body)
    return sections, duplicates, order


def parse_cases(text: str) -> list[tuple[str, str, str]]:
    return [
        (match.group("module"), match.group("suite"), match.group("case"))
        for match in CASE_RE.finditer(text)
    ]


def parse_evidence_refs(text: str) -> list[str]:
    refs: list[str] = []
    for match in EVIDENCE_REF_RE.finditer(text):
        ref = match.group(0).rstrip(".")
        if ref and ref not in refs:
            refs.append(ref)
    return refs


def validate_sections(report: Report, where: str, text: str) -> None:
    sections, duplicates, order = parse_sections(text)
    for number in (1, 2, 3):
        expected = SECTION_KEYWORD[number]
        if number not in sections:
            report.add(where, f"缺少第 {number} 节「{expected}」标题（R3 三节式）")
            continue
        name, body = sections[number]
        if name != expected:
            report.add(where, f"第 {number} 节标题应为「{expected}」，实际为「{name}」（R3）")
        if not body:
            report.add(where, f"第 {number} 节「{expected}」内容为空（R3）")
    for number in sorted(set(duplicates)):
        report.add(where, f"第 {number} 节标题重复出现（R3/R8）")
    if order and order != sorted(order):
        report.warnings.append(f"{where}: 三节顺序不是 1 → 2 → 3（R3）")


def validate_cases(
    report: Report, where: str, module: str, suite: str, text: str
) -> list[tuple[str, str, str]]:
    cases = parse_cases(text)
    for case_module, case_suite, case_name in cases:
        if case_module != module:
            report.add(
                where,
                f"用例行「{case_module}#{case_suite}#{case_name}」的模块 ≠ 目录名「{module}」"
                "（R8：一个 txt 只能一个模块）",
            )
        elif case_suite != suite:
            report.add(
                where,
                f"用例行「{case_module}#{case_suite}#{case_name}」的套件 ≠ 文件名「{suite}」"
                "（R8：一个 txt 只能一个 Testsuite）",
                legacy_ok=(suite == module),
            )
    return cases


def validate_evidence(
    report: Report, where: str, module_dir: Path, suite: str, text: str
) -> list[str]:
    refs = parse_evidence_refs(text)
    if not refs:
        report.add(
            where,
            "txt 未引用任何 evidence/ 佐证（R4：佐证放同模块 evidence/ 并用相对本文件的路径引用）",
            legacy_ok=True,
        )
    for ref in refs:
        if ".." in PurePosixPath(ref).parts:
            report.add(where, f"佐证引用路径越界：{ref}（R4 只允许本模块 evidence/ 下的相对路径）")
            continue
        target = module_dir / ref
        try:
            exists = target.is_file()
        except OSError:
            exists = False
        if not exists:
            report.add(where, f"引用的佐证不存在：{ref}（R4）")
        elif not target.name.startswith(f"{suite}-"):
            report.add(where, f"佐证文件名未带「{suite}-」前缀：{ref}（R4）", legacy_ok=True)
    return refs


def validate_zip(report: Report, where: str, suite: str, zip_path: Path, refs: list[str]) -> None:
    try:
        with zipfile.ZipFile(zip_path) as archive:
            raw_names = archive.namelist()
    except (OSError, zipfile.BadZipFile) as error:
        report.add(where, f"附件不是可读 zip：{zip_path.name}（{error}）")
        return

    members: list[str] = []
    for name in raw_names:
        normalized = name.replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        if normalized and not normalized.endswith("/"):
            members.append(normalized)

    base = ""
    if members:
        first_parts = {member.split("/", 1)[0] for member in members if "/" in member}
        if len(first_parts) == 1:
            candidate = f"{next(iter(first_parts))}/"
            if any(member == f"{candidate}{suite}.txt" for member in members):
                base = candidate

    txt_member = f"{base}{suite}.txt"
    if txt_member not in members:
        other_txts = [member for member in members if member.lower().endswith(".txt")]
        detail = f"（zip 内 txt：{'、'.join(other_txts[:5])}）" if other_txts else ""
        report.add(where, f"zip 内缺少本套件 txt「{suite}.txt」{detail}（R4）")

    evidence_members = [member for member in members if member.startswith(f"{base}evidence/")]
    if not evidence_members:
        report.add(where, "zip 内没有 evidence/ 目录（R4/R5：附件须含该 txt 与本套件佐证）")

    ref_set = set(refs)
    for ref in refs:
        if f"{base}{ref}" not in members:
            report.add(where, f"zip 内缺少被 txt 引用的佐证：{ref}（R4）")

    for member in evidence_members:
        relative = member[len(base) :]
        name = member.rsplit("/", 1)[-1]
        if not name.startswith(f"{suite}-"):
            report.add(
                where,
                f"zip 内含非本套件前缀的佐证：{relative}（R4：只放 {suite}- 前缀文件）",
                legacy_ok=True,
            )
        elif relative not in ref_set:
            report.warnings.append(f"{where}: zip 含未被 txt 引用的佐证 {relative}（可能多余）")

    extra_txts = [
        member
        for member in members
        if member.lower().endswith(".txt")
        and member != txt_member
        and not member.startswith(f"{base}evidence/")
    ]
    if extra_txts:
        report.warnings.append(
            f"{where}: zip 内还有其它 txt：{'、'.join(extra_txts[:5])}（R4 只应含 {suite}.txt）"
        )


def inspect_suite(
    report: Report,
    module_dir: Path,
    module_name: str,
    txt: Path,
    module_zips: list[Path],
) -> SuiteInfo:
    suite = txt.stem
    info = SuiteInfo(module_name, suite, txt, module_dir)
    where = f"{module_name}/{txt.name}"

    try:
        text = txt.read_text(encoding="utf-8-sig")
    except OSError as error:
        report.add(where, f"无法读取 txt：{error}")
        return info

    if suite == module_name:
        report.add(
            where,
            "txt 文件名等于模块名，疑似「一模块一文件」的模块级合并材料（R2/R8：一 Testsuite 一文件）",
            legacy_ok=True,
        )

    validate_sections(report, where, text)
    info.cases = validate_cases(report, where, module_name, suite, text)
    info.refs = validate_evidence(report, where, module_dir, suite, text)

    zip_path = module_dir / f"{suite}{ZIP_SUFFIX}"
    if not zip_path.is_file():
        other_names = [item.name for item in module_zips]
        detail = f"（模块内现有 zip：{'、'.join(other_names)}）" if other_names else "（模块内没有任何 zip）"
        report.add(
            where,
            f"缺少成对附件「{suite}{ZIP_SUFFIX}」{detail}（R2/R4/R5）",
            legacy_ok=bool(other_names),
        )
    else:
        validate_zip(report, where, suite, zip_path, info.refs)

    return info


def load_records(path: Path) -> list:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise UsageError(f"无法读取 records 文件：{error}") from error
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise UsageError(f"records 不是合法 JSON：{error}") from error
    if isinstance(data, dict):
        for key in ("records", "waivers", "items"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list):
        raise UsageError("records 顶层应为记录数组（或含 records 数组的对象）")
    return data


def check_records(report: Report, root: Path, records_path: Path, suites: list[SuiteInfo]) -> None:
    records = load_records(records_path)
    suite_index = {(info.module, info.suite): info for info in suites}
    by_key: dict[tuple[str, str], dict] = {}

    for index, record in enumerate(records):
        where = f"records[{index}]"
        if not isinstance(record, dict):
            report.add(where, "记录不是 JSON 对象（R5）")
            continue
        module = str(record.get("moduleName") or "").strip()
        suite = str(record.get("testsuite") or "").strip()
        if not module or not suite:
            report.add(where, "缺少 moduleName / testsuite（R5）")
            continue
        where = f"{module}/{suite}（records[{index}]）"
        key = (module, suite)
        if key in by_key:
            report.add(where, "同一「模块+套件」出现多条记录（R2/R6：一个 Testsuite 一条申请）")
            continue
        by_key[key] = record

        info = suite_index.get(key)
        if info is None:
            report.add(where, "records 中没有对应套件（目录内找不到该模块/套件的 txt，R6 查重键不匹配）")
            continue

        category = record.get("testCategory")
        if not category:
            report.warnings.append(f"{where}: 缺少 testCategory（R5 应为 ACTS）")
        elif str(category).strip() != "ACTS":
            report.add(where, f"测试套分类应为 ACTS，实际为「{category}」（R5）")

        for field, expected, label in (
            ("attachmentPath", info.module_dir / f"{info.suite}{ZIP_SUFFIX}", "附件 zip"),
            ("attachmentTxt", info.txt, "txt 正文"),
        ):
            raw = record.get(field)
            if not raw:
                report.add(where, f"缺少 {field}（R5：{label}）")
                continue
            if path_identity(str(raw), root) != path_identity(str(expected)):
                report.add(where, f"{field} 与目录不一致：记录={raw} 期望={expected}")

        txt_cases = [case_name for _, _, case_name in info.cases]
        listed = record.get("testcases")
        count_field = record.get("caseCount")
        if isinstance(listed, list):
            listed_text = [str(item) for item in listed]
            if len(listed_text) != len(txt_cases):
                report.add(
                    where,
                    f"用例数不一致：txt 列出 {len(txt_cases)} 条 / 记录 testcases {len(listed_text)} 条（R5/R6）",
                )
            if isinstance(count_field, int) and count_field != len(listed_text):
                report.add(where, f"caseCount={count_field} 与 testcases 数量 {len(listed_text)} 不一致（R5）")
            missing = [case_name for case_name in txt_cases if case_name not in listed_text]
            extra = [case_name for case_name in listed_text if case_name not in txt_cases]
            if missing or extra:
                report.warnings.append(
                    f"{where}: 记录用例清单与 txt 不完全一致（记录少 {len(missing)} 条、多 {len(extra)} 条；"
                    "硬校验只比数量，逐条差异仅供参考）"
                )
        elif isinstance(count_field, int):
            if count_field != len(txt_cases):
                report.add(where, f"用例数不一致：txt {len(txt_cases)} 条 / 记录 caseCount {count_field}（R5/R6）")
        else:
            report.add(where, "缺少 testcases 清单与 caseCount（R5：测试用例名称必填）")

        if not str(record.get("waiverReason") or "").strip():
            report.warnings.append(f"{where}: waiverReason 为空（R5 应填 txt 第 3 节全文）")

    for info in suites:
        if (info.module, info.suite) not in by_key:
            report.add(
                f"{info.module}/{info.suite}",
                "缺少平台豁免申请记录（records 与套件必须一一对应，R2/R5）",
            )


def inspect_tree(report: Report) -> tuple[int, list[SuiteInfo]]:
    root = report.root
    try:
        children = sorted(root.iterdir(), key=lambda item: item.name.lower())
    except OSError as error:
        raise UsageError(f"无法读取豁免目录：{error}") from error

    module_dirs = [item for item in children if item.is_dir() and not item.name.startswith(".")]
    suites: list[SuiteInfo] = []

    for module_dir in module_dirs:
        module_name = module_dir.name
        try:
            entries = sorted(module_dir.iterdir(), key=lambda item: item.name.lower())
        except OSError as error:
            report.add(module_name, f"无法读取模块目录：{error}")
            continue
        txts = [item for item in entries if item.is_file() and item.suffix.lower() == ".txt"]
        zips = [item for item in entries if item.is_file() and item.suffix.lower() == ".zip"]
        if not txts:
            report.add(module_name, "模块目录下没有任何 .txt（R1/R2）")
        for txt in txts:
            suites.append(inspect_suite(report, module_dir, module_name, txt, zips))

        expected_zips = {f"{txt.stem}{ZIP_SUFFIX}" for txt in txts}
        for zip_path in zips:
            if zip_path.name not in expected_zips:
                report.add(
                    f"{module_name}/{zip_path.name}",
                    "zip 没有对应的 <套件>.txt（R2/R4：附件必须与套件 txt 成对）",
                    legacy_ok=True,
                )

    if not module_dirs:
        report.add(str(root), "目录下没有任何模块子目录（R1：exemption/<测试大项>/<模块>/）")

    return len(module_dirs), suites


def print_report(report: Report, module_count: int, suite_count: int, case_count: int) -> None:
    print(f"豁免资料校验：{report.root}")
    print(
        f"汇总：{module_count} 模块 / {suite_count} 套件 / {case_count} 用例 / {len(report.problems)} 问题"
    )
    if report.warnings:
        print(f"警告：{len(report.warnings)} 条（--allow-legacy 降级，不计入问题数）")
    if report.problems:
        print("问题清单：")
        for index, message in enumerate(report.problems, 1):
            print(f"  {index}. {message}")
    if report.warnings:
        print("警告清单：")
        for index, message in enumerate(report.warnings, 1):
            print(f"  {index}. {message}")
    if not report.problems:
        print("校验通过：目录结构、三节内容、佐证与 zip、平台申请记录均符合豁免资料规范。")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="校验 OpenHarmony 豁免资料目录与提交模型（只读，不修改任何文件）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "退出码：0 = 无问题；1 = 存在问题；2 = 参数错误。\n"
            "规则见 docs/操作手册.md 第六节「豁免资料规范（waiver）」。"
        ),
    )
    parser.add_argument(
        "--exemption-dir",
        required=True,
        help="豁免大项目录，如 .../exemption/Acts；接受 D:\\ohos\\... 或 /mnt/d/... 或普通路径",
    )
    parser.add_argument(
        "--records",
        help="可选：waiver-records-*.json；校验每条记录的套件对应、用例数与附件路径",
    )
    parser.add_argument(
        "--allow-legacy",
        action="store_true",
        help="允许旧版命名（一模块一文件、模块级 zip、未带套件前缀等），只警告不判错",
    )
    args = parser.parse_args(argv)

    try:
        root = resolve_exemption_dir(args.exemption_dir)
        records_path = resolve_records_file(args.records) if args.records else None
        report = Report(root, args.allow_legacy)
        module_count, suites = inspect_tree(report)
        if records_path is not None:
            check_records(report, root, records_path, suites)
    except UsageError as error:
        print(f"参数错误：{error}", file=sys.stderr)
        return EXIT_USAGE
    except OSError as error:
        print(f"参数错误：{error}", file=sys.stderr)
        return EXIT_USAGE

    case_count = sum(len(info.cases) for info in suites)
    print_report(report, module_count, len(suites), case_count)
    return EXIT_PROBLEMS if report.problems else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
