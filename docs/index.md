# Proof Garden docs

Proof Garden is for reproducible, scriptable web-debugging micro-proofs. Start with the fetch tool, then expand with new probes.

- Run the fetcher: `python tools/proof_fetch.py --url https://example.com`
- Tool source: [`tools/proof_fetch.py`](../tools/proof_fetch.py)

Planned micro-proofs to add:
- Cache/state divergence proof: compare hashes before/after cookie or cache toggles.
- Redirect stability proof: record chains and hash each hop for regressions.
- Compression/encoding proof: verify gzip/brotli differences without changing content length expectations.
