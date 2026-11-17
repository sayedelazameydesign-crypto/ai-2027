/**
 * LLM Adapter — EXECUTION-ORDER-MVP.md §2.
 * Single interface, one provider. If OPENAI_API_KEY is set, calls OpenAI;
 * otherwise falls back to a deterministic planner/summarizer so the loop is
 * provable end-to-end offline. No routing, no fallback chains beyond this.
 */

export interface Message {
  role: 'system' | 'user' | 'assistant';
  content: string;
}

export interface CompletionOpts {
  json?: boolean;
  maxTokens?: number;
}

export async function complete(messages: Message[], opts?: CompletionOpts): Promise<string> {
  const key = process.env.OPENAI_API_KEY;
  // LLM_MODE=mock forces the deterministic planner even when a key exists —
  // keeps the e2e acceptance suite a stable, reproducible CI gate.
  if (key && process.env.LLM_MODE !== 'mock') return openaiComplete(key, messages, opts);
  return deterministicComplete(messages);
}

async function openaiComplete(key: string, messages: Message[], opts?: CompletionOpts): Promise<string> {
  const base = (process.env.OPENAI_BASE_URL ?? 'https://api.openai.com/v1').replace(/\/$/, '');
  const res = await fetch(`${base}/chat/completions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${key}` },
    body: JSON.stringify({
      model: process.env.OPENAI_MODEL ?? 'gpt-4o-mini',
      messages,
      max_tokens: opts?.maxTokens ?? 1024,
      ...(opts?.json ? { response_format: { type: 'json_object' } } : {}),
    }),
  });
  if (!res.ok) throw new Error(`LLM HTTP ${res.status}: ${(await res.text()).slice(0, 300)}`);
  const data = (await res.json()) as { choices: { message: { content: string } }[] };
  return data.choices[0]?.message?.content ?? '';
}

/* ------------------------------------------------------------------ */
/* Deterministic fallback: proves the loop without external API keys.  */
/* ------------------------------------------------------------------ */

function deterministicComplete(messages: Message[]): string {
  const system = messages.find((m) => m.role === 'system')?.content ?? '';
  // Always plan from the ORIGINAL task (first user message) — a repair-retry
  // message must not be mistaken for the task itself.
  const user = messages.find((m) => m.role === 'user')?.content ?? '';
  if (system.includes('[PLANNER]')) return deterministicPlan(user);
  if (system.includes('[SUMMARIZER]')) return deterministicSummary(user);
  return 'OK';
}

function deterministicPlan(task: string): string {
  const url = task.match(/https?:\/\/[^\s"')]+/)?.[0]?.replace(/[.,;:!?)\]]+$/, '');
  const fileMatch = task.match(/([\w-]+\.(?:txt|md|json|csv|html))/i)?.[1];
  const steps: { tool: string; input: Record<string, unknown> }[] = [];

  // Honor explicit "with/using the X tool" phrasing — including nonexistent
  // tools, so invalid plans surface as Zod failures (acceptance test §6.6).
  const explicit = [...task.matchAll(/(?:with|using) the ([\w.]+) tool/gi)].map((m) => m[1].toLowerCase());
  const alias: Record<string, string> = {
    browser: 'browser.run',
    shell: 'shell.exec',
    web: 'web.fetch',
    fetch: 'web.fetch',
    fs: 'fs.readwrite',
    file: 'fs.readwrite',
  };
  const unknown = explicit.find((e) => !alias[e] && !['browser.run', 'shell.exec', 'web.fetch', 'fs.readwrite'].includes(e));
  if (unknown) {
    steps.push({ tool: `${unknown}.run`, input: { task } });
    steps.push({ tool: 'fs.readwrite', input: { mode: 'write', path: 'result.txt', content: 'unreachable' } });
    return JSON.stringify({ steps });
  }

  const wantsTitle = /title/i.test(task);
  const browserAvailable = process.env.BROWSER_DISABLED !== '1';
  const wantsBrowser = browserAvailable
    && (explicit.some((e) => alias[e] === 'browser.run') || /browser/i.test(task));

  // Long-running task support (used by the cancellation acceptance test):
  // "sleep N" + shell → a step that blocks ~N seconds inside the sandbox.
  const sleepSec = task.match(/sleep\s+(\d+)/i)?.[1];
  if (sleepSec && (explicit.some((e) => alias[e] === 'shell.exec') || /shell/i.test(task))) {
    const ms = Math.min(Number(sleepSec), 55) * 1000;
    steps.push({
      tool: 'shell.exec',
      input: { bin: 'node', args: ['-e', `setTimeout(() => console.log('slept'), ${ms})`] },
    });
    steps.push({ tool: 'fs.readwrite', input: { mode: 'write', path: 'sleep.txt', content: 'done' } });
    return JSON.stringify({ steps });
  }


  if (url && wantsBrowser) {
    steps.push({ tool: 'browser.run', input: { url, action: wantsTitle ? 'extractTitle' : 'extractText' } });
  } else if (url) {
    steps.push({ tool: 'web.fetch', input: { url } });
  } else if (explicit.length > 0) {
    steps.push({ tool: 'shell.exec', input: { bin: 'node', args: ['-e', 'console.log("noop")'] } });
  } else {
    // Module 2: ambiguous goal → ask, don't guess.
    return JSON.stringify({ clarify: 'ما الهدف تحديدًا؟ اذكر رابطًا لجلبه، أو أمرًا لتنفيذه، أو ملفًا لكتابته.' });
  }

  if (fileMatch || /write|save|احفظ|اكتب/i.test(task)) {
    steps.push({
      tool: 'fs.readwrite',
      input: {
        mode: 'write',
        path: fileMatch ?? 'result.txt',
        content: wantsTitle ? '{{steps.0.title}}' : '{{steps.0.text}}',
      },
    });
  } else {
    steps.push({ tool: 'fs.readwrite', input: { mode: 'write', path: 'trace.txt', content: '{{steps.0.title}}' } });
  }
  return JSON.stringify({ steps });
}

function deterministicSummary(context: string): string {
  const titles = [...context.matchAll(/"title"\s*:\s*"([^"]+)"/g)].map((m) => m[1]);
  const files = [...context.matchAll(/"path"\s*:\s*"([^"]+)"/g)].map((m) => m[1]);
  const parts: string[] = ['Task completed.'];
  if (titles.length) parts.push(`Extracted title: "${titles[0]}".`);
  if (files.length) parts.push(`Wrote file: ${files[files.length - 1]}.`);
  return parts.join(' ');
}
