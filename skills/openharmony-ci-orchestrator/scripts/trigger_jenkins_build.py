#!/usr/bin/env python3
"""Validate and enqueue one Jenkins parameterized build.

The script is intentionally limited to phase 1 of the OpenHarmony CI flow.
It does not poll a build, download artifacts, change a device, or update GitLab.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin
from urllib.request import (HTTPCookieProcessor, HTTPRedirectHandler, Request,
                            build_opener)


DEFAULT_BASE_URL = "http://192.168.13.121:8080"
DEFAULT_JOB = "OpenHarmony-V6.1-RockChip"
REQUIRED_PARAMETERS = ("FIRMWARE_BRANCH", "BUILD_MODE", "FIRMWARE_TYPE")
BRANCH_PATTERN = re.compile(r"^[A-Za-z0-9._/-]+$")
SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{7,64}$")
PARAMETER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
REDIRECT_CODES = frozenset((301, 302, 303, 307, 308))


class JenkinsError(RuntimeError):
    """A user-actionable Jenkins request or configuration error."""


class NoRedirectHandler(HTTPRedirectHandler):
    """Expose Jenkins' queue Location header instead of following it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def build_http_opener():
    return build_opener(HTTPCookieProcessor(CookieJar()), NoRedirectHandler)


def credentials():
    user = os.environ.get("JENKINS_USER")
    token = os.environ.get("JENKINS_API_TOKEN")
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
    tree = "name,buildable,property[parameterDefinitions[name]]"
    url = f"{job_url}/api/json?{urlencode({'tree': tree})}"
    request = Request(url, headers={**headers, "Accept": "application/json"})
    payload = request_json(opener, request, timeout)
    if payload.get("name") is None:
        raise JenkinsError("Jenkins response does not describe a job")
    if payload.get("buildable") is not True:
        raise JenkinsError(f"Jenkins job is not buildable: {job_url}")
    names = set()
    for prop in payload.get("property", []):
        for definition in prop.get("parameterDefinitions", []):
            name = definition.get("name")
            if isinstance(name, str):
                names.add(name)
    missing = [name for name in REQUIRED_PARAMETERS if name not in names]
    if missing:
        raise JenkinsError(
            "Jenkins job is missing required parameters: " + ", ".join(missing)
        )
    return {"name": payload.get("name"), "buildable": True,
            "parameters": sorted(names)}


def crumb_headers(opener, base_url, headers, timeout):
    url = f"{base_url.rstrip('/')}/crumbIssuer/api/json"
    request = Request(url, headers={**headers, "Accept": "application/json"})
    try:
        with opener.open(request, timeout=timeout) as response:
            payload = json.loads(response_body(response, 32 * 1024))
    except HTTPError as error:
        if error.code == 404:
            return {}
        body = response_body(error)
        raise JenkinsError(
            f"Jenkins crumb request failed with HTTP {error.code}: {body[:300]}"
        ) from error
    except (URLError, json.JSONDecodeError) as error:
        raise JenkinsError(f"Unable to obtain Jenkins crumb: {error}") from error
    field = payload.get("crumbRequestField")
    crumb = payload.get("crumb")
    if not field or not crumb:
        raise JenkinsError("Jenkins crumb response is incomplete")
    return {field: crumb}


