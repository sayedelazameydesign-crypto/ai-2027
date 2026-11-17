import { z } from 'zod';

/** Tool contract — EXECUTION-ORDER-MVP.md §4. */
export interface ToolContext {
  runId: string;
  sandboxDir: string; // /tmp/agent-runs/<runId>/
  signal: AbortSignal;
  kv: Map<string, unknown>;
}

export interface Tool<I = unknown, O = unknown> {
  name: string;
  description: string;
  /** Phase 2 policy: 'sensitive' tools require human approval before execution (deny-by-default). */
  risk: 'safe' | 'sensitive';
  /** Zod schema may transform (e.g. defaults): input type I, any raw shape. */
  inputSchema: z.ZodType<I, z.ZodTypeDef, any>;
  outputSchema: z.ZodType<O, z.ZodTypeDef, any>;
  execute(input: I, ctx: ToolContext): Promise<O>;
}

const registry = new Map<string, Tool<any, any>>();

export function register<I, O>(tool: Tool<I, O>): void {
  registry.set(tool.name, tool as Tool<any, any>);
}

export function getTool(name: string): Tool<any, any> | undefined {
  return registry.get(name);
}

export function toolNames(): string[] {
  return [...registry.keys()];
}

/** Rendered into the planner prompt. */
export function manifest(): string {
  return [...registry.values()]
    .map((t) => {
      const inShape = JSON.stringify(zodToHint(t.inputSchema));
      const outShape = JSON.stringify(zodToHint(t.outputSchema));
      return `- ${t.name}: ${t.description}\n  input: ${inShape}\n  output keys (for {{steps.N.KEY}} refs): ${outShape}`;
    })
    .join('\n');
}

function zodToHint(schema: z.ZodTypeAny): unknown {
  const def = schema._def;
  if (def.typeName === 'ZodObject') {
    const out: Record<string, unknown> = {};
    const shape = (schema as z.AnyZodObject).shape;
    for (const k of Object.keys(shape)) out[k] = zodToHint(shape[k]);
    return out;
  }
  if (def.typeName === 'ZodEnum') return def.values.join('|');
  if (def.typeName === 'ZodOptional') return `${JSON.stringify(zodToHint(def.innerType))}?`;
  if (def.typeName === 'ZodArray') return [zodToHint(def.type)];
  return def.typeName.replace('Zod', '').toLowerCase();
}

/** Zod-parse in, Zod-parse out — §4. Parse failure = step error, verbatim. */
export async function runTool(name: string, rawInput: unknown, ctx: ToolContext): Promise<unknown> {
  const tool = getTool(name);
  if (!tool) throw new Error(`Tool not found in registry: ${name}`);
  const input = tool.inputSchema.parse(rawInput);
  const output = await tool.execute(input, ctx);
  return tool.outputSchema.parse(output);
}
