// Daily Vercel cron (Hobby: once/day). An activity source OUTSIDE Supabase so the
// Free project is not judged idle even if its own pg_cron were to stop.
const URL = "https://ziaedqqhvsmcwcmlbcpi.supabase.co/functions/v1/ycloud-webhook/healthz";
export default async function handler(req, res) {
  try {
    const r = await fetch(URL, { cache: "no-store", signal: AbortSignal.timeout(10000) });
    const body = await r.json().catch(() => null);
    const ok = r.status === 200 && body && body.ok === true;
    res.status(ok ? 200 : 503).json({ ok, http: r.status, at: new Date().toISOString() });
  } catch (e) {
    res.status(503).json({ ok: false, error: e && e.name ? e.name : "error", at: new Date().toISOString() });
  }
}
