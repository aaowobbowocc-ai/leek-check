"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import { Ghost, Mail, KeyRound, ArrowRight, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { createClient } from "@/lib/supabase/client";
import { useSession } from "@/lib/store";

type Mode = "intro" | "login" | "signup";

export default function LoginPage() {
  const router = useRouter();
  const setGuest = useSession((s) => s.setGuest);
  const [mode, setMode] = useState<Mode>("intro");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPwd, setConfirmPwd] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const supabase = createClient();

  const enterAsGuest = () => {
    setGuest(true);
    router.push("/");
  };

  const handleEmailLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    setLoading(false);
    if (error) {
      setError(error.message.includes("Invalid login") ? "Email 或密碼錯誤" : error.message);
      return;
    }
    router.push("/");
  };

  const handleSignup = async (e: React.FormEvent) => {
    e.preventDefault();
    if (password !== confirmPwd) {
      setError("兩次密碼不一致");
      return;
    }
    if (password.length < 6) {
      setError("密碼至少 6 字");
      return;
    }
    setLoading(true);
    setError(null);
    const { error } = await supabase.auth.signUp({ email, password });
    setLoading(false);
    if (error) {
      setError(error.message);
      return;
    }
    router.push("/");
  };

  const handleGoogle = async () => {
    setLoading(true);
    setError(null);
    const { error } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: `${window.location.origin}/auth/callback` },
    });
    if (error) {
      setError(error.message);
      setLoading(false);
    }
  };

  return (
    <main className="min-h-dvh flex flex-col items-center px-6 pt-[max(24px,env(safe-area-inset-top))] pb-[max(24px,env(safe-area-inset-bottom))]">
      {/* Hero */}
      <motion.div
        initial={{ opacity: 0, y: -8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4 }}
        className="text-center mt-12 mb-10"
      >
        <div className="text-xs tracking-[0.3em] text-brand-300 font-bold mb-2">
          LEEK CHECK
        </div>
        <h1 className="text-5xl mb-1">🩺</h1>
        <h2 className="text-3xl font-extrabold text-white mb-2">韭菜健檢</h2>
        <p className="text-brand-200 text-sm">買進前,先做一次健檢</p>
      </motion.div>

      {/* Card */}
      <div className="w-full max-w-sm bg-ink-900/60 backdrop-blur-sm border border-ink-700 rounded-3xl p-6 shadow-2xl">
        <AnimatePresence mode="wait">
          {mode === "intro" && (
            <motion.div
              key="intro"
              initial={{ opacity: 0, x: -12 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 12 }}
              transition={{ duration: 0.25 }}
              className="space-y-3"
            >
              <Button
                variant="guest"
                size="lg"
                className="w-full"
                onClick={enterAsGuest}
              >
                <Ghost className="w-5 h-5" />
                以訪客身分試用
              </Button>
              <p className="text-xs text-slate-400 text-center px-2 pb-2">
                訪客資料只存裝置 · 想長期用請註冊雲端同步
              </p>

              <div className="relative my-4">
                <div className="absolute inset-0 flex items-center">
                  <div className="w-full border-t border-ink-700" />
                </div>
                <div className="relative flex justify-center text-xs">
                  <span className="bg-ink-900 px-3 text-slate-500">
                    或註冊永久保存
                  </span>
                </div>
              </div>

              <Button
                variant="primary"
                size="lg"
                className="w-full"
                onClick={() => setMode("login")}
              >
                <KeyRound className="w-5 h-5" />
                Email 登入 / 註冊
              </Button>

              <Button
                variant="secondary"
                size="lg"
                className="w-full"
                onClick={handleGoogle}
                disabled={loading}
              >
                <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none">
                  <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
                  <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
                  <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
                  <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
                </svg>
                使用 Google 繼續
              </Button>
            </motion.div>
          )}

          {(mode === "login" || mode === "signup") && (
            <motion.form
              key={mode}
              initial={{ opacity: 0, x: 12 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -12 }}
              transition={{ duration: 0.25 }}
              onSubmit={mode === "login" ? handleEmailLogin : handleSignup}
              className="space-y-4"
            >
              <div className="flex gap-2 p-1 bg-ink-950 rounded-xl">
                <button
                  type="button"
                  onClick={() => setMode("login")}
                  className={`flex-1 py-2 rounded-lg text-sm font-semibold transition-colors ${mode === "login" ? "bg-brand-500 text-ink-950" : "text-slate-400"}`}
                >
                  🔑 登入
                </button>
                <button
                  type="button"
                  onClick={() => setMode("signup")}
                  className={`flex-1 py-2 rounded-lg text-sm font-semibold transition-colors ${mode === "signup" ? "bg-brand-500 text-ink-950" : "text-slate-400"}`}
                >
                  ✨ 註冊
                </button>
              </div>

              <div className="space-y-3">
                <Input
                  type="email"
                  placeholder="email@example.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  autoComplete="email"
                  required
                />
                <Input
                  type="password"
                  placeholder={mode === "signup" ? "密碼(至少 6 字)" : "密碼"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete={mode === "signup" ? "new-password" : "current-password"}
                  required
                />
                {mode === "signup" && (
                  <Input
                    type="password"
                    placeholder="再次輸入密碼"
                    value={confirmPwd}
                    onChange={(e) => setConfirmPwd(e.target.value)}
                    autoComplete="new-password"
                    required
                  />
                )}
              </div>

              {error && (
                <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/30 rounded-lg p-3">
                  ⚠️ {error}
                </div>
              )}

              <Button type="submit" size="lg" className="w-full" disabled={loading}>
                {loading ? "處理中..." : mode === "login" ? "🔑 登入" : "✨ 註冊"}
                {!loading && <ArrowRight className="w-4 h-4" />}
              </Button>

              <button
                type="button"
                onClick={() => setMode("intro")}
                className="block w-full text-xs text-slate-500 hover:text-slate-300 transition-colors"
              >
                ← 回主選單
              </button>
            </motion.form>
          )}
        </AnimatePresence>
      </div>

      {/* Footer */}
      <div className="mt-auto pt-6 text-center text-xs text-slate-500 space-y-1">
        <div className="flex items-center justify-center gap-1.5">
          <Sparkles className="w-3 h-3 text-brand-400" />
          純客觀資料分析 · 不報明牌
        </div>
        <div>
          <a href="https://aaowobbowocc-ai.github.io/leek-check/privacy.html" className="hover:text-brand-300">隱私政策</a>
          {" · "}
          <a href="https://aaowobbowocc-ai.github.io/leek-check/delete-account.html" className="hover:text-brand-300">帳號刪除</a>
        </div>
      </div>
    </main>
  );
}
