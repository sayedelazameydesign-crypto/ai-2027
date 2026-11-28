// Server-side proxy to the PII-free Supabase watchdog status (no secrets involved).
const BASE = "https://ziaedqqhvsmcwcmlbcpi.supabase.co/functions/v1/ycloud-webhook";

async function probe(path) {
  const t0 = Date.now();
  try {
    const r = await fetch(BASE + path, { cache: "no-store", signal: AbortSignal.timeout(10000) });
    const text = await r.text();
    let body = null;
    try { body = JSON.parse(text); } catch { body = null; }
    return { http: r.status, ms: Date.now() - t0, body };
  } catch (e) {
    return { http: 0, ms: Date.now() - t0, error: e && e.name ? e.name : "error" };
  }
}

export default async function handler(req, res) {
  const [healthz, status] = await Promise.all([probe("/healthz"), probe("/status")]);
  res.setHeader("Cache-Control", "public, s-maxage=30, stale-while-revalidate=30");
  res.status(200).json({
    checked_at: new Date().toISOString(),
    observer: "vercel",
    healthz,
    status: status.body,
    status_http: status.http,
  });
}
