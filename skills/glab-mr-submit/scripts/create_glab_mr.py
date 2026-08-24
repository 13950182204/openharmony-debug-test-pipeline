#!/usr/bin/env python3
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import quote
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULTS_PATH = SKILL_DIR / "assets" / "defaults.env"
CREDENTIAL_STORE = Path.home() / ".dsh" / "gitlab-credentials.json"
TEST_MARKERS = ("XTS", "HATS", "ACTS", "DCTS")
ACTION_TYPES = ("修改", "优化", "升级", "新增", "修复", "同步", "回退", "重构", "适配", "迁移", "移除")
CHIP_TAGS = ("RK3568", "A333/A537")
MANAGED_LABELS = ("A333/A537", "RK3568", "XTS", "通用框架层修改")
VERSION_TOKEN = re.compile(r"(?<![0-9A-Za-z.])[Vv](\d+)\.(\d+)\.(\d+|x)(?![0-9A-Za-z.])")
MILESTONE_VERSION = re.compile(r"[Vv](\d+)\.(\d+)\.(\d+)\s+release版本")
TEST_SECTIONS = (
    "## 具体:",
    "## 问题分析:",
    "## 修改内容:",
    "## 测试环境:",
    "## 测试命令:",
    "## 测试结果:",
    "## 测试结果截图:",
)


class MrError(Exception):
    pass


def run(cmd, cwd, check=True, capture=True):
    kwargs = {
        "cwd": str(cwd),
        "text": True,
        "check": False,
    }
    if capture:
        kwargs.update({"stdout": subprocess.PIPE, "stderr": subprocess.PIPE})
    result = subprocess.run(cmd, **kwargs)
    if check and result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else ""
        stdout = result.stdout.strip() if result.stdout else ""
        detail = stderr or stdout or f"exit code {result.returncode}"
        raise MrError(f"Command failed: {quote_cmd(cmd)}\n{detail}")
    return result


def quote_cmd(cmd):
    return " ".join(shlex.quote(str(part)) for part in cmd)


def load_defaults(path):
    data = {}
    if not path.exists():
        raise MrError(f"Defaults file not found: {path}")
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise MrError(f"Invalid defaults line: {raw}")
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def parse_message(path):
    text = Path(path).read_text(encoding="utf-8").replace("\r\n", "\n").strip()
    if not text:
        raise MrError("Commit message is empty")
    lines = text.split("\n")
    subject = lines[0].strip()
    body = "\n".join(lines[1:]).strip()
    return subject, body, text


def parse_title(subject):
    text = subject.strip()
    match = re.match(r"^\[([^\]]+)\]\s+", text)
    if not match:
        raise MrError("MR title must start with '[动作] ' and use spaces between bracket fields")
    action = match.group(1).strip()
    if action not in ACTION_TYPES:
        raise MrError(f"Unsupported MR title action '[{action}]'; allowed actions: {', '.join(ACTION_TYPES)}")
    text = text[match.end():]

    chip = None
    for candidate in CHIP_TAGS:
        prefix = f"[{candidate}]"
        if text.startswith(prefix):
            remainder = text[len(prefix):]
            if not remainder.startswith(" "):
                raise MrError("MR title bracket fields must be separated by one or more spaces")
            chip = candidate
            text = remainder.lstrip()
            break

    xts = False
    if text.startswith("[XTS]"):
        remainder = text[len("[XTS]"):]
        if not remainder.startswith(" "):
            raise MrError("MR title bracket fields must be separated by one or more spaces")
        xts = True
        text = remainder.lstrip()

    if re.match(r"^\[[^\]]+\]", text):
        raise MrError("MR title may contain only one chip field followed by an optional [XTS] field")
    if not text:
        raise MrError("MR title must contain a non-empty summary after its bracket fields")
    return {"action": action, "chip": chip, "xts": xts, "summary": text}


def is_test_related(subject, files, title_info=None):
    if title_info and title_info["xts"]:
        return True
    haystack = " ".join([subject] + files)
    upper = haystack.upper()
    if any(marker in upper for marker in TEST_MARKERS):
        return True
    return any(path.startswith("test/xts/") or "/test/xts/" in path for path in files)


def normalize_labels(values):
    labels = []
    for value in values or []:
        for item in value.split(","):
            label = item.strip()
            if label and label not in labels:
                labels.append(label)
    return labels


