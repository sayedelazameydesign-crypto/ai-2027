# Platform — Single-Worker Agent Loop (MVP)

Chat-triggered agent loop, built strictly per [`docs/EXECUTION-ORDER-MVP.md`](docs/EXECUTION-ORDER-MVP.md).

**Flow:** user task → LLM planner (2–4 steps) → sequential tool executor → SSE trace stream → assistant answer persisted to DB.

## Stack

- **Next.js 14 (App Router)** — chat UI + API routes + SSE, all in-process
- **Drizzle ORM + SQLite** (`better-sqlite3`) — swap the driver/connection string for MySQL in production; agent code and schema stay untouched
- **Zod** — plan validation + double-parsing (input/output) on every tool call
- **Playwright (chromium headless)** — `browser.run` tool

## Run

```bash
npm install
npx playwright install chromium   # + `npx playwright install-deps chromium` on bare Linux
npm run dev                       # http://localhost:3000
```

Optional: set `OPENAI_API_KEY` to use a real LLM planner/summarizer. Without it,
a deterministic fallback planner proves the loop end-to-end offline.

## Acceptance suite (EXECUTION-ORDER §6)

With the dev server running:

```bash
npm run e2e
```

Verifies: SSE event order (`run:start → plan:created → step:* → run:done`),
plan validity (2–4 steps, registry-only tools), sandbox file output, assistant
message persistence, and graceful `run:failed` on invalid plans with the
process staying alive. **Current status: 12/12 passing.**

## Architecture

```
src/
  agent/
    loop.ts               # state machine: RECEIVED→PLANNING→EXECUTING→SUMMARIZING→DONE/FAILED
    tools/
      registry.ts         # Tool<I,O>, manifest(), Zod parse-in/parse-out
      shell-exec.ts       # execFile only, allowlist, 30s/1MB caps, env scrubbed to PATH
      web-fetch.ts        # 10s timeout, 100KB truncation
      browser-run.ts      # headless chromium, 15s nav, closed in finally
      fs-readwrite.ts     # confined to /tmp/agent-runs/<runId>/, traversal rejected
  lib/
    llm.ts                # single adapter: complete(messages, opts)
    bus.ts                # in-memory event bus + flat KV per run
  db/                     # Drizzle schema (messages) — zero further migrations
  app/
    page.tsx              # chat UI + live SSE trace panel
    api/tasks             # POST — starts a run in-process
    api/runs/[id]/events  # GET — SSE trace stream
    api/messages          # GET — chat history
    api/tools, api/health # registry names / liveness probe
e2e/mvp-loop.test.ts      # acceptance suite (§6)
```

## Limits (by design — see §5 hard boundaries)

The sandbox is a run-scoped directory with an allowlisted `execFile` — there is
no container/VM isolation in MVP. No queues, no multi-agent orchestration, no
re-planning, no embeddings, no new DB tables until the single-worker loop is
proven in production use.
