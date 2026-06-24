"use client";

import * as React from "react";
import { motion, AnimatePresence } from "framer-motion";
import { cn } from "@/lib/utils";

type SheetProps = {
  open: boolean;
  onClose: () => void;
  children: React.ReactNode;
  title?: string;
};

/** 底部彈出抽屜 — mobile-native 感 */
export function Sheet({ open, onClose, children, title }: SheetProps) {
  React.useEffect(() => {
    if (open) document.body.style.overflow = "hidden";
    else document.body.style.overflow = "";
    return () => { document.body.style.overflow = ""; };
  }, [open]);

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            onClick={onClose}
            className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm"
          />
          <motion.div
            initial={{ y: "100%" }}
            animate={{ y: 0 }}
            exit={{ y: "100%" }}
            transition={{ type: "spring", stiffness: 350, damping: 32 }}
            className={cn(
              "fixed bottom-0 inset-x-0 z-50 bg-ink-900 rounded-t-3xl border-t border-ink-700",
              "pb-[max(24px,env(safe-area-inset-bottom))] max-h-[90dvh] overflow-y-auto"
            )}
          >
            <div className="sticky top-0 bg-ink-900 z-10 pt-3 px-5">
              <div className="w-10 h-1 bg-ink-700 rounded-full mx-auto" />
              {title && (
                <h2 className="text-lg font-extrabold text-white mt-3 mb-3">
                  {title}
                </h2>
              )}
            </div>
            <div className="px-5 pt-2">{children}</div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
