"use client";

import { useState, useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { Bell, BellRing, X, Trash2 } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import {
  loadActiveAlerts, createAlert, deleteAlert,
  type PriceAlert,
} from "@/lib/alerts";
import { toast } from "@/lib/toast";
import { useSession } from "@/lib/store";

/** 個股頁的 🔔 警示按鈕 + 設定 sheet */
export function AlertBell({ ticker, currentPrice }: { ticker: string; currentPrice?: number }) {
  const isGuest = useSession((s) => s.isGuest);
  const [open, setOpen] = useState(false);
  const [userId, setUserId] = useState<string | null>(null);
  const qc = useQueryClient();

  useEffect(() => {
    if (isGuest) return;
    createClient().auth.getUser().then(({ data }) => setUserId(data.user?.id ?? null));
  }, [isGuest]);

  const alertsQ = useQuery({
    queryKey: ["alerts-active", ticker],
    queryFn: () => loadActiveAlerts(ticker),
    enabled: !isGuest && !!userId,
    staleTime: 30_000,
  });
  const activeCount = (alertsQ.data ?? []).length;

  if (isGuest) {
    return (
      <button
        onClick={() => toast("登入後可使用價格警示功能", "warn")}
        className="relative w-10 h-10 rounded-st flex items-center justify-center"
        style={{
          background: "linear-gradient(180deg, #1c2028, #11141a)",
          border: "1px solid #2a3340",
          color: "#64748b",
        }}
      >
        <Bell className="w-5 h-5" />
      </button>
    );
  }

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="relative w-10 h-10 rounded-st flex items-center justify-center active:scale-95"
        style={{
          background: activeCount > 0
            ? "linear-gradient(135deg, color-mix(in srgb, var(--accent) 25%, transparent), color-mix(in srgb, var(--accent-deep) 15%, transparent))"
            : "linear-gradient(180deg, #1c2028, #11141a)",
          border: `1px solid ${activeCount > 0 ? "color-mix(in srgb, var(--accent) 50%, transparent)" : "#2a3340"}`,
          color: activeCount > 0 ? "var(--accent)" : "#64748b",
          boxShadow: activeCount > 0 ? "0 0 12px var(--accent-glow)" : "none",
        }}
        title="價格警示"
      >
        {activeCount > 0 ? <BellRing className="w-5 h-5" /> : <Bell className="w-5 h-5" />}
        {activeCount > 0 && (
          <span
            className="absolute -top-1 -right-1 w-4 h-4 rounded-full text-[9px] font-bold flex items-center justify-center"
            style={{ background: "var(--accent)", color: "#0f1218" }}
          >
            {activeCount}
          </span>
        )}
      </button>

      <AlertSheet
        open={open}
        onClose={() => setOpen(false)}
        ticker={ticker}
        currentPrice={currentPrice}
        alerts={alertsQ.data ?? []}
        userId={userId}
        onChange={() => qc.invalidateQueries({ queryKey: ["alerts-active", ticker] })}
      />
    </>
  );
}

