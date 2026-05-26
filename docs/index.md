# Proof Garden docs

Proof Garden is for reproducible, scriptable web-debugging micro-proofs. Start with the fetch tool, then expand with new probes.

- Run the fetcher: `python tools/proof_fetch.py --url https://example.com`
- Tool source: [`tools/proof_fetch.py`](../tools/proof_fetch.py)

- Compare identity vs compressed bytes: `python tools/proof_compression_diff.py --url https://example.com --max-bytes 4096`
- Tool source: [`tools/proof_compression_diff.py`](../tools/proof_compression_diff.py)

- Trace redirects without downloading bodies: `python tools/proof_redirect_chain.py --url https://example.com --max-hops 5`
- Tool source: [`tools/proof_redirect_chain.py`](../tools/proof_redirect_chain.py)

Planned micro-proofs to add:
- Cache/state divergence proof: compare hashes before/after cookie or cache toggles.
- Redirect+content proof: record chains and also hash final bodies (or hop bodies) for regressions.
- Variant sampling proof: compare identity responses across regions/agents with consistent capture settings.

Last updated: 2026-05-26
