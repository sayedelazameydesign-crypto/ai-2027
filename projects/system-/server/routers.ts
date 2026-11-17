import { z } from "zod";
import { COOKIE_NAME } from "@shared/const";
import { getSessionCookieOptions } from "./_core/cookies";
import { systemRouter } from "./_core/systemRouter";
import { protectedProcedure, publicProcedure, router } from "./_core/trpc";
import { createTask, deleteTask, getTasksByUserId, setTaskCompleted, updateTaskTitle } from "./db";

const taskIdInput = z.object({ id: z.number().int().positive() });
const titleInput = z.object({
  id: z.number().int().positive(),
  title: z.string().trim().min(1, "عنوان المهمة مطلوب").max(500, "العنوان طويل جدًا"),
});

export const appRouter = router({
  system: systemRouter,
  auth: router({
    me: publicProcedure.query(opts => opts.ctx.user),
    logout: publicProcedure.mutation(({ ctx }) => {
      const cookieOptions = getSessionCookieOptions(ctx.req);
      ctx.res.clearCookie(COOKIE_NAME, { ...cookieOptions, maxAge: -1 });
      return { success: true } as const;
    }),
  }),
  tasks: router({
    list: protectedProcedure.query(({ ctx }) => getTasksByUserId(ctx.user.id)),
    create: protectedProcedure
      .input(z.object({ title: z.string().trim().min(1, "عنوان المهمة مطلوب").max(500, "العنوان طويل جدًا") }))
      .mutation(({ ctx, input }) => createTask(ctx.user.id, input.title)),
    updateTitle: protectedProcedure.input(titleInput).mutation(({ ctx, input }) => updateTaskTitle(ctx.user.id, input.id, input.title)),
    toggle: protectedProcedure
      .input(taskIdInput.extend({ completed: z.boolean() }))
      .mutation(({ ctx, input }) => setTaskCompleted(ctx.user.id, input.id, input.completed)),
    delete: protectedProcedure.input(taskIdInput).mutation(async ({ ctx, input }) => {
      await deleteTask(ctx.user.id, input.id);
      return { success: true } as const;
    }),
  }),
});

export type AppRouter = typeof appRouter;
