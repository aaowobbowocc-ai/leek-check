"use client";

import { useState, useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { Bell, X, Trash2, Inbox } from "lucide-react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";
import { loadAlerts, deleteAlert, markAlertRead, type PriceAlert } from "@/lib/alerts";
import { toast } from "@/lib/toast";
import { useSession } from "@/lib/store";

/** 訊息中心 — brand bar 右側鈴鐺 + slide-up sheet */
export function NotificationCenter() {
  const isGuest = useSession((s) => s.isGuest);
  const [open, setOpen] = useState(false);
  const [userId, setUserId] = useState<string | null>(null);
  const router = useRouter();
  const qc = useQueryClient();

  useEffect(() => {
    if (isGuest) return;
    createClient().auth.getUser().then(({ data }) => setUserId(data.user?.id ?? null));
  }, [isGuest]);

  const alertsQ = useQuery({
    queryKey: ["alerts-all", userId],
    queryFn: () => loadAlerts(),
    enabled: !isGuest && !!userId,
    staleTime: 30_000,
    refetchInterval: 60_000,  // 每分鐘背景重撈
  });
  const all = alertsQ.data ?? [];
  const triggered = all.filter((a) => a.triggered_at);
  const unread = triggered.filter((a) => !a.is_read);
  const active = all.filter((a) => !a.triggered_at);

  if (isGuest) return null;  // 訪客不顯示

  const handleClick = async (a: PriceAlert) => {
    if (!a.is_read) {
      await markAlertRead(a.id);
      qc.invalidateQueries({ queryKey: ["alerts-all", userId] });
    }
    setOpen(false);
    router.push(`/ticker/${a.ticker}`);
  };

  const handleDelete = async (id: number) => {
    try {
      await deleteAlert(id);
      toast("已刪除", "ok");
      qc.invalidateQueries({ queryKey: ["alerts-all", userId] });
      qc.invalidateQueries({ queryKey: ["alerts-active"] });
    } catch (e) {
      toast(`刪除失敗:${(e as Error).message}`, "error");
    }
  };

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="relative w-9 h-9 rounded-full flex items-center justify-center active:scale-95"
        style={{
          background: unread.length > 0
            ? "linear-gradient(135deg, color-mix(in srgb, var(--accent) 25%, transparent), transparent)"
            : "transparent",
          color: unread.length > 0 ? "var(--accent)" : "#64748b",
        }}
        title="訊息中心"
      >
        <Bell className="w-5 h-5" />
        {unread.length > 0 && (
          <span
            className="absolute -top-0.5 -right-0.5 min-w-[16px] h-4 px-1 rounded-full text-[9px] font-bold flex items-center justify-center"
            style={{ background: "var(--accent)", color: "#0f1218" }}
          >
            {unread.length}
          </span>
        )}
      </button>

      <AnimatePresence>
        {open && (
          <>
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="fixed inset-0 bg-black/60 z-40 backdrop-blur-sm"
              onClick={() => setOpen(false)}
            />
            <motion.div
              initial={{ y: "100%" }}
              animate={{ y: 0 }}
              exit={{ y: "100%" }}
              transition={{ type: "spring", damping: 30, stiffness: 280 }}
              className="fixed left-0 right-0 bottom-0 z-50 rounded-t-3xl p-5 pb-8 max-h-[80vh] overflow-y-auto"
              style={{
                background: "linear-gradient(180deg, #1c2028, #16181d)",
                border: "1px solid #2f343d",
                borderBottom: "none",
                boxShadow: "0 -12px 40px rgba(0,0,0,0.6)",
              }}
            >
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-lg font-extrabold text-st-fg flex items-center gap-2">
                  🔔 訊息中心
                  {unread.length > 0 && (
                    <span className="text-xs font-bold px-2 py-0.5 rounded-full"
                          style={{ background: "var(--accent-soft)", color: "var(--accent)" }}>
                      {unread.length} 未讀
                    </span>
                  )}
                </h3>
                <button onClick={() => setOpen(false)} className="text-st-muted active:scale-90">
                  <X className="w-5 h-5" />
                </button>
              </div>

              {/* 已觸發 section */}
              {triggered.length > 0 && (
                <div className="space-y-2 mb-4">
                  <div className="text-xs text-st-soft font-bold">🚨 已觸發 ({triggered.length})</div>
                  {triggered.map((a) => (
                    <TriggeredCard
                      key={a.id}
                      a={a}
                      onClick={() => handleClick(a)}
                      onDelete={() => handleDelete(a.id)}
                    />
                  ))}
                </div>
              )}

              {/* Active section */}
              {active.length > 0 && (
                <div className="space-y-2">
                  <div className="text-xs text-st-soft font-bold">⏳ 等待中 ({active.length})</div>
                  {active.map((a) => (
                    <ActiveCard
                      key={a.id}
                      a={a}
                      onClick={() => handleClick(a)}
                      onDelete={() => handleDelete(a.id)}
                    />
                  ))}
                </div>
              )}

              {/* Empty */}
              {all.length === 0 && (
                <div className="text-center py-8">
                  <Inbox className="w-12 h-12 text-st-muted mx-auto mb-2 opacity-40" />
                  <div className="text-sm text-st-muted">沒有警示</div>
                  <div className="text-[10px] text-st-muted mt-1">
                    到個股頁面點 🔔 設定第一個
                  </div>
                </div>
              )}

              <div className="text-[10px] text-st-muted text-center mt-4">
                ⏰ 平日 9:00-13:30 每 3 分鐘自動檢查
              </div>
            </motion.div>
          </>
        )}
      </AnimatePresence>
    </>
  );
}

