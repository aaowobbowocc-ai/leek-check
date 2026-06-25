"use client";

import { useState, useMemo, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { useQueryClient } from "@tanstack/react-query";
import { Wallet, AlertTriangle, PieChart, Banknote, Plus, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AddTickerSheet } from "@/components/add-ticker-sheet";
import { addCloudTicker } from "@/lib/watchlist";
import { api } from "@/lib/api";
import { useSession } from "@/lib/store";
import { loadCloudWatchlist, updateCloudHolding, type WatchlistItem } from "@/lib/watchlist";
import { createClient } from "@/lib/supabase/client";
import { chgColor, chgArrow, cardTier } from "@/lib/tier";
import { formatNumber, formatCurrency, formatPct } from "@/lib/utils";
import { Sheet } from "@/components/ui/sheet";
import { Input } from "@/components/ui/input";

export function BookPanel() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const isGuest = useSession((s) => s.isGuest);
  const guestList = useSession((s) => s.guestWatchlist);
  const updateGuest = useSession((s) => s.updateGuestHolding);
  const addGuest = useSession((s) => s.addGuestItem);
  const [userId, setUserId] = useState<string | null>(null);
  const [selling, setSelling] = useState<WatchlistItem | null>(null);
  const [adding, setAdding] = useState(false);
  const [pickFromWatch, setPickFromWatch] = useState(false);
  const [newHolding, setNewHolding] = useState<WatchlistItem | null>(null);

  useEffect(() => {
    if (isGuest) return;
    createClient().auth.getUser().then(({ data }) => setUserId(data.user?.id ?? null));
  }, [isGuest]);

  const cloudQ = useQuery({
    queryKey: ["watchlist-cloud", userId],
    queryFn: loadCloudWatchlist,
    enabled: !isGuest && !!userId,
    staleTime: 30_000,
  });
  const list: WatchlistItem[] = isGuest ? guestList : (cloudQ.data ?? []);

  // 只取有持股的
  const holdings = list.filter((x) => x.shares && x.cost_per_share);
  const tickers = holdings.map((x) => x.ticker);

  const quotesQ = useQuery({
    queryKey: ["quotes-batch", [...tickers].sort()],
    queryFn: () => (tickers.length ? api.getQuotesBatch(tickers) : Promise.resolve([])),
    enabled: tickers.length > 0,
    staleTime: 60_000,
  });
  const quoteMap = useMemo(() => {
    const m = new Map<string, import("@/lib/api").Quote>();
    (quotesQ.data ?? []).forEach((q) => m.set(q.ticker, q));
    return m;
  }, [quotesQ.data]);

  // 計算 portfolio
  const portfolio = useMemo(() => {
    let totalMv = 0;
    let totalCost = 0;
    let totalRealized = 0;  // (預留)
    const items: Array<{
      item: WatchlistItem;
      q: import("@/lib/api").Quote;
      mv: number; cost: number; pnl: number; pnlPct: number;
      isEtf: boolean;
    }> = [];
    holdings.forEach((it) => {
      const q = quoteMap.get(it.ticker);
      if (!q) return;
      const costIncl = it.cost_per_share! * 1.001425;
      const cost = it.shares! * costIncl;
      const mv = it.shares! * q.price;
      const pnl = mv - cost;
      const pnlPct = (pnl / cost) * 100;
      totalCost += cost;
      totalMv += mv;
      const isEtf = it.ticker.startsWith("00") || it.ticker === "0050" || it.ticker === "0056";
      items.push({ item: it, q: q!, mv, cost, pnl, pnlPct, isEtf });
    });
    const totalPnl = totalMv - totalCost;
    const totalPnlPct = totalCost > 0 ? (totalPnl / totalCost) * 100 : 0;
    // 集中度 — 只算個股(ETF 本身就是分散投資,不算集中度問題)
    const nonEtfItems = items.filter(x => !x.isEtf);
    const maxItem = nonEtfItems.length > 0 ? nonEtfItems.reduce((a, b) => (a.mv > b.mv ? a : b)) : null;
    const maxConcPct = totalMv > 0 && maxItem ? (maxItem.mv / totalMv) * 100 : 0;
    // 按市值排序
    items.sort((a, b) => b.mv - a.mv);
    return { items, totalMv, totalCost, totalPnl, totalPnlPct, totalRealized, maxConcPct, maxItem };
  }, [holdings, quoteMap]);

  return (
    <div className="space-y-4 pb-4">
      {/* Header */}
      <div>
        <h2 className="text-2xl font-extrabold text-st-fg flex items-center gap-2">
          <Wallet className="w-6 h-6 text-teal-300" /> 記帳
        </h2>
        <p className="text-st-muted text-xs mt-1">
          {portfolio.items.length} 檔持股 · 添加方式 ↓
        </p>
      </div>

      {/* 兩個加持股按鈕 */}
      <div className="grid grid-cols-2 gap-2">
        <Button variant="primary" size="md" onClick={() => setAdding(true)}>
          <Plus className="w-4 h-4" /> 直接加股票
        </Button>
        {(() => {
          const candidates = list.filter(x => !x.shares && !x.cost_per_share);
          return (
            <Button
              variant="secondary"
              size="md"
              onClick={() => setPickFromWatch(true)}
              disabled={candidates.length === 0}
            >
              <Search className="w-4 h-4" /> 從觀察加 ({candidates.length})
            </Button>
          );
        })()}
      </div>

      {/* Empty */}
      {portfolio.items.length === 0 && (
        <motion.div
          initial={{ opacity: 0, scale: 0.96 }}
          animate={{ opacity: 1, scale: 1 }}
          className="rounded-st p-8 text-center"
          style={{ background: "#16181d", border: "1px dashed #2f343d" }}
        >
          <Banknote className="w-12 h-12 text-st-muted mx-auto mb-3" />
          <h3 className="text-lg font-bold text-st-fg mb-2">還沒有持股</h3>
          <p className="text-sm text-st-muted mb-1">
            到 <b className="text-teal-300">觀察清單</b> 編輯任一檔股票,
          </p>
          <p className="text-sm text-st-muted">
            填入<b className="text-teal-300">股數 + 平均成本</b> → 自動同步來這裡
          </p>
        </motion.div>
      )}

      {/* Portfolio summary banner */}
      {portfolio.items.length > 0 && (
        <motion.div
          initial={{ opacity: 0, y: -6 }}
          animate={{ opacity: 1, y: 0 }}
          className="rounded-st p-4 hero-halo"
          style={{
            background: "linear-gradient(135deg, #0f766e 0%, #0a1a1f 50%, #16181d 100%)",
            border: "1px solid #2f343d",
            boxShadow: [
              "inset 0 1px 0 rgba(255,255,255,0.1)",
              "inset 0 -1px 0 rgba(0,0,0,0.4)",
              "0 0 24px rgba(20,184,166,0.12)",
            ].join(", "),
          }}
        >
          <div className="text-[10px] text-teal-300 font-bold tracking-[0.2em] mb-2">
            💰 PORTFOLIO 總覽
          </div>
          <div className="grid grid-cols-2 gap-3">
            <BigStat
              label="總市值"
              value={formatCurrency(portfolio.totalMv)}
              sub={`成本 ${formatCurrency(portfolio.totalCost)}`}
            />
            <BigStat
              label={portfolio.totalPnl >= 0 ? "📈 未實現損益" : "📉 未實現損益"}
              value={`${portfolio.totalPnl >= 0 ? "+" : ""}${formatCurrency(portfolio.totalPnl)}`}
              sub={`${chgArrow(portfolio.totalPnlPct)} ${formatPct(portfolio.totalPnlPct)}`}
              tint={portfolio.totalPnl >= 0 ? "up" : "down"}
            />
          </div>
          <div className="grid grid-cols-2 gap-3 mt-3 pt-3 border-t border-st-border/50">
            <Inline label="集中度" value={`${portfolio.maxConcPct.toFixed(1)}%`} sub={portfolio.maxItem?.item.ticker || ""} />
            <Inline label="持股檔數" value={`${portfolio.items.length} 檔`} sub="多元分散建議 ≥ 5" />
          </div>
          {portfolio.maxConcPct > 30 && (
            <div className="mt-3 flex items-start gap-2 bg-amber-500/15 border border-amber-500/40 rounded-lg p-2.5">
              <AlertTriangle className="w-4 h-4 text-amber-300 mt-0.5 flex-shrink-0" />
              <div className="text-xs text-amber-200">
                <b>集中度警示</b>:最大單檔 <b className="tabular-nums">{portfolio.maxConcPct.toFixed(0)}%</b> 已超過 30%,建議分散。
              </div>
            </div>
          )}
        </motion.div>
      )}

      {/* 持股清單 — 按市值 desc */}
      {portfolio.items.length > 0 && (
        <div className="space-y-3">
          <div className="flex items-center gap-2 px-1">
            <PieChart className="w-4 h-4 text-teal-300" />
            <h3 className="font-extrabold text-st-fg">持股明細</h3>
            <span className="text-[10px] text-st-muted ml-auto">按市值排序</span>
          </div>
          <div className="space-y-2">
            {portfolio.items.map((it, i) => {
              const { ticker, name, industry } = { ticker: it.item.ticker, name: it.q.name, industry: it.q.industry };
              const tier = cardTier(ticker, industry);
              const concPct = portfolio.totalMv > 0 ? (it.mv / portfolio.totalMv) * 100 : 0;
              return (
                <motion.div
                  key={ticker}
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: i * 0.04 }}
                  className="relative"
                >
                  <motion.button
                    onClick={() => router.push(`/ticker/${ticker}`)}
                    whileTap={{ scale: 0.98 }}
                    className="w-full text-left rounded-st p-3"
                    style={{
                      background: [
                        "radial-gradient(circle at 12% 18%, rgba(255,255,255,0.08) 0%, transparent 35%)",
                        "linear-gradient(180deg, #1c2028 0%, #16181d 50%, #11141a 100%)",
                      ].join(", "),
                      border: `1px solid #3a4150`,
                      borderLeft: `3px solid ${tier.light}`,
                      boxShadow: "inset 0 1px 0 rgba(255,255,255,0.08), inset 0 -1px 0 rgba(0,0,0,0.4)",
                    }}
                  >
                  {/* row 1: ticker + name + price */}
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="text-lg">{tier.icon}</span>
                        <div>
                          <div className="font-extrabold text-st-fg tabular-nums" style={{ fontSize: "0.95rem" }}>
                            {ticker}
                          </div>
                          <div className="text-[10px] text-st-muted">
                            {name} · {industry}
                          </div>
                        </div>
                      </div>
                    </div>
                    <div className="text-right flex-shrink-0">
                      <div className="tabular-nums font-bold text-st-fg" style={{ fontSize: "0.95rem" }}>
                        {formatNumber(it.q.price)}
                      </div>
                      <div className="tabular-nums text-[10px]" style={{ color: chgColor(it.q.change_pct) }}>
                        {chgArrow(it.q.change_pct)} {formatPct(it.q.change_pct)}
                      </div>
                    </div>
                  </div>

                  {/* row 2: detail grid */}
                  <div className="mt-2 pt-2 border-t border-st-border grid grid-cols-4 gap-1 text-[10px]">
                    <Mini label="股數" value={it.item.shares!.toLocaleString()} />
                    <Mini label="成本" value={formatNumber(it.item.cost_per_share!, 1)} />
                    <Mini label="市值" value={formatNumber(it.mv, 0)} />
                    <Mini
                      label="損益"
                      value={`${it.pnl >= 0 ? "+" : ""}${formatNumber(it.pnl, 0)}`}
                      tint={it.pnl >= 0 ? "up" : "down"}
                      sub={`${chgArrow(it.pnlPct)} ${it.pnlPct.toFixed(1)}%`}
                    />
                  </div>

                  {/* row 3: 集中度條 */}
                  <div className="mt-2 flex items-center gap-2 text-[10px]">
                    <span className="text-st-muted">配置</span>
                    <div className="flex-1 h-1 bg-ink-800 rounded-full overflow-hidden">
                      <div
                        className="h-full rounded-full transition-all"
                        style={{
                          width: `${concPct}%`,
                          background: concPct > 30 ? "#fbbf24" : tier.light,
                        }}
                      />
                    </div>
                    <span className="tabular-nums font-bold" style={{
                      color: concPct > 30 ? "#fbbf24" : "#cbd5e1",
                    }}>
                      {concPct.toFixed(1)}%
                    </span>
                  </div>
                  </motion.button>
                  <button
                    onClick={() => setSelling(it.item)}
                    className="w-full mt-1.5 rounded-st py-2 text-xs font-bold flex items-center justify-center gap-1.5 active:scale-95 transition-all border"
                    style={{
                      background: "linear-gradient(135deg, rgba(244,63,94,0.12), rgba(244,63,94,0.06))",
                      borderColor: "rgba(244,63,94,0.4)",
                      color: "#fda4af",
                      boxShadow: "inset 0 1px 0 rgba(255,255,255,0.05)",
                    }}
                  >
                    📉 賣出 {it.item.ticker}
                  </button>
                </motion.div>
              );
            })}
          </div>
        </div>
      )}

      {/* Disclaimer */}
      {portfolio.items.length > 0 && (
        <div className="text-[10px] text-st-muted leading-relaxed px-1 pt-2">
          💡 損益含買進 0.1425% 手續費(gross 慣例,跟券商一致)。
          實際賣出會再扣 ~0.4255%(手續費 + 證交稅)。
        </div>
      )}

      {/* AddTickerSheet — 從搜尋加新股票(加完開 holding modal)*/}
      <AddTickerSheet
        open={adding}
        onClose={() => setAdding(false)}
        onPick={async (info) => {
          const newItem: WatchlistItem = { ticker: info.ticker, type: info.type };
          if (isGuest) addGuest(newItem);
          else if (userId) {
            try {
              await addCloudTicker({ ...newItem, position: list.length }, userId);
              await queryClient.invalidateQueries({ queryKey: ["watchlist-cloud", userId] });
            } catch (e) { alert("加入失敗:" + (e as Error).message); }
          }
          setAdding(false);
          setNewHolding(newItem);  // 立刻打開填股數的 modal
        }}
        existingKeys={new Set(list.map((x) => `${x.ticker}-${x.type}`))}
      />

      {/* 從觀察清單選 sheet */}
      <PickFromWatchSheet
        open={pickFromWatch}
        candidates={list.filter(x => !x.shares && !x.cost_per_share)}
        quoteMap={quoteMap}
        onClose={() => setPickFromWatch(false)}
        onPick={(item) => { setPickFromWatch(false); setNewHolding(item); }}
      />

      {/* 填股數 + 成本 sheet — 共用 */}
      <FillHoldingSheet
        item={newHolding}
        currentPrice={newHolding ? quoteMap.get(newHolding.ticker)?.price : undefined}
        open={!!newHolding}
        onClose={() => setNewHolding(null)}
        onConfirm={async (shares, cost) => {
          if (!newHolding) return;
          if (isGuest) {
            updateGuest(newHolding.ticker, newHolding.type, shares, cost, new Date().toISOString().slice(0, 10));
          } else {
            try {
              await updateCloudHolding(newHolding.ticker, newHolding.type, shares, cost, new Date().toISOString().slice(0, 10));
              await queryClient.invalidateQueries({ queryKey: ["watchlist-cloud", userId] });
            } catch (e) { alert("儲存失敗:" + (e as Error).message); }
          }
          setNewHolding(null);
        }}
      />

      {/* SellSheet — 賣出計算實現損益 */}
      <SellSheet
        item={selling}
        currentPrice={selling ? quoteMap.get(selling.ticker)?.price : undefined}
        open={!!selling}
        onClose={() => setSelling(null)}
        onConfirm={async (sellPrice, sellShares) => {
          if (!selling) return;
          const remaining = selling.shares! - sellShares;
          if (isGuest) {
            if (remaining <= 0) {
              updateGuest(selling.ticker, selling.type, null, null, null);
            } else {
              updateGuest(selling.ticker, selling.type, remaining, selling.cost_per_share!, selling.entry_date);
            }
          } else {
            try {
              if (remaining <= 0) {
                await updateCloudHolding(selling.ticker, selling.type, null, null, null);
              } else {
                await updateCloudHolding(selling.ticker, selling.type, remaining, selling.cost_per_share!, selling.entry_date);
              }
              await queryClient.invalidateQueries({ queryKey: ["watchlist-cloud", userId] });
            } catch (e) { alert("賣出失敗:" + (e as Error).message); }
          }
          setSelling(null);
        }}
      />
    </div>
  );
}

