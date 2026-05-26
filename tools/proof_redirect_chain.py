#!/usr/bin/env python3
"""
Capture an explicit redirect chain hop-by-hop using curl header-only requests.
Useful for proving redirect behavior without fetching bodies.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, Optional
from urllib.parse import urljoin


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Trace redirect hops for a URL.")
    parser.add_argument("--url", required=True, help="Starting URL.")
    parser.add_argument("--timeout", type=float, default=20, help="Max seconds per request (default: 20).")
    parser.add_argument(
        "--connect-timeout",
        type=float,
        default=5,
        help="Max seconds for TCP connect (default: 5).",
    )
    parser.add_argument(
        "--max-hops",
        type=int,
        default=10,
        help="Maximum redirect hops to follow (default: 10).",
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


def parse_status_from_headers(header_text: str) -> Optional[int]:
    for line in header_text.splitlines():
        line = line.strip()
        if line.startswith("HTTP/"):
            parts = line.split()
            if len(parts) > 1:
                try:
                    return int(parts[1])
                except ValueError:
                    return None
            break
    return None


def run_head_request(
    url: str,
    timeout: float,
    connect_timeout: float,
    user_agent: Optional[str],
    insecure: bool,
) -> Dict[str, object]:
    headers_file = tempfile.NamedTemporaryFile(delete=False)
    headers_file.close()

    cmd = [
        "curl",
        "-sS",
        "-D",
        headers_file.name,
        "-o",
        "/dev/null",
        "--max-time",
        str(timeout),
        "--connect-timeout",
        str(connect_timeout),
        "--write-out",
        "%{http_code}\n%{url_effective}\n",
        url,
    ]
    if user_agent:
        cmd.extend(["-A", user_agent])
    if insecure:
        cmd.append("-k")

    result = subprocess.run(cmd, capture_output=True)

    try:
        raw_headers = read_file_bytes(headers_file.name)
    finally:
        os.unlink(headers_file.name)

    write_out = parse_write_out(result.stdout)
    header_text = raw_headers.decode(errors="replace")
    status_code = parse_status_from_headers(header_text) or write_out.get("http_code")

    location = None
    for line in header_text.splitlines():
        if ":" in line:
            name, value = line.split(":", 1)
            if name.strip().lower() == "location":
                location = value.strip()
                break

    return {
        "url": url,
        "status_code": status_code,
        "url_effective": write_out.get("url_effective"),
        "location": location,
        "response_headers_raw": header_text,
        "curl_exit": result.returncode,
        "curl_stderr": result.stderr.decode(errors="replace"),
    }


def build_report(args: argparse.Namespace) -> Dict[str, object]:
    hops = []
    current_url = args.url
    final_status: Optional[int] = None

    for idx in range(args.max_hops):
        hop = run_head_request(
            url=current_url,
            timeout=args.timeout,
            connect_timeout=args.connect_timeout,
            user_agent=args.user_agent,
            insecure=args.insecure,
        )
        hop["index"] = idx

        location = hop.get("location")
        status = hop.get("status_code")
        next_url = None

        if status and 300 <= status < 400 and location:
            next_url = urljoin(current_url, location)
            current_url = next_url
        else:
            final_status = status
            hops.append({**hop, "resolved_next_url": None})
            break

        hops.append({**hop, "resolved_next_url": next_url})

    if final_status is None and hops:
        final_status = hops[-1].get("status_code")

    return {
        "input": {
            "url": args.url,
            "timeout": args.timeout,
            "connect_timeout": args.connect_timeout,
            "max_hops": args.max_hops,
            "user_agent": args.user_agent,
            "insecure": args.insecure,
        },
        "hops": hops,
        "final_url": hops[-1]["url_effective"] or hops[-1]["url"] if hops else args.url,
        "final_status": final_status,
    }


def print_summary(report: Dict[str, object]) -> None:
    hops = report["hops"]
    final_url = report["final_url"]
    final_status = report["final_status"]
    print(f"Hops: {len(hops)} final_status={final_status} final_url={final_url}")
    if hops:
        first = hops[0]
        print(f"First: status={first.get('status_code')} url={first.get('url_effective') or first.get('url')}")

    warnings = []
    for hop in hops:
        if hop.get("curl_exit"):
            warnings.append(
                f"hop {hop.get('index')} curl exit {hop.get('curl_exit')}: {hop.get('curl_stderr', '').strip()}"
            )
    if warnings:
        print("Warnings:")
        for w in warnings:
            print(f"- {w}")


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

    for hop in report["hops"]:
        if hop.get("curl_exit"):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
