import Database from 'better-sqlite3';
import { drizzle as drizzleSqlite, BetterSQLite3Database } from 'drizzle-orm/better-sqlite3';
import { drizzle as drizzleLibsql } from 'drizzle-orm/libsql';
import { createClient } from '@libsql/client';
import * as schema from './schema';

/**
 * Dual-driver DB (zero-card deployment path):
 *  - TURSO_DATABASE_URL set  → libSQL/Turso (persistent, survives ephemeral hosts)
 *  - otherwise               → local better-sqlite3 file (dev / VM with disk)
 * Same Drizzle schema + query API. Write ordering for fire-and-forget trace
 * updates is guaranteed by the loop's write queue, not by the driver.
 */

const DDL = `
    CREATE TABLE IF NOT EXISTS messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      role TEXT NOT NULL,
      content TEXT NOT NULL,
      run_id TEXT,
      created_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS runs (
      id TEXT PRIMARY KEY,
      task TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'goal_submitted',
      plan TEXT,
      trace TEXT,
      cancel_reason TEXT,
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS approvals (
      id TEXT PRIMARY KEY,
      run_id TEXT NOT NULL,
      step_index INTEGER NOT NULL,
      tool TEXT NOT NULL,
      input TEXT,
      plan_hash TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending',
      decision_reason TEXT,
      created_at INTEGER NOT NULL,
      decided_at INTEGER
    );
`;

type DB = BetterSQLite3Database<typeof schema>;
const g = globalThis as unknown as { __db?: DB; __dbReady?: Promise<void> };

function init(): DB {
  const tursoUrl = process.env.TURSO_DATABASE_URL;
  if (tursoUrl) {
    const client = createClient({ url: tursoUrl, authToken: process.env.TURSO_AUTH_TOKEN });
    g.__dbReady = client.executeMultiple(DDL).then(() => undefined);
    // Same sqlite-core schema/API; async driver — cast keeps call-sites unchanged,
    // ordering handled by the loop write queue.
    return drizzleLibsql(client, { schema }) as unknown as DB;
  }
  const sqlite = new Database(process.env.DB_PATH ?? 'sqlite.db');
  sqlite.pragma('journal_mode = WAL');
  sqlite.exec(DDL);
  g.__dbReady = Promise.resolve();
  return drizzleSqlite(sqlite, { schema });
}

export const db = g.__db ?? (g.__db = init());
/** Await before first query on cold-started ephemeral hosts (DDL race guard). */
export const dbReady: Promise<void> = g.__dbReady ?? Promise.resolve();
export { schema };
