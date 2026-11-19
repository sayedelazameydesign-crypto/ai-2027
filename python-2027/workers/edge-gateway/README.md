# AI-2027 Edge Gateway Worker ($0 Cloudflare Workers Tier)

Zero-dependency JavaScript ESM Cloudflare Worker acting as a rate-limited, auth-protected edge gateway in front of a Python 2027 backend (e.g., Google Colab tunnel or Render free service).

## Features ($0)

- Constant-time Bearer token check (`GATEWAY_TOKEN` secret) for `POST /v1/route` & `POST /v1/audit`
- Per-IP sliding-window rate limiter (`AI2027_RATE_LIMIT_PER_MIN`)
- Strict SSRF-safe `UPSTREAM_URL` forwarding (`https://` only) with `AbortSignal.timeout`
- Graceful deterministic `$0` fallback when upstream is unconfigured or unavailable

## Local Verification

```bash
node --test python-2027/workers/edge-gateway/tests/gateway.test.mjs
```
