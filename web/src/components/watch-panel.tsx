"use client";

import { useState, useEffect, useMemo } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import {
  Plus, Star, Settings as SettingsIcon, TrendingUp, TrendingDown,
  AlertTriangle, Wallet, BarChart3,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/chip";
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
    queryKey: ["quotes-batch", tickers.join(",")],
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
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-extrabold text-white flex items-center gap-2">
            <Star className="w-6 h-6 text-amber-400 fill-amber-400" /> 觀察清單
          </h2>
          <p className="text-slate-400 text-xs mt-1">{list.length} 檔追蹤中</p>
        </div>
        <Button variant="primary" size="sm" onClick={() => setAdding(true)}>
          <Plus className="w-4 h-4" /> 加股票
        </Button>
      </div>

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

      {/* Cards */}
      <AnimatePresence mode="popLayout">
        {list.map((item, i) => {
          const q = quoteMap.get(item.ticker);
          const up = (q?.change_pct ?? 0) >= 0;
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
              transition={{ delay: i * 0.03 }}
              className="bg-ink-900/60 border border-ink-700 rounded-2xl overflow-hidden hover:border-brand-500/40 transition-colors"
            >
              {/* Main card body — click to /ticker/{tk} */}
              <button
                onClick={() => router.push(`/ticker/${item.ticker}`)}
                className="w-full text-left p-4 active:scale-[0.98] transition-transform"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 flex-wrap mb-1">
                      <span className="text-brand-300 font-mono text-xs font-bold">{item.ticker}</span>
                      {q?.industry && <Chip tone="default">{q.industry}</Chip>}
                      {hasHolding && <Chip tone="brand">持股</Chip>}
                    </div>
                    <h3 className="font-extrabold text-white truncate">
                      {q?.name || item.ticker}
                    </h3>
                    {q && (
                      <div className="flex items-center gap-3 text-[10px] text-slate-500 mt-1">
                        <span>開 {formatNumber(q.open, 1)}</span>
                        <span>高 {formatNumber(q.high, 1)}</span>
                        <span>低 {formatNumber(q.low, 1)}</span>
                      </div>
                    )}
                  </div>
                  <div className="text-right flex-shrink-0">
                    {q ? (
                      <>
                        <div className="text-2xl font-extrabold text-white leading-none">
                          {formatNumber(q.price)}
                        </div>
                        <div className={`text-xs font-bold flex items-center gap-0.5 justify-end mt-1 ${up ? "text-emerald-400" : "text-rose-400"}`}>
                          {up ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
                          {formatPct(q.change_pct)}
                        </div>
                      </>
                    ) : quotesQ.isFetching ? (
                      <div className="text-xs text-slate-500">載入中⋯</div>
                    ) : (
                      <div className="text-xs text-slate-500">無資料</div>
                    )}
                  </div>
                </div>

                {/* 持股損益 row */}
                {hasHolding && q && (
                  <div className="mt-3 pt-3 border-t border-ink-800 grid grid-cols-3 gap-2 text-xs">
                    <Cell label="股數" value={`${item.shares!.toLocaleString()}`} />
                    <Cell label="成本" value={formatNumber(item.cost_per_share!)} />
                    <Cell
                      label="損益"
                      value={`${pnl >= 0 ? "+" : ""}${formatNumber(pnl, 0)}`}
                      tint={pnl >= 0 ? "green" : "red"}
                      sub={formatPct(pnlPct)}
                    />
                  </div>
                )}
              </button>

              {/* Action bar */}
              <div className="flex border-t border-ink-800">
                <button
                  onClick={() => router.push(`/ticker/${item.ticker}`)}
                  className="flex-1 py-2.5 text-xs font-semibold text-brand-300 hover:bg-brand-500/10 transition-colors flex items-center justify-center gap-1.5"
                >
                  <BarChart3 className="w-3.5 h-3.5" /> 4 面健檢
                </button>
                <div className="w-px bg-ink-800" />
                <button
                  onClick={() => setEditing(item)}
                  className="flex-1 py-2.5 text-xs font-semibold text-slate-400 hover:bg-ink-800 transition-colors flex items-center justify-center gap-1.5"
                >
                  <SettingsIcon className="w-3.5 h-3.5" /> 編輯
                </button>
              </div>
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

function Cell({ label, value, sub, tint }: { label: string; value: string; sub?: string; tint?: "green" | "red" }) {
  const tintCls =
    tint === "green" ? "text-emerald-400" : tint === "red" ? "text-red-400" : "text-white";
  return (
    <div>
      <div className="text-[10px] text-slate-500 mb-0.5">{label}</div>
      <div className={`font-bold ${tintCls}`}>{value}</div>
      {sub && <div className={`text-[10px] ${tintCls}`}>{sub}</div>}
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
