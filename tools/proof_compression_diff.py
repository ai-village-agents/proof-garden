#!/usr/bin/env python3
"""
Compare identity vs compressed transfers for a URL, capturing headers, hashes, and lengths.
Uses curl (via subprocess) only, matching proof-first capture style.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, Optional


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare identity vs compressed transfers for a URL."
    )
    parser.add_argument("--url", required=True, help="Target URL to fetch.")
    parser.add_argument("--timeout", type=float, default=20, help="Max seconds per request (default: 20).")
    parser.add_argument(
        "--connect-timeout",
        type=float,
        default=5,
        help="Max seconds for TCP connect (default: 5).",
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        help="If set, cap downloaded bytes via Range and curl --max-filesize.",
    )
    parser.add_argument("--out", help="Optional path to write the JSON report.")
    parser.add_argument("--user-agent", help="Override the User-Agent header.")
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Allow insecure TLS (passes -k to curl).",
    )
    return parser.parse_args()


def parse_write_out(write_out: bytes) -> Dict[str, Optional[str]]:
    """Extract http_code and url_effective from curl --write-out output."""
    lines = write_out.decode(errors="replace").splitlines()
    http_code: Optional[int] = None
    url_effective: Optional[str] = None
    if lines:
        try:
            http_code = int(lines[0])
        except ValueError:
            http_code = None
    if len(lines) > 1:
        url_effective = lines[1]
    return {"http_code": http_code, "url_effective": url_effective}


def read_file_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def run_curl(
    url: str,
    headers: Optional[list[str]],
    timeout: float,
    connect_timeout: float,
    allow_compressed: bool,
    max_bytes: Optional[int],
    user_agent: Optional[str],
    insecure: bool,
) -> Dict[str, object]:
    headers_file = tempfile.NamedTemporaryFile(delete=False)
    body_file = tempfile.NamedTemporaryFile(delete=False)
    headers_file.close()
    body_file.close()

    cmd = [
        "curl",
        "-sS",
        "-D",
        headers_file.name,
        "-o",
        body_file.name,
        "--max-time",
        str(timeout),
        "--connect-timeout",
        str(connect_timeout),
        "--write-out",
        "%{http_code}\n%{url_effective}\n",
    ]

    if headers:
        for hdr in headers:
            cmd.extend(["-H", hdr])
    if allow_compressed:
        cmd.extend(["--compressed", "--raw"])
    if max_bytes and max_bytes > 0:
        cmd.extend(["--max-filesize", str(max_bytes), "-H", f"Range: bytes=0-{max_bytes - 1}"])
    if user_agent:
        cmd.extend(["-A", user_agent])
    if insecure:
        cmd.append("-k")

    cmd.append(url)

    result = subprocess.run(cmd, capture_output=True)

    try:
        body = read_file_bytes(body_file.name)
        raw_headers = read_file_bytes(headers_file.name)
    finally:
        os.unlink(body_file.name)
        os.unlink(headers_file.name)

    write_out = parse_write_out(result.stdout)
    body_hash = hashlib.sha256(body).hexdigest()
    content_encoding = None
    for line in raw_headers.decode(errors="replace").splitlines():
        if ":" in line:
            name, value = line.split(":", 1)
            if name.strip().lower() == "content-encoding":
                content_encoding = value.strip()
                break

    truncated = bool(max_bytes and len(body) >= max_bytes)

    return {
        "url_requested": url,
        "url_effective": write_out.get("url_effective"),
        "http_code": write_out.get("http_code"),
        "response_headers": raw_headers.decode(errors="replace"),
        "body_sha256": body_hash,
        "body_bytes_len": len(body),
        "curl_exit": result.returncode,
        "curl_stderr": result.stderr.decode(errors="replace"),
        "content_encoding": content_encoding,
        "truncated": truncated,
    }


def build_report(args: argparse.Namespace) -> Dict[str, object]:
    identity = run_curl(
        url=args.url,
        headers=["Accept-Encoding: identity"],
        timeout=args.timeout,
        connect_timeout=args.connect_timeout,
        allow_compressed=False,
        max_bytes=args.max_bytes,
        user_agent=args.user_agent,
        insecure=args.insecure,
    )
    compressed = run_curl(
        url=args.url,
        headers=None,
        timeout=args.timeout,
        connect_timeout=args.connect_timeout,
        allow_compressed=True,
        max_bytes=args.max_bytes,
        user_agent=args.user_agent,
        insecure=args.insecure,
    )

    return {
        "input": {
            "url": args.url,
            "timeout": args.timeout,
            "connect_timeout": args.connect_timeout,
            "max_bytes": args.max_bytes,
            "user_agent": args.user_agent,
            "insecure": args.insecure,
        },
        "identity": identity,
        "compressed": compressed,
    }


def print_summary(report: Dict[str, object]) -> None:
    identity = report["identity"]
    compressed = report["compressed"]

    def fmt(item: Dict[str, object], label: str) -> str:
        status = item.get("http_code")
        status_display = status if status is not None else "?"
        trunc = " truncated" if item.get("truncated") else ""
        encoding = f" enc={item.get('content_encoding')}" if label == "compressed" else ""
        return (
            f"{label}: status={status_display} url={item.get('url_effective') or item.get('url_requested')} "
            f"bytes={item.get('body_bytes_len')} sha256={item.get('body_sha256')}{encoding}{trunc}"
        )

    print(fmt(identity, "identity"))
    print(fmt(compressed, "compressed"))

    errors = []
    for label, item in (("identity", identity), ("compressed", compressed)):
        if item.get("curl_exit"):
            errors.append(f"{label} curl exit {item.get('curl_exit')}: {item.get('curl_stderr', '').strip()}")
    if errors:
        print("Warnings:")
        for err in errors:
            print(f"- {err}")


def main() -> int:
    args = parse_args()

    if not shutil.which("curl"):
        sys.stderr.write("curl is required but not found on PATH.\n")
        return 1

    try:
        report = build_report(args)
    except Exception as exc:  # pragma: no cover - defensive
        sys.stderr.write(f"unexpected error: {exc}\n")
        return 1

    print_summary(report)

    json_output = json.dumps(report, indent=2)
    if args.out:
        try:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(json_output)
        except OSError as exc:
            sys.stderr.write(f"could not write report to {args.out}: {exc}\n")
            return 1
    else:
        print(json_output)

    for item in (report["identity"], report["compressed"]):
        if item.get("curl_exit"):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
