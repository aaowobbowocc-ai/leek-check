"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";
import { useSession } from "@/lib/store";
import { motion } from "framer-motion";
import { Loader2 } from "lucide-react";
import { MainLayout } from "@/components/main-layout";

export default function HomePage() {
  const router = useRouter();
  const isGuest = useSession((s) => s.isGuest);
  const hasHydrated = useSession((s) => s.hasHydrated);
  const [authChecked, setAuthChecked] = useState(false);
  const [authed, setAuthed] = useState(false);

  useEffect(() => {
    if (!hasHydrated) return;  // 等 zustand 先讀完 localStorage
    const supabase = createClient();
    supabase.auth.getSession().then(({ data }) => {
      const hasSession = !!data.session;
      setAuthed(hasSession);
      setAuthChecked(true);
      if (!hasSession && !isGuest) {
        router.replace("/login");
      }
    });
  }, [isGuest, router, hasHydrated]);

  if (!hasHydrated || !authChecked) {
    return (
      <main className="min-h-dvh flex flex-col items-center justify-center">
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.1 }}
          className="text-center"
        >
          <Loader2 className="w-8 h-8 animate-spin text-brand-400 mx-auto mb-3" />
          <p className="text-brand-300 text-sm">韭菜健檢 載入中⋯</p>
        </motion.div>
      </main>
    );
  }

  if (!authed && !isGuest) {
    return null;
  }

  return <MainLayout />;
}
