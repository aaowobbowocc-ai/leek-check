"use client";

import { useState, useEffect, useMemo } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import {
  Plus, Star, AlertTriangle, Wallet, Pin, PinOff,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { StockRow } from "@/components/stock-row";
import { api, type Quote } from "@/lib/api";
import {
  loadCloudWatchlist, addCloudTicker, removeCloudTicker, updateCloudHolding,
  type WatchlistItem,
} from "@/lib/watchlist";
import { useSession } from "@/lib/store";
import { createClient } from "@/lib/supabase/client";
import { formatNumber, formatPct, formatCurrency } from "@/lib/utils";
import { AddTickerSheet } from "@/components/add-ticker-sheet";
import { EditHoldingSheet } from "@/components/edit-holding-sheet";

export function WatchPanel() {
  const router = useRouter();
  const isGuest = useSession((s) => s.isGuest);
  const guestList = useSession((s) => s.guestWatchlist);
  const addGuest = useSession((s) => s.addGuestItem);
  const removeGuest = useSession((s) => s.removeGuestItem);
  const updateGuest = useSession((s) => s.updateGuestHolding);

  const queryClient = useQueryClient();
  const [userId, setUserId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<WatchlistItem | null>(null);
  const picks = useSession((s) => s.briefingPicks);
  const togglePick = useSession((s) => s.togglePick);

  // 抓登入者 id
  useEffect(() => {
    if (isGuest) return;
    const sb = createClient();
    sb.auth.getUser().then(({ data }) => setUserId(data.user?.id ?? null));
  }, [isGuest]);

  // Cloud watchlist(僅登入者)
  const cloudQ = useQuery({
    queryKey: ["watchlist-cloud", userId],
    queryFn: loadCloudWatchlist,
    enabled: !isGuest && !!userId,
    staleTime: 30_000,
  });

  const list: WatchlistItem[] = isGuest ? guestList : (cloudQ.data ?? []);
  const tickers = useMemo(() => list.map((x) => x.ticker), [list]);

  // 批次抓 quote
  const quotesQ = useQuery({
    queryKey: ["quotes-batch", [...tickers].sort()],
    queryFn: () => (tickers.length ? api.getQuotesBatch(tickers) : Promise.resolve([])),
    enabled: tickers.length > 0,
    staleTime: 60_000,
  });
  const quoteMap = useMemo(() => {
    const m = new Map<string, Quote>();
    (quotesQ.data ?? []).forEach((q) => m.set(q.ticker, q));
    return m;
  }, [quotesQ.data]);

  // 操作:加 / 移除 / 更新
  const handleAdd = async (info: { ticker: string; name: string; industry: string; type: string }) => {
    const item: WatchlistItem = { ticker: info.ticker, type: info.type };
    if (isGuest) addGuest(item);
    else if (userId) {
      try {
        await addCloudTicker({ ...item, position: list.length }, userId);
        await queryClient.invalidateQueries({ queryKey: ["watchlist-cloud", userId] });
      } catch (e) { alert(`新增失敗: ${(e as Error).message}`); }
    }
    setAdding(false);
  };

  const handleRemove = async (it: WatchlistItem) => {
    if (isGuest) removeGuest(it.ticker, it.type);
    else {
      try {
        await removeCloudTicker(it.ticker, it.type);
        await queryClient.invalidateQueries({ queryKey: ["watchlist-cloud", userId] });
      } catch (e) { alert(`移除失敗: ${(e as Error).message}`); }
    }
    setEditing(null);
  };

  const handleSaveHolding = async (
    shares: number | null, cost: number | null, entryDate: string | null
  ) => {
    if (!editing) return;
    if (isGuest) {
      updateGuest(editing.ticker, editing.type, shares, cost, entryDate);
    } else {
      try {
        await updateCloudHolding(editing.ticker, editing.type, shares, cost, entryDate);
        await queryClient.invalidateQueries({ queryKey: ["watchlist-cloud", userId] });
      } catch (e) { alert(`儲存失敗: ${(e as Error).message}`); }
    }
  };

  // Portfolio summary
  const summary = useMemo(() => {
    let totalMv = 0;
    let totalCost = 0;
    const holdingsMv: number[] = [];
    list.forEach((it) => {
      if (!it.shares || !it.cost_per_share) return;
      const q = quoteMap.get(it.ticker);
      if (!q) return;
      const costIncl = it.cost_per_share * 1.001425;
      totalCost += it.shares * costIncl;
      const mv = it.shares * q.price;
      totalMv += mv;
      holdingsMv.push(mv);
    });
    const pnl = totalMv - totalCost;
    const pct = totalCost > 0 ? (pnl / totalCost) * 100 : 0;
    // 集中度 = 最大 / 總
    let maxConc = 0;
    if (totalMv > 0 && holdingsMv.length > 0) {
      const max = Math.max(...holdingsMv);
      maxConc = (max / totalMv) * 100;
    }
    return { totalMv, totalCost, pnl, pct, maxConc, holdingCount: holdingsMv.length };
  }, [list, quoteMap]);

  return (
    <div className="space-y-4 pb-4">
      {/* Header — streamlit 樣式 */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-2xl font-extrabold text-st-fg flex items-center gap-2">
            <Star className="w-6 h-6 text-amber-300" style={{ fill: "#fbbf24" }} /> 我的觀察清單
          </h2>
          <p className="text-st-muted text-xs mt-1">
            點卡片進場分析 · {list.length} 檔 · 🌅 晨報精選 {picks.length}/5
          </p>
        </div>
        <Button variant="primary" size="sm" onClick={() => setAdding(true)}>
          <Plus className="w-4 h-4" /> 加股票
        </Button>
      </div>

      {/* 📊 漲跌分布 mini stats(只在 quotes 載入後)*/}
      {quotesQ.data && quotesQ.data.length > 0 && (
        <WatchStatsRow quotes={quotesQ.data} />
      )}

      {/* Portfolio summary (有持股才秀) */}
      {summary.holdingCount > 0 && (
        <motion.div
          initial={{ opacity: 0, y: -4 }}
          animate={{ opacity: 1, y: 0 }}
          className="bg-gradient-to-br from-brand-700/40 via-brand-800/20 to-ink-900 border border-ink-700 rounded-2xl p-4"
        >
          <div className="flex items-center gap-2 text-xs text-brand-300 font-bold tracking-wider mb-2">
            <Wallet className="w-3.5 h-3.5" /> 持股 PORTFOLIO ({summary.holdingCount} 檔)
          </div>
          <div className="grid grid-cols-2 gap-3">
            <SummaryCell label="💰 總市值" value={formatCurrency(summary.totalMv)} />
            <SummaryCell
              label={summary.pnl >= 0 ? "📈 損益" : "📉 損益"}
              value={`${summary.pnl >= 0 ? "+" : ""}${formatCurrency(summary.pnl)}`}
              subValue={formatPct(summary.pct)}
              tint={summary.pnl >= 0 ? "green" : "red"}
            />
          </div>
          {summary.maxConc > 30 && (
            <div className="mt-3 flex items-start gap-2 bg-amber-500/10 border border-amber-500/30 rounded-lg p-2.5">
              <AlertTriangle className="w-4 h-4 text-amber-400 mt-0.5 flex-shrink-0" />
              <div className="text-xs text-amber-200">
                集中度警示:最大單檔 <b>{summary.maxConc.toFixed(0)}%</b> 已超過 30%,
                建議分散。
              </div>
            </div>
          )}
        </motion.div>
      )}

      {/* Empty state */}
      {list.length === 0 && (
        <motion.div
          initial={{ opacity: 0, scale: 0.96 }}
          animate={{ opacity: 1, scale: 1 }}
          className="bg-ink-900/50 border border-dashed border-ink-700 rounded-2xl p-8 text-center"
        >
          <div className="text-5xl mb-3">⭐</div>
          <h3 className="text-lg font-bold text-white mb-2">還沒有觀察的股票</h3>
          <p className="text-sm text-slate-400 mb-5">
            加入第一檔開始追蹤健檢分數
          </p>
          <Button variant="primary" size="md" onClick={() => setAdding(true)}>
            <Plus className="w-4 h-4" /> 加第一檔股票
          </Button>
        </motion.div>
      )}

      {/* Pills with expandable brief */}
      <AnimatePresence mode="popLayout">
        {list.map((item) => {
          const q = quoteMap.get(item.ticker);
          const hasHolding = !!(item.shares && item.cost_per_share);
          let pnl = 0;
          let pnlPct = 0;
          if (hasHolding && q) {
            const cost = item.shares! * item.cost_per_share! * 1.001425;
            const mv = item.shares! * q.price;
            pnl = mv - cost;
            pnlPct = (mv / cost - 1) * 100;
          }
          return (
            <motion.div
              key={`${item.ticker}-${item.type}`}
              layout
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.95 }}
            >
              <StockRow
                ticker={item.ticker}
                name={q?.name ?? ""}
                industry={q?.industry ?? "—"}
                quote={q}
                hasHolding={hasHolding}
                holding={
                  hasHolding && q
                    ? {
                        shares: item.shares!,
                        cost_per_share: item.cost_per_share!,
                        pnl,
                        pnlPct,
                      }
                    : undefined
                }
                isPicked={picks.includes(item.ticker)}
                onPin={() => togglePick(item.ticker)}
                onOpen={() => router.push(`/ticker/${item.ticker}`)}
              />
            </motion.div>
          );
        })}
      </AnimatePresence>

      <AddTickerSheet
        open={adding}
        onClose={() => setAdding(false)}
        onPick={handleAdd}
        existingKeys={new Set(list.map((x) => `${x.ticker}-${x.type}`))}
      />
      <EditHoldingSheet
        item={editing}
        open={!!editing}
        onClose={() => setEditing(null)}
        onSave={handleSaveHolding}
        onRemove={editing ? () => handleRemove(editing) : undefined}
      />
    </div>
  );
}

function WatchStatsRow({ quotes }: { quotes: import("@/lib/api").Quote[] }) {
  const ups = quotes.filter(q => q.change_pct > 0).length;
  const flats = quotes.filter(q => q.change_pct === 0).length;
  const downs = quotes.filter(q => q.change_pct < 0).length;
  const avgChg = quotes.length > 0
    ? quotes.reduce((s, q) => s + q.change_pct, 0) / quotes.length
    : 0;
  const top = [...quotes].sort((a, b) => b.change_pct - a.change_pct)[0];
  const bot = [...quotes].sort((a, b) => a.change_pct - b.change_pct)[0];
  return (
    <div
      className="rounded-st p-3"
      style={{
        background: [
          "radial-gradient(circle at 12% 18%, rgba(255,255,255,0.05), transparent 35%)",
          "linear-gradient(180deg, #1c2028 0%, #16181d 100%)",
        ].join(", "),
        border: "1px solid #3a4150",
        boxShadow: "inset 0 1px 0 rgba(255,255,255,0.06), inset 0 -1px 0 rgba(0,0,0,0.4)",
      }}
    >
      <div className="grid grid-cols-4 gap-2 text-center">
        <div>
          <div className="text-[10px] text-st-muted">漲</div>
          <div className="tabular-nums font-extrabold text-rose-400" style={{ fontSize: "1.05rem" }}>
            {ups}
          </div>
        </div>
        <div>
          <div className="text-[10px] text-st-muted">平</div>
          <div className="tabular-nums font-extrabold text-st-muted" style={{ fontSize: "1.05rem" }}>
            {flats}
          </div>
        </div>
        <div>
          <div className="text-[10px] text-st-muted">跌</div>
          <div className="tabular-nums font-extrabold text-emerald-400" style={{ fontSize: "1.05rem" }}>
            {downs}
          </div>
        </div>
        <div>
          <div className="text-[10px] text-st-muted">平均</div>
          <div className="tabular-nums font-extrabold" style={{
            fontSize: "1.05rem",
            color: avgChg > 0 ? "#ef4444" : avgChg < 0 ? "#10b981" : "#94a3b8",
          }}>
            {avgChg >= 0 ? "+" : ""}{avgChg.toFixed(2)}%
          </div>
        </div>
      </div>
      {top && bot && (
        <div className="grid grid-cols-2 gap-2 mt-3 pt-3 border-t border-st-border text-[10px]">
          <div>
            <span className="text-st-muted">🔥 最強 </span>
            <span className="text-rose-400 font-bold tabular-nums">
              {top.ticker} {top.change_pct >= 0 ? "+" : ""}{top.change_pct.toFixed(2)}%
            </span>
          </div>
          <div className="text-right">
            <span className="text-emerald-400 font-bold tabular-nums">
              {bot.ticker} {bot.change_pct >= 0 ? "+" : ""}{bot.change_pct.toFixed(2)}%
            </span>
            <span className="text-st-muted"> 最弱 ❄️</span>
          </div>
        </div>
      )}
    </div>
  );
}

function Cell({ label, value, sub, tint }: { label: string; value: string; sub?: string; tint?: "up" | "down" }) {
  // 台股慣例:up 紅 / down 綠
  const color =
    tint === "up" ? "#ef4444" : tint === "down" ? "#10b981" : "#fff";
  return (
    <div>
      <div className="text-[10px] text-st-muted mb-0.5">{label}</div>
      <div className="font-bold" style={{ color }}>{value}</div>
      {sub && <div className="text-[10px] font-bold" style={{ color }}>{sub}</div>}
    </div>
  );
}

function SummaryCell({ label, value, subValue, tint }: { label: string; value: string; subValue?: string; tint?: "green" | "red" }) {
  const c = tint === "green" ? "text-emerald-300" : tint === "red" ? "text-red-300" : "text-white";
  return (
    <div>
      <div className="text-[10px] tracking-widest text-slate-400 font-bold">{label}</div>
      <div className={`text-xl font-extrabold mt-1 ${c}`}>{value}</div>
      {subValue && <div className={`text-xs font-bold mt-0.5 ${c}`}>{subValue}</div>}
    </div>
  );
}