function TriggeredCard({ a, onClick, onDelete }: {
  a: PriceAlert;
  onClick: () => void;
  onDelete: () => void;
}) {
  const symbol = a.condition === "above" ? "🔼" : "🔽";
  const triggeredAt = a.triggered_at
    ? new Date(a.triggered_at).toLocaleString("zh-TW", {
        month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
      })
    : "";
  return (
    <div
      className="flex items-center gap-2 rounded p-3"
      style={{
        background: a.is_read ? "#0f1218" : "color-mix(in srgb, var(--accent) 12%, #0f1218)",
        border: `1px solid ${a.is_read ? "#2f343d" : "color-mix(in srgb, var(--accent) 35%, transparent)"}`,
      }}
    >
      <button onClick={onClick} className="flex items-center gap-2 flex-1 min-w-0 text-left active:scale-[0.98]">
        {!a.is_read && (
          <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: "var(--accent)" }} />
        )}
        <span className="text-base flex-shrink-0">{symbol}</span>
        <div className="flex-1 min-w-0">
          <div className="text-xs font-bold text-st-fg flex items-center gap-1.5">
            <span className="tabular-nums">{a.ticker}</span>
            <span className="text-st-muted">觸發</span>
            <span className="tabular-nums" style={{ color: "var(--accent)" }}>
              ${a.triggered_price?.toFixed(2) ?? "?"}
            </span>
          </div>
          <div className="text-[10px] text-st-muted tabular-nums">
            目標 {a.condition === "above" ? "≥" : "≤"} ${a.target_price.toFixed(2)} · {triggeredAt}
          </div>
          {a.note && <div className="text-[10px] text-st-soft truncate mt-0.5">「{a.note}」</div>}
        </div>
      </button>
      <button
        onClick={onDelete}
        className="w-7 h-7 rounded flex items-center justify-center text-st-muted hover:bg-rose-500/15 hover:text-rose-300 active:scale-90 transition-colors flex-shrink-0"
      >
        <Trash2 className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}

function ActiveCard({ a, onClick, onDelete }: {
  a: PriceAlert;
  onClick: () => void;
  onDelete: () => void;
}) {
  const symbol = a.condition === "above" ? "🔼" : "🔽";
  const createdAt = new Date(a.created_at).toLocaleString("zh-TW", {
    month: "2-digit", day: "2-digit",
  });
  return (
    <div
      className="flex items-center gap-2 rounded p-2.5"
      style={{ background: "#0f1218", border: "1px solid #2f343d" }}
    >
      <button onClick={onClick} className="flex items-center gap-2 flex-1 min-w-0 text-left active:scale-[0.98]">
        <span className="text-sm opacity-60 flex-shrink-0">{symbol}</span>
        <div className="flex-1 min-w-0">
          <div className="text-xs font-bold text-st-fg">
            <span className="tabular-nums">{a.ticker}</span>
            <span className="text-st-muted ml-1.5 font-normal">
              {a.condition === "above" ? "≥" : "≤"}
            </span>
            <span className="tabular-nums ml-1">${a.target_price.toFixed(2)}</span>
          </div>
          <div className="text-[10px] text-st-muted">設於 {createdAt}</div>
        </div>
      </button>
      <button
        onClick={onDelete}
        className="w-7 h-7 rounded flex items-center justify-center text-st-muted hover:bg-rose-500/15 hover:text-rose-300 active:scale-90 transition-colors flex-shrink-0"
      >
        <Trash2 className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}
