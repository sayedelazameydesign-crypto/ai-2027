const MAX_BODY_BYTES = 32_768;
const rateWindows = new Map();

function securityHeaders(traceId, origin = null, allowedOrigins = []) {
  const headers = {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "x-trace-id": traceId,
  };
  if (origin && allowedOrigins.includes(origin)) {
    headers["access-control-allow-origin"] = origin;
    headers["vary"] = "Origin";
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
  const traceId = crypto.randomUUID().replaceAll("-", "");
  const url = new URL(request.url);
  const origin = request.headers.get("origin");
  const allowedOrigins = String(env.ALLOWED_ORIGINS || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  const headers = securityHeaders(traceId, origin, allowedOrigins);
  const method = request.method.toUpperCase();

  if (method === "OPTIONS") {
    return new Response(null, {
      status: 204,
      headers: {
        ...headers,
        "access-control-allow-methods": "GET, POST, OPTIONS",
        "access-control-allow-headers": "authorization, content-type",
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
        cost_usd: 0.0,
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
        cost_usd: 0.0,
        trace_id: traceId,
      },
      200,
      headers,
    );
  }

  if (url.pathname === "/v1/agent/plan") {
    if (method !== "POST") {
      return jsonResponse({ error: "Method not allowed" }, 405, headers);
    }
    const expectedToken = String(env.GATEWAY_TOKEN || "").trim();
    if (expectedToken) {
      const auth = request.headers.get("authorization") || "";
      const supplied = auth.toLowerCase().startsWith("bearer ")
        ? auth.slice(7).trim()
        : "";
      if (!supplied || !constantTimeEqual(supplied, expectedToken)) {
        return jsonResponse({ error: "Unauthorized" }, 401, headers);
      }
    }
    const rawBody = await request.text();
    let payload;
    try {
      payload = JSON.parse(rawBody || "{}");
    } catch {
      return jsonResponse({ error: "Invalid JSON body" }, 400, headers);
    }
    const rawTask = payload && typeof payload === "object" ? (payload.task || payload.prompt) : null;
    if (typeof rawTask !== "string" || !rawTask.trim()) {
      return jsonResponse({ error: "task must be a non-empty string" }, 400, headers);
    }
    return jsonResponse(
      {
        task: rawTask.trim().slice(0, 2000),
        gateway: "cloudflare-edge-gateway",
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

  if (url.pathname === "/v1/route") {
    if (method !== "POST") {
      return jsonResponse({ error: "Method not allowed" }, 405, headers);
    }
    const expectedToken = String(env.GATEWAY_TOKEN || "").trim();
    if (expectedToken) {
      const auth = request.headers.get("authorization") || "";
      const supplied = auth.toLowerCase().startsWith("bearer ")
        ? auth.slice(7).trim()
        : "";
      if (!supplied || !constantTimeEqual(supplied, expectedToken)) {
        return jsonResponse({ error: "Unauthorized" }, 401, headers);
      }
    }

    const clientIp =
      request.headers.get("cf-connecting-ip") ||
      request.headers.get("x-forwarded-for") ||
      "127.0.0.1";
    const rateLimit = Number.parseInt(String(env.AI2027_RATE_LIMIT_PER_MIN || "30"), 10) || 30;
    if (!checkRateLimit(clientIp, rateLimit, nowFn())) {
      return jsonResponse({ error: "Rate limit exceeded", trace_id: traceId }, 429, headers);
    }

    const rawBody = await request.text();
    if (new TextEncoder().encode(rawBody).length > MAX_BODY_BYTES) {
      return jsonResponse({ error: "Payload too large" }, 413, headers);
    }

    let payload;
    try {
      payload = JSON.parse(rawBody || "{}");
    } catch {
      return jsonResponse({ error: "Invalid JSON body" }, 400, headers);
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

    if (allowRemote && env.GEMINI_API_KEY && !String(env.GEMINI_API_KEY).includes("placeholder")) {
      const model = String(env.GEMINI_MODEL || "").trim() || "gemini-2.5-flash";
      try {
        const gemRes = await fetchImpl(
          `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`,
          {
            method: "POST",
            headers: {
              "content-type": "application/json",
              "x-goog-api-key": String(env.GEMINI_API_KEY).trim(),
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
            return jsonResponse(
              {
                provider: "gemini",
                model,
                text: text.trim(),
                simulated: false,
                gateway_proxied: false,
                cost_usd: 0.0,
                trace_id: traceId,
              },
              200,
              headers,
            );
          }
        }
      } catch {
        // Fall through to NVIDIA or deterministic fallback
      }
    }

    if (allowRemote && env.NVIDIA_API_KEY && !String(env.NVIDIA_API_KEY).includes("placeholder")) {
      const model = String(env.NVIDIA_MODEL || "").trim() || "meta/llama-3.1-8b-instruct";
      try {
        const nvRes = await fetchImpl("https://integrate.api.nvidia.com/v1/chat/completions", {
          method: "POST",
          headers: {
            "content-type": "application/json",
            authorization: `Bearer ${String(env.NVIDIA_API_KEY).trim()}`,
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
            return jsonResponse(
              {
                provider: "nvidia",
                model,
                text: text.trim(),
                simulated: false,
                gateway_proxied: false,
                cost_usd: 0.0,
                trace_id: traceId,
              },
              200,
              headers,
            );
          }
        }
      } catch {
        // Fall through to deterministic $0 edge fallback
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
        trace_id: traceId,
      },
      200,
      headers,
    );
  }

  return jsonResponse({ error: "Not found" }, 404, headers);
}

export default {
  async fetch(request, env) {
    return handleGatewayFetch(request, env);
  },
};
