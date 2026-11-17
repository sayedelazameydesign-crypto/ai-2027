import { useMemo, useState } from "react";
import { trpc } from "@/lib/trpc";
import DashboardLayout from "@/components/DashboardLayout";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Separator } from "@/components/ui/separator";
import { toast } from "sonner";
import { Check, CheckCircle2, Circle, ListTodo, Loader2, Pencil, Plus, Sparkles, Trash2, X } from "lucide-react";
import type { Task } from "../../../drizzle/schema";

type Filter = "all" | "active" | "completed";

const filters: Array<{ value: Filter; label: string }> = [
  { value: "all", label: "الكل" },
  { value: "active", label: "النشطة" },
  { value: "completed", label: "المكتملة" },
];

export default function Home() {
  const utils = trpc.useUtils();
  const { data: tasks = [], isLoading, isError } = trpc.tasks.list.useQuery();
  const [filter, setFilter] = useState<Filter>("all");
  const [newTitle, setNewTitle] = useState("");
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editingTitle, setEditingTitle] = useState("");

  const createMutation = trpc.tasks.create.useMutation({
    onSuccess: async () => {
      setNewTitle("");
      toast.success("تمت إضافة المهمة");
      await utils.tasks.list.invalidate();
    },
    onError: error => toast.error(error.message || "تعذرت إضافة المهمة"),
  });
  const updateTitleMutation = trpc.tasks.updateTitle.useMutation({
    onSuccess: async () => {
      setEditingId(null);
      toast.success("تم تحديث المهمة");
      await utils.tasks.list.invalidate();
    },
    onError: error => toast.error(error.message || "تعذر تحديث المهمة"),
  });
  const toggleMutation = trpc.tasks.toggle.useMutation({
    onSuccess: async () => await utils.tasks.list.invalidate(),
    onError: error => toast.error(error.message || "تعذر تحديث الحالة"),
  });
  const deleteMutation = trpc.tasks.delete.useMutation({
    onSuccess: async () => {
      toast.success("تم حذف المهمة");
      await utils.tasks.list.invalidate();
    },
    onError: error => toast.error(error.message || "تعذر حذف المهمة"),
  });

  const visibleTasks = useMemo(() => {
    if (filter === "active") return tasks.filter(task => !task.completed);
    if (filter === "completed") return tasks.filter(task => task.completed);
    return tasks;
  }, [filter, tasks]);
  const completedCount = tasks.filter(task => task.completed).length;
  const activeCount = tasks.length - completedCount;

  const addTask = (event: React.FormEvent) => {
    event.preventDefault();
    const title = newTitle.trim();
    if (!title || createMutation.isPending) return;
    createMutation.mutate({ title });
  };

  const startEditing = (task: Task) => {
    setEditingId(task.id);
    setEditingTitle(task.title);
  };

  const saveEditing = (taskId: number) => {
    const title = editingTitle.trim();
    if (!title) return toast.error("لا يمكن أن يكون عنوان المهمة فارغًا");
    updateTitleMutation.mutate({ id: taskId, title });
  };

  return (
    <DashboardLayout>
      <div dir="rtl" className="min-h-[calc(100vh-2rem)] bg-[#f6f7fb] -m-4 px-4 py-8 text-slate-900 sm:px-8 lg:px-12">
        <div className="mx-auto max-w-6xl space-y-8">
          <header className="flex flex-col justify-between gap-5 md:flex-row md:items-end">
            <div>
              <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-indigo-100 bg-white px-3 py-1 text-xs font-semibold text-indigo-600 shadow-sm">
                <Sparkles className="h-3.5 w-3.5" /> مساحة إنجازك اليومية
              </div>
              <h1 className="text-3xl font-bold tracking-tight text-slate-950 sm:text-4xl">لوحة المهام</h1>
              <p className="mt-2 text-sm text-slate-500 sm:text-base">نظّم يومك بوضوح، وأنجز ما يهمك خطوةً بعد خطوة.</p>
            </div>
            <div className="rounded-2xl border border-white/80 bg-white/80 px-5 py-3 text-left shadow-sm backdrop-blur">
              <p className="text-xs font-medium text-slate-400">نسبة الإنجاز</p>
              <p className="mt-1 text-2xl font-bold text-indigo-600">{tasks.length ? Math.round((completedCount / tasks.length) * 100) : 0}%</p>
            </div>
          </header>

          <section className="grid gap-4 sm:grid-cols-3">
            <StatCard label="إجمالي المهام" value={tasks.length} icon={<ListTodo className="h-5 w-5" />} tone="indigo" />
            <StatCard label="المهام المكتملة" value={completedCount} icon={<CheckCircle2 className="h-5 w-5" />} tone="emerald" />
            <StatCard label="المتبقية" value={activeCount} icon={<Circle className="h-5 w-5" />} tone="amber" />
          </section>

          <Card className="overflow-hidden rounded-3xl border-0 bg-white shadow-[0_18px_60px_rgba(30,41,59,0.08)]">
            <CardHeader className="space-y-5 border-b border-slate-100 p-5 sm:p-7">
              <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
                <div>
                  <CardTitle className="text-xl font-bold text-slate-950">قائمة مهامك</CardTitle>
                  <p className="mt-1 text-sm text-slate-400">أضف مهمة جديدة وابدأ بتحويل خطتك إلى إنجاز.</p>
                </div>
                <div className="flex rounded-xl bg-slate-100 p-1">
                  {filters.map(item => (
                    <button key={item.value} onClick={() => setFilter(item.value)} className={`rounded-lg px-3 py-2 text-xs font-semibold transition-all ${filter === item.value ? "bg-white text-indigo-600 shadow-sm" : "text-slate-500 hover:text-slate-800"}`}>
                      {item.label}
                    </button>
                  ))}
                </div>
              </div>
              <form onSubmit={addTask} className="flex gap-2">
                <Input value={newTitle} onChange={event => setNewTitle(event.target.value)} placeholder="ما المهمة التي تريد إنجازها؟" aria-label="عنوان المهمة الجديدة" className="h-12 rounded-xl border-slate-200 bg-slate-50 px-4 focus-visible:ring-indigo-500" />
                <Button type="submit" disabled={!newTitle.trim() || createMutation.isPending} className="h-12 shrink-0 rounded-xl bg-indigo-600 px-4 hover:bg-indigo-700 sm:px-6">
                  {createMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
                  <span className="mr-2 hidden sm:inline">إضافة</span>
                </Button>
              </form>
            </CardHeader>
            <CardContent className="p-0">
              {isLoading && <div className="flex items-center justify-center gap-3 p-12 text-sm text-slate-400"><Loader2 className="h-5 w-5 animate-spin text-indigo-500" /> جارٍ تحميل مهامك...</div>}
              {isError && <div className="p-12 text-center text-sm text-rose-500">تعذر تحميل المهام. حاول تحديث الصفحة.</div>}
              {!isLoading && !isError && visibleTasks.length === 0 && <EmptyState filter={filter} />}
              {!isLoading && visibleTasks.length > 0 && <div className="divide-y divide-slate-100">{visibleTasks.map(task => <TaskRow key={task.id} task={task} editingId={editingId} editingTitle={editingTitle} setEditingTitle={setEditingTitle} startEditing={startEditing} saveEditing={saveEditing} cancelEditing={() => setEditingId(null)} onToggle={completed => toggleMutation.mutate({ id: task.id, completed })} onDelete={() => deleteMutation.mutate({ id: task.id })} isBusy={toggleMutation.isPending || updateTitleMutation.isPending || deleteMutation.isPending} />)}</div>}
            </CardContent>
          </Card>
          <p className="text-center text-xs text-slate-400">يتم حفظ تغييراتك تلقائيًا وبأمان في قاعدة البيانات.</p>
        </div>
      </div>
    </DashboardLayout>
  );
}

