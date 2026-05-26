# Proof Garden

Proof Garden is a tiny toolkit for reproducible web-debugging micro-proofs. Instead of “try a hard reload” guesses, we collect proof-first artifacts (hashes, headers, commands) that others can rerun.

## Principle
- Hard reload isn’t proof. Start with proof-first captures that are scriptable and shareable.

## What’s included
- Tiny CLI tools for deterministic fetches and header/body hashing.
- Writeups for investigation steps that can be rerun.
- Reproducible commands you can copy/paste into incident timelines.

## Quickstart
Run the first tool to fetch a URL with deterministic Accept-Encoding and ranged hashing:

```bash
python tools/proof_fetch.py --url https://example.com
```

Customise the fetch if needed:
- Set a timeout: `python tools/proof_fetch.py --url https://example.com --timeout 10`
- Request compressed content: `python tools/proof_fetch.py --url https://example.com --compressed`
- Adjust ranges: `python tools/proof_fetch.py --url https://example.com --range-head 0-2047 --range-tail-bytes 8192`
- Save a JSON report: `python tools/proof_fetch.py --url https://example.com --out artifacts/example.json`

See `python tools/proof_fetch.py --help` for all options.

## License
MIT. See `LICENSE`.
