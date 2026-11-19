const MAX_BODY_BYTES = 32_768;
const PUBLIC_READ_ROUTES = new Set(["/health", "/v1/protocol"]);
const GEMINI_FREE_MODELS = new Set([
  "gemini-2.5-flash",
  "gemini-2.5-flash-lite",
  "gemini-2.0-flash",
  "gemini-2.0-flash-lite",
  "gemini-1.5-flash",
  "gemini-1.5-flash-8b",
]);
const NVIDIA_FREE_MODELS = new Set([
  "meta/llama-3.1-8b-instruct",
  "meta/llama-3.1-70b-instruct",
  "meta/llama-3.3-70b-instruct",
  "mistralai/mixtral-8x7b-instruct-v0.1",
  "qwen/qwen2.5-72b-instruct",
]);
const rateWindows = new Map();

function resolveTrace(request) {
  const tp = (request.headers.get("traceparent") || "").trim().toLowerCase();
  const match = /^00-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}$/.exec(tp);
  if (match && !/^0+$/.test(match[1]) && !/^0+$/.test(match[2])) {
    const spanId = crypto.randomUUID().replaceAll("-", "").slice(0, 16);
    return { traceId: match[1], traceparent: `00-${match[1]}-${spanId}-01` };
  }
  const xid = (request.headers.get("x-trace-id") || "").trim().toLowerCase();
  const traceId = /^[0-9a-f]{32}$/.test(xid) && !/^0+$/.test(xid)
    ? xid
    : crypto.randomUUID().replaceAll("-", "");
  const spanId = crypto.randomUUID().replaceAll("-", "").slice(0, 16);
  return { traceId, traceparent: `00-${traceId}-${spanId}-01` };
}

function securityHeaders(traceId, traceparent, origin = null, allowedOrigins = [], authMode = "bearer") {
  const headers = {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "x-trace-id": traceId,
    traceparent,
    "x-ai2027-auth-mode": authMode,
  };
  if (origin && allowedOrigins.includes(origin)) {
    headers["access-control-allow-origin"] = origin;
    headers.vary = "Origin";
  }
  return headers;
}

function jsonResponse(payload, status, headers) {
  return new Response(JSON.stringify(payload), { status, headers });
}

function constantTimeEqual(a, b) {
  const enc = new TextEncoder();
  const ab = enc.encode(a);
  const bb = enc.encode(b);
  if (ab.length !== bb.length) {
    return false;
  }
  let diff = 0;
  for (let i = 0; i < ab.length; i += 1) {
    diff |= ab[i] ^ bb[i];
  }
  return diff === 0;
}

function authorizeRequest(pathname, request, env) {
  if (PUBLIC_READ_ROUTES.has(pathname)) {
    return { ok: true, status: 200, error: "", mode: "public-read" };
  }
  const expectedToken = String(env.GATEWAY_TOKEN || "").trim();
  if (expectedToken) {
    const auth = request.headers.get("authorization") || "";
    const supplied = auth.toLowerCase().startsWith("bearer ") ? auth.slice(7).trim() : "";
    if (!supplied || !constantTimeEqual(supplied, expectedToken)) {
      return { ok: false, status: 401, error: "Unauthorized", mode: "bearer" };
    }
    return { ok: true, status: 200, error: "", mode: "bearer" };
  }
  const publicRoutes = String(env.AI2027_PUBLIC_ROUTES || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  const envMode = String(env.AI2027_ENV || env.ENVIRONMENT || "production").toLowerCase();
  const allowPublic = ["1", "true", "yes"].includes(
    String(env.AI2027_ALLOW_PUBLIC_UNAUTH || "").trim().toLowerCase(),
  );
  if (publicRoutes.includes(pathname) || (allowPublic && envMode !== "production")) {
    return { ok: true, status: 200, error: "", mode: "explicit-public" };
  }
  return {
    ok: false,
    status: 401,
    error: "Fail-closed: GATEWAY_TOKEN is required (or declare path in AI2027_PUBLIC_ROUTES).",
    mode: "fail-closed",
  };
}

async function readBoundedBody(request) {
  const clRaw = request.headers.get("content-length");
  if (clRaw !== null) {
    const clVal = Number(clRaw);
    if (!Number.isInteger(clVal) || clVal < 0) {
      return { ok: false, status: 400, error: "Invalid Content-Length header" };
    }
    if (clVal > MAX_BODY_BYTES) {
      return { ok: false, status: 413, error: "Payload too large" };
    }
  }
  if (request.body && typeof request.body.getReader === "function") {
    const reader = request.body.getReader();
    const chunks = [];
    let total = 0;
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const buf = value instanceof Uint8Array ? value : new Uint8Array(value);
        total += buf.byteLength;
        if (total > MAX_BODY_BYTES) {
          await reader.cancel().catch(() => {});
          return { ok: false, status: 413, error: "Payload too large" };
        }
        chunks.push(buf);
      }
    } finally {
      reader.releaseLock?.();
    }
    const merged = new Uint8Array(total);
    let offset = 0;
    for (const c of chunks) {
      merged.set(c, offset);
      offset += c.byteLength;
    }
    return { ok: true, text: new TextDecoder().decode(merged) };
  }
  const rawBody = await request.text();
  if (new TextEncoder().encode(rawBody).length > MAX_BODY_BYTES) {
    return { ok: false, status: 413, error: "Payload too large" };
  }
  return { ok: true, text: rawBody };
}

