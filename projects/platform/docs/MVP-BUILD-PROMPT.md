# MVP Build Prompt — Single-Worker Agent Loop

> Paste this prompt (sections 1–8) into your coding agent against the `new-new` branch.
> Scope contract: anything in §7 "Out of scope" must be rejected even if the existing
> codebase or older prompts reference it.

---

## 1. Objective

Ship a provable end-to-end agent loop on top of the existing chat UI + Drizzle/MySQL
schema:

**User sends a task via chat → agent produces a 2–4 step plan → executes steps by
calling 1+ registered tool → streams every state transition over the existing SSE
trace channel → posts the final result back into the chat thread.**

Single owner, single process, free-tier infra. No Redis, no queue, no workers, no
new DB tables.

## 2. Ground rules

- **Runtime:** one Node.js process. The agent loop runs in-process with the web
  server (or as one `node` sidecar — pick whichever the repo already favors, do not
  introduce a process manager).
- **DB:** use the existing Drizzle schema as-is. If the loop needs persistence
  (run status, step log), store JSON in existing columns/tables. Creating a
  migration is a hard failure.
- **Streaming:** reuse the SSE trace endpoint already in the repo. Do not build a
  second channel. If an event shape is missing, extend the existing event union.
- **LLM calls:** one provider, one model, behind a single `llm.ts` adapter with a
  `complete(messages, opts)` function. No routing, no fallback chains.
- **No memory system.** If the plan needs scratch state between steps, use an
  in-memory `Map<runId, Record<string, unknown>>` (flat KV). It may vanish on
  restart; that is acceptable for MVP.

## 3. Agent loop (Phase 1–2 equivalent)

Implement `src/agent/loop.ts` as an explicit state machine:

```
RECEIVED → PLANNING → EXECUTING(step i of n) → (step ok? next : RETRY once → FAIL)
        → SUMMARIZING → DONE | FAILED
```

Requirements:

- `runAgent(task: TaskInput): Promise<RunResult>` — single entry point.
- **Planner:** one LLM call. System prompt instructs: "Produce a plan of 2–4 steps.
  Each step names exactly one tool from the provided tool manifest and its input."
  Validate the plan with Zod (`PlanSchema`: 2–4 steps, each step's `tool` must exist
  in the registry, `input` must parse against that tool's input schema). One repair
  retry on validation failure, then FAIL the run with the Zod error in the trace.
- **Executor:** sequential `for` loop over steps. Per step: emit `step:start`,
  call the tool, emit `step:result` or `step:error`. One retry per step, then fail
  the run. Hard caps: 4 steps, 60s per tool call (`AbortController`), 5 min per run.
- **Summarizer:** one final LLM call that turns step results into the user-facing
  answer. Emit `run:done` with it.
- No dynamic re-planning, no branching, no parallel steps. If a plan turns out
  wrong mid-run, the run fails with a useful trace. That is the MVP behavior.

## 4. Tool registry (already partially built — finish it)

`src/agent/tools/registry.ts`:

```ts
interface Tool<I, O> {
  name: string;
  description: string;        // shown to the planner
  inputSchema: z.ZodType<I>;
  outputSchema: z.ZodType<O>;
  execute(input: I, ctx: ToolCtx): Promise<O>;
}
```

- Registry = plain `Map<string, Tool>` + `manifest()` that renders name/description/
  JSON-schema of inputs for the planner prompt.
- Inputs are Zod-parsed before `execute`, outputs Zod-parsed after. A parse failure
  is a step error, surfaced in the trace verbatim.

Exactly these tools for MVP (3–5, no more):

1. `shell.exec` — sandboxed command execution (see §5).
2. `web.fetch` — fetch a URL, return status + trimmed text (10s timeout, 100KB cap).
3. `browser.run` — Playwright script against a page: goto, extract text/screenshot
   (the Phase 7 spec, already corrected — implement as speced).
4. `fs.readwrite` — read/write files inside the run's sandbox dir only.
5. (optional, only if trivially cheap) `kv.get/set` over the in-memory Map.

## 5. Sandbox (Phase 7, corrected spec)

- `shell.exec` uses `child_process.execFile` — **never** `exec`/shell interpolation.
  Allowlist of binaries (`node`, `python3`, `ls`, `cat`, `grep`… keep it under 10),
  cwd pinned to `/tmp/agent-runs/<runId>/`, 30s timeout, 1MB stdout cap, env
  scrubbed to `PATH` only.
- Playwright: chromium headless, one browser instance reused across steps in a run,
  closed in a `finally`. Navigation timeout 15s. No downloads, no auth flows.
- No containers, no VM isolation. Document this limitation in the README with one
  sentence; do not build around it.

## 6. Wiring (loop → registry → sandbox → UI)

- Chat message with role `task` (or the existing trigger the UI uses) starts a run.
- Every loop transition emits an SSE trace event:
  `run:start`, `plan:created` (full plan JSON), `step:start`, `step:result`,
  `step:error`, `run:done`, `run:failed`. Keep the payload shape the existing trace
  viewer expects; extend, don't replace.
- Final answer is written back as an assistant chat message through the existing
  message path, so refreshing the page shows it even if SSE dropped.

## 7. Out of scope — reject even if referenced elsewhere in the repo/prompts

- Redis, BullMQ, any queue/worker/distributed runtime (M10.x)
- Multi-agent orchestration, handoffs, the 14-agent design
- Multi-Strategy Reasoner / dynamic re-planning
- Gemini semantic embeddings (old Phase 4), Knowledge Object Serialization
- CircuitBreaker (old Phase 6), self-healing DAG
- New DB tables, the 50+ table enterprise package, the 18 SQL views
- Layered governance / observability beyond the SSE trace
- Per-task container isolation

## 8. Definition of done (acceptance test)

One scripted E2E run, checked into `e2e/mvp-loop.test.ts`, that:

1. POSTs the task: *"Fetch https://example.com, extract the page title with the
   browser tool, and write it to result.txt in the sandbox."*
2. Asserts SSE emits, in order: `run:start` → `plan:created` (2–4 steps, every tool
   exists in the registry) → ≥2 `step:start`/`step:result` pairs → `run:done`.
3. Asserts `/tmp/agent-runs/<runId>/result.txt` contains "Example Domain".
4. Asserts a final assistant message landed in the DB via the existing schema.
5. Negative case: a task whose plan references a nonexistent tool → `run:failed`
   with the Zod validation error in the trace, process still alive.

If all five pass, the MVP loop is proven and v2 items (§7) become eligible for
discussion — not before.
