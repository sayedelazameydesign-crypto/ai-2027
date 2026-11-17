# Execution Order: Single-Worker Agent Loop (MVP Cut)

Apply the following engineering directive to the `new-new` branch strictly.
Reject any architectural concepts outside this document — even if referenced
elsewhere in the repository, older prompts, or the handbook.

---

### 1. Architectural Scope Constraint

Implement a single-process, single-worker agent execution loop directly
integrated with the existing Chat UI and Drizzle/MySQL database schema.

**Target Flow:**
User input → LLM Planner (2–4 steps) → Tool Executor → SSE Trace Streamer →
Assistant DB Response Output.

---

### 2. Mandatory Runtime Rules

* **Process Model:** Run the agent loop fully in-process inside the existing
  web application server. Do not introduce background queues, worker pools, or
  process managers.
* **Database Isolation:** Zero schema migrations. Store ephemeral execution
  state and runtime logs inside an in-memory `Map<string, Record<string, unknown>>`
  keyed by `runId`. State may vanish on restart; that is acceptable for MVP.
* **LLM Adapter Interface:** Centralize all model calls inside `src/lib/llm.ts`
  behind a single interface — one provider, one model, no routing or fallback
  chains:
  `complete(messages: Message[], opts?: CompletionOpts): Promise<string>`
* **Trace Engine Integration:** Extend the existing SSE payload union types.
  Emit events strictly through the pre-built SSE trace endpoint. Do not build a
  second channel.

---

### 3. Agent State Machine Specification (`src/agent/loop.ts`)

Implement the execution workflow as a strict, non-branching state machine:

```
RECEIVED ──► PLANNING ──► EXECUTING (step i of n) ──► SUMMARIZING ──► DONE / FAILED
                              │
                              └─ step error ──► RETRY (max 1) ──► FAILED
```

1. **Planner Phase:**
   - Single LLM call prompting for a structured JSON plan containing 2 to 4
     sequential steps. Each step names exactly one tool from the registry
     manifest plus its input.
   - Validate response output against `PlanSchema` (Zod): 2–4 steps, each
     step's `tool` must exist in the registry, each `input` must parse against
     that tool's `inputSchema`.
   - Execute exactly 1 repair retry on schema parse failures. Reject and exit
     to `FAILED` — surfacing the raw Zod error in the SSE trace — if repair fails.

2. **Executor Phase:**
   - Iterate sequentially over validated steps. No dynamic re-planning, no
     branching, no parallel steps.
   - Enforce caps: 4 maximum steps per run, 60-second limit per tool execution
     (`AbortController`), 5-minute global run timeout.
   - Allow a maximum of 1 retry per failing step before marking the overall
     run as `FAILED`.
   - Emit `step:start` before and `step:result` / `step:error` after every
     tool call.

3. **Summarizer Phase:**
   - Single LLM call to synthesize raw step responses into a human-readable
     assistant message.
   - Persist the final answer to the DB using the existing Chat Message schema
     (so a page refresh shows it even if SSE dropped) and emit `run:done`.

---

### 4. Tool Registry Specification (`src/agent/tools/registry.ts`)

Define all tools using explicit Zod validation wrappers:

```typescript
export interface Tool<I, O> {
  name: string;
  description: string;              // rendered into the planner manifest
  inputSchema: z.ZodType<I>;
  outputSchema: z.ZodType<O>;
  execute(input: I, ctx: ToolContext): Promise<O>;
}
```

Registry = plain `Map<string, Tool>` plus a `manifest()` function rendering
name/description/input JSON-schema for the planner prompt. Inputs are
Zod-parsed before `execute`, outputs Zod-parsed after; a parse failure is a
step error surfaced verbatim in the trace.

Implement the following core tools ONLY:

* `shell.exec`: Sandboxed system execution via `child_process.execFile` —
  never `exec` or any shell interpolation. Binary allowlist: `node`, `python3`,
  `ls`, `cat`, `grep`. Working directory pinned to `/tmp/agent-runs/<runId>/`.
  Hard limits: 30s execution timeout, 1MB stdout cap, environment scrubbed to
  `PATH` only.
* `web.fetch`: HTTP client with 10s execution timeout and 100KB body
  truncation limit.
* `browser.run`: Headless Playwright (chromium) script wrapper. One browser
  instance per run, reused across steps, closed in a `finally` block. 15s
  navigation timeout. No downloads, no auth flows.
* `fs.readwrite`: File system reader/writer restricted strictly to the
  run-scoped sandbox directory `/tmp/agent-runs/<runId>/` (path-traversal
  rejected).

**Sandbox limitation note:** there is no container/VM isolation in MVP.
Document this in one sentence in the README; do not build around it.

---

### 5. Definitive Exclusions (Hard Boundaries)

Immediately drop/reject any code or abstractions related to:

* Distributed messaging / queues (Redis, BullMQ), worker pools, distributed
  runtime (M10.x).
* Multi-agent orchestrators or 14-agent network layers, agent handoffs.
* Dynamic re-planning or multi-strategy reasoners.
* CircuitBreaker logic, DAG healing routines, or semantic embedding pipelines
  (Gemini embeddings, Knowledge Object Serialization).
* Additional SQL schemas, views, enterprise multi-tenant tables (the 50+ table
  package, the 18 SQL views).
* Per-task container isolation; observability layers beyond the SSE trace.

---

### 6. Acceptance Verification Suite (`e2e/mvp-loop.test.ts`)

Build an automated end-to-end integration test confirming:

1. `POST` request with prompt: *"Fetch https://example.com, extract the page
   title with the browser tool, and write it to result.txt in the sandbox."*
2. Correct sequential SSE emission: `run:start` ──► `plan:created` ──►
   `step:start` / `step:result` (≥2 pairs) ──► `run:done`.
3. Assert the `plan:created` payload contains 2–4 steps and every referenced
   tool exists in the registry.
4. Validation of file creation at `/tmp/agent-runs/<runId>/result.txt`
   matching contents `"Example Domain"`.
5. Verification of Assistant message insertion into the MySQL database using
   the existing Drizzle model.
6. Error boundary handling: a task whose plan references a nonexistent tool
   gracefully fails the run (`run:failed`) with the raw Zod error in the
   trace, while the primary HTTP process stays alive.

All six must pass before any item in §5 becomes eligible for discussion.
