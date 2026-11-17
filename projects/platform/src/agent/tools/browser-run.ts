import { z } from 'zod';
import { register, Tool } from './registry';

/**
 * browser.run — §4: headless chromium via Playwright.
 * One browser instance per run (reused across steps), closed in `finally`
 * by the loop's cleanup. 15s navigation timeout. No downloads, no auth.
 */

const inputSchema = z.object({
  url: z.string().url(),
  action: z.enum(['extractTitle', 'extractText']),
});

const outputSchema = z.object({
  url: z.string(),
  title: z.string().optional(),
  text: z.string().optional(),
  status: z.number().nullable(),
  /** Screenshot file name inside the run sandbox — rendered live in the UI "agent computer". */
  screenshot: z.string().optional(),
});

type I = z.infer<typeof inputSchema>;
type O = z.infer<typeof outputSchema>;

// Per-run browser cache; loop.ts calls closeBrowser(runId) in its finally block.
type PWBrowser = import('playwright').Browser;
const g = globalThis as unknown as { __browsers?: Map<string, PWBrowser> };
const browsers = g.__browsers ?? (g.__browsers = new Map());

async function getBrowser(runId: string): Promise<PWBrowser> {
  const existing = browsers.get(runId);
  if (existing && existing.isConnected()) return existing;
  const { chromium } = await import('playwright');
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  browsers.set(runId, browser);
  return browser;
}

export async function closeBrowser(runId: string): Promise<void> {
  const b = browsers.get(runId);
  browsers.delete(runId);
  if (b) await b.close().catch(() => {});
}

const browserRun: Tool<I, O> = {
  name: 'browser.run',
  risk: 'safe',
  description: 'Open a URL in headless chromium and extract the page title or visible text. 15s navigation timeout.',
  inputSchema,
  outputSchema,
  async execute(input, ctx): Promise<O> {
    const browser = await getBrowser(ctx.runId);
    const page = await browser.newPage({ acceptDownloads: false });
    try {
      const resp = await page.goto(input.url, { timeout: 15_000, waitUntil: 'domcontentloaded' });
      const out: O = { url: input.url, status: resp?.status() ?? null };
      if (input.action === 'extractTitle') out.title = await page.title();
      if (input.action === 'extractText') out.text = (await page.innerText('body')).slice(0, 100 * 1024);
      // Live view for the UI: capture what the agent's browser sees.
      const shotName = `browser-${Date.now()}.png`;
      const path = await import('node:path');
      await page.screenshot({ path: path.join(ctx.sandboxDir, shotName) }).then(
        () => { out.screenshot = shotName; },
        () => {},
      );
      return out;
    } finally {
      await page.close().catch(() => {});
    }
  },
};

register(browserRun);
export { browserRun };
