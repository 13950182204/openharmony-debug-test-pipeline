#!/usr/bin/env python3
"""Read-only Jenkins API client for the OpenHarmony MR-gate flow.

平台 CI 契约（2026-10 起）：MR 提交后由 GitLab 流水线自动驱动 Jenkins 门禁构建，
本插件**不再触发任何 Jenkins 构建**（原 phase-1 的入队实现与 queue 解析已整体删除，
代码中不再出现任何构建触发端点）。这里只保留只读能力：作业元数据、构建列表、
构建参数、归档产物清单与 consoleText。

本模块被 ci_orchestrator.py 与 phase3_runner.py 共享（错误类型、凭据、HTTP opener、
URL 构造、JSON 读取）。
"""

from __future__ import annotations

import base64
import json
import os
import re
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import (HTTPCookieProcessor, HTTPRedirectHandler, Request,
                            build_opener)

from orchestrator_config import configured_value

# 平台门禁主机（GitLab Runner bridge 驱动 OpenHarmony-V6.1-MR-Gate 的那一台）。
DEFAULT_BASE_URL = "http://192.168.97.54:8080"
SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{7,64}$")
DEFAULT_BUILD_TREE = "number,url,result,building,actions[parameters[name,value]]"


class JenkinsError(RuntimeError):
    """A user-actionable Jenkins request or configuration error."""


class NoRedirectHandler(HTTPRedirectHandler):
    """Expose Jenkins' redirect/Location header instead of following it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def build_http_opener():
    return build_opener(HTTPCookieProcessor(CookieJar()), NoRedirectHandler)


def credentials():
    """Return the Basic auth header for Jenkins reads, or {} when unset.

    只读读取（构建参数 / 产物下载 / consoleText）在启用鉴权的 Jenkins 上同样需要凭据；
    两个变量必须成对提供，只给一个会得到 Jenkins 的 401 而不是半可用状态。
    """
    user = os.environ.get("JENKINS_USER") or configured_value("JENKINS_USER")
    token = os.environ.get("JENKINS_API_TOKEN") or configured_value("JENKINS_API_TOKEN")
    if bool(user) != bool(token):
        raise JenkinsError(
            "JENKINS_USER and JENKINS_API_TOKEN must be set together"
        )
    if not user:
        return {}
    value = base64.b64encode(f"{user}:{token}".encode()).decode()
    return {"Authorization": f"Basic {value}"}


def make_url(base_url, job, suffix=""):
    parts = [part for part in job.strip("/").split("/") if part]
    if not parts:
        raise JenkinsError("Jenkins job name must not be empty")
    path = "".join(f"/job/{quote(part, safe='')}" for part in parts)
    return f"{base_url.rstrip('/')}{path}{suffix}"


def read_error_hint(error: Exception) -> str:
    """Add an actionable remedy to Jenkins auth/permission failures.

    对本插件最重要的一种现场：JENKINS_BASE_URL 指向新主机，而 jenkins.env 里还是旧主机的
    账号（或根本没配），表现是 401/403，而不是"作业不存在"。
    """
    text = str(error)
    if "HTTP 401" in text or "HTTP 403" in text:
        return (
            f"{text} -- Jenkins 只读访问被拒：请在 "
            "~/.config/openharmony-ci-orchestrator/jenkins.env 里把 JENKINS_BASE_URL 指向平台门禁主机"
            "并配对该主机有效的 JENKINS_USER/JENKINS_API_TOKEN（只读即可；两者必须成对）"
        )
    return text


def response_body(response, limit=4096):
    try:
        return response.read(limit).decode("utf-8", errors="replace")
    except (OSError, UnicodeError):
        return ""


def request_json(opener, request, timeout):
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response_body(response, 1024 * 1024)
    except HTTPError as error:
        body = response_body(error)
        raise JenkinsError(
            f"Jenkins GET failed with HTTP {error.code}: {body[:300]}"
        ) from error
    except URLError as error:
        raise JenkinsError(f"Jenkins connection failed: {error.reason}") from error
    try:
        return json.loads(body)
    except json.JSONDecodeError as error:
        raise JenkinsError("Jenkins returned invalid JSON") from error


def job_metadata(opener, job_url, headers, timeout):
    """Read one job's identity, buildability and declared parameter names."""
    tree = "name,buildable,property[parameterDefinitions[name]]"
    url = f"{job_url.rstrip('/')}/api/json?{urlencode({'tree': tree})}"
    request = Request(url, headers={**headers, "Accept": "application/json"})
    payload = request_json(opener, request, timeout)
    if payload.get("name") is None:
        raise JenkinsError("Jenkins response does not describe a job")
    names = set()
    for prop in payload.get("property", []):
        for definition in prop.get("parameterDefinitions", []):
            name = definition.get("name")
            if isinstance(name, str):
                names.add(name)
    return {"name": payload.get("name"), "buildable": payload.get("buildable") is True,
            "parameters": sorted(names)}


def job_builds(opener, job_url, headers, timeout, limit=20, tree=DEFAULT_BUILD_TREE):
    """Read the newest builds of one job (newest first) without triggering anything."""
    if limit < 1:
        raise JenkinsError("Build lookup limit must be positive")
    url = f"{job_url.rstrip('/')}/api/json?{urlencode({'tree': f'builds[{tree}]'})}"
    request = Request(url, headers={**headers, "Accept": "application/json"})
    payload = request_json(opener, request, timeout)
    builds = payload.get("builds")
    if not isinstance(builds, list):
        raise JenkinsError("Jenkins job response has no build list")
    return [item for item in builds[:limit] if isinstance(item, dict)]


def build_metadata(opener, build_url, headers, timeout, tree=DEFAULT_BUILD_TREE):
    request = Request(
        f"{build_url.rstrip('/')}/api/json?{urlencode({'tree': tree})}", headers=headers
    )
    return request_json(opener, request, timeout)


def build_artifacts(opener, build_url, headers, timeout):
    metadata = build_metadata(
        opener, build_url, headers, timeout, tree="artifacts[relativePath,fileName]"
    )
    artifacts = metadata.get("artifacts")
    if not isinstance(artifacts, list):
        raise JenkinsError("Jenkins build response has no artifact list")
    return [item for item in artifacts if isinstance(item, dict)]


def build_console_text(opener, build_url, headers, timeout=120, limit=64 * 1024 * 1024):
    """Read one build's consoleText as a utf-8 decoded string (bounded by `limit`)."""
    request = Request(
        f"{build_url.rstrip('/')}/consoleText",
        headers={**headers, "Accept": "text/plain"},
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.read(limit).decode("utf-8", "replace")
    except HTTPError as error:
        raise JenkinsError(
            f"Jenkins consoleText failed with HTTP {error.code}"
        ) from error
    except URLError as error:
        raise JenkinsError(f"Jenkins connection failed: {error.reason}") from error
