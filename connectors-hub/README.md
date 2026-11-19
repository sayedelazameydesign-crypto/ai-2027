# Python Unified Connectors Hub — MVP foundation

A local-first Python service for searching connected accounts, reading explicitly identified resources, and brokering agent-requested changes through a human approval step. The intended first host is the user's Windows machine; CeliaOS and other agents can consume the local API later.

> **Honest status:** this is the safety and API foundation, not a claim that 12 accounts are connected or that every service's OAuth flow has been completed. The 12 connector identities and read-search paths are present, but Google OAuth consent, provider credential onboarding, and live read/write verification still require per-provider setup. No credentials are included in this repository. Do not describe a provider as connected until a real authorized read succeeds.

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

`credentials set` prompts in the terminal without echoing the token. It accepts these provider-issued values: `dropbox_access_token`, `github_token`, `google_access_token`, `linear_api_key`, `notion_integration_token`, `supabase_publishable_key`, `supabase_user_jwt`, and `vercel_token`. Google uses one token for Gmail, Calendar, Drive, Docs, Sheets, and Slides; this does not mean that token has all six sets of scopes. Do not enter account passwords. Google browser OAuth and refresh-token onboarding are not implemented yet, so its manual access-token option is temporary and short-lived.

For Supabase, set `CONNECTORS_HUB_SUPABASE_URL`, `CONNECTORS_HUB_SUPABASE_SEARCH_TABLES`, and `CONNECTORS_HUB_SUPABASE_SEARCH_COLUMNS` in the environment before starting the Hub. The table and column allowlists default to empty and must be deliberately configured. Use only a publishable/anon key plus a user-scoped JWT and keep RLS enabled; service-role/secret keys are rejected.

`credentials status` reports whether local credential material exists, not whether provider scopes are valid. A service is not verified until a real authorized read succeeds.

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

These tests cover all 12 search and resource-read dispatchers plus the local auth/approval safety paths, but they are not live connection checks. No provider accounts have been tested from this repository. Add live checks one connector at a time after authorization, using read-only scopes first. Mutating tests must use a dedicated test resource and explicit local approval; never use a real mailbox, production calendar, production database, or production deployment for test writes.

## Remaining MVP work

1. Add provider-native browser OAuth flows and refresh-token rotation, starting with the Google account and Dropbox.
2. Add read-only credential validation and connection tests for every provider; exercise only the exact account the owner authorized.
3. Add/verify connector-specific search and read behavior, including pagination and provider errors; document unsupported cross-service search cases.
4. Add typed, provider-specific mutation builders so human previews are meaningful (rather than relying on raw API paths), then add a test for each operation's approval requirement.
5. Add a CeliaOS adapter after the independent local service has passed its read-only acceptance tests.

Until those steps pass, this is a safe foundation and connector catalog—not a completed, fully verified 12-service hub.
