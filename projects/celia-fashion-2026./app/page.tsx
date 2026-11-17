import { signIn } from "@/auth";
import { Sparkles } from "lucide-react";

export default function LoginPage() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-black relative overflow-hidden">
      {/* Background Effect */}
      <div className="absolute inset-0 z-0">
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[500px] h-[500px] bg-[var(--accent)] opacity-20 blur-[150px] rounded-full" />
      </div>

      {/* Card */}
      <div className="relative z-10 w-full max-w-sm p-8 bg-zinc-900/50 backdrop-blur-xl border border-white/10 rounded-2xl shadow-2xl">
        <div className="text-center mb-8">
          <div className="inline-flex items-center gap-2 mb-4 text-[var(--accent)]">
            <Sparkles className="w-6 h-6" />
            <span className="font-mono text-sm tracking-widest uppercase">
              Member Access
            </span>
          </div>

          <h1 className="text-3xl font-bold tracking-tighter text-white mb-2">
            WELCOME BACK
          </h1>
          <p className="text-zinc-400 text-sm">
            Enter the future of fashion commerce.
          </p>
        </div>

        {/* Google */}
        <form
          action={async () => {
            "use server";
            await signIn("google", { redirectTo: "/dashboard" });
          }}
        >
          <button className="w-full py-3 bg-white text-black font-bold uppercase tracking-wide rounded hover:bg-[var(--accent)] transition-colors">
            Continue with Google
          </button>
        </form>

        {/* Divider */}
        <div className="my-6 flex items-center justify-center text-xs text-zinc-500 uppercase tracking-widest">
          Or
        </div>

        {/* GitHub */}
        <form
          action={async () => {
            "use server";
            await signIn("github", { redirectTo: "/dashboard" });
          }}
        >
          <button className="w-full py-3 bg-zinc-800 text-white font-bold uppercase tracking-wide rounded hover:bg-zinc-700 transition-colors">
            Continue with GitHub
          </button>
        </form>
      </div>
    </div>
  );
}