def trigger_build(opener, trigger_url, headers, params, timeout):
    data = urlencode(params).encode("utf-8")
    request = Request(
        trigger_url,
        data=data,
        method="POST",
        headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            status = response.status
            location = response.headers.get("Location")
            body = response_body(response)
    except HTTPError as error:
        if error.code not in REDIRECT_CODES:
            body = response_body(error)
            raise JenkinsError(
                f"Jenkins build trigger failed with HTTP {error.code}: {body[:300]}"
            ) from error
        status = error.code
        location = error.headers.get("Location") if error.headers else None
        body = response_body(error)
    except URLError as error:
        raise JenkinsError(f"Jenkins build trigger connection failed: {error.reason}") from error
    if status not in REDIRECT_CODES and status not in (200, 201, 202):
        raise JenkinsError(f"Unexpected Jenkins trigger response: HTTP {status}")
    return status, location, body


def resolve_queue(opener, queue_url, headers, timeout, wait_seconds):
    if not queue_url or wait_seconds <= 0:
        return {}
    deadline = time.monotonic() + wait_seconds
    api_url = f"{queue_url.rstrip('/')}/api/json"
    while time.monotonic() < deadline:
        request = Request(api_url, headers={**headers, "Accept": "application/json"})
        payload = request_json(opener, request, timeout)
        if payload.get("cancelled"):
            raise JenkinsError("Jenkins cancelled the queued build")
        executable = payload.get("executable") or {}
        number = executable.get("number")
        if number is not None:
            return {"build_number": number, "build_url": executable.get("url")}
        time.sleep(min(5, max(1, deadline - time.monotonic())))
    return {"queue_pending": True}


def parse_parameter(item):
    if "=" not in item:
        raise JenkinsError(f"Parameter must use NAME=VALUE: {item!r}")
    name, value = item.split("=", 1)
    if not PARAMETER_PATTERN.fullmatch(name):
        raise JenkinsError(f"Invalid Jenkins parameter name: {name!r}")
    if name in REQUIRED_PARAMETERS:
        raise JenkinsError(
            f"Use the dedicated option for Jenkins parameter {name}, not --parameter"
        )
    return name, value


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("JENKINS_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--job", default=os.environ.get("JENKINS_JOB", DEFAULT_JOB))
    parser.add_argument("--branch", required=True)
    parser.add_argument("--source-sha")
    parser.add_argument("--mr-iid")
    parser.add_argument("--build-mode", choices=("INCREMENTAL", "FULL"), default="INCREMENTAL")
    parser.add_argument("--firmware-type", default="XTS")
    parser.add_argument("--parameter", action="append", default=[], metavar="NAME=VALUE")
    parser.add_argument("--verify-job", action="store_true", help="Perform a read-only job metadata check")
    parser.add_argument("--dry-run", action="store_true", help="Print the request without POSTing to Jenkins")
    parser.add_argument("--allow-anonymous", action="store_true", help="Permit a real POST without Basic auth on an intentionally anonymous Jenkins")
    parser.add_argument("--wait-seconds", type=int, default=0, metavar="N", help="Optionally wait for a build number after enqueue")
    parser.add_argument("--timeout-seconds", type=int, default=20, metavar="N")
    return parser.parse_args()


def main():
    args = parse_args()
    if not BRANCH_PATTERN.fullmatch(args.branch) or ".." in args.branch:
        raise JenkinsError(f"Invalid branch name: {args.branch!r}")
    if args.source_sha and not SHA_PATTERN.fullmatch(args.source_sha):
        raise JenkinsError(f"Invalid source SHA: {args.source_sha!r}")
    if args.wait_seconds < 0 or args.timeout_seconds <= 0:
        raise JenkinsError("wait-seconds must be non-negative and timeout-seconds positive")

    params = {
        "FIRMWARE_BRANCH": args.branch,
        "BUILD_MODE": args.build_mode,
        "FIRMWARE_TYPE": args.firmware_type,
    }
    for item in args.parameter:
        name, value = parse_parameter(item)
        params[name] = value

    opener = build_http_opener()
    headers = {"User-Agent": "openharmony-ci-orchestrator/phase1"}
    auth = credentials()
    headers.update(auth)
    job_url = make_url(args.base_url, args.job)
    metadata = None
    if args.verify_job or not args.dry_run:
        metadata = job_metadata(opener, job_url, headers, args.timeout_seconds)

    result = {
        "phase": 1,
        "action": "dry-run" if args.dry_run else "trigger",
        "job": args.job,
        "job_url": job_url,
        "source_branch": args.branch,
        "source_sha": args.source_sha,
        "mr_iid": args.mr_iid,
        "parameters": params,
    }
    if metadata is not None:
        result["job_metadata"] = metadata
    if args.dry_run:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    if not auth and not args.allow_anonymous:
        raise JenkinsError(
            "A real trigger requires JENKINS_USER and JENKINS_API_TOKEN; use --allow-anonymous only for an intentionally anonymous Jenkins"
        )

    headers.update(crumb_headers(opener, args.base_url, headers, args.timeout_seconds))
    trigger_url = f"{job_url}/buildWithParameters"
    status, location, body = trigger_build(
        opener, trigger_url, headers, params, args.timeout_seconds
    )
    queue_url = urljoin(trigger_url, location) if location else None
    result.update({
        "http_status": status,
        "queue_url": queue_url,
        "response_excerpt": body[:300] if body else None,
    })
    result.update(resolve_queue(
        opener, queue_url, headers, args.timeout_seconds, args.wait_seconds
    ))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except JenkinsError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(2)