def derive_labels(title_info):
    labels = []
    if title_info["chip"]:
        labels.append(title_info["chip"])
    else:
        labels.append("通用框架层修改")
    if title_info["xts"]:
        labels.append("XTS")
    return labels


def resolve_labels(title_info, values):
    provided = normalize_labels(values)
    provided_managed = set(provided) & set(MANAGED_LABELS)
    chip = title_info["chip"]
    conflicting_chips = set(CHIP_TAGS) & provided_managed
    if chip and conflicting_chips - {chip}:
        raise MrError("Explicit chip labels must not conflict with the chip field in the MR title")
    if "XTS" in provided_managed and not title_info["xts"]:
        raise MrError("The XTS label requires an [XTS] field in the MR title")
    labels = []
    for label in derive_labels(title_info) + provided:
        if label not in labels:
            labels.append(label)
    return labels


def section_body(body, heading):
    lines = body.splitlines()
    try:
        start = next(index for index, line in enumerate(lines) if line.strip() == heading) + 1
    except StopIteration:
        return ""
    end = len(lines)
    for index in range(start, len(lines)):
        if re.match(r"^##\s+", lines[index].strip()):
            end = index
            break
    return "\n".join(lines[start:end]).strip()


def markdown_image_links(text):
    return re.findall(r"!\[[^]]*\]\(([^)]+)\)", text)


def validate_markdown_record(body, screenshot_paths):
    previous = -1
    for heading in TEST_SECTIONS:
        positions = [index for index, line in enumerate(body.splitlines()) if line.strip() == heading]
        position = next((item for item in positions if item > previous), None)
        if position is None:
            raise MrError(f"MR description is missing required Markdown heading: {heading}")
        previous = position

    command_section = section_body(body, "## 测试命令:")
    if "```bash" not in command_section or "```" not in command_section.split("```bash", 1)[1]:
        raise MrError("MR description must put the test command in a fenced bash code block")

    screenshot_section = section_body(body, "## 测试结果截图:")
    if not screenshot_paths and not markdown_image_links(screenshot_section):
        raise MrError("MR description must contain a screenshot Markdown image or use --screenshot")


def validate_message(subject, body, files, screenshot_paths=None):
    screenshot_paths = screenshot_paths or []
    title_info = parse_title(subject)
    related = is_test_related(subject, files, title_info)
    markdown_record = related or bool(screenshot_paths)
    if related and not title_info["xts"]:
        raise MrError("XTS/HATS/ACTS/DCTS related changes must include an [XTS] title field")

    if markdown_record and "## 具体:" not in body:
        raise MrError("MR with test evidence must use the heading '## 具体:'")
    if not markdown_record and "具体:" not in body:
        raise MrError("Commit body must contain '具体:'")

    detail = section_body(body, "## 具体:") if markdown_record else body.split("具体:", 1)[1].strip()
    if not detail:
        raise MrError("Commit body must explain the reason after '具体:'")
    if len(detail) < 8:
        raise MrError("Commit detail after '具体:' is too short to explain the reason")
    if markdown_record:
        validate_markdown_record(body, screenshot_paths)
    return related


def append_screenshot_links(body, links):
    if not links:
        return body
    heading = "## 测试结果截图:"
    marker = body.find(heading)
    if marker < 0:
        raise MrError(f"Cannot append screenshots because heading is missing: {heading}")
    start = marker + len(heading)
    next_heading = re.search(r"\n##\s+", body[start:])
    end = start + next_heading.start() if next_heading else len(body)
    before = body[:end].rstrip()
    after = body[end:].lstrip()
    image_block = "\n\n".join(links)
    if after:
        return f"{before}\n\n{image_block}\n\n{after}".strip()
    return f"{before}\n\n{image_block}".strip()


