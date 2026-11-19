# AI-2027 Cloudflare Python Worker ($0 Tier)

Edge Python Worker running on Cloudflare's Pyodide runtime (`python_workers` compatibility flag).

## Endpoints

- `GET /health` — runtime readiness (`cost_usd: 0.0`)
- `GET /v1/protocol` — 10-rule protocol summary
- `POST /v1/route` — Free-First AI routing (`gemini` -> `groq` -> `openrouter` -> `deterministic`) with `asyncio.timeout`, retries on `429`/`5xx`, and `X-Trace-Id` tracing

## Optional Cloudflare Secrets (never committed)

```bash
npx wrangler secret put GEMINI_API_KEY
npx wrangler secret put GROQ_API_KEY
npx wrangler secret put OPENROUTER_API_KEY
npx wrangler secret put WORKER_AUTH_TOKEN   # optional Bearer protection for POST /v1/route
```

## Verification status

- Handler logic (`handle_worker_request`) is contract-tested locally in `python-2027/tests/test_python_worker.py` using simulated async `fetch` transports.
- Live deployment (`npx wrangler deploy`) requires a Cloudflare account and has **not** been executed from this offline sandbox.