function PickFromWatchSheet({
  open, candidates, quoteMap, onClose, onPick,
}: {
  open: boolean;
  candidates: WatchlistItem[];
  quoteMap: Map<string, import("@/lib/api").Quote>;
  onClose: () => void;
  onPick: (item: WatchlistItem) => void;
}) {
  return (
    <Sheet open={open} onClose={onClose} title="📂 從觀察清單選">
      <div className="space-y-2 pb-6">
        {candidates.length === 0 && (
          <div className="text-sm text-st-muted text-center py-6">
            觀察清單裡所有股票都已記帳
          </div>
        )}
        {candidates.map((c) => {
          const q = quoteMap.get(c.ticker);
          return (
            <button
              key={`${c.ticker}-${c.type}`}
              onClick={() => onPick(c)}
              className="w-full text-left rounded-st p-3 flex items-center justify-between active:scale-[0.98]"
              style={{ background: "#16181d", border: "1px solid #2f343d" }}
            >
              <div>
                <div className="tabular-nums font-bold text-st-fg">{c.ticker}</div>
                <div className="text-xs text-st-muted">{q?.name ?? "—"} · {q?.industry ?? "—"}</div>
              </div>
              <div className="text-right">
                {q && (
                  <div className="tabular-nums text-sm font-bold text-st-fg">
                    {q.price.toFixed(2)}
                  </div>
                )}
                <div className="text-[10px] text-teal-300">→ 填股數</div>
              </div>
            </button>
          );
        })}
      </div>
    </Sheet>
  );
}