function classifyGatewayModel(provider, model, env) {
  const extra = new Set(
    String(env.AI2027_FREE_MODELS || "")
      .split(",")
      .map((s) => s.trim().toLowerCase())
      .filter(Boolean),
  );
  const norm = String(model || "").trim().toLowerCase();
  const allowed = provider === "gemini" ? GEMINI_FREE_MODELS : NVIDIA_FREE_MODELS;
  if (allowed.has(norm) || extra.has(norm)) {
    return { billingTier: "free_tier", costKnown: false, costStatus: "declared_free_tier_unbilled" };
  }
  return { billingTier: "unknown_cost", costKnown: false, costStatus: "unknown_cost_blocked" };
}

function checkRateLimit(ip, limitPerMin, nowMs) {
  const windowStart = nowMs - 60_000;
  const entries = (rateWindows.get(ip) || []).filter((ts) => ts > windowStart);
  if (entries.length >= limitPerMin) {
    rateWindows.set(ip, entries);
    return false;
  }
  entries.push(nowMs);
  rateWindows.set(ip, entries);
  return true;
}

function parseUpstreamUrl(raw) {
  if (!raw || typeof raw !== "string") {
    return null;
  }
  try {
    const parsed = new URL(raw.trim());
    if (parsed.protocol !== "https:") {
      return null;
    }
    if (parsed.username || parsed.password) {
      return null;
    }
    return parsed.toString().replace(/\/$/, "");
  } catch {
    return null;
  }
}

export function resetRateLimitsForTest() {
  rateWindows.clear();
}

