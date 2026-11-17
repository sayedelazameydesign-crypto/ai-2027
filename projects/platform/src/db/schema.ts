import { sqliteTable, text, integer } from 'drizzle-orm/sqlite-core';

/**
 * Minimal chat schema — the "existing schema" for MVP purposes.
 * Per EXECUTION-ORDER-MVP.md §2: no further migrations; ephemeral run state
 * lives in the in-memory bus, NOT in new tables.
 */
export const messages = sqliteTable('messages', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  role: text('role', { enum: ['user', 'assistant', 'system'] }).notNull(),
  content: text('content').notNull(),
  runId: text('run_id'),
  createdAt: integer('created_at', { mode: 'timestamp_ms' })
    .notNull()
    .$defaultFn(() => new Date()),
});

export type MessageRow = typeof messages.$inferSelect;

/**
 * v2 Phase 1 — Module 2 contract: persistent run registry.
 * Deliberate, announced migration (the MVP "zero migrations" gate closed
 * with the MVP itself; this is the first approved v2 table).
 */
export const runs = sqliteTable('runs', {
  id: text('id').primaryKey(),
  task: text('task').notNull(),
  // Module 2 states (Phase 1 subset):
  // goal_submitted | planning | plan_validated | plan_rejected |
  // executing | completed | failed | cancelled
  status: text('status').notNull().default('goal_submitted'),
  plan: text('plan', { mode: 'json' }),
  trace: text('trace', { mode: 'json' }),
  cancelReason: text('cancel_reason'),
  createdAt: integer('created_at', { mode: 'timestamp_ms' })
    .notNull()
    .$defaultFn(() => new Date()),
  updatedAt: integer('updated_at', { mode: 'timestamp_ms' })
    .notNull()
    .$defaultFn(() => new Date()),
});

export type RunRow = typeof runs.$inferSelect;

/**
 * v2 Phase 2 — Module 2 approval gate + celia binding principle:
 * an approval is bound to (runId, stepIndex, tool, planHash); any mismatch
 * blocks execution — deny-by-default.
 */
export const approvals = sqliteTable('approvals', {
  id: text('id').primaryKey(),
  runId: text('run_id').notNull(),
  stepIndex: integer('step_index').notNull(),
  tool: text('tool').notNull(),
  input: text('input', { mode: 'json' }),
  planHash: text('plan_hash').notNull(),
  status: text('status').notNull().default('pending'), // pending | approved | rejected
  decisionReason: text('decision_reason'),
  createdAt: integer('created_at', { mode: 'timestamp_ms' })
    .notNull()
    .$defaultFn(() => new Date()),
  decidedAt: integer('decided_at', { mode: 'timestamp_ms' }),
});

export type ApprovalRow = typeof approvals.$inferSelect;
