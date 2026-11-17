/**
 * Unit tests — template fallback semantics (AUDIT-001 F2 mandatory follow-up).
 * Run: npm run test:unit
 * Guards against the fallback ever becoming a silent failure.
 */
import { resolveTemplates } from '../src/agent/templates';

let passed = 0, failed = 0;
function eq(actual: unknown, expected: unknown, label: string) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a === e) { console.log(`  ✅ ${label}`); passed++; }
  else { console.error(`  ❌ ${label}\n     expected: ${e}\n     actual:   ${a}`); failed++; }
}

const outputs = [
  { url: 'https://x', title: 'Example Domain', status: 200 },          // browser.run
  { stdout: '19\n', stderr: '', exitCode: 0 },                          // shell.exec
  { path: 'a.txt', bytesWritten: 5 },                                   // fs.readwrite (no salient string)
];

console.log('═══ F2 follow-up: {{steps.N.KEY}} semantics ═══');

// 1) known keys — exact value
eq(resolveTemplates('{{steps.0.title}}', outputs), 'Example Domain', 'known key → exact value');
eq(resolveTemplates('{{steps.1.stdout}}', outputs), '19\n', 'known key (stdout) → exact value');

// 2) unknown key "output" → salient scalar fallback (documented behavior)
eq(resolveTemplates('{{steps.0.output}}', outputs), 'Example Domain', "unknown 'output' → salient title");
eq(resolveTemplates('{{steps.1.output}}', outputs), '19\n', "unknown 'output' → salient stdout");

// 3) unknown key, no salient string → compact JSON (NOT silent empty)
eq(resolveTemplates('{{steps.2.output}}', outputs), JSON.stringify(outputs[2]), 'no salient key → compact JSON');

// 4) missing step index → empty string (defined, not crash)
eq(resolveTemplates('{{steps.9.title}}', outputs), '', 'missing step → empty string');

// 5) nested resolution inside objects/arrays
eq(resolveTemplates({ content: 'T={{steps.0.title}}', list: ['{{steps.1.exitCode}}'] }, outputs),
   { content: 'T=Example Domain', list: ['0'] }, 'nested object/array resolution');

// 6) non-template strings untouched
eq(resolveTemplates('plain text', outputs), 'plain text', 'plain string untouched');

console.log(`\n═══ UNIT RESULT: ${passed} passed, ${failed} failed ═══`);
process.exit(failed === 0 ? 0 : 1);
