# Python Unified Connectors Hub — MVP foundation

A local-first Python service for searching connected accounts, reading explicitly identified resources, and brokering agent-requested changes through a human approval step. The intended first host is the user's Windows machine; CeliaOS and other agents can consume the local API later.

> **Honest status:** this is a safety and API foundation, not a claim that 12 accounts are connected or that every service's OAuth flow has been completed. The 12 connector identities and read-search paths are present, and a guarded text-inference endpoint supports local LM Studio plus explicitly opted-in NVIDIA-hosted inference. No model/device or NVIDIA account has been live-tested from this repository; Google OAuth consent, provider credential onboarding, and live read/write verification still require per-provider setup. No credentials are included in this repository. Do not describe a provider as connected until a real authorized read succeeds.

## Safety model

- The API is intended to bind to `127.0.0.1` only. Do not expose it to a LAN or the internet.
- All API routes except `/health` require a bearer token created by `connectors-hub init` and stored in the Windows/OS keyring. The token is shown once in the local terminal; never paste it into chat or commit it.
- API access is read-only with respect to connected services. A mutating request can only create a **pending proposal**. There is deliberately no HTTP approval or execution endpoint.
- To execute a proposal, a person must run `connectors-hub approve <id>` in a local terminal, review the exact connector, method, path, query, and payload, then type the displayed confirmation phrase. Approval is single-use and expires. If a request times out after it may have reached a provider, the result is recorded as `unknown` and is not retried automatically.
- Credentials and the local encryption key are stored via the operating-system keyring. Pending request payloads are encrypted at rest; audit entries store hashes and state transitions, not provider responses or access tokens.
- The intended Google account is `sayedelazameydesign@gmail.com`. The optional Google login hint is only a local hint; an email address grants no access. Browser OAuth onboarding is not implemented, and when added, the owner must verify the selected account and grant least-privilege scopes at Google's consent screen.
- Supabase configuration must use a publishable/anon key and a user-scoped JWT protected by RLS. A `service_role`/admin key is refused. Never put a Supabase secret key in a browser, this repository, or an agent prompt.
- Search and resource reads are live pass-throughs, not a central data sync; their content is returned to the caller and not cached or indexed by the Hub. Supabase access requires explicit table and column allowlists and RLS; it does not enumerate or scan every table.
- The application does not reuse ChatGPT/Arena connector credentials. Each Python provider connection is separately authorized.

## Connector catalog

The API catalogs the 12 requested targets: Dropbox, GitHub, Gmail, Google Calendar, Drive, Docs, Sheets, Slides, Linear, Notion, Supabase, and Vercel. Google Docs/Sheets/Slides use the same Google identity as Drive but their own resource APIs. GitHub is accessed through its API directly; the GitHub app connector is not available through this Arena connector interface.

A catalog entry is not proof of access. `GET /v1/connectors` reports each provider as `unconfigured`, `configured`, or `needs_resource_config`. A live connection check and a representative successful read are required before calling it verified.

## Local setup (Windows PowerShell)

Use Python 3.12. The package is kept in this repository's root-level `connectors-hub/` directory, outside the archived `projects/` tree.

```powershell
cd connectors-hub
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
connectors-hub init
connectors-hub credentials set github_token
connectors-hub serve
```

`credentials set` prompts in the terminal without echoing the token. It accepts these provider-issued values: `dropbox_access_token`, `github_token`, `google_access_token`, `linear_api_key`, `notion_integration_token`, `nvidia_api_key`, `supabase_publishable_key`, `supabase_user_jwt`, and `vercel_token`. Google uses one token for Gmail, Calendar, Drive, Docs, Sheets, and Slides; this does not mean that token has all six sets of scopes. Do not enter account passwords. Google browser OAuth and refresh-token onboarding are not implemented yet, so its manual access-token option is temporary and short-lived.