function AlertSheet({
  open, onClose, ticker, currentPrice, alerts, userId, onChange,
}: {
  open: boolean;
  onClose: () => void;
  ticker: string;
  currentPrice?: number;
  alerts: PriceAlert[];
  userId: string | null;
  onChange: () => void;
}) {
  const [condition, setCondition] = useState<"above" | "below">("above");
  const [price, setPrice] = useState("");
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (open && currentPrice && !price) {
      // 預設目標價:漲 5% (above) 或 跌 5% (below)
      setPrice((currentPrice * 1.05).toFixed(2));
    }
  }, [open, currentPrice, price]);

  useEffect(() => {
    if (!price || !currentPrice) return;
    // 切換方向時自動調整預填
    if (condition === "above" && parseFloat(price) <= currentPrice) {
      setPrice((currentPrice * 1.05).toFixed(2));
    } else if (condition === "below" && parseFloat(price) >= currentPrice) {
      setPrice((currentPrice * 0.95).toFixed(2));
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [condition]);

  const handleCreate = async () => {
    if (!userId) return;
    const p = parseFloat(price);
    if (!p || p <= 0) {
      toast("請輸入合理的目標價", "warn");
      return;
    }
    setSubmitting(true);
    try {
      await createAlert(userId, ticker, condition, p, note);
      toast(`🔔 已設定:${ticker} ${condition === "above" ? "≥" : "≤"} $${p}`, "ok");
      setPrice(currentPrice ? (currentPrice * (condition === "above" ? 1.05 : 0.95)).toFixed(2) : "");
      setNote("");
      onChange();
    } catch (e) {
      toast(`設定失敗:${(e as Error).message}`, "error");
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await deleteAlert(id);
      toast("已刪除警示", "ok");
      onChange();
    } catch (e) {
      toast(`刪除失敗:${(e as Error).message}`, "error");
    }
  };

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 bg-black/60 z-40 backdrop-blur-sm"
            onClick={onClose}
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
                🔔 {ticker} 價格警示
              </h3>
              <button onClick={onClose} className="text-st-muted active:scale-90">
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* 目前價提示 */}
            {currentPrice && (
              <div className="text-xs text-st-muted mb-3">
                目前價:<span className="text-st-fg font-bold tabular-nums">${currentPrice.toFixed(2)}</span>
              </div>
            )}

            {/* 新增警示 form */}
            <div
              className="rounded-st p-3 space-y-3 mb-4"
              style={{ background: "#0f1218", border: "1px solid #2f343d" }}
            >
              <div className="text-xs text-st-soft font-bold">➕ 新增警示</div>

              {/* 方向 toggle */}
              <div className="grid grid-cols-2 gap-2">
                <button
                  onClick={() => setCondition("above")}
                  className="rounded-st py-2 text-xs font-bold"
                  style={{
                    background: condition === "above"
                      ? "linear-gradient(135deg, rgba(239,68,68,0.2), rgba(239,68,68,0.08))"
                      : "linear-gradient(180deg, #1c2028, #11141a)",
                    border: `1px solid ${condition === "above" ? "rgba(239,68,68,0.5)" : "#2a3340"}`,
                    color: condition === "above" ? "#fca5a5" : "#94a3b8",
                  }}
                >
                  🔼 漲到 ≥
                </button>
                <button
                  onClick={() => setCondition("below")}
                  className="rounded-st py-2 text-xs font-bold"
                  style={{
                    background: condition === "below"
                      ? "linear-gradient(135deg, rgba(16,185,129,0.2), rgba(16,185,129,0.08))"
                      : "linear-gradient(180deg, #1c2028, #11141a)",
                    border: `1px solid ${condition === "below" ? "rgba(16,185,129,0.5)" : "#2a3340"}`,
                    color: condition === "below" ? "#6ee7b7" : "#94a3b8",
                  }}
                >
                  🔽 跌到 ≤
                </button>
              </div>

              {/* 目標價 input */}
              <div>
                <label className="text-[10px] text-st-muted">目標價 (NT$)</label>
                <input
                  type="number"
                  step="0.01"
                  value={price}
                  onChange={(e) => setPrice(e.target.value)}
                  placeholder="輸入目標價"
                  className="w-full rounded px-3 py-2 text-st-fg tabular-nums font-bold mt-1 outline-none"
                  style={{ background: "#1c2028", border: "1px solid #2a3340" }}
                />
              </div>

              {/* 備註 */}
              <div>
                <label className="text-[10px] text-st-muted">備註(選填)</label>
                <input
                  type="text"
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder="如:跌破支撐再進場"
                  maxLength={50}
                  className="w-full rounded px-3 py-2 text-st-fg text-xs mt-1 outline-none"
                  style={{ background: "#1c2028", border: "1px solid #2a3340" }}
                />
              </div>

              <button
                onClick={handleCreate}
                disabled={submitting || !price}
                className="btn-smart w-full"
              >
                ✨ {submitting ? "設定中⋯" : "設定警示"}
              </button>
            </div>

            {/* 已設警示列表 */}
            {alerts.length > 0 && (
              <div className="space-y-2">
                <div className="text-xs text-st-soft font-bold">📋 目前 {alerts.length} 個警示</div>
                {alerts.map((a) => (
                  <div
                    key={a.id}
                    className="flex items-center gap-2 rounded p-2.5"
                    style={{ background: "#0f1218", border: "1px solid #2f343d" }}
                  >
                    <span className="text-base">{a.condition === "above" ? "🔼" : "🔽"}</span>
                    <div className="flex-1 min-w-0">
                      <div className="text-xs text-st-fg font-bold tabular-nums">
                        {a.condition === "above" ? "≥" : "≤"} ${a.target_price.toFixed(2)}
                      </div>
                      {a.note && (
                        <div className="text-[10px] text-st-muted truncate">{a.note}</div>
                      )}
                    </div>
                    <button
                      onClick={() => handleDelete(a.id)}
                      className="w-7 h-7 rounded flex items-center justify-center text-st-muted hover:bg-rose-500/15 hover:text-rose-300 active:scale-90 transition-colors"
                      title="刪除警示"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            )}

            {alerts.length === 0 && (
              <div className="text-center text-xs text-st-muted py-4">
                還沒有設定警示 — 上面新增第一個
              </div>
            )}

            <div className="text-[10px] text-st-muted text-center mt-4">
              ⏰ 平日 9:00-13:30 每 3 分鐘自動檢查
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
