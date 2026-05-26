#!/usr/bin/env python3
"""
Fetch a URL with reproducible headers and ranged reads, emitting hashes plus a JSON report.
Uses curl (via subprocess) to preserve exact transfer details and Accept-Encoding control.
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
from typing import Dict, List, Optional


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reproducible fetch with hashing and header capture.")
    parser.add_argument("--url", required=True, help="Target URL to fetch.")
    parser.add_argument("--timeout", type=float, default=20, help="Max seconds per request (default: 20).")
    parser.add_argument(
        "--range-head",
        default="0-4095",
        help="Byte range for the head request, e.g. 0-4095 (default).",
    )
    parser.add_argument(
        "--range-tail-bytes",
        type=int,
        default=4096,
        help="Number of bytes from the end for the tail request (default: 4096).",
    )
    parser.add_argument(
        "--accept-encoding",
        default="identity",
        help="Accept-Encoding header value (default: identity for stable lengths).",
    )
    parser.add_argument(
        "--compressed",
        action="store_true",
        help="Allow gzip/brotli and ask curl to decompress (overrides default identity).",
    )
    parser.add_argument("--out", help="Optional path to write the JSON report.")
    return parser.parse_args()


def parse_header_blocks(raw_headers: bytes) -> List[Dict[str, object]]:
    """Split header sections (for redirects) into structured blocks."""
    text = raw_headers.decode(errors="replace")
    # curl writes headers with CRLF; split on blank lines.
    sections = [block for block in text.split("\r\n\r\n") if block.strip()]
    blocks: List[Dict[str, object]] = []
    for section in sections:
        lines = [ln for ln in section.split("\r\n") if ln]
        if not lines:
            continue
        status_line = lines[0]
        headers = []
        for line in lines[1:]:
            if ":" in line:
                name, value = line.split(":", 1)
                headers.append({"name": name.strip(), "value": value.strip()})
        blocks.append({"status_line": status_line, "headers": headers})
    return blocks


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


def run_curl_fetch(
    url: str,
    accept_encoding: str,
    timeout: float,
    range_header: Optional[str] = None,
    compressed: bool = False,
) -> Dict[str, object]:
    headers_file = tempfile.NamedTemporaryFile(delete=False)
    body_file = tempfile.NamedTemporaryFile(delete=False)
    headers_file.close()
    body_file.close()

    cmd = [
        "curl",
        "-sSL",
        "-D",
        headers_file.name,
        "-o",
        body_file.name,
        "--max-time",
        str(timeout),
        "--write-out",
        "%{http_code}\n%{url_effective}\n",
    ]

    # Default to identity for stable content length; compressed toggles decompress.
    cmd.extend(["-H", f"Accept-Encoding: {accept_encoding}"])
    if range_header:
        cmd.extend(["-H", f"Range: bytes={range_header}"])
    if compressed:
        cmd.append("--compressed")

    cmd.append(url)

    result = subprocess.run(cmd, capture_output=True)

    try:
        with open(body_file.name, "rb") as fh:
            body = fh.read()
        with open(headers_file.name, "rb") as fh:
            raw_headers = fh.read()
    finally:
        os.unlink(body_file.name)
        os.unlink(headers_file.name)

    info = parse_write_out(result.stdout)
    hash_hex = hashlib.sha256(body).hexdigest()
    header_blocks = parse_header_blocks(raw_headers)

    return {
        "status_code": info["http_code"],
        "final_url": info["url_effective"],
        "headers": header_blocks,
        "sha256": hash_hex,
        "bytes": len(body),
        "return_code": result.returncode,
        "stderr": result.stderr.decode(errors="replace"),
    }


def build_report(args: argparse.Namespace) -> Dict[str, object]:
    accept_encoding = args.accept_encoding
    if args.compressed and args.accept_encoding == "identity":
        accept_encoding = "gzip, br"

    full = run_curl_fetch(
        url=args.url,
        accept_encoding=accept_encoding,
        timeout=args.timeout,
        compressed=args.compressed,
    )
    head = run_curl_fetch(
        url=args.url,
        accept_encoding=accept_encoding,
        timeout=args.timeout,
        range_header=args.range_head,
        compressed=args.compressed,
    )
    tail_range = f"-{args.range_tail_bytes}" if args.range_tail_bytes > 0 else "0-0"
    tail = run_curl_fetch(
        url=args.url,
        accept_encoding=accept_encoding,
        timeout=args.timeout,
        range_header=tail_range,
        compressed=args.compressed,
    )

    return {
        "input": {
            "url": args.url,
            "timeout": args.timeout,
            "accept_encoding": accept_encoding,
            "compressed": args.compressed,
            "range_head": args.range_head,
            "range_tail_bytes": args.range_tail_bytes,
        },
        "full": full,
        "range_head": {**head, "range": f"bytes={args.range_head}"},
        "range_tail": {**tail, "range": f"bytes={tail_range}"},
    }


def print_summary(report: Dict[str, object]) -> None:
    full = report["full"]
    head = report["range_head"]
    tail = report["range_tail"]

    def line(label: str, item: Dict[str, object]) -> str:
        status = item.get("status_code")
        status_display = status if status is not None else "?"
        url = item.get("final_url") or report["input"]["url"]
        return (
            f"{label}: status={status_display} url={url} "
            f"bytes={item.get('bytes')} sha256={item.get('sha256')}"
        )

    print(line("Full fetch", full))
    print(line("Head range", head))
    print(line("Tail range", tail))

    errors = []
    for label, item in (("full", full), ("range_head", head), ("range_tail", tail)):
        if item.get("return_code", 0) != 0:
            errors.append(f"{label} curl exit {item.get('return_code')}: {item.get('stderr', '').strip()}")
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
    print(json_output)

    if args.out:
        try:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(json_output)
            print(f"Wrote JSON report to {args.out}")
        except OSError as exc:
            sys.stderr.write(f"could not write report to {args.out}: {exc}\n")
            return 1

    # Return non-zero if any curl call failed; otherwise success.
    for item in (report["full"], report["range_head"], report["range_tail"]):
        if item.get("return_code", 0) != 0:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
