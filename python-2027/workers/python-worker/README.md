# AI-2027 Python Worker (`$0` Cloudflare Free Tier · Python 3.14 target)

Runs on Cloudflare Workers (`compatibility_date = "2026-09-08"`, `compatibility_flags = ["python_workers", "python_workers_314"]`) with `requires-python = ">=3.13"` and **zero** bundled third-party runtime packages (`budget guard + Free-First routing`).

## Security & Budget Guarantees

- **Fail-Closed Auth (Default):** `/v1/route` and `/v1/agent/plan` reject requests with HTTP `401` unless `WORKER_AUTH_TOKEN` is configured and matched via constant-time Bearer token, **or** the route is explicitly declared in `AI2027_PUBLIC_ROUTES` (only `/health` and `/v1/protocol` are public read-only by default).
- **Bounded Request Reading:** `Default.fetch()` verifies authentication and checks `Content-Length` + streaming chunks (`<= 32 KiB`) **before** buffering the full request body in memory.
- **Executable Zero-Spend (`AI2027_MAX_SPEND_USD=0`):** Models not explicitly allowlisted as `local` or `free_tier` (e.g., OpenRouter models missing `:free`) are classified `unknown_cost` and blocked pre-flight even when an API key is set.
- **Distributed Trace IDs:** Propagates W3C `traceparent` and `X-Trace-Id`.

## Test & Dry-Run

```bash
cd python-2027
.venv/bin/pytest tests/test_python_worker.py -v
npx wrangler deploy --dry-run --config workers/python-worker/wrangler.toml
```

> **Honest Scope:** Local `pytest` exercises `handle_worker_request` and bounded stream readers offline; live Cloudflare deployment (`wrangler deploy`) requires your Cloudflare account.
