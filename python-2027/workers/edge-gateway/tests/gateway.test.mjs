import assert from "node:assert/strict";
import test from "node:test";
import { handleGatewayFetch, resetRateLimitsForTest } from "../src/index.mjs";

test("GET /health reports $0 gateway readiness and security headers", async () => {
  resetRateLimitsForTest();
  const res = await handleGatewayFetch(new Request("https://edge.local/health"));
  assert.equal(res.status, 200);
  assert.equal(res.headers.get("x-content-type-options"), "nosniff");
  assert.ok(res.headers.get("x-trace-id"));
  const data = await res.json();
  assert.equal(data.ok, true);
  assert.equal(data.cost_usd, 0);
  assert.equal(data.upstream_configured, false);
});

test("POST /v1/route enforces Bearer auth when GATEWAY_TOKEN is configured", async () => {
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
  assert.equal(body.cost_usd, 0);
});

test("POST /v1/route enforces per-IP sliding-window rate limit", async () => {
  resetRateLimitsForTest();
  const env = { AI2027_RATE_LIMIT_PER_MIN: "2" };
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
  const env = { UPSTREAM_URL: "https://colab-backend.example.com" };
  const okProxy = await handleGatewayFetch(
    new Request("https://edge.local/v1/route", {
      method: "POST",
      body: JSON.stringify({ prompt: "run via python backend" }),
    }),
    env,
    {
      fetchImpl: async () =>
        new Response(JSON.stringify({ provider: "groq", text: "from python", cost_usd: 0 }), {
          status: 200,
        }),
    },
  );
  assert.equal(okProxy.status, 200);
  const okBody = await okProxy.json();
  assert.equal(okBody.gateway_proxied, true);
  assert.equal(okBody.provider, "groq");

  const fallbackRes = await handleGatewayFetch(
    new Request("https://edge.local/v1/route", {
      method: "POST",
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

test("POST /v1/route validates input and rejects non-HTTPS upstream URLs", async () => {
  resetRateLimitsForTest();
  const badJson = await handleGatewayFetch(
    new Request("https://edge.local/v1/route", { method: "POST", body: "{not-json" }),
  );
  assert.equal(badJson.status, 400);

  const blankPrompt = await handleGatewayFetch(
    new Request("https://edge.local/v1/route", {
      method: "POST",
      body: JSON.stringify({ prompt: "   " }),
    }),
  );
  assert.equal(blankPrompt.status, 400);

  const httpUpstreamIgnored = await handleGatewayFetch(
    new Request("https://edge.local/health"),
    { UPSTREAM_URL: "http://insecure.example.com" },
  );
  const health = await httpUpstreamIgnored.json();
  assert.equal(health.upstream_configured, false);
});

test("POST /v1/route uses GEMINI_API_KEY and NVIDIA_API_KEY when UPSTREAM_URL is absent", async () => {
  resetRateLimitsForTest();
  const seen = [];
  const res = await handleGatewayFetch(
    new Request("https://edge.local/v1/route", {
      method: "POST",
      body: JSON.stringify({ prompt: "hello actions keys" }),
    }),
    {
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
  assert.equal(data.simulated, false);
  assert.equal(seen.length, 2);
});