def normalize_branch_suffix(subject):
    title_info = parse_title(subject)
    text = title_info["summary"]
    if text.startswith(title_info["action"]):
        text = text[len(title_info["action"]):].strip()
    if text.endswith("问题"):
        text = text[:-2].strip()

    replacements = {
        "异常报错": "_异常报错",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    text = re.sub(r"[\s/\\:：,，;；()（）\[\]{}<>《》\"'`]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    text = re.sub(r"[^0-9A-Za-z_.\-\u4e00-\u9fff]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        raise MrError("Cannot derive branch suffix from commit subject")
    return text[:80]


def version_key(version):
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        return (-1, -1, -1)
    return tuple(int(item) for item in match.groups())


def next_patch_version(version):
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        return version
    major, minor, patch = (int(item) for item in match.groups())
    return f"v{major}.{minor}.{patch + 1}"


def infer_iteration(repo, remote, base_version, fallback):
    pattern = f"refs/heads/v*/{base_version}_*"
    result = run(["git", "ls-remote", "--heads", remote, pattern], repo)
    versions = set()
    for line in result.stdout.splitlines():
        ref = line.split()[-1] if line.split() else ""
        match = re.search(r"refs/heads/(v[^/]+)/" + re.escape(base_version) + r"_", ref)
        if match and re.fullmatch(r"v\d+\.\d+\.\d+", match.group(1)):
            versions.add(match.group(1))
    if not versions:
        return fallback
    latest = sorted(versions, key=version_key)[-1]
    return next_patch_version(latest)


def branch_exists(repo, remote, branch):
    local = run(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], repo, check=False)
    if local.returncode == 0:
        return True
    remote_result = run(["git", "ls-remote", "--heads", remote, f"refs/heads/{branch}"], repo)
    return bool(remote_result.stdout.strip())


def unique_branch(repo, remote, base_branch):
    if not branch_exists(repo, remote, base_branch):
        return base_branch
    for idx in range(2, 100):
        candidate = f"{base_branch}_{idx}"
        if not branch_exists(repo, remote, candidate):
            return candidate
    raise MrError(f"Could not create a unique branch name from {base_branch}")


def ensure_repo(repo):
    if shutil.which("git") is None:
        raise MrError("git is not available")
    if shutil.which("glab") is None:
        raise MrError("glab is not available")
    run(["git", "rev-parse", "--show-toplevel"], repo)


def ensure_glab_auth(repo, hostname, api_protocol):
    result = run(["glab", "auth", "status", "--hostname", hostname], repo, check=False)
    if result.returncode == 0:
        return
    # Fallback: auto-login from the dsh-gitlab-credentials plugin store; the
    # token is written to glab's STDIN, never to the command line or logs.
    if glab_login_from_store(hostname, api_protocol):
        result = run(["glab", "auth", "status", "--hostname", hostname], repo, check=False)
        if result.returncode == 0:
            return
    detail = result.stderr.strip() or result.stdout.strip() or "no authenticated token found"
    hint = ("Authenticate once with glab auth login --hostname {0} --api-protocol {1} --use-keyring, "
            "or save the token for this host in 设置 > GitLab 凭据.").format(hostname, api_protocol)
    raise MrError(
        f"GitLab authentication failed for {hostname} ({api_protocol} API): {detail}. {hint}"
    )


def load_credential_store():
    """Read the dsh-gitlab-credentials store when present (never logs tokens)."""
    try:
        if not CREDENTIAL_STORE.exists():
            return None
        doc = json.loads(CREDENTIAL_STORE.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else None
    except (OSError, ValueError):
        return None


def glab_login_from_store(hostname, api_protocol):
    """Log the stored token into glab for one host; token flows over STDIN."""
    doc = load_credential_store()
    if not doc:
        return False
    hosts = doc.get("hosts") if isinstance(doc, dict) else None
    record = hosts.get(hostname) if isinstance(hosts, dict) else None
    if not isinstance(record, dict):
        return False
    token = record.get("token")
    if not isinstance(token, str) or not token.strip():
        return False
    protocol = record.get("apiProtocol")
    if protocol not in ("http", "https"):
        protocol = api_protocol
    api_host = record.get("apiHost") or hostname
    git_protocol = record.get("gitProtocol")
    if git_protocol not in ("ssh", "https"):
        git_protocol = "ssh"
    result = subprocess.run(
        ["glab", "auth", "login", "--hostname", hostname, "--api-host", api_host,
         "--api-protocol", protocol, "--git-protocol", git_protocol, "--stdin"],
        input=f"{token.strip()}\n", text=True, capture_output=True,
    )
    return result.returncode == 0


def load_mr_preferences():
    """GUI-configured MR defaults from the dsh-gitlab-credentials store."""
    doc = load_credential_store()
    if not doc:
        return {}
    prefs = doc.get("mrPreferences")
    return prefs if isinstance(prefs, dict) else {}


def ensure_index_integrity(repo):
    result = run(["git", "ls-files", "-z"], repo)
    tracked = [item for item in result.stdout.split("\0") if item]
    if not tracked:
        raise MrError(
            "Git index is empty or incomplete. Initialize the release worktree index with 'git read-tree HEAD' "
            "before using an index-only worktree."
        )


def status_paths(repo):
    result = run(["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"], repo)
    paths = []
    for item in result.stdout.split("\0"):
        if len(item) >= 3 and item[2] == " ":
            paths.append(item[3:])
    return paths


def ensure_only_files_changed(repo, files):
    allowed = set(files)
    unexpected = sorted(set(status_paths(repo)) - allowed)
    if unexpected:
        preview = ", ".join(unexpected[:20])
        suffix = " ..." if len(unexpected) > 20 else ""
        raise MrError(
            f"Worktree contains changes outside --files ({len(unexpected)} paths): {preview}{suffix}. "
            "Create a clean release worktree and apply only the selected patch."
        )


def ensure_remote_and_target(repo, remote, target):
    remotes = run(["git", "remote"], repo).stdout.split()
    if remote not in remotes:
        raise MrError(f"Remote '{remote}' not found")
    local = run(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{target}"], repo, check=False)
    remote_result = run(["git", "ls-remote", "--heads", remote, f"refs/heads/{target}"], repo)
    if local.returncode != 0 and not remote_result.stdout.strip():
        raise MrError(f"Target branch '{target}' not found locally or on remote '{remote}'")


def ensure_base_ref(repo, remote, base_ref):
    if not base_ref:
        return
    if base_ref.startswith(f"{remote}/"):
        branch = base_ref.split("/", 1)[1]
        run(["git", "fetch", "--quiet", remote, branch], repo)
    exists = run(["git", "rev-parse", "--verify", "--quiet", base_ref], repo, check=False)
    if exists.returncode != 0:
        raise MrError(f"Base ref '{base_ref}' not found. Create or fetch the release worktree base first")
    ancestor = run(["git", "merge-base", "--is-ancestor", base_ref, "HEAD"], repo, check=False)
    if ancestor.returncode != 0:
        raise MrError(
            f"Current HEAD is not based on {base_ref}. "
            f"Create a fresh worktree from {base_ref} and apply only the selected patch before submitting"
        )


def normalize_assignee(value):
    assignee = (value or "").strip()
    if assignee.startswith("@"):
        assignee = assignee[1:]
    return assignee


def project_path_from_remote(repo, remote):
    project = run(["git", "remote", "get-url", remote], repo).stdout.strip()
    if project.endswith(".git"):
        project = project[:-4]
    if "://" in project:
        project = project.split("://", 1)[1]
        project = project.split("/", 1)[1]
    elif ":" in project:
        project = project.rsplit(":", 1)[1]
    return project.strip("/")


def encoded_project_path(repo, remote):
    return quote(project_path_from_remote(repo, remote), safe="")


def gitlab_json(repo, hostname, endpoint):
    result = run(["glab", "api", "--hostname", hostname, endpoint], repo)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise MrError(f"GitLab returned invalid JSON for {endpoint}") from exc


def project_labels(repo, hostname, project):
    payload = gitlab_json(repo, hostname, f"projects/{project}/labels?per_page=100")
    if not isinstance(payload, list):
        raise MrError("GitLab labels response was not a list")
    return {item.get("name") for item in payload if item.get("name")}


def active_milestones(repo, hostname, project):
    payload = gitlab_json(repo, hostname, f"projects/{project}/milestones?state=active&per_page=100")
    if not isinstance(payload, list):
        raise MrError("GitLab milestones response was not a list")
    return payload


def milestone_version(milestone):
    match = MILESTONE_VERSION.search(str(milestone.get("title", "")))
    if not match:
        return None
    return tuple(int(item) for item in match.groups())


def milestone_for_token(token, milestones):
    major, minor, patch = token
    versioned = [(milestone, milestone_version(milestone)) for milestone in milestones]
    exact = [] if patch == "x" else [
        milestone for milestone, version in versioned
        if version == (int(major), int(minor), int(patch))
    ]
    if len(exact) == 1:
        return exact[0]
    family = [milestone for milestone, version in versioned if version and version[:2] == (int(major), int(minor))]
    return family[0] if len(family) == 1 else None


def match_milestone_text(text, milestones):
    tokens = VERSION_TOKEN.findall(text or "")
    if not tokens:
        return None
    candidates = []
    unresolved = False
    for token in tokens:
        candidate = milestone_for_token(token, milestones)
        if candidate is None:
            unresolved = True
        elif candidate not in candidates:
            candidates.append(candidate)
    if unresolved or len(candidates) != 1:
        return None
    return candidates[0]


def resolve_explicit_milestone(value, milestones):
    target = str(value or "").strip()
    if not target:
        raise MrError("--milestone cannot be empty")
    matches = [item for item in milestones if target == str(item.get("title", ""))]
    if target.isdigit():
        matches = [
            item for item in milestones
            if target in (str(item.get("id", "")), str(item.get("iid", "")))
        ]
    if len(matches) != 1:
        raise MrError(f"Active milestone not found or not unique: {target}")
    return matches[0]


def resolve_milestone(repo, hostname, project, explicit, branch, subject, body, mode):
    milestones = active_milestones(repo, hostname, project)
    if explicit:
        return resolve_explicit_milestone(explicit, milestones), "explicit"
    if mode != "branch_then_message":
        raise MrError(f"Unsupported MILESTONE_MATCH_MODE: {mode}")
    branch_match = match_milestone_text(branch, milestones)
    if branch_match:
        return branch_match, "source branch"
    message_match = match_milestone_text(f"{subject}\n{body}", milestones)
    if message_match:
        return message_match, "title or description"
    return None, "no unique active milestone match"


def ensure_labels_exist(repo, hostname, project, labels):
    missing = sorted(set(labels) - project_labels(repo, hostname, project))
    if missing:
        raise MrError(f"Required GitLab labels do not exist: {', '.join(missing)}")


def ensure_screenshot_files(screenshot_paths):
    for item in screenshot_paths:
        source = Path(item).expanduser().resolve()
        if not source.is_file():
            raise MrError(f"Screenshot file does not exist: {source}")


def current_user(repo, hostname):
    result = run(["glab", "api", "--hostname", hostname, "user"], repo)
    user_id = re.search(r'"id"\s*:\s*(\d+)', result.stdout)
    username = re.search(r'"username"\s*:\s*"([^"]+)"', result.stdout)
    if not user_id or not username:
        raise MrError("Could not resolve current GitLab user id for assignee fallback")
    return user_id.group(1), username.group(1)


def upload_screenshots(repo, remote, hostname, screenshot_paths):
    links = []
    project = encoded_project_path(repo, remote)
    for item in screenshot_paths:
        source = Path(item).expanduser().resolve()

        upload_path = source
        temporary_directory = None
        try:
            # Snap-packaged glab may not be able to read /tmp. Keep the upload copy in HOME.
            if not str(source).startswith(str(Path.home()) + os.sep):
                temporary_directory = tempfile.mkdtemp(prefix=".glab-mr-upload-", dir=Path.home())
                upload_path = Path(temporary_directory) / source.name
                shutil.copyfile(source, upload_path)
            result = run([
                "glab", "api", "--hostname", hostname, "--method", "POST",
                f"projects/{project}/uploads", "--form", f"file=@{upload_path}",
            ], repo)
            try:
                payload = json.loads(result.stdout)
            except json.JSONDecodeError as exc:
                raise MrError(f"GitLab screenshot upload returned invalid JSON for {source}") from exc
            markdown = payload.get("markdown")
            if not markdown or not payload.get("url"):
                raise MrError(f"GitLab screenshot upload returned no Markdown link for {source}")
            links.append(markdown)
        finally:
            if temporary_directory:
                shutil.rmtree(temporary_directory, ignore_errors=True)
    return links


def mr_create_command(branch, target, subject, body, assignee, labels, milestone):
    command = [
        "glab", "mr", "create",
        "--source-branch", branch,
        "--target-branch", target,
        "--title", subject,
        "--description", body,
        "--remove-source-branch",
    ]
    if assignee:
        command.extend(["--assignee", assignee])
    if labels:
        command.extend(["--label", ",".join(labels)])
    if milestone:
        command.extend(["--milestone", milestone["title"]])
    command.append("--yes")
    return command


def create_mr(repo, remote, hostname, branch, target, subject, body, assignee, labels, milestone):
    mr_cmd = mr_create_command(branch, target, subject, body, assignee, labels, milestone)

    result = run(mr_cmd, repo, check=False, capture=True)
    if result.returncode == 0:
        print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        match = re.search(r"https?://\S+/merge_requests/(\d+)", result.stdout)
        if not match:
            raise MrError("MR was created, but its URL could not be parsed")
        return match.group(0)
    output = "\n".join(item for item in (result.stdout, result.stderr) if item)
    if not assignee or "Failed to find user by name" not in output:
        detail = output.strip() or f"exit code {result.returncode}"
        raise MrError(f"Command failed: {quote_cmd(mr_cmd)}\n{detail}")

    user_id, username = current_user(repo, hostname)
    if assignee != username:
        detail = output.strip() or f"exit code {result.returncode}"
        raise MrError(f"Command failed: {quote_cmd(mr_cmd)}\n{detail}")

    print(output, end="", file=sys.stderr)
    print(f"Assignee lookup by username failed for '{assignee}', retrying with GitLab user id fallback.", file=sys.stderr)
    fallback_cmd = mr_create_command(branch, target, subject, body, "", labels, milestone)
    fallback = run(fallback_cmd, repo)
    print(fallback.stdout, end="")
    if fallback.stderr:
        print(fallback.stderr, end="", file=sys.stderr)

    match = re.search(r"https?://\S+/merge_requests/(\d+)", fallback.stdout)
    if not match:
        raise MrError("MR was created, but its IID could not be parsed for assignee fallback")
    encoded_project = encoded_project_path(repo, remote)
    run([
        "glab", "api", "--hostname", hostname, "--method", "PUT",
        f"projects/{encoded_project}/merge_requests/{match.group(1)}",
        "--form", f"assignee_ids[]={user_id}",
    ], repo)
    return match.group(0)


def normalize_description(text):
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    normalized = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        next_line = lines[index + 1].strip() if index + 1 < len(lines) else ""
        next_nonempty = next((item.strip() for item in lines[index + 1:] if item.strip()), "")
        indented_next = index + 1 < len(lines) and lines[index + 1].startswith("    ")
        if stripped == "-" and (next_nonempty.startswith("![") or indented_next):
            continue
        if stripped.startswith("![") and line.startswith("    "):
            line = stripped
        normalized.append(line.rstrip())
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(normalized)).strip()
    return re.sub(r"\n\n+(!\[)", r"\n\1", text)


def verify_mr(repo, remote, hostname, mr_url, branch, target, subject, body, files, labels, milestone):
    match = re.search(r"https?://\S+/merge_requests/(\d+)", mr_url)
    if not match:
        raise MrError(f"Cannot verify MR without an IID: {mr_url}")
    iid = match.group(1)
    project = encoded_project_path(repo, remote)
    details_result = run([
        "glab", "api", "--hostname", hostname,
        f"projects/{project}/merge_requests/{iid}",
    ], repo)
    changes_result = run([
        "glab", "api", "--hostname", hostname,
        f"projects/{project}/merge_requests/{iid}/changes",
    ], repo)
    try:
        details = json.loads(details_result.stdout)
        changes = json.loads(changes_result.stdout)
    except json.JSONDecodeError as exc:
        raise MrError(f"GitLab returned invalid JSON while verifying MR !{iid}") from exc

    errors = []
    if details.get("state") != "opened":
        errors.append(f"state={details.get('state')!r}")
    if details.get("source_branch") != branch:
        errors.append(f"source_branch={details.get('source_branch')!r}")
    if details.get("target_branch") != target:
        errors.append(f"target_branch={details.get('target_branch')!r}")
    if details.get("title") != subject:
        errors.append("title does not match the commit title")
    if normalize_description(details.get("description", "")) != normalize_description(body):
        errors.append("description does not match the commit body")
    if set(details.get("labels", [])) != set(labels):
        errors.append(f"labels={details.get('labels', [])!r}, expected={labels!r}")
    expected_milestone = milestone.get("title") if milestone else None
    actual_milestone = (details.get("milestone") or {}).get("title")
    if actual_milestone != expected_milestone:
        errors.append(f"milestone={actual_milestone!r}, expected={expected_milestone!r}")

    changed_paths = set()
    for change in changes.get("changes", []):
        path = change.get("new_path") or change.get("old_path")
        if path:
            changed_paths.add(path)
    if changed_paths != set(files):
        errors.append(f"changed paths={sorted(changed_paths)!r}, expected={sorted(files)!r}")
    if errors:
        raise MrError(f"MR !{iid} post-create verification failed: {'; '.join(errors)}")
    print(f"Verified MR !{iid}: exact files, title, labels, milestone, target branch, description, and state=opened")


def normalize_files(repo, files):
    if not files:
        raise MrError("No files were provided. Refuse to use git add .")
    normalized = []
    for item in files:
        path = Path(item)
        if path.is_absolute():
            try:
                rel = path.resolve().relative_to(repo.resolve())
            except ValueError as exc:
                raise MrError(f"File is outside repo: {item}") from exc
        else:
            rel = path
        rel_text = rel.as_posix()
        if rel_text in (".", ""):
            raise MrError("Refuse to stage repository root")
        if not (repo / rel_text).exists():
            raise MrError(f"File does not exist: {rel_text}")
        tracked = run(["git", "ls-files", "--error-unmatch", "--", rel_text], repo, check=False)
        if tracked.returncode != 0:
            raise MrError(f"File is not tracked by the release worktree: {rel_text}")
        normalized.append(rel_text)
    return normalized


def ensure_files_changed(repo, files):
    changed = set(status_paths(repo))
    missing = [path for path in files if path not in changed]
    if missing:
        raise MrError(f"Provided files have no visible git changes: {', '.join(missing)}")


def staged_paths(repo):
    result = run(["git", "diff", "--cached", "--name-only", "-z"], repo)
    return {item for item in result.stdout.split("\0") if item}


def ensure_staged_files_exact(repo, files):
    staged = staged_paths(repo)
    expected = set(files)
    if staged != expected:
        unexpected = sorted(staged - expected)
        missing = sorted(expected - staged)
        raise MrError(f"Staged paths do not exactly match --files; unexpected={unexpected!r}, missing={missing!r}")


def current_branch(repo):
    return run(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo).stdout.strip()


def execute(args):
    repo = Path(args.repo).resolve()
    defaults = load_defaults(Path(args.defaults).resolve() if args.defaults else DEFAULTS_PATH)
    prefs = load_mr_preferences()
    remote = args.remote or defaults.get("REMOTE", "origin")
    base_version = args.base_version or defaults.get("BASE_VERSION", "v6.1.0.31")
    target = args.target_branch or prefs.get("targetBranch") or defaults.get("TARGET_BRANCH", f"{base_version}_release")
    base_ref = args.base_ref or defaults.get("WORKTREE_BASE_REF", f"{remote}/{target}")
    iteration = args.iteration_version or defaults.get("ITERATION_VERSION", "auto")
    fallback = args.fallback_iteration or defaults.get("FALLBACK_ITERATION_VERSION", "v1.1.x")
    assignee = normalize_assignee(args.assignee or prefs.get("assignee") or defaults.get("ASSIGNEE", "cx"))
    hostname = args.hostname or defaults.get("GITLAB_HOST", "")
    api_protocol = defaults.get("API_PROTOCOL", "https")
    milestone_mode = defaults.get("MILESTONE_MATCH_MODE", "branch_then_message")
    milestone_required = defaults.get("MILESTONE_REQUIRED", "false").lower() == "true"
    screenshot_paths = args.screenshot or []
    if not hostname:
        raise MrError("GitLab host is not configured; set GITLAB_HOST in defaults.env or pass --hostname")

    ensure_repo(repo)
    ensure_glab_auth(repo, hostname, api_protocol)
    ensure_remote_and_target(repo, remote, target)
    ensure_base_ref(repo, remote, base_ref)
    ensure_index_integrity(repo)

    files = normalize_files(repo, args.files)
    subject, body, message = parse_message(args.message_file)
    ensure_screenshot_files(screenshot_paths)
    title_info = parse_title(subject)
    related = validate_message(subject, body, files, screenshot_paths)
    labels = resolve_labels(title_info, args.label or prefs.get("labels") or None)
    ensure_only_files_changed(repo, files)
    ensure_files_changed(repo, files)

    if iteration == "auto":
        iteration = infer_iteration(repo, remote, base_version, fallback)

    suffix = normalize_branch_suffix(subject)
    base_branch = f"{iteration}/{base_version}_{suffix}"
    branch = args.branch or unique_branch(repo, remote, base_branch)
    project = encoded_project_path(repo, remote)
    ensure_labels_exist(repo, hostname, project, labels)
    milestone, milestone_reason = resolve_milestone(
        repo, hostname, project, args.milestone or prefs.get("milestone") or None, branch, subject, body, milestone_mode
    )
    if milestone_required and milestone is None:
        raise MrError("No milestone matched and MILESTONE_REQUIRED=true")

    summary = {
        "repo": str(repo),
        "current_branch": current_branch(repo),
        "remote": remote,
        "base_ref": base_ref,
        "target_branch": target,
        "source_branch": branch,
        "assignee": f"@{assignee}" if assignee else "",
        "test_related": str(related).lower(),
        "mr_title": subject,
        "labels": ",".join(labels) or "none",
        "milestone": milestone.get("title") if milestone else "none",
        "milestone_reason": milestone_reason,
        "mr_description": body,
        "screenshots": "\n".join(f"  - {path}" for path in screenshot_paths) or "  - none",
        "files": "\n".join(f"  - {path}" for path in files),
    }
    print("Planned glab MR submission:")
    for key in ("repo", "current_branch", "remote", "base_ref", "target_branch", "source_branch", "assignee", "test_related", "mr_title", "labels", "milestone", "milestone_reason"):
        print(f"{key}: {summary[key]}")
    print("mr_description:")
    print(body)
    print("screenshots:")
    print(summary["screenshots"])
    print("files:")
    print(summary["files"])

    if args.dry_run:
        print("dry_run: true")
        return

    run(["git", "switch", "-c", branch], repo, capture=False)
    run(["git", "add", "--"] + files, repo, capture=False)
    ensure_staged_files_exact(repo, files)

    screenshot_links = upload_screenshots(repo, remote, hostname, screenshot_paths) if screenshot_paths else []
    body = append_screenshot_links(body, screenshot_links)
    validate_message(subject, body, files, screenshot_paths)
    final_message = f"{subject}\n\n{body}\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, prefix="glab-mr-message-", suffix=".txt") as message_file:
        message_file.write(final_message)
        commit_message_path = message_file.name
    try:
        run(["git", "commit", "-F", commit_message_path], repo, capture=False)
    finally:
        try:
            Path(commit_message_path).unlink()
        except FileNotFoundError:
            pass
    run(["git", "push", "-u", remote, branch], repo, capture=False)
    mr_url = create_mr(repo, remote, hostname, branch, target, subject, body, assignee, labels, milestone)
    verify_mr(repo, remote, hostname, mr_url, branch, target, subject, body, files, labels, milestone)


def main():
    parser = argparse.ArgumentParser(description="Create a compliant glab merge request.")
    parser.add_argument("--repo", default=".", help="Git repository path")
    parser.add_argument("--message-file", required=True, help="Commit message file")
    parser.add_argument("--files", nargs="+", required=True, help="Exact files to stage and commit")
    parser.add_argument("--defaults", help="Optional defaults.env path")
    parser.add_argument("--remote", help="Override remote")
    parser.add_argument("--base-version", help="Override base version, e.g. v6.1.0.31")
    parser.add_argument("--target-branch", help="Override MR target branch")
    parser.add_argument("--base-ref", help="Release worktree base ref that must be an ancestor of HEAD")
    parser.add_argument("--iteration-version", help="Override iteration version or auto")
    parser.add_argument("--fallback-iteration", help="Override fallback iteration")
    parser.add_argument("--assignee", help="MR assignee username, defaults to cx")
    parser.add_argument("--branch", help="Explicit source branch")
    parser.add_argument("--hostname", help="GitLab hostname, defaults to GITLAB_HOST in defaults.env")
    parser.add_argument("--label", action="append", help="Additional label(s), comma-separated; repeat for multiple values")
    parser.add_argument("--milestone", help="Explicit active milestone title, global ID, or IID; otherwise infer non-blockingly")
    parser.add_argument("--screenshot", action="append", help="Local test-result screenshot to upload; repeat for multiple images")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print actions without mutating git or GitLab")
    args = parser.parse_args()
    try:
        execute(args)
    except MrError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
