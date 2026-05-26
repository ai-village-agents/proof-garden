#!/usr/bin/env python3
"""
Produce a JSON report that proves what GitHub Pages has deployed for a repo and its live site.
Fetches GitHub Pages metadata via `gh api` and hashes a capped body download of the live URL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from typing import Dict, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


DEFAULT_REPO = "ai-village-agents/proof-garden"
DEFAULT_URL = "https://ai-village-agents.github.io/proof-garden/"
DEFAULT_ACCEPT_ENCODING = "identity"
DEFAULT_TIMEOUT = 20
DEFAULT_MAX_BYTES = 200_000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report GitHub Pages deployment details and live content hash.")
    parser.add_argument("--repo", default=DEFAULT_REPO, help=f"GitHub repo (default: {DEFAULT_REPO}).")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"Live URL to verify (default: {DEFAULT_URL}).")
    parser.add_argument(
        "--accept-encoding",
        default=DEFAULT_ACCEPT_ENCODING,
        help=f"Accept-Encoding header value (default: {DEFAULT_ACCEPT_ENCODING}).",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help=f"Request timeout seconds (default: {DEFAULT_TIMEOUT}).")
    parser.add_argument(
        "--contains",
        help="Optional substring to search for in the fetched body (UTF-8), recorded as a boolean in the report.",
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=DEFAULT_MAX_BYTES,
        help=f"Maximum bytes to read from the body before truncating (default: {DEFAULT_MAX_BYTES}).",
    )
    return parser.parse_args()


def parse_http_response(text: str) -> Tuple[Optional[int], Dict[str, str], str]:
    lines = text.splitlines()
    header_lines = []
    body_lines = []
    in_body = False
    for line in lines:
        if not in_body and line.strip() == "":
            in_body = True
            continue
        if in_body:
            body_lines.append(line)
        else:
            header_lines.append(line)

    status_line = header_lines[0] if header_lines else ""
    status_code = None
    parts = status_line.split()
    if len(parts) >= 2:
        try:
            status_code = int(parts[1])
        except ValueError:
            status_code = None

    headers: Dict[str, str] = {}
    for line in header_lines[1:]:
        if ":" in line:
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()

    body = "\n".join(body_lines)
    return status_code, headers, body


def run_gh_api(path: str, timeout: float) -> Dict[str, object]:
    cmd = ["gh", "api", "--include", path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return {"path": path, "error": "gh executable not found"}
    except subprocess.TimeoutExpired:
        return {"path": path, "error": f"gh api timed out after {timeout} seconds", "timeout": timeout}
    except Exception as exc:
        return {"path": path, "error": str(exc)}

    status_code, headers, body_text = parse_http_response(result.stdout)
    parsed_body: Optional[object] = None
    if body_text.strip():
        try:
            parsed_body = json.loads(body_text)
        except json.JSONDecodeError:
            parsed_body = body_text

    return {
        "path": path,
        "status_code": status_code,
        "headers": headers,
        "body": parsed_body,
        "exit_code": result.returncode,
        "stderr": result.stderr.strip(),
    }


def extract_pages_info(api_result: Dict[str, object]) -> Dict[str, object]:
    status = api_result.get("status_code")
    body = api_result.get("body")
    if status and 200 <= status < 300 and isinstance(body, dict):
        source = body.get("source", {}) or {}
        return {
            "build_type": body.get("build_type"),
            "status": body.get("status"),
            "html_url": body.get("html_url"),
            "source": {
                "branch": source.get("branch"),
                "path": source.get("path"),
            },
            "raw": body,
        }
    return {"error": api_result}


def extract_latest_build(api_result: Dict[str, object]) -> Dict[str, object]:
    status = api_result.get("status_code")
    body = api_result.get("body")
    if status == 404:
        return {"error": api_result}
    if status and 200 <= status < 300 and isinstance(body, dict):
        return {
            "commit": body.get("commit"),
            "status": body.get("status"),
            "created_at": body.get("created_at"),
            "updated_at": body.get("updated_at"),
            "raw": body,
        }
    return {"error": api_result}


def build_request(url: str, accept_encoding: str) -> Request:
    headers = {
        "User-Agent": "proof-pages-deploy/1.0",
        "Accept-Encoding": accept_encoding,
    }
    return Request(url, headers=headers)


def read_body_with_limit(response, max_bytes: int) -> Tuple[bytes, bool]:
    body = b""
    truncated = False
    content_length = response.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                truncated = True
        except ValueError:
            pass

    while len(body) < max_bytes:
        chunk_size = min(8192, max_bytes - len(body))
        chunk = response.read(chunk_size)
        if not chunk:
            break
        body += chunk
        if len(body) >= max_bytes:
            # We hit the limit; assume truncated unless content-length proved otherwise.
            truncated = truncated or bool(response.read(1))
            break

    return body, truncated


def fetch_live_url(url: str, accept_encoding: str, timeout: float, max_bytes: int, contains: Optional[str]) -> Dict[str, object]:
    request = build_request(url, accept_encoding)
    try:
        with urlopen(request, timeout=timeout) as response:
            body, truncated = read_body_with_limit(response, max_bytes)
            sha256_hex = hashlib.sha256(body).hexdigest()
            header_subset = {}
            for key in ["content-type", "content-encoding", "cache-control", "etag", "last-modified"]:
                value = response.headers.get(key)
                if value is not None:
                    header_subset[key] = value

            result = {
                "http_status": response.getcode(),
                "final_url": response.geturl(),
                "headers": header_subset,
                "bytes_read": len(body),
                "truncated": truncated,
                "sha256": sha256_hex,
            }
            if contains is not None:
                contains_bytes = contains.encode("utf-8")
                result["contains"] = contains_bytes in body

            return result
    except HTTPError as exc:
        return {
            "http_status": exc.code,
            "final_url": url,
            "error": str(exc),
        }
    except URLError as exc:
        return {
            "http_status": None,
            "final_url": url,
            "error": str(exc),
        }
    except Exception as exc:  # pragma: no cover - defensive
        return {"http_status": None, "final_url": url, "error": str(exc)}


def validate_https(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("URL must use https scheme for proof capture")


def build_report(args: argparse.Namespace) -> Dict[str, object]:
    fetch_error: Optional[str] = None
    try:
        validate_https(args.url)
    except Exception as exc:
        fetch_error = str(exc)

    pages_result = run_gh_api(f"/repos/{args.repo}/pages", timeout=args.timeout)
    latest_build_result = run_gh_api(f"/repos/{args.repo}/pages/builds/latest", timeout=args.timeout)

    if fetch_error:
        fetch_result = {"http_status": None, "final_url": args.url, "error": fetch_error}
    else:
        fetch_result = fetch_live_url(
            url=args.url,
            accept_encoding=args.accept_encoding,
            timeout=args.timeout,
            max_bytes=args.max_bytes,
            contains=args.contains,
        )

    return {
        "input": {
            "repo": args.repo,
            "url": args.url,
            "accept_encoding": args.accept_encoding,
            "timeout": args.timeout,
            "contains": args.contains,
            "max_bytes": args.max_bytes,
        },
        "github_pages": extract_pages_info(pages_result),
        "latest_build": extract_latest_build(latest_build_result),
        "fetch": fetch_result,
    }


def main() -> int:
    args = parse_args()
    try:
        report = build_report(args)
    except Exception as exc:  # pragma: no cover - defensive
        report = {
            "input": {
                "repo": args.repo,
                "url": args.url,
                "accept_encoding": args.accept_encoding,
                "timeout": args.timeout,
                "contains": args.contains,
                "max_bytes": args.max_bytes,
            },
            "github_pages": {"error": f"unexpected error: {exc}"},
            "latest_build": {"error": f"unexpected error: {exc}"},
            "fetch": {"error": f"unexpected error: {exc}"},
        }

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
