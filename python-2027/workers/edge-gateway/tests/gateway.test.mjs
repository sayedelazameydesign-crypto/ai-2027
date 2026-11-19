import assert from "node:assert/strict";
import test from "node:test";
import { handleGatewayFetch, resetRateLimitsForTest } from "../src/index.mjs";

test("GET /health reports $0 gateway readiness and traceparent headers", async () => {
  resetRateLimitsForTest();
  const res = await handleGatewayFetch(
    new Request("https://edge.local/health", {
      headers: { traceparent: "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01" },
    }),
  );
  assert.equal(res.status, 200);
  assert.equal(res.headers.get("x-content-type-options"), "nosniff");
  assert.equal(res.headers.get("x-trace-id"), "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
  assert.ok((res.headers.get("traceparent") || "").startsWith("00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-"));
  const data = await res.json();
  assert.equal(data.ok, true);
  assert.equal(data.cost_usd, 0);
  assert.equal(data.max_spend_usd, 0);
});

test("POST /v1/route is fail-closed without GATEWAY_TOKEN or AI2027_PUBLIC_ROUTES", async () => {
  resetRateLimitsForTest();
  const res = await handleGatewayFetch(
    new Request("https://edge.local/v1/route", {
      method: "POST",
      body: JSON.stringify({ prompt: "hello" }),
    }),
    {},
  );
  assert.equal(res.status, 401);
  const body = await res.json();
  assert.match(body.error, /Fail-closed/i);
});

test("POST /v1/route enforces Bearer auth and bounded payload limit", async () => {
  resetRateLimitsForTest();
  const env = { GATEWAY_TOKEN: "secret-gateway-fixture" };
  const unauth = await handleGatewayFetch(
    new Request("https://edge.local/v1/route", {
      method: "POST",
      body: JSON.stringify({ prompt: "hello" }),
    }),
    env,
  );
  assert.equal(unauth.status, 401);

  const tooBigHeader = await handleGatewayFetch(
    new Request("https://edge.local/v1/route", {
      method: "POST",
      headers: {
        authorization: "Bearer secret-gateway-fixture",
        "content-length": "999999",
      },
      body: JSON.stringify({ prompt: "small" }),
    }),
    env,
  );
  assert.equal(tooBigHeader.status, 413);

  const authed = await handleGatewayFetch(
    new Request("https://edge.local/v1/route", {
      method: "POST",
      headers: { authorization: "Bearer secret-gateway-fixture" },
      body: JSON.stringify({ prompt: "hello world" }),
    }),
    env,
  );
  assert.equal(authed.status, 200);
  const body = await authed.json();
  assert.equal(body.provider, "deterministic");
  assert.equal(body.simulated, true);
  assert.equal(body.billing_tier, "local");
  assert.equal(body.cost_known, true);
});

test("POST /v1/route blocks unapproved model at MAX_SPEND=0 even when API key exists", async () => {
  resetRateLimitsForTest();
  let called = false;
  const res = await handleGatewayFetch(
    new Request("https://edge.local/v1/route", {
      method: "POST",
      headers: { authorization: "Bearer tok-budget" },
      body: JSON.stringify({ prompt: "block unknown cost model" }),
    }),
    {
      GATEWAY_TOKEN: "tok-budget",
      GEMINI_API_KEY: "AIza-edge-key-present",
      GEMINI_MODEL: "gemini-2.5-pro-paid-unknown",
      AI2027_MAX_SPEND_USD: "0",
    },
    {
      fetchImpl: async () => {
        called = true;
        return new Response("unexpected", { status: 200 });
      },
    },
  );
  assert.equal(res.status, 200);
  assert.equal(called, false);
  const data = await res.json();
  assert.equal(data.provider, "deterministic");
  assert.equal(data.unknown_cost_blocked, 1);
  assert.equal(data.attempts[0].error, "budget_blocked");
});

test("POST /v1/route enforces per-IP sliding-window rate limit when explicitly public", async () => {
  resetRateLimitsForTest();
  const env = { AI2027_PUBLIC_ROUTES: "/v1/route", AI2027_RATE_LIMIT_PER_MIN: "2" };
  const makeReq = () =>
    new Request("https://edge.local/v1/route", {
      method: "POST",
      headers: { "cf-connecting-ip": "203.0.113.10" },
      body: JSON.stringify({ prompt: "ping" }),
    });
  assert.equal((await handleGatewayFetch(makeReq(), env, { nowFn: () => 1000 })).status, 200);
  assert.equal((await handleGatewayFetch(makeReq(), env, { nowFn: () => 2000 })).status, 200);
  const blocked = await handleGatewayFetch(makeReq(), env, { nowFn: () => 3000 });
  assert.equal(blocked.status, 429);
});

test("POST /v1/route proxies to HTTPS upstream and falls back when upstream fails", async () => {
  resetRateLimitsForTest();
  const env = {
    GATEWAY_TOKEN: "tok-proxy",
    UPSTREAM_URL: "https://colab-backend.example.com",
  };
  const okProxy = await handleGatewayFetch(
    new Request("https://edge.local/v1/route", {
      method: "POST",
      headers: { authorization: "Bearer tok-proxy" },
      body: JSON.stringify({ prompt: "run via python backend" }),
    }),
    env,
    {
      fetchImpl: async (_url, init) => {
        assert.ok(init.headers.traceparent);
        return new Response(JSON.stringify({ provider: "groq", text: "from python", cost_usd: 0 }), {
          status: 200,
        });
      },
    },
  );
  assert.equal(okProxy.status, 200);
  const okBody = await okProxy.json();
  assert.equal(okBody.gateway_proxied, true);
  assert.equal(okBody.provider, "groq");

  const fallbackRes = await handleGatewayFetch(
    new Request("https://edge.local/v1/route", {
      method: "POST",
      headers: { authorization: "Bearer tok-proxy" },
      body: JSON.stringify({ prompt: "upstream down test" }),
    }),
    env,
    {
      fetchImpl: async () => {
        throw new Error("tunnel offline");
      },
    },
  );
  assert.equal(fallbackRes.status, 200);
  const fbBody = await fallbackRes.json();
  assert.equal(fbBody.gateway_proxied, false);
  assert.equal(fbBody.simulated, true);
});

test("POST /v1/route uses GEMINI_API_KEY and NVIDIA_API_KEY when UPSTREAM_URL is absent", async () => {
  resetRateLimitsForTest();
  const seen = [];
  const res = await handleGatewayFetch(
    new Request("https://edge.local/v1/route", {
      method: "POST",
      headers: { authorization: "Bearer tok-keys" },
      body: JSON.stringify({ prompt: "hello actions keys" }),
    }),
    {
      GATEWAY_TOKEN: "tok-keys",
      GEMINI_API_KEY: "AIza-edge-fixture-key",
      NVIDIA_API_KEY: "nvapi-edge-fixture-key",
      NVIDIA_MODEL: "meta/llama-3.1-8b-instruct",
    },
    {
      fetchImpl: async (url, init) => {
        seen.push(String(url));
        if (new URL(String(url)).hostname === "generativelanguage.googleapis.com") {
          assert.equal(init.headers["x-goog-api-key"], "AIza-edge-fixture-key");
          return new Response("quota", { status: 429 });
        }
        assert.equal(new URL(String(url)).hostname, "integrate.api.nvidia.com");
        assert.equal(init.headers.authorization, "Bearer nvapi-edge-fixture-key");
        return new Response(
          JSON.stringify({ choices: [{ message: { content: "nvidia edge ok" } }] }),
          { status: 200 },
        );
      },
    },
  );
  assert.equal(res.status, 200);
  const data = await res.json();
  assert.equal(data.provider, "nvidia");
  assert.equal(data.text, "nvidia edge ok");
  assert.equal(data.billing_tier, "free_tier");
  assert.equal(data.cost_known, false);
  assert.equal(seen.length, 2);
});

test("POST /v1/agent/plan returns structured 4-step edge plan and enforces auth", async () => {
  resetRateLimitsForTest();
  const unauth = await handleGatewayFetch(
    new Request("https://edge.local/v1/agent/plan", {
      method: "POST",
      body: JSON.stringify({ task: "Analyze repo" }),
    }),
    { GATEWAY_TOKEN: "secret-gw-token" },
  );
  assert.equal(unauth.status, 401);

  const ok = await handleGatewayFetch(
    new Request("https://edge.local/v1/agent/plan", {
      method: "POST",
      headers: { authorization: "Bearer secret-gw-token" },
      body: JSON.stringify({ task: "Build verified HTML dashboard" }),
    }),
    { GATEWAY_TOKEN: "secret-gw-token" },
  );
  assert.equal(ok.status, 200);
  const body = await ok.json();
  assert.equal(body.steps.length, 4);
  assert.equal(body.cost_usd, 0.0);
  assert.equal(body.billing_tier, "local");

  const badMethod = await handleGatewayFetch(new Request("https://edge.local/v1/agent/plan"));
  assert.equal(badMethod.status, 405);
});