function StatCard({ label, value, icon, tone }: { label: string; value: number; icon: React.ReactNode; tone: "indigo" | "emerald" | "amber" }) {
  const colors = { indigo: "bg-indigo-50 text-indigo-600", emerald: "bg-emerald-50 text-emerald-600", amber: "bg-amber-50 text-amber-600" };
  return <Card className="rounded-2xl border-0 bg-white shadow-sm"><CardContent className="flex items-center justify-between p-5"><div><p className="text-sm text-slate-500">{label}</p><p className="mt-2 text-3xl font-bold text-slate-950">{value}</p></div><div className={`rounded-2xl p-3 ${colors[tone]}`}>{icon}</div></CardContent></Card>;
}

function EmptyState({ filter }: { filter: Filter }) {
  return <div className="flex flex-col items-center justify-center px-6 py-16 text-center"><div className="mb-4 rounded-2xl bg-indigo-50 p-4 text-indigo-500"><Check className="h-7 w-7" /></div><h3 className="font-semibold text-slate-800">{filter === "completed" ? "لا توجد مهام مكتملة بعد" : filter === "active" ? "أنجزت كل مهامك النشطة" : "ابدأ بإضافة أول مهمة"}</h3><p className="mt-2 text-sm text-slate-400">ستظهر مهامك هنا لتتمكن من متابعتها بسهولة.</p></div>;
}