function FillHoldingSheet({
  item, currentPrice, open, onClose, onConfirm,
}: {
  item: WatchlistItem | null;
  currentPrice?: number;
  open: boolean;
  onClose: () => void;
  onConfirm: (shares: number, cost: number) => Promise<void>;
}) {
  const [shares, setShares] = useState("");
  const [cost, setCost] = useState("");
  useEffect(() => {
    if (item) {
      setShares("");
      setCost(currentPrice ? currentPrice.toFixed(2) : "");
    }
  }, [item, currentPrice]);
  if (!item) return null;

  const s = Number(shares);
  const c = Number(cost);
  const valid = s > 0 && c > 0;
  const totalCost = s * c * 1.001425;

  return (
    <Sheet open={open} onClose={onClose} title={`💰 填 ${item.ticker} 持股`}>
      <div className="space-y-4 pb-6">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <div className="text-xs text-st-muted mb-1">股數</div>
            <Input
              type="number"
              inputMode="numeric"
              value={shares}
              onChange={(e) => setShares(e.target.value)}
              placeholder="例:1000"
              autoFocus
            />
          </div>
          <div>
            <div className="text-xs text-st-muted mb-1">平均成本(每股)</div>
            <Input
              type="number"
              inputMode="decimal"
              value={cost}
              onChange={(e) => setCost(e.target.value)}
              placeholder={currentPrice ? `現價 ${currentPrice.toFixed(2)}` : "例:500"}
            />
          </div>
        </div>

        {valid && (
          <div
            className="rounded-st p-3"
            style={{ background: "#0f1218", border: "1px solid #2f343d", borderLeft: "3px solid #5eead4" }}
          >
            <div className="text-xs text-st-muted">總成本(含買進 0.1425% 手續費)</div>
            <div className="tabular-nums font-extrabold text-st-fg mt-1" style={{ fontSize: "1.4rem" }}>
              NT$ {Math.round(totalCost).toLocaleString()}
            </div>
          </div>
        )}

        <div className="flex gap-2 pt-2">
          <Button variant="primary" size="lg" className="flex-1" disabled={!valid}
                    onClick={() => onConfirm(s, c)}>
            ✅ 儲存
          </Button>
          <Button variant="ghost" size="lg" onClick={onClose}>取消</Button>
        </div>
      </div>
    </Sheet>
  );
}

