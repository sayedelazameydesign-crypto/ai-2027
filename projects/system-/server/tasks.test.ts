import { beforeEach, describe, expect, it, vi } from "vitest";
import { appRouter } from "./routers";
import type { TrpcContext } from "./_core/context";
import type { Task } from "../drizzle/schema";
import { createTask, deleteTask, getTasksByUserId, setTaskCompleted, updateTaskTitle } from "./db";

vi.mock("./db", () => ({
  createTask: vi.fn(),
  deleteTask: vi.fn(),
  getTasksByUserId: vi.fn(),
  setTaskCompleted: vi.fn(),
  updateTaskTitle: vi.fn(),
}));

const mockedCreateTask = vi.mocked(createTask);
const mockedDeleteTask = vi.mocked(deleteTask);
const mockedGetTasksByUserId = vi.mocked(getTasksByUserId);
const mockedSetTaskCompleted = vi.mocked(setTaskCompleted);
const mockedUpdateTaskTitle = vi.mocked(updateTaskTitle);

function createContext(user?: TrpcContext["user"]): TrpcContext {
  return {
    user,
    req: { protocol: "https", headers: {} } as TrpcContext["req"],
    res: { clearCookie: () => undefined } as TrpcContext["res"],
  };
}

const authenticatedUser: NonNullable<TrpcContext["user"]> = {
  id: 42,
  openId: "todo-test-user",
  email: "todo@example.com",
  name: "Todo Test User",
  loginMethod: "test",
  role: "user",
  createdAt: new Date(),
  updatedAt: new Date(),
  lastSignedIn: new Date(),
};

const sampleTask = (overrides: Partial<Task> = {}): Task => ({
  id: 7,
  userId: 42,
  title: "مراجعة الخطة اليومية",
  completed: false,
  createdAt: new Date(),
  updatedAt: new Date(),
  ...overrides,
});

describe("tasks router", () => {
  beforeEach(() => vi.clearAllMocks());

  it("requires authentication to list tasks", async () => {
    const caller = appRouter.createCaller(createContext());
    await expect(caller.tasks.list()).rejects.toMatchObject({ code: "UNAUTHORIZED" });
  });

  it("rejects blank task titles before database access", async () => {
    const caller = appRouter.createCaller(createContext(authenticatedUser));
    await expect(caller.tasks.create({ title: "   " })).rejects.toMatchObject({ code: "BAD_REQUEST" });
    expect(mockedCreateTask).not.toHaveBeenCalled();
  });

  it("lists tasks for the authenticated user", async () => {
    mockedGetTasksByUserId.mockResolvedValue([sampleTask()]);
    const result = await appRouter.createCaller(createContext(authenticatedUser)).tasks.list();
    expect(mockedGetTasksByUserId).toHaveBeenCalledWith(42);
    expect(result[0]?.title).toBe("مراجعة الخطة اليومية");
  });

  it("creates a task with the authenticated user id", async () => {
    mockedCreateTask.mockResolvedValue(sampleTask({ title: "مهمة جديدة" }));
    const result = await appRouter.createCaller(createContext(authenticatedUser)).tasks.create({ title: "مهمة جديدة" });
    expect(mockedCreateTask).toHaveBeenCalledWith(42, "مهمة جديدة");
    expect(result.title).toBe("مهمة جديدة");
  });

  it("updates title and completion state", async () => {
    mockedUpdateTaskTitle.mockResolvedValue(sampleTask({ title: "عنوان محدث" }));
    mockedSetTaskCompleted.mockResolvedValue(sampleTask({ completed: true }));
    const caller = appRouter.createCaller(createContext(authenticatedUser));
    await caller.tasks.updateTitle({ id: 7, title: "عنوان محدث" });
    const toggled = await caller.tasks.toggle({ id: 7, completed: true });
    expect(mockedUpdateTaskTitle).toHaveBeenCalledWith(42, 7, "عنوان محدث");
    expect(mockedSetTaskCompleted).toHaveBeenCalledWith(42, 7, true);
    expect(toggled.completed).toBe(true);
  });

  it("deletes a task for the authenticated user", async () => {
    mockedDeleteTask.mockResolvedValue(undefined);
    const result = await appRouter.createCaller(createContext(authenticatedUser)).tasks.delete({ id: 7 });
    expect(mockedDeleteTask).toHaveBeenCalledWith(42, 7);
    expect(result).toEqual({ success: true });
  });

  it("rejects invalid task identifiers", async () => {
    const caller = appRouter.createCaller(createContext(authenticatedUser));
    await expect(caller.tasks.toggle({ id: 0, completed: true })).rejects.toMatchObject({ code: "BAD_REQUEST" });
    await expect(caller.tasks.delete({ id: -4 })).rejects.toMatchObject({ code: "BAD_REQUEST" });
  });
});
