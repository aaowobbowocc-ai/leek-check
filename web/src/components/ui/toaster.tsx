"use client";

import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { onToast, type ToastEvent } from "@/lib/toast";

export function Toaster() {
  const [items, setItems] = useState<ToastEvent[]>([]);

  useEffect(() => {
    return onToast((evt) => {
      setItems((s) => [...s, evt]);
      setTimeout(() => {
        setItems((s) => s.filter((x) => x.id !== evt.id));
      }, 2200);
    });
  }, []);

  return (
    <div
      className="fixed left-1/2 -translate-x-1/2 z-50 flex flex-col items-center gap-2 pointer-events-none"
      style={{ top: "max(72px, calc(env(safe-area-inset-top) + 72px))" }}
    >
      <AnimatePresence>
        {items.map((it) => {
          const colors = {
            info: { bg: "color-mix(in srgb, var(--accent) 18%, transparent)", border: "color-mix(in srgb, var(--accent) 50%, transparent)", color: "var(--accent)" },
            ok: { bg: "rgba(16,185,129,0.18)", border: "rgba(16,185,129,0.5)", color: "#5eead4" },
            warn: { bg: "rgba(251,191,36,0.18)", border: "rgba(251,191,36,0.5)", color: "#fbbf24" },
            error: { bg: "rgba(244,63,94,0.18)", border: "rgba(244,63,94,0.5)", color: "#fda4af" },
          }[it.tone];
          return (
            <motion.div
              key={it.id}
              initial={{ opacity: 0, y: -10, scale: 0.92 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -10, scale: 0.92 }}
              transition={{ type: "spring", stiffness: 300, damping: 25 }}
              className="rounded-st px-4 py-2.5 text-sm font-bold pointer-events-auto backdrop-blur-md"
              style={{
                background: colors.bg,
                border: `1px solid ${colors.border}`,
                color: colors.color,
                boxShadow: `0 4px 16px ${colors.border}`,
              }}
            >
              {it.message}
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
}