function SellSheet({
  item, currentPrice, open, onClose, onConfirm,
}: {
  item: WatchlistItem | null;
  currentPrice?: number;
  open: boolean;
  onClose: () => void;
  onConfirm: (sellPrice: number, sellShares: number) => Promise<void>;
}) {
  const [price, setPrice] = useState("");
  const [shares, setShares] = useState("");
  useEffect(() => {
    if (item) {
      setPrice(currentPrice ? currentPrice.toFixed(2) : "");
      setShares(String(item.shares ?? ""));
    }
  }, [item, currentPrice]);
  if (!item) return null;

  const p = Number(price);
  const s = Number(shares);
  const valid = p > 0 && s > 0 && s <= (item.shares ?? 0);
  const cost = item.cost_per_share! * 1.001425 * s;          // gross cost incl buy fee
  const sellNet = p * s * (1 - 0.001425 - 0.003);             // 扣手續費 + 證交稅
  const realized = sellNet - cost;
  const realizedPct = cost > 0 ? (realized / cost) * 100 : 0;
  const tint = realized >= 0 ? "#ef4444" : "#10b981";

  return (
    <Sheet open={open} onClose={onClose} title={`📉 賣出 ${item.ticker}`}>
      <div className="space-y-4 pb-6">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <div className="text-xs text-st-muted mb-1">賣出價</div>
            <Input
              type="number"
              inputMode="decimal"
              value={price}
              onChange={(e) => setPrice(e.target.value)}
              placeholder="例:520"
            />
          </div>
          <div>
            <div className="text-xs text-st-muted mb-1">
              賣出股數(最多 {item.shares?.toLocaleString()})
            </div>
            <Input
              type="number"
              inputMode="numeric"
              value={shares}
              onChange={(e) => setShares(e.target.value)}
              placeholder={String(item.shares ?? "")}
            />
          </div>
        </div>

        {/* 即時計算 */}
        {valid && (
          <div
            className="rounded-st p-4 space-y-2"
            style={{ background: "#0f1218", border: `1px solid ${tint}40`, borderLeft: `3px solid ${tint}` }}
          >
            <div className="text-xs text-st-muted">實現損益(扣手續費 + 證交稅 0.4255%)</div>
            <div className="tabular-nums font-extrabold" style={{ fontSize: "1.6rem", color: tint }}>
              {realized >= 0 ? "+" : ""}{Math.round(realized).toLocaleString()}
            </div>
            <div className="tabular-nums text-sm font-bold" style={{ color: tint }}>
              {realized >= 0 ? "▲" : "▼"} {realizedPct.toFixed(2)}%
            </div>
            <div className="text-[10px] text-st-muted pt-2 border-t border-st-border">
              成本:{Math.round(cost).toLocaleString()} · 賣出淨:{Math.round(sellNet).toLocaleString()}
            </div>
          </div>
        )}

        <div className="flex gap-2 pt-2">
          <Button variant="primary" size="lg" className="flex-1" disabled={!valid}
                    onClick={() => onConfirm(p, s)}>
            ✅ 確認賣出
          </Button>
          <Button variant="ghost" size="lg" onClick={onClose}>取消</Button>
        </div>

        <div className="text-[10px] text-st-muted leading-relaxed">
          💡 實現損益 = 賣出淨入(扣 0.4255%) − 買進成本(含 0.1425%)。
          剩餘股數會留在持股,全賣出則自動清掉。
        </div>
      </div>
    </Sheet>
  );
}

