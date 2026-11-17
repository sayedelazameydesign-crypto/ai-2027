import { and, desc, eq } from "drizzle-orm";
import { drizzle } from "drizzle-orm/mysql2";
import { InsertUser, Task, InsertTask, tasks, users } from "../drizzle/schema";
import { ENV } from "./_core/env";

let _db: ReturnType<typeof drizzle> | null = null;

export async function getDb() {
  if (!_db && process.env.DATABASE_URL) {
    try {
      _db = drizzle(process.env.DATABASE_URL);
    } catch (error) {
      console.warn("[Database] Failed to connect:", error);
      _db = null;
    }
  }
  return _db;
}

export async function upsertUser(user: InsertUser): Promise<void> {
  if (!user.openId) throw new Error("User openId is required for upsert");

  const db = await getDb();
  if (!db) {
    console.warn("[Database] Cannot upsert user: database not available");
    return;
  }

  const values: InsertUser = { openId: user.openId };
  const updateSet: Record<string, unknown> = {};
  const textFields = ["name", "email", "loginMethod"] as const;

  for (const field of textFields) {
    if (user[field] !== undefined) {
      const normalized = user[field] ?? null;
      values[field] = normalized;
      updateSet[field] = normalized;
    }
  }

  if (user.lastSignedIn !== undefined) {
    values.lastSignedIn = user.lastSignedIn;
    updateSet.lastSignedIn = user.lastSignedIn;
  }
  if (user.role !== undefined) {
    values.role = user.role;
    updateSet.role = user.role;
  } else if (user.openId === ENV.ownerOpenId) {
    values.role = "admin";
    updateSet.role = "admin";
  }
  if (!values.lastSignedIn) values.lastSignedIn = new Date();
  if (Object.keys(updateSet).length === 0) updateSet.lastSignedIn = new Date();

  await db.insert(users).values(values).onDuplicateKeyUpdate({ set: updateSet });
}

export async function getUserByOpenId(openId: string) {
  const db = await getDb();
  if (!db) return undefined;
  const result = await db.select().from(users).where(eq(users.openId, openId)).limit(1);
  return result[0];
}

export async function getTasksByUserId(userId: number): Promise<Task[]> {
  const db = await getDb();
  if (!db) throw new Error("Database is not available");
  return db.select().from(tasks).where(eq(tasks.userId, userId)).orderBy(desc(tasks.createdAt));
}

export async function createTask(userId: number, title: string): Promise<Task> {
  const db = await getDb();
  if (!db) throw new Error("Database is not available");
  const result = await db.insert(tasks).values({ userId, title, completed: false });
  const created = await db.select().from(tasks).where(and(eq(tasks.id, result[0].insertId), eq(tasks.userId, userId))).limit(1);
  if (!created[0]) throw new Error("Task was not created");
  return created[0];
}

export async function updateTaskTitle(userId: number, taskId: number, title: string): Promise<Task> {
  const db = await getDb();
  if (!db) throw new Error("Database is not available");
  await db.update(tasks).set({ title }).where(and(eq(tasks.id, taskId), eq(tasks.userId, userId)));
  const updated = await db.select().from(tasks).where(and(eq(tasks.id, taskId), eq(tasks.userId, userId))).limit(1);
  if (!updated[0]) throw new Error("Task not found");
  return updated[0];
}

export async function setTaskCompleted(userId: number, taskId: number, completed: boolean): Promise<Task> {
  const db = await getDb();
  if (!db) throw new Error("Database is not available");
  await db.update(tasks).set({ completed }).where(and(eq(tasks.id, taskId), eq(tasks.userId, userId)));
  const updated = await db.select().from(tasks).where(and(eq(tasks.id, taskId), eq(tasks.userId, userId))).limit(1);
  if (!updated[0]) throw new Error("Task not found");
  return updated[0];
}

export async function deleteTask(userId: number, taskId: number): Promise<void> {
  const db = await getDb();
  if (!db) throw new Error("Database is not available");
  await db.delete(tasks).where(and(eq(tasks.id, taskId), eq(tasks.userId, userId)));
}
