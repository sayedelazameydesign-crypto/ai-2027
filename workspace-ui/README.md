# AI-2027 Workspace UI

Arabic-first, bilingual workspace that uses the real **Agent Studio** API for sessions, task runs, streamed events, system status, approvals and verified artifacts. Its Connectors page reads the live, authenticated **Connectors Hub** catalog through a narrow, read-only Agent Studio endpoint; it does not render placeholder connector cards.

## Integration boundaries

- **Agent Studio is the default workspace.** Status is fetched from `/v1/studio/status`; sessions and tasks use `/v1/studio/sessions` and its SSE stream. The UI displays actual provider, budget, memory, approval, plan, event and artifact data.
- **Remote processing is opt-in in this UI.** Task requests set `allow_remote` to `false` unless the user explicitly enables the control. Provider credential presence is not presented as proof of a live provider connection.
- **Connectors are read from Connectors Hub.** The workspace calls `/v1/studio/connectors/catalog`, which makes a server-to-server authenticated `GET /v1/connectors` request and returns only catalog metadata. The bearer token never enters browser code; the route does not proxy search, read, proposal or write operations.
- Connector states retain the Hub's exact distinction: `unconfigured`, `configured`, or `needs_resource_config`. **Configured means credential material is present, not that a real provider read was verified.** The UI says so explicitly.
- Approval buttons only record Agent Studio's review decision; the UI explicitly says that this does not execute an external change. Connectors Hub writes still follow its separately authorized local approval path.
- Artifact previews are fetched as text and rendered in an escaped `<pre>` element; HTML and SVG are never executed.
- The previous bounded Live Agent file workspace remains available under **Kernel workspace**. Command HUD remains available under Operations.

## Run locally (loopback-only)

### 1. Start Agent Studio

Install the Python package once if it is not already available:

```bash
python3 -m pip install -e python-2027
```

Start the Studio API from the repository root:

```bash
ai2027-py27 --root . studio --host 127.0.0.1 --port 8080
```

The Vite development server proxies `/studio-api/*` to `AI2027_STUDIO_URL` (default `http://127.0.0.1:8080`).

### 2. Start Connectors Hub when connector data is needed

Follow [`connectors-hub/README.md`](../connectors-hub/README.md) to install and initialize the local Hub, then run its `connectors-hub serve` command on the default loopback port `8765`. `GET /v1/connectors` requires the Hub's local bearer token.

Set these **server-side environment variables before starting Agent Studio**:

```bash
export CONNECTORS_HUB_URL=http://127.0.0.1:8765
export CONNECTORS_HUB_API_TOKEN='<the local token printed by connectors-hub init>'
```

Do not use a `VITE_`-prefixed variable, paste the token into the UI, or commit it. If the token is missing, invalid, or the Hub is offline, the Connectors page reports that exact class of problem and shows no sample data.

### 3. Start the existing kernel services and the UI

The one-command script starts Live Agent and Command HUD on loopback and creates a random owner-only credential file at `.ember-up/api-credentials.json`:

```bash
./scripts/ember_up.sh --no-browser
```

In another terminal:

```bash
cd workspace-ui
npm ci
npm run dev
```

Open `http://127.0.0.1:4173`. Vite binds to `127.0.0.1` and accepts only local Host/Origin values; attempts to bind this credential proxy to a public interface are rejected. Same-origin `/api/*` requests go to Live Agent `8090`: the server-side Vite middleware reads the owner-only file, injects the reader credential for GETs, and injects the operator credential only for `POST /api/run`. Browser-supplied Authorization headers are overwritten. No bearer credential is included in the JavaScript bundle, browser storage, cookies, or URLs. `/ops/*` is similarly restricted to validated local same-origin requests; the proxy rewrites the already-validated Origin for Command HUD's loopback guard. `/studio-api/*` remains a relative proxy to Agent Studio `8080`.

Missing, malformed, symlinked, or non-private credential files fail closed for private API calls. The gateway requires verifiable POSIX owner-only file/directory modes; platforms where those permissions cannot be verified fail closed (this is not native Windows validation). The Live Agent still enforces `AI2027_READ_ONLY=1`, regardless of the local proxy's operator credential. `/api/health` remains a minimal public liveness response; the reader-protected `/api/capabilities` supplies the goal list and memory count used by the Kernel workspace.

This is a **trusted single-user local development bridge**, not multi-user authentication. Do not expose Vite, Live Agent, or Command HUD through an Arena/public preview, tunnel, public reverse proxy, or production deployment. The static production build does not include this proxy or credentials; internet access requires a separately approved identity gateway, not this local token bridge. Keep `CONNECTORS_HUB_URL` and `CONNECTORS_HUB_API_TOKEN` in the Agent Studio server environment, never in the static bundle.

## Build checks

```bash
npm run typecheck
npm run build
```

The React/TypeScript/Vite client is a view over existing services. It does not replace Agent Studio, Connectors Hub, the Python kernel, or the Live Agent service, and it adds no mock task, connector, provider, or evidence data.