export async function handleGatewayFetch(
  request,
  env = {},
  { fetchImpl = globalThis.fetch, nowFn = () => Date.now() } = {},
) {
  const { traceId, traceparent } = resolveTrace(request);
  const url = new URL(request.url);
  const origin = request.headers.get("origin");
  const allowedOrigins = String(env.ALLOWED_ORIGINS || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  const method = request.method.toUpperCase();
  const maxSpend = Math.max(0, Number.parseFloat(String(env.AI2027_MAX_SPEND_USD || "0")) || 0);
  const auth = authorizeRequest(url.pathname, request, env);
  const headers = securityHeaders(traceId, traceparent, origin, allowedOrigins, auth.mode);

  if (method === "OPTIONS") {
    return new Response(null, {
      status: 204,
      headers: {
        ...headers,
        "access-control-allow-methods": "GET, POST, OPTIONS",
        "access-control-allow-headers": "authorization, content-type, traceparent, x-trace-id",
      },
    });
  }

  if (url.pathname === "/health") {
    if (method !== "GET" && method !== "HEAD") {
      return jsonResponse({ error: "Method not allowed" }, 405, headers);
    }
    return jsonResponse(
      {
        ok: true,
        service: "ai2027-edge-gateway",
        upstream_configured: Boolean(parseUpstreamUrl(env.UPSTREAM_URL)),
        auth_mode: String(env.GATEWAY_TOKEN || "").trim() ? "bearer" : "fail-closed",
        max_spend_usd: maxSpend,
        cost_usd: 0.0,
        cost_known: true,
        trace_id: traceId,
      },
      200,
      headers,
    );
  }

  if (url.pathname === "/v1/protocol") {
    if (method !== "GET") {
      return jsonResponse({ error: "Method not allowed" }, 405, headers);
    }
    return jsonResponse(
      {
        protocol: "Python 2027 Software Excellence Protocol",
        gateway: "cloudflare-edge-gateway",
        providers: ["upstream", "gemini", "nvidia", "deterministic"],
        agent_endpoints: ["/v1/route", "/v1/agent/plan"],
        max_spend_usd: maxSpend,
        cost_usd: 0.0,
        trace_id: traceId,
      },
      200,
      headers,
    );
  }

  if (url.pathname !== "/v1/agent/plan" && url.pathname !== "/v1/route") {
    return jsonResponse({ error: "Not found" }, 404, headers);
  }
  if (method !== "POST") {
    return jsonResponse({ error: "Method not allowed" }, 405, headers);
  }
  if (!auth.ok) {
    return jsonResponse({ error: auth.error }, auth.status, headers);
  }

  if (url.pathname === "/v1/route") {
    const clientIp =
      request.headers.get("cf-connecting-ip") ||
      request.headers.get("x-forwarded-for") ||
      "127.0.0.1";
    const rateLimit = Number.parseInt(String(env.AI2027_RATE_LIMIT_PER_MIN || "30"), 10) || 30;
    if (!checkRateLimit(clientIp, rateLimit, nowFn())) {
      return jsonResponse({ error: "Rate limit exceeded", trace_id: traceId }, 429, headers);
    }
  }

  const bodyRead = await readBoundedBody(request);
  if (!bodyRead.ok) {
    return jsonResponse({ error: bodyRead.error }, bodyRead.status, headers);
  }

  let payload;
  try {
    payload = JSON.parse(bodyRead.text || "{}");
  } catch {
    return jsonResponse({ error: "Invalid JSON body" }, 400, headers);
  }

  if (url.pathname === "/v1/agent/plan") {
    const rawTask = payload && typeof payload === "object" ? payload.task || payload.prompt : null;
    if (typeof rawTask !== "string" || !rawTask.trim()) {
      return jsonResponse({ error: "task must be a non-empty string" }, 400, headers);
    }
    return jsonResponse(
      {
        task: rawTask.trim().slice(0, 2000),
        gateway: "cloudflare-edge-gateway",
        billing_tier: "local",
        cost_known: true,
        cost_status: "proven_zero_local",
        cost_usd: 0.0,
        trace_id: traceId,
        steps: [
          { step: 1, tool: "workspace.list_files", purpose: "Discover project context" },
          { step: 2, tool: "memory.search", purpose: "Recall verified L1 knowledge" },
          { step: 3, tool: "sandbox.run_python", purpose: "Execute bounded computation" },
          { step: 4, tool: "sandbox.write_artifact", purpose: "Deliver L0 SHA-256 artifact" },
        ],
      },
      200,
      headers,
    );
  }

  if (!payload || typeof payload !== "object" || typeof payload.prompt !== "string" || !payload.prompt.trim()) {
    return jsonResponse({ error: "prompt must be a non-empty string" }, 400, headers);
  }

  const upstream = parseUpstreamUrl(env.UPSTREAM_URL);
  const timeoutMs = Number.parseInt(String(env.AI2027_UPSTREAM_TIMEOUT_MS || "7000"), 10) || 7000;
  if (upstream) {
    try {
      const upstreamRes = await fetchImpl(`${upstream}/v1/route`, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "x-trace-id": traceId,
          traceparent,
        },
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(timeoutMs),
      });
      if (upstreamRes.ok) {
        const data = await upstreamRes.json();
        return jsonResponse({ ...data, gateway_proxied: true }, 200, headers);
      }
    } catch {
      // Fall through to configured edge provider secrets or deterministic fallback
    }
  }

  const allowRemote = payload.allow_remote !== false;
  const cleanPrompt = payload.prompt.trim();
  const maxTokens =
    Number.isInteger(payload.max_tokens) && payload.max_tokens >= 1 && payload.max_tokens <= 4096
      ? payload.max_tokens
      : 256;
  const attempts = [];
  let unknownBlocked = 0;

  if (allowRemote && env.GEMINI_API_KEY && !String(env.GEMINI_API_KEY).includes("placeholder")) {
    const model = String(env.GEMINI_MODEL || "").trim() || "gemini-2.5-flash";
    const budget = classifyGatewayModel("gemini", model, env);
    if (budget.billingTier === "unknown_cost" && maxSpend <= 0) {
      unknownBlocked += 1;
      attempts.push({ provider: "gemini", model, ok: false, error: "budget_blocked", ...budget });
    } else {
      try {
        const gemRes = await fetchImpl(
          `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`,
          {
            method: "POST",
            headers: {
              "content-type": "application/json",
              "x-goog-api-key": String(env.GEMINI_API_KEY).trim(),
              traceparent,
            },
            body: JSON.stringify({
              contents: [{ role: "user", parts: [{ text: cleanPrompt }] }],
              generationConfig: { maxOutputTokens: maxTokens },
            }),
            signal: AbortSignal.timeout(timeoutMs),
          },
        );
        if (gemRes.ok) {
          const gemData = await gemRes.json();
          const text = gemData?.candidates?.[0]?.content?.parts?.[0]?.text;
          if (typeof text === "string" && text.trim()) {
            attempts.push({ provider: "gemini", model, ok: true, ...budget });
            return jsonResponse(
              {
                provider: "gemini",
                model,
                text: text.trim(),
                simulated: false,
                gateway_proxied: false,
                cost_usd: 0.0,
                billing_tier: budget.billingTier,
                cost_known: budget.costKnown,
                cost_status: budget.costStatus,
                unknown_cost_blocked: unknownBlocked,
                attempts,
                trace_id: traceId,
              },
              200,
              headers,
            );
          }
        }
        attempts.push({ provider: "gemini", model, ok: false, error: `http_${gemRes.status}`, ...budget });
      } catch {
        attempts.push({ provider: "gemini", model, ok: false, error: "network_error", ...budget });
      }
    }
  }

  if (allowRemote && env.NVIDIA_API_KEY && !String(env.NVIDIA_API_KEY).includes("placeholder")) {
    const model = String(env.NVIDIA_MODEL || "").trim() || "meta/llama-3.1-8b-instruct";
    const budget = classifyGatewayModel("nvidia", model, env);
    if (budget.billingTier === "unknown_cost" && maxSpend <= 0) {
      unknownBlocked += 1;
      attempts.push({ provider: "nvidia", model, ok: false, error: "budget_blocked", ...budget });
    } else {
      try {
        const nvRes = await fetchImpl("https://integrate.api.nvidia.com/v1/chat/completions", {
          method: "POST",
          headers: {
            "content-type": "application/json",
            authorization: `Bearer ${String(env.NVIDIA_API_KEY).trim()}`,
            traceparent,
          },
          body: JSON.stringify({
            model,
            messages: [{ role: "user", content: cleanPrompt }],
            max_tokens: maxTokens,
          }),
          signal: AbortSignal.timeout(timeoutMs),
        });
        if (nvRes.ok) {
          const nvData = await nvRes.json();
          const text = nvData?.choices?.[0]?.message?.content;
          if (typeof text === "string" && text.trim()) {
            attempts.push({ provider: "nvidia", model, ok: true, ...budget });
            return jsonResponse(
              {
                provider: "nvidia",
                model,
                text: text.trim(),
                simulated: false,
                gateway_proxied: false,
                cost_usd: 0.0,
                billing_tier: budget.billingTier,
                cost_known: budget.costKnown,
                cost_status: budget.costStatus,
                unknown_cost_blocked: unknownBlocked,
                attempts,
                trace_id: traceId,
              },
              200,
              headers,
            );
          }
        }
        attempts.push({ provider: "nvidia", model, ok: false, error: `http_${nvRes.status}`, ...budget });
      } catch {
        attempts.push({ provider: "nvidia", model, ok: false, error: "network_error", ...budget });
      }
    }
  }

  const preview = cleanPrompt.replace(/\s+/g, " ").slice(0, 140);
  return jsonResponse(
    {
      provider: "deterministic",
      model: "edge-gateway-fallback-v1",
      text: `[edge-gateway-fallback | simulated=true | cost=$0.00] ${preview}`,
      simulated: true,
      gateway_proxied: false,
      cost_usd: 0.0,
      billing_tier: "local",
      cost_known: true,
      cost_status: "proven_zero_local",
      unknown_cost_blocked: unknownBlocked,
      attempts,
      trace_id: traceId,
    },
    200,
    headers,
  );
}

export default {
  async fetch(request, env) {
    return handleGatewayFetch(request, env);
  },
};