function BigStat({ label, value, sub, tint }: { label: string; value: string; sub?: string; tint?: "up" | "down" }) {
  const c = tint === "up" ? "#ef4444" : tint === "down" ? "#10b981" : "#fff";
  return (
    <div>
      <div className="text-[10px] tracking-widest text-st-muted font-bold">{label}</div>
      <div className="text-xl font-extrabold mt-1 tabular-nums" style={{ color: c }}>{value}</div>
      {sub && (
        <div className="text-[10px] tabular-nums mt-0.5" style={{ color: tint ? c : "#94a3b8" }}>
          {sub}
        </div>
      )}
    </div>
  );
}

function Inline({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div>
      <div className="text-[10px] text-st-muted">{label}</div>
      <div className="font-bold text-st-fg tabular-nums">{value}</div>
      {sub && <div className="text-[9px] text-st-muted tabular-nums">{sub}</div>}
    </div>
  );
}

function Mini({ label, value, tint, sub }: { label: string; value: string; tint?: "up" | "down"; sub?: string }) {
  const color = tint === "up" ? "#ef4444" : tint === "down" ? "#10b981" : "#fff";
  return (
    <div className="text-center">
      <div className="text-[9px] text-st-muted">{label}</div>
      <div className="tabular-nums font-bold mt-0.5" style={{ color, fontSize: "0.78rem" }}>{value}</div>
      {sub && <div className="tabular-nums text-[8px]" style={{ color }}>{sub}</div>}
    </div>
  );
}