For Supabase, set `CONNECTORS_HUB_SUPABASE_URL`, `CONNECTORS_HUB_SUPABASE_SEARCH_TABLES`, and `CONNECTORS_HUB_SUPABASE_SEARCH_COLUMNS` in the environment before starting the Hub. The table and column allowlists default to empty and must be deliberately configured. Use only a publishable/anon key plus a user-scoped JWT and keep RLS enabled; service-role/secret keys are rejected.

`credentials status` reports whether local credential material exists, not whether provider scopes are valid. A service is not verified until a real authorized read succeeds.

## Local AI reasoning (LM Studio first; NVIDIA optional)

`POST /v1/reasoning` is a text-only completion route protected by the same local bearer token. It does not run tools or connector actions. The default provider is `local`: install and start [LM Studio](https://lmstudio.ai/download), download and load a model, enable its OpenAI-compatible local server on `127.0.0.1:1234`, and use the model ID shown by LM Studio. The Hub neither downloads nor loads models and does not need an API key for local inference.

Choose the model and quantization with the machine's available VRAM in mind: `4B` describes parameter count, while quantization choices such as `Q4`/`Q5` trade memory for some quality. The model weights, context/KV cache, and other applications also consume memory; there is no single guaranteed VRAM figure. For the video-mentioned Qwen 3.5 4B model, use the exact model ID LM Studio reports rather than assuming its display name is an API ID. Set these variables before starting `connectors-hub serve` (the Hub does not load `.env` files automatically):

```powershell
$env:CONNECTORS_HUB_LM_STUDIO_MODEL = "<model-id-shown-by-LM-Studio>"
$env:CONNECTORS_HUB_REASONING_CONTEXT_TOKENS = "8192"
$env:CONNECTORS_HUB_REASONING_MAX_OUTPUT_TOKENS = "1024"
```

Set the Hub context budget no higher than the context length actually loaded in LM Studio, and choose a context/quantization that fits VRAM. The default 8,192-token budget includes the input, a framing reserve, and requested output; for a conservative tokenizer-independent check the Hub estimates input tokens from UTF-8 byte length, not the model's exact tokenizer. A larger prompt is rejected before inference. To summarize several books, send bounded chapter/section chunks locally and combine their summaries in later local calls; this endpoint does not read book files or automatically split/upload them.

Example local call:

```powershell
$body = @{ prompt = "Summarize this passage in five concise bullet points: ..."; max_tokens = 400 } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8765/v1/reasoning -Method Post `
  -Headers $headers -ContentType 'application/json' -Body $body
```

The optional `mode` field makes the model useful for the user-provided Big Bro Fares editorial identity: `gaming_case_study` (game narrative → leadership/behavior analysis), `life_lessons` (career and well-being), or `tech_as_tool` (practical benefits, costs, privacy/security, and hype checks). `general` remains the default. These are focused prompt instructions, not a claim of parity with Claude or Manus. The model is an editorial assistant, not the creator: it must not invent the creator's personal stories or credentials, fabricate game scenes or evidence, or copy third-party scripts; it should create original material and distinguish facts from interpretation.

NVIDIA-hosted inference is a separate, optional remote path. Store the provider key in the OS keyring with `connectors-hub credentials set nvidia_api_key` (hidden input; never put the key in `.env`, Git, a prompt, or an HTTP request body), and set `CONNECTORS_HUB_NVIDIA_MODEL` to a model ID supported by NVIDIA NIM. A request is rejected unless it explicitly sets `provider = "nvidia"` **and** `allow_remote_processing = $true`; the endpoint and model provider do not fall back automatically. This confirmation means the prompt will leave the machine for NVIDIA-hosted processing:

```powershell
$env:CONNECTORS_HUB_NVIDIA_MODEL = "<NVIDIA-NIM-model-id>"
connectors-hub credentials set nvidia_api_key
$body = @{ provider = "nvidia"; prompt = "Summarize this passage."; max_tokens = 400; allow_remote_processing = $true } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8765/v1/reasoning -Method Post `
  -Headers $headers -ContentType 'application/json' -Body $body
```

The local Hub retrieves its NVIDIA key from the OS keyring only after explicit remote consent and sends it to the fixed NVIDIA inference endpoint over HTTPS. The GitHub repository secret is separate: it is available only to GitHub workflows, not copied to a developer's OS keyring; local use requires separately running the hidden-input `credentials set nvidia_api_key` command. This inference route cannot approve or execute `/v1/actions` proposals. Separately, `.github/workflows/nvidia-live-check.yml` is a manual-only smoke test using the repository `NVIDIA_API_KEY` secret in one step; it uses a fixed tiny prompt, a user-supplied model ID, no retries, and never logs response text or provider error bodies. It does not run on pushes or PRs and has not been run from this branch. Unit tests use mocked providers only; no live NVIDIA key or model connection has been tested.

The `init` command creates a local API bearer token and encryption key in the OS keyring. Keep the one-time token in the local agent's secret configuration. Example request:

```powershell
$headers = @{ Authorization = "Bearer <local-token>" }
Invoke-RestMethod http://127.0.0.1:8765/v1/connectors -Headers $headers
```

Search request example:

```powershell
$body = @{ query = "project roadmap"; limit = 10 } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8765/v1/search -Method Post `
  -Headers $headers -ContentType 'application/json' -Body $body
```

To read one resource, use `POST /v1/read` with a connector-specific resource type and ID. It constructs fixed provider API routes; callers cannot supply arbitrary URLs or raw HTTP paths. Example:

```powershell
$body = @{ connector = "gmail"; resource_type = "message"; resource_id = "<provider-message-id>" } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8765/v1/read -Method Post `
  -Headers $headers -ContentType 'application/json' -Body $body
```

Supported read types are: Dropbox `file` (UTF-8 text files only); GitHub `issue` (`owner/repo#number`), `repository` (`owner/repo`), and `file` (`owner/repo/path`); Gmail `message`; Google Calendar `event`; Google Drive `file` (metadata and small UTF-8 text files; binary downloads are not returned); Google Docs `document`; Google Sheets `spreadsheet` (currently the bounded `A1:Z100` range); Google Slides `presentation`; Linear `issue`; Notion `page` (page properties and up to 500 top-level blocks); Supabase `row` (requires `resource` set to an explicitly allowlisted table and uses its `id` column); and Vercel `project` (project metadata only, not environment variables or deployment logs). Text responses are capped at 512 KiB. Provider scopes, resource sharing, Supabase RLS, and provider-side authorization still apply.

For an agent-requested change, submit a proposal to `POST /v1/actions`. The API returns a pending ID only; it cannot execute the action. Review and approve locally:

```powershell
connectors-hub pending
connectors-hub approve <action-id>
```

The CLI prints the exact request preview and asks for a one-time confirmation phrase. Do not approve a request whose recipient, target, method, path, or payload you have not checked.

## Local tests

The unit suite uses mocked HTTP responses, an in-memory keyring double, and temporary databases. It does not contact provider APIs or modify connected accounts.

```powershell
python -m unittest discover -s tests -v
```

These tests cover all 12 search and resource-read dispatchers, local auth/approval safety paths, and local/NVIDIA inference using mocked HTTP responses. They are not live connection checks. No model/device or provider account has been tested from this repository. Add live checks one connector at a time after authorization, using read-only scopes first. Mutating tests must use a dedicated test resource and explicit local approval; never use a real mailbox, production calendar, production database, or production deployment for test writes.

## Remaining MVP work

1. Add provider-native browser OAuth flows and refresh-token rotation, starting with the Google account and Dropbox.
2. Add read-only credential validation and connection tests for every provider; exercise only the exact account the owner authorized.
3. Add/verify connector-specific search and read behavior, including pagination and provider errors; document unsupported cross-service search cases.
4. Add typed, provider-specific mutation builders so human previews are meaningful (rather than relying on raw API paths), then add a test for each operation's approval requirement.
5. Add a CeliaOS adapter after the independent local service has passed its read-only acceptance tests.

Until those steps pass, this is a safe foundation and connector catalog—not a completed, fully verified 12-service hub.
