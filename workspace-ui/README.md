# AI-2027 Workspace UI — first implementation

A bilingual (Arabic-first, RTL) task workspace over the existing Live Agent API. It is intentionally a **real, bounded front end**, not a mock chat app: the initial surface supports file creation, directory creation/listing, policy-wall checks, run history, artifact download/preview, and the actual `DecisionRecord` evidence.

## Honest scope

- The current planner is deterministic. This interface does **not** call an LLM and does not pretend to understand arbitrary natural-language requests.
- Each run uses a fresh sandbox. Files can be downloaded, but they are not shared automatically between runs.
- Progress is not simulated. The current `/api/run` request is synchronous; the UI shows a waiting state and then renders the real record when it returns.
- Preview renders file contents as escaped plain text in a `<pre>` element; it never executes generated HTML or scripts.
- The existing Command HUD remains a separate operations surface at `/ops/` while developing through Vite.

## Run locally / in Arena

From the repository root, start the API and operations HUD (the script reuses healthy instances and runs the HTTP smoke checks):

```bash
./scripts/ember_up.sh --no-browser
```

In another terminal:

```bash
cd workspace-ui
npm install
npm run dev -- --host 0.0.0.0
```

Open the Vite preview at port `4173`. Its same-origin Vite proxy routes `/api/*` to the Live Agent on port `8090`, and `/ops/*` to the Command HUD on port `8091`; browser code never reaches a sandbox `localhost` URL directly.

## Build checks

```bash
npm run typecheck
npm run build
```

The UI uses React + TypeScript + Vite; the Python kernel and its zero-dependency runtime are unchanged. No production authentication, multi-user isolation, model provider, or asynchronous task/event API is added in this first slice.
