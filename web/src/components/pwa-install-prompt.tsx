"use client";

import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { X, Download, Share } from "lucide-react";

type BeforeInstallPromptEvent = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
};

const DISMISSED_KEY = "leek-pwa-dismissed";
const DISMISS_DAYS = 7;

export function PwaInstallPrompt() {
  const [deferredPrompt, setDeferredPrompt] = useState<BeforeInstallPromptEvent | null>(null);
  const [isIOS, setIsIOS] = useState(false);
  const [show, setShow] = useState(false);

  useEffect(() => {
    // 之前 dismiss 過,N 天內不再顯示
    const dismissed = localStorage.getItem(DISMISSED_KEY);
    if (dismissed) {
      const days = (Date.now() - parseInt(dismissed)) / (1000 * 60 * 60 * 24);
      if (days < DISMISS_DAYS) return;
    }

    // 已安裝(standalone)就不顯示
    if (window.matchMedia("(display-mode: standalone)").matches) return;
    if ((window.navigator as unknown as { standalone?: boolean }).standalone) return;

    // iOS 用戶 — 沒 beforeinstallprompt event,直接顯示「加到主畫面」教學
    const ua = navigator.userAgent;
    const ios = /iPad|iPhone|iPod/.test(ua) && !(window as unknown as { MSStream?: unknown }).MSStream;
    if (ios) {
      setIsIOS(true);
      // 延遲 8 秒再彈,避免一開就嚇人
      setTimeout(() => setShow(true), 8000);
      return;
    }

    // Android / Desktop Chromium
    const handler = (e: Event) => {
      e.preventDefault();
      setDeferredPrompt(e as BeforeInstallPromptEvent);
      setTimeout(() => setShow(true), 8000);
    };
    window.addEventListener("beforeinstallprompt", handler);
    return () => window.removeEventListener("beforeinstallprompt", handler);
  }, []);

  const handleInstall = async () => {
    if (!deferredPrompt) return;
    deferredPrompt.prompt();
    const { outcome } = await deferredPrompt.userChoice;
    if (outcome === "accepted") setShow(false);
    setDeferredPrompt(null);
  };

  const dismiss = () => {
    localStorage.setItem(DISMISSED_KEY, String(Date.now()));
    setShow(false);
  };

  return (
    <AnimatePresence>
      {show && (
        <motion.div
          initial={{ y: 100, opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          exit={{ y: 100, opacity: 0 }}
          transition={{ type: "spring", damping: 25, stiffness: 280 }}
          className="fixed z-40 left-4 right-4 rounded-st p-4 backdrop-blur-md"
          style={{
            bottom: "calc(76px + env(safe-area-inset-bottom))",
            background: "linear-gradient(135deg, color-mix(in srgb, var(--accent) 18%, #1c2028), #16181d)",
            border: "1px solid color-mix(in srgb, var(--accent) 45%, transparent)",
            boxShadow: "0 12px 32px rgba(0,0,0,0.5), 0 0 24px var(--accent-glow)",
          }}
        >
          <button
            onClick={dismiss}
            className="absolute top-2 right-2 w-7 h-7 rounded flex items-center justify-center text-st-muted hover:bg-white/10 active:scale-90"
          >
            <X className="w-4 h-4" />
          </button>
          <div className="flex items-start gap-3 pr-6">
            <span className="text-2xl flex-shrink-0">📱</span>
            <div className="flex-1">
              <div className="font-extrabold text-st-fg text-sm">把韭菜健檢加到主畫面</div>
              <div className="text-[11px] text-st-soft mt-1">
                像 App 一樣秒開,離線也能看快取
              </div>
              {isIOS ? (
                <div className="text-[11px] text-st-soft mt-2 flex items-center gap-1">
                  點下方 <Share className="w-3.5 h-3.5 inline text-blue-300" /> 分享 → <b className="text-accent">加入主畫面</b>
                </div>
              ) : (
                <button
                  onClick={handleInstall}
                  className="mt-3 rounded-st px-3 py-1.5 text-xs font-bold flex items-center gap-1.5 active:scale-95"
                  style={{
                    background: "linear-gradient(135deg, var(--accent), var(--accent-deep))",
                    color: "#0f1218",
                    boxShadow: "0 4px 12px var(--accent-glow)",
                  }}
                >
                  <Download className="w-3.5 h-3.5" /> 立刻安裝
                </button>
              )}
            </div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