function TaskRow({ task, editingId, editingTitle, setEditingTitle, startEditing, saveEditing, cancelEditing, onToggle, onDelete, isBusy }: { task: Task; editingId: number | null; editingTitle: string; setEditingTitle: (value: string) => void; startEditing: (task: Task) => void; saveEditing: (id: number) => void; cancelEditing: () => void; onToggle: (completed: boolean) => void; onDelete: () => void; isBusy: boolean }) {
  const isEditing = editingId === task.id;
  return <div className="group flex items-center gap-3 px-5 py-4 transition-colors hover:bg-slate-50/80 sm:px-7"><Checkbox checked={task.completed} onCheckedChange={checked => onToggle(Boolean(checked))} aria-label={task.completed ? "إلغاء إكمال المهمة" : "تحديد المهمة كمكتملة"} className="h-5 w-5 rounded-md border-slate-300 data-[state=checked]:border-indigo-600 data-[state=checked]:bg-indigo-600" />
    <div className="min-w-0 flex-1">{isEditing ? <div className="flex gap-2"><Input autoFocus value={editingTitle} onChange={event => setEditingTitle(event.target.value)} onKeyDown={event => { if (event.key === "Enter") saveEditing(task.id); if (event.key === "Escape") cancelEditing(); }} className="h-9 rounded-lg" /><Button size="icon" variant="ghost" onClick={() => saveEditing(task.id)} disabled={isBusy} aria-label="حفظ"><Check className="h-4 w-4 text-emerald-600" /></Button><Button size="icon" variant="ghost" onClick={cancelEditing} aria-label="إلغاء"><X className="h-4 w-4" /></Button></div> : <button onDoubleClick={() => startEditing(task)} className={`block max-w-full truncate text-right text-sm font-medium transition-colors ${task.completed ? "text-slate-400 line-through" : "text-slate-700"}`} title="انقر مرتين للتعديل">{task.title}</button>}</div>
    {!isEditing && <div className="flex items-center gap-1 opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100"><Button variant="ghost" size="icon" onClick={() => startEditing(task)} aria-label="تعديل المهمة" className="h-9 w-9 text-slate-400 hover:text-indigo-600"><Pencil className="h-4 w-4" /></Button><AlertDialog><AlertDialogTrigger asChild><Button variant="ghost" size="icon" aria-label="حذف المهمة" className="h-9 w-9 text-slate-400 hover:text-rose-600"><Trash2 className="h-4 w-4" /></Button></AlertDialogTrigger><AlertDialogContent dir="rtl"><AlertDialogHeader><AlertDialogTitle>حذف المهمة؟</AlertDialogTitle><AlertDialogDescription>سيتم حذف «{task.title}» نهائيًا من قائمة مهامك. لا يمكن التراجع عن هذا الإجراء.</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>إلغاء</AlertDialogCancel><AlertDialogAction onClick={onDelete} className="bg-rose-600 hover:bg-rose-700">حذف المهمة</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog></div>}
  </div>;
}
