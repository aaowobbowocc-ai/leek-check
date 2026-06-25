"use client";

import { useState, useEffect, useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useQuery } from "@tanstack/react-query";
import {
  Sunrise, Star, Search, Radio, User, Ghost, LogOut, Flame, X, Wallet, Trophy,
  ArrowUpRight, ArrowDownRight, BarChart3,
} from "lucide-react";
import { useSession } from "@/lib/store";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useRouter } from "next/navigation";
import { api, type Quote } from "@/lib/api";
import { WatchPanel } from "@/components/watch-panel";
import { BookPanel } from "@/components/book-panel";
import { StockRow } from "@/components/stock-row";
import { createClient } from "@/lib/supabase/client";
import { HOT_STOCK_CATEGORIES, ALL_HOT_TICKERS, chgColor, chgArrow } from "@/lib/tier";

type Tab = "brief" | "watch" | "book" | "search" | "scan" | "me";

const TABS: { id: Tab; icon: typeof Star; label: string }[] = [
  { id: "brief", icon: Sunrise, label: "晨報" },
  { id: "watch", icon: Star, label: "觀察" },
  { id: "book", icon: Wallet, label: "記帳" },
  { id: "search", icon: Search, label: "搜尋" },
  { id: "scan", icon: Radio, label: "策略" },
  { id: "me", icon: User, label: "我的" },
];

export function MainLayout() {
  const [active, setActive] = useState<Tab>("brief");
  const isGuest = useSession((s) => s.isGuest);
  const clearGuest = useSession((s) => s.clearGuest);
  const router = useRouter();

  return (
    <div className="min-h-dvh pb-[calc(72px+env(safe-area-inset-bottom))]">
      {/* Status bar safe area + brand bar */}
      <div className="bg-ink-950/80 backdrop-blur-md sticky top-0 z-30">
        <div style={{ height: "env(safe-area-inset-top)" }} />
        <div className="px-4 py-2.5 flex items-center justify-between border-b border-ink-800">
          <div className="flex items-center gap-2">
            <span className="text-xl">🩺</span>
            <span className="font-extrabold text-white">韭菜健檢</span>
            <span className="text-[10px] text-brand-300 font-bold tracking-widest bg-brand-500/10 px-1.5 py-0.5 rounded">
              BETA
            </span>
          </div>
          {isGuest && (
            <button
              onClick={() => { clearGuest(); router.push("/login"); }}
              className="text-xs text-amber-300 flex items-center gap-1 font-semibold"
            >
              <Ghost className="w-3 h-3" /> 訪客
            </button>
          )}
        </div>
      </div>

      {/* Content area */}
      <main className="px-4 pt-4 animate-page-in">
        <AnimatePresence mode="wait">
          <motion.div
            key={active}
            initial={{ opacity: 0, x: 6 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -6 }}
            transition={{ duration: 0.18 }}
          >
            {active === "brief" && <BriefPanel onNav={setActive} />}
            {active === "watch" && <WatchPanel />}
            {active === "book" && <BookPanel />}
            {active === "search" && <SearchPanel />}
            {active === "scan" && <ScanPanel />}
            {active === "me" && <MePanel />}
          </motion.div>
        </AnimatePresence>
      </main>

      {/* Bottom tab bar */}
      <nav className="fixed bottom-0 inset-x-0 bg-ink-950/95 backdrop-blur-xl border-t border-ink-800 pb-[env(safe-area-inset-bottom)] z-40">
        <div className="flex justify-around">
          {TABS.map(({ id, icon: Icon, label }) => {
            const isActive = active === id;
            return (
              <button
                key={id}
                onClick={() => setActive(id)}
                className="flex-1 flex flex-col items-center gap-1 py-2.5 relative active:scale-95 transition-transform"
              >
                <motion.div
                  animate={{
                    scale: isActive ? 1.1 : 1,
                    y: isActive ? -2 : 0,
                  }}
                  transition={{ type: "spring", stiffness: 400, damping: 20 }}
                >
                  <Icon
                    className={`w-5 h-5 transition-colors ${isActive ? "text-brand-400" : "text-slate-500"}`}
                    strokeWidth={isActive ? 2.5 : 2}
                    fill={isActive && (id === "watch") ? "currentColor" : "none"}
                  />
                </motion.div>
                <span
                  className={`text-[10px] font-semibold transition-colors ${isActive ? "text-brand-300" : "text-slate-500"}`}
                >
                  {label}
                </span>
                {isActive && (
                  <motion.div
                    layoutId="active-tab-pill"
                    className="absolute top-0 left-1/2 -translate-x-1/2 w-8 h-0.5 bg-brand-400 rounded-full shadow-[0_0_10px_rgba(94,234,212,0.6)]"
                  />
                )}
              </button>
            );
          })}
        </div>
      </nav>
    </div>
  );
}

function BriefPanel({ onNav }: { onNav: (t: Tab) => void }) {
  const router = useRouter();
  const hour = new Date().getHours();
  const greeting = hour < 12 ? "🌅 早安" : hour < 18 ? "☀️ 午安" : "🌙 晚安";
  const isGuest = useSession((s) => s.isGuest);
  const guestList = useSession((s) => s.guestWatchlist);
  const [userId, setUserId] = useState<string | null>(null);

  useEffect(() => {
    if (isGuest) return;
    createClient().auth.getUser().then(({ data }) => setUserId(data.user?.id ?? null));
  }, [isGuest]);

  const wlQ = useQuery({
    queryKey: ["watchlist-cloud", userId],
    queryFn: () => import("@/lib/watchlist").then(m => m.loadCloudWatchlist()),
    enabled: !isGuest && !!userId,
    staleTime: 60_000,
  });
  const wlList = isGuest ? guestList : (wlQ.data ?? []);
  const wlTickers = wlList.map(x => x.ticker);

  // 觀察清單批次 quote
  const wlQuotesQ = useQuery({
    queryKey: ["wl-quotes", [...wlTickers].sort()],
    queryFn: () => wlTickers.length ? api.getQuotesBatch(wlTickers) : Promise.resolve([]),
    enabled: wlTickers.length > 0,
    staleTime: 60_000,
  });
  const wlQuotes = wlQuotesQ.data ?? [];

  // 策略結果
  const stratQ = useQuery({
    queryKey: ["strategy-results"],
    queryFn: () => api.getStrategyResults(),
    staleTime: 5 * 60_000,
  });
  const totalHits = stratQ.data
    ? Object.values(stratQ.data.strategies).reduce((s, arr) => s + arr.length, 0)
    : 0;

  // 觀察清單漲跌摘要
  const wlSummary = useMemo(() => {
    if (wlQuotes.length === 0) return null;
    const ups = wlQuotes.filter(q => q.change_pct > 0).length;
    const downs = wlQuotes.filter(q => q.change_pct < 0).length;
    const top = [...wlQuotes].sort((a, b) => b.change_pct - a.change_pct).slice(0, 3);
    const bot = [...wlQuotes].sort((a, b) => a.change_pct - b.change_pct).slice(0, 3);
    return { ups, downs, top, bot, total: wlQuotes.length };
  }, [wlQuotes]);

  return (
    <div className="space-y-4 pb-4">
      {/* Hero 問候 */}
      <motion.div
        initial={{ opacity: 0, y: -6 }}
        animate={{ opacity: 1, y: 0 }}
        className="rounded-st p-5 hero-halo"
        style={{
          background: "linear-gradient(135deg, #0f766e 0%, #0a1a1f 50%, #16181d 100%)",
          border: "1px solid #2f343d",
          boxShadow: [
            "inset 0 1px 0 rgba(255,255,255,0.1)",
            "inset 0 -1px 0 rgba(0,0,0,0.4)",
            "0 0 32px rgba(20,184,166,0.12)",
          ].join(", "),
        }}
      >
        <div className="text-[10px] tracking-[0.25em] text-teal-300 font-bold">
          {new Date().toLocaleDateString("zh-TW", { dateStyle: "long" })} · {["週日","週一","週二","週三","週四","週五","週六"][new Date().getDay()]}
        </div>
        <h2 className="text-2xl font-extrabold text-st-fg mt-1.5 leading-tight">
          {greeting},今日市場健檢
        </h2>
        <p className="text-teal-200 text-xs mt-2">
          開盤前 5 分鐘看一眼 · 盤後分析,不適合盤中即時下單
        </p>
      </motion.div>

      {/* 策略命中今日 */}
      {stratQ.data && totalHits > 0 && (
        <motion.button
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          onClick={() => onNav("scan")}
          whileTap={{ scale: 0.98 }}
          className="w-full rounded-st p-4 text-left"
          style={{
            background: [
              "radial-gradient(circle at 12% 18%, rgba(255,255,255,0.08), transparent 35%)",
              "linear-gradient(180deg, #1c2028 0%, #16181d 50%, #11141a 100%)",
            ].join(", "),
            border: "1px solid #3a4150",
            borderLeft: "3px solid #5eead4",
            boxShadow: "inset 0 1px 0 rgba(255,255,255,0.08), inset 0 -1px 0 rgba(0,0,0,0.4)",
          }}
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <span className="text-3xl">📡</span>
              <div>
                <div className="text-xs text-teal-300 font-bold tracking-wider">[Paper] 策略訊號</div>
                <div className="font-extrabold text-st-fg mt-0.5">
                  今日 <span className="text-teal-300 tabular-nums text-lg">{totalHits}</span> 檔命中 7 種真 alpha 策略
                </div>
              </div>
            </div>
            <span className="text-teal-300">→</span>
          </div>
        </motion.button>
      )}

      {/* 觀察清單巡禮 */}
      {wlSummary && (
        <motion.div
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.05 }}
          className="rounded-st p-4"
          style={{
            background: [
              "radial-gradient(circle at 12% 18%, rgba(255,255,255,0.06), transparent 35%)",
              "linear-gradient(180deg, #1c2028 0%, #16181d 50%, #11141a 100%)",
            ].join(", "),
            border: "1px solid #3a4150",
            borderLeft: "3px solid #fbbf24",
            boxShadow: "inset 0 1px 0 rgba(255,255,255,0.08), inset 0 -1px 0 rgba(0,0,0,0.4)",
          }}
        >
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <span className="text-2xl">⭐</span>
              <div>
                <div className="text-xs text-amber-300 font-bold tracking-wider">觀察清單巡禮</div>
                <div className="font-extrabold text-st-fg text-sm">
                  共 {wlSummary.total} 檔 · <span className="text-red-400 tabular-nums">{wlSummary.ups}</span> 漲 / <span className="text-emerald-400 tabular-nums">{wlSummary.downs}</span> 跌
                </div>
              </div>
            </div>
            <button
              onClick={() => onNav("watch")}
              className="text-teal-300 text-xs font-bold"
            >
              全部 →
            </button>
          </div>
          <div className="grid grid-cols-2 gap-2">
            {/* 漲多 top 3 */}
            <div>
              <div className="text-[10px] text-st-muted font-bold mb-1 flex items-center gap-1">
                <TrendingUpIcon className="w-3 h-3 text-red-400" /> 漲多 TOP 3
              </div>
              <div className="space-y-1">
                {wlSummary.top.map((q: import("@/lib/api").Quote) => (
                  <MiniQuoteRow key={q.ticker} q={q} onClick={() => router.push(`/ticker/${q.ticker}`)} />
                ))}
              </div>
            </div>
            <div>
              <div className="text-[10px] text-st-muted font-bold mb-1 flex items-center gap-1">
                <TrendingDownIcon className="w-3 h-3 text-emerald-400" /> 跌多 TOP 3
              </div>
              <div className="space-y-1">
                {wlSummary.bot.map((q) => (
                  <MiniQuoteRow key={q.ticker} q={q} onClick={() => router.push(`/ticker/${q.ticker}`)} />
                ))}
              </div>
            </div>
          </div>
        </motion.div>
      )}

      {!wlSummary && (wlQ.isFetched || isGuest) && wlList.length === 0 && (
        <div
          className="rounded-st p-5 text-center"
          style={{ background: "#16181d", border: "1px dashed #2f343d" }}
        >
          <div className="text-3xl mb-2">⭐</div>
          <p className="text-sm text-st-muted">
            還沒有觀察清單。<button onClick={() => onNav("search")} className="text-teal-300 font-bold underline">去搜尋股票</button>加入第一檔。
          </p>
        </div>
      )}

      {/* 🌡️ 大盤狀態 — real data */}
      <MarketDashboardCard />
    </div>
  );
}

function MarketDashboardCard() {
  const { data, isLoading } = useQuery({
    queryKey: ["market-dashboard"],
    queryFn: () => api.getMarketDashboard(),
    staleTime: 5 * 60_000,
  });
  const items: Array<{ key: "taiex" | "vix" | "sp500" | "nasdaq" | "nikkei" | "dxj" | "gold" | "oil" | "silver"; emoji: string }> = [
    { key: "taiex", emoji: "🇹🇼" },
    { key: "vix", emoji: "😱" },
    { key: "sp500", emoji: "🇺🇸" },
    { key: "nasdaq", emoji: "💻" },
    { key: "nikkei", emoji: "🇯🇵" },
    { key: "dxj", emoji: "💴" },
    { key: "gold", emoji: "🪙" },
    { key: "oil", emoji: "🛢️" },
    { key: "silver", emoji: "🥈" },
  ];
  return (
    <div
      className="rounded-st p-4"
      style={{
        background: [
          "radial-gradient(circle at 12% 18%, rgba(255,255,255,0.06), transparent 35%)",
          "linear-gradient(180deg, #1c2028 0%, #16181d 50%, #11141a 100%)",
        ].join(", "),
        border: "1px solid #3a4150",
        borderLeft: "3px solid #60a5fa",
        boxShadow: "inset 0 1px 0 rgba(255,255,255,0.08), inset 0 -1px 0 rgba(0,0,0,0.4)",
      }}
    >
      <div className="flex items-center gap-2 mb-3">
        <span className="text-2xl">🌡️</span>
        <div>
          <div className="text-xs text-blue-400 font-bold tracking-wider">大盤狀態</div>
          <div className="font-extrabold text-st-fg text-sm">全球指數即時</div>
        </div>
      </div>
      {isLoading && <div className="text-xs text-st-muted">載入中⋯</div>}
      {data && (
        <div className="grid grid-cols-2 gap-2">
          {items.map(({ key, emoji }) => {
            const idx = data[key];
            if (!idx) return null;
            const c = chgColor(idx.change_pct);
            return (
              <div
                key={key}
                className="rounded p-2"
                style={{ background: "#0f1218", border: "1px solid #2f343d" }}
              >
                <div className="flex items-center gap-1.5 text-[10px] text-st-muted mb-1">
                  <span>{emoji}</span>
                  <span className="font-bold truncate">{idx.name}</span>
                </div>
                <div className="flex items-baseline justify-between">
                  <div className="tabular-nums font-extrabold text-st-fg" style={{ fontSize: "0.95rem" }}>
                    {idx.price.toFixed(idx.price > 100 ? 0 : 2)}
                  </div>
                  <div className="tabular-nums font-bold text-[10px]" style={{ color: c }}>
                    {chgArrow(idx.change_pct)} {Math.abs(idx.change_pct).toFixed(2)}%
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function TrendingUpIcon({ className }: { className: string }) {
  return <svg viewBox="0 0 16 16" className={className} fill="currentColor"><path d="M3 11l4-4 3 3 5-5v3h2V3h-5v2h3l-5 5-3-3-5 5z"/></svg>;
}
function TrendingDownIcon({ className }: { className: string }) {
  return <svg viewBox="0 0 16 16" className={className} fill="currentColor"><path d="M3 5l4 4 3-3 5 5v-3h2v6h-5v-2h3L9 6l-3 3-5-5z"/></svg>;
}

function RankRow({ rank, item, onClick, mode }: {
  rank: number; item: import("@/lib/api").RankItem; onClick: () => void;
  mode: "up" | "down" | "vol";
}) {
  const c = chgColor(item.change_pct);
  const medal = rank === 1 ? "🥇" : rank === 2 ? "🥈" : rank === 3 ? "🥉" : `${rank}.`;
  const showVol = mode === "vol";
  const Icon = mode === "up" ? ArrowUpRight : mode === "down" ? ArrowDownRight : BarChart3;
  return (
    <motion.button
      onClick={onClick}
      whileTap={{ scale: 0.98 }}
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      className="w-full text-left rounded-st flex items-center gap-3 p-3"
      style={{
        background: "linear-gradient(180deg, #1c2028 0%, #16181d 50%, #11141a 100%)",
        border: "1px solid #3a4150",
        borderLeft: `3px solid ${c}`,
        boxShadow: "inset 0 1px 0 rgba(255,255,255,0.08), inset 0 -1px 0 rgba(0,0,0,0.4)",
      }}
    >
      <div className="w-7 text-center text-base font-bold tabular-nums" style={{ color: rank <= 3 ? "#fbbf24" : "#94a3b8" }}>
        {medal}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-2">
          <span className="tabular-nums text-sm font-bold text-st-fg">{item.ticker}</span>
          <span className="text-xs text-st-soft truncate">{item.name}</span>
        </div>
        <div className="text-[10px] text-st-muted">{item.industry || "—"}</div>
      </div>
      <div className="text-right flex-shrink-0">
        <div className="tabular-nums font-bold text-st-fg text-sm">
          {item.price.toFixed(2)}
        </div>
        {showVol ? (
          <div className="text-[10px] text-purple-300 tabular-nums">
            <Icon className="w-3 h-3 inline" /> {fmtVolMain(item.volume)}
          </div>
        ) : (
          <div className="text-[10px] tabular-nums font-bold" style={{ color: c }}>
            <Icon className="w-3 h-3 inline" /> {chgArrow(item.change_pct)} {Math.abs(item.change_pct).toFixed(2)}%
          </div>
        )}
      </div>
    </motion.button>
  );
}

function fmtVolMain(v: number): string {
  if (v >= 100_000_000) return `${(v / 100_000_000).toFixed(1)}億`;
  if (v >= 10_000) return `${(v / 10_000).toFixed(0)}萬`;
  return v.toLocaleString();
}

function MiniQuoteRow({ q, onClick }: { q: import("@/lib/api").Quote; onClick: () => void }) {
  const c = chgColor(q.change_pct);
  return (
    <button
      onClick={onClick}
      className="w-full text-left flex items-center justify-between gap-1 rounded px-1.5 py-1.5 hover:bg-white/[0.02] active:scale-[0.97]"
    >
      <span className="text-[10px] text-st-soft truncate flex-1 min-w-0">{q.ticker}</span>
      <span className="tabular-nums text-[10px] font-bold flex-shrink-0" style={{ color: c }}>
        {chgArrow(q.change_pct)} {Math.abs(q.change_pct).toFixed(1)}%
      </span>
    </button>
  );
}

function SearchPanel() {
  const [q, setQ] = useState("");
  const [mode, setMode] = useState<"hot" | "up" | "down" | "vol">("hot");
  const router = useRouter();
  const isSearching = q.trim().length >= 1;

  // 排行榜 fetch (依 mode)
  const rankBy = mode === "up" ? "up" : mode === "down" ? "down" : "volume";
  const { data: rankData, isLoading: rankLoading } = useQuery({
    queryKey: ["ranking", rankBy],
    queryFn: () => api.getRanking(rankBy as "up" | "down" | "volume", 20),
    enabled: !isSearching && mode !== "hot",
    staleTime: 3 * 60_000,
  });

  // 搜尋結果
  const { data: results, isFetching: searchFetching } = useQuery({
    queryKey: ["search", q],
    queryFn: () => api.searchTickers(q),
    enabled: isSearching,
    staleTime: 300_000,
  });

  // 熱門股 batch quote(只在空白時跑)
  const { data: hotQuotes, isLoading: hotLoading } = useQuery({
    queryKey: ["hot-quotes", ALL_HOT_TICKERS.join(",")],
    queryFn: () => api.getQuotesBatch(ALL_HOT_TICKERS),
    enabled: !isSearching,
    staleTime: 120_000,
  });
  const hotQuoteMap: Record<string, Quote> = {};
  (hotQuotes ?? []).forEach((q) => { hotQuoteMap[q.ticker] = q; });

  return (
    <div className="space-y-4 pb-4">
      {/* Search header */}
      <div>
        <h2 className="text-2xl font-extrabold text-st-fg">🔍 搜尋</h2>
        <p className="text-st-muted text-xs mt-1">
          輸入代碼或公司名 → 看 4 面健檢分數
        </p>
      </div>

      {/* Search input with clear button */}
      <div className="relative">
        <Search className="w-4 h-4 text-st-muted absolute left-4 top-1/2 -translate-y-1/2 z-10" />
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="2330 / 台積電 / 0050"
          autoFocus
          className="pl-10 pr-10"
        />
        {q && (
          <button
            onClick={() => setQ("")}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-st-muted hover:text-st-fg p-1.5"
            aria-label="清除"
          >
            <X className="w-4 h-4" />
          </button>
        )}
      </div>

      {/* 搜尋結果 */}
      {isSearching && (
        <>
          {searchFetching && (
            <div className="text-sm text-st-muted">搜尋中⋯</div>
          )}
          <div className="space-y-1.5">
            {(results ?? []).slice(0, 20).map((r) => (
              <StockRow
                key={r.ticker}
                ticker={r.ticker}
                name={r.name}
                industry={r.industry}
                quote={hotQuoteMap[r.ticker]}
                onOpen={() => router.push(`/ticker/${r.ticker}`)}
              />
            ))}
            {!searchFetching && results?.length === 0 && (
              <div className="text-sm text-st-muted text-center py-8">
                找不到「{q}」相關股票
              </div>
            )}
          </div>
        </>
      )}

      {/* Mode toggle(空白搜尋時顯示)*/}
      {!isSearching && (
        <div className="flex gap-1 bg-st-bg border border-st-border rounded-st p-1">
          {([
            { k: "hot", label: "🔥 熱門", color: "#fbbf24" },
            { k: "up", label: "▲ 漲幅榜", color: "#ef4444" },
            { k: "down", label: "▼ 跌幅榜", color: "#10b981" },
            { k: "vol", label: "📊 量爆榜", color: "#a78bfa" },
          ] as const).map((m) => (
            <button
              key={m.k}
              onClick={() => setMode(m.k)}
              className="flex-1 text-[11px] font-bold py-1.5 rounded transition-colors"
              style={{
                background: mode === m.k ? m.color + "30" : "transparent",
                color: mode === m.k ? m.color : "#94a3b8",
                border: mode === m.k ? `1px solid ${m.color}60` : "1px solid transparent",
              }}
            >
              {m.label}
            </button>
          ))}
        </div>
      )}

      {/* 排行榜模式 — 拉 ranking data */}
      {!isSearching && mode !== "hot" && (
        <div className="space-y-2">
          {rankLoading && (
            <div className="text-xs text-st-muted text-center py-4">
              📡 載入排行榜中⋯
            </div>
          )}
          {rankData?.items.map((r, i) => (
            <RankRow key={r.ticker} rank={i + 1} item={r} onClick={() => router.push(`/ticker/${r.ticker}`)} mode={mode} />
          ))}
        </div>
      )}

      {/* 熱門股(空白搜尋時顯示)*/}
      {!isSearching && mode === "hot" && (
        <div className="space-y-5">
          {/* 大標 */}
          <div className="flex items-center gap-2 mt-2">
            <Flame className="w-4 h-4 text-amber-300" />
            <span className="text-xs text-amber-300 font-bold tracking-widest">
              熱門股推薦
            </span>
          </div>

          {/* 每個分類 */}
          {HOT_STOCK_CATEGORIES.map((cat, ci) => (
            <motion.div
              key={cat.key}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: ci * 0.05 }}
              className="space-y-2"
            >
              <div>
                <h3 className="text-base font-extrabold text-st-fg flex items-center gap-2">
                  <span className="text-xl">{cat.emoji}</span>
                  {cat.label}
                </h3>
                <p className="text-[10px] text-st-muted">{cat.desc}</p>
              </div>
              <div className="space-y-1">
                {cat.tickers.map((tk) => {
                  const q = hotQuoteMap[tk];
                  if (hotLoading && !q) {
                    return (
                      <div
                        key={tk}
                        className="shimmer rounded-st h-[52px]"
                      />
                    );
                  }
                  return (
                    <StockRow
                      key={tk}
                      ticker={tk}
                      name={q?.name ?? ""}
                      industry={q?.industry ?? ""}
                      quote={q}
                      onOpen={() => router.push(`/ticker/${tk}`)}
                    />
                  );
                })}
              </div>
            </motion.div>
          ))}

          {/* 小提示 */}
          <div className="text-center text-[10px] text-st-muted pt-2">
            💡 找不到想看的?直接輸入代碼或公司名搜尋
          </div>
        </div>
      )}
    </div>
  );
}

function ScanPanel() {
  const router = useRouter();
  const { data, isLoading } = useQuery({
    queryKey: ["strategy-results"],
    queryFn: () => api.getStrategyResults(),
    staleTime: 60_000,
  });

  const STRATEGY_META: Record<string, { icon: string; name: string; alpha: string; frame: string }> = {
    rev_yoy: { icon: "💰", name: "月營收 YoY 高成長", alpha: "+5.10%", frame: "60d" },
    low_retail: { icon: "👻", name: "散戶比例極端低位", alpha: "+11.3pp", frame: "20d" },
    high_retail: { icon: "⚠️", name: "散戶比例極端高位", alpha: "Avoid", frame: "—" },
    quiet_limitdown: { icon: "📉", name: "量縮跌停反彈", alpha: "+4.27%", frame: "5d" },
    quiet_limitup: { icon: "📈", name: "量縮漲停", alpha: "+4.83%", frame: "20d" },
    ab_consensus: { icon: "🤝", name: "AB 雙重共識", alpha: "+8.78%", frame: "60d" },
    govbank_reverse: { icon: "🏦", name: "政府行庫反向", alpha: "+1.62pp", frame: "60d" },
  };

  return (
    <div className="space-y-4 pb-4">
      <div>
        <h2 className="text-2xl font-extrabold text-white">📡 策略掃描</h2>
        <p className="text-slate-400 text-xs mt-1">
          {data
            ? `${data.fresh ? "✓ 資料新鮮" : "⚠️ 資料過期"} · 更新於 ${new Date(data.updated_at).toLocaleString("zh-TW", { hour: "2-digit", minute: "2-digit", month: "2-digit", day: "2-digit" })}`
            : "讀取中⋯"}
        </p>
      </div>
      {isLoading && <div className="text-sm text-slate-500">載入中⋯</div>}
      {data && Object.entries(data.strategies).map(([key, hits]) => {
        const meta = STRATEGY_META[key] ?? { icon: "📊", name: key, alpha: "—", frame: "—" };
        return (
          <div key={key} className="bg-ink-900/60 border border-ink-700 rounded-2xl p-4">
            <div className="flex items-start justify-between mb-3">
              <div className="flex items-center gap-3">
                <div className="text-3xl">{meta.icon}</div>
                <div>
                  <div className="font-bold text-white">{meta.name}</div>
                  <div className="text-xs text-brand-300">
                    alpha {meta.alpha} · {meta.frame}
                  </div>
                </div>
              </div>
              <span className="text-xs bg-brand-500/15 text-brand-300 font-bold px-2 py-1 rounded">
                {hits.length} 命中
              </span>
            </div>
            {hits.length > 0 ? (
              <div className="space-y-1.5">
                {hits.slice(0, 5).map((h) => (
                  <button
                    key={h.ticker}
                    onClick={() => router.push(`/ticker/${h.ticker}`)}
                    className="w-full text-left text-sm bg-ink-800/50 hover:bg-ink-700/80 rounded-lg px-3 py-2 flex items-center justify-between active:scale-[0.98]"
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      <span className="text-brand-300 font-mono text-xs">{h.ticker}</span>
                      <span className="font-bold text-white truncate">{h.name}</span>
                    </div>
                    {h.metric != null && (
                      <span className="text-xs text-emerald-400 font-bold flex-shrink-0">
                        {h.metric.toFixed(2)}
                      </span>
                    )}
                  </button>
                ))}
                {hits.length > 5 && (
                  <div className="text-xs text-slate-500 text-center pt-1">
                    還有 {hits.length - 5} 檔 — 即將支援「看全部」
                  </div>
                )}
              </div>
            ) : (
              <div className="text-sm text-slate-500 text-center py-3">
                今日沒有命中
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function MePanel() {
  const isGuest = useSession((s) => s.isGuest);
  const clearGuest = useSession((s) => s.clearGuest);
  const router = useRouter();
  const [email, setEmail] = useState<string | null>(null);
  const [sub, setSub] = useState<"home" | "global" | "selfcheck" | "about">("home");

  useEffect(() => {
    const sb = createClient();
    sb.auth.getUser().then(({ data }) => setEmail(data.user?.email ?? null));
  }, []);

  if (sub === "global") return <GlobalMarketsPanel onBack={() => setSub("home")} />;
  if (sub === "selfcheck") return <SelfCheckPanel onBack={() => setSub("home")} />;
  if (sub === "about") return <AboutPanel onBack={() => setSub("home")} />;

  return (
    <div className="space-y-4 pb-4">
      <h2 className="text-2xl font-extrabold text-st-fg">👤 我的</h2>

      {isGuest ? (
        <div
          className="rounded-st p-5"
          style={{
            background: "#16181d",
            border: "1px solid rgba(251, 191, 36, 0.3)",
            borderLeft: "3px solid #fbbf24",
          }}
        >
          <div className="flex items-center gap-2 mb-2">
            <Ghost className="w-5 h-5 text-amber-300" />
            <h3 className="font-bold text-st-fg">訪客模式</h3>
          </div>
          <p className="text-sm text-amber-200 mb-4">
            資料只存裝置,清快取就消失。註冊即可永久雲端同步。
          </p>
          <Button
            variant="primary"
            size="lg"
            className="w-full"
            onClick={() => { clearGuest(); router.push("/login"); }}
          >
            ✨ 註冊保存資料
          </Button>
        </div>
      ) : (
        <div
          className="rounded-st p-5"
          style={{
            background: "#16181d",
            border: "1px solid #2f343d",
            borderLeft: "3px solid #5eead4",
          }}
        >
          <div className="text-xs text-st-muted font-bold tracking-widest">已登入</div>
          <div className="text-st-fg font-bold mt-1 break-all">{email ?? "—"}</div>
        </div>
      )}

      {/* Sub-menu links */}
      <div className="space-y-2">
        <MenuItem
          icon="🌍"
          title="多市場"
          desc="台美日韓印越黃金 — 全球 ETF 配置"
          onClick={() => setSub("global")}
        />
        <MenuItem
          icon="🥬"
          title="韭菜病自檢"
          desc="8 題快速問卷,看你的韭菜病等級"
          onClick={() => setSub("selfcheck")}
        />
        <MenuItem
          icon="🔔"
          title="通知設定"
          desc="集中度警示、晨報推播"
          badge="WIP"
        />
        <MenuItem
          icon="💎"
          title="升級 PRO"
          desc="晨報精選 5 檔 + 觀察清單一鍵巡禮 + 無限 AI 解讀"
          badge="WIP"
        />
        <MenuItem
          icon="❓"
          title="關於 / 名詞解釋"
          desc="健檢分數 / 稀有度 / VIX / MA200 等 14 個名詞"
          onClick={() => setSub("about")}
        />
      </div>

      {!isGuest && (
        <Button
          variant="ghost"
          size="lg"
          className="w-full"
          onClick={async () => {
            const sb = createClient();
            await sb.auth.signOut();
            router.push("/login");
          }}
        >
          <LogOut className="w-4 h-4" /> 登出
        </Button>
      )}
    </div>
  );
}

function MenuItem({ icon, title, desc, badge, onClick }: {
  icon: string; title: string; desc: string; badge?: string; onClick?: () => void;
}) {
  return (
    <button
      onClick={onClick}
      disabled={!onClick && !badge}
      className="w-full text-left rounded-st p-3.5 flex items-center gap-3 active:scale-[0.98] transition-transform disabled:opacity-60"
      style={{
        background: [
          "radial-gradient(circle at 12% 18%, rgba(255,255,255,0.06), transparent 35%)",
          "linear-gradient(180deg, #1c2028 0%, #16181d 100%)",
        ].join(", "),
        border: "1px solid #3a4150",
        boxShadow: "inset 0 1px 0 rgba(255,255,255,0.06), inset 0 -1px 0 rgba(0,0,0,0.3)",
      }}
    >
      <span className="text-2xl flex-shrink-0">{icon}</span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-bold text-st-fg">{title}</span>
          {badge && (
            <span className="text-[9px] font-bold tracking-wider text-amber-300 bg-amber-500/15 px-1.5 py-0.5 rounded">{badge}</span>
          )}
        </div>
        <div className="text-xs text-st-muted mt-0.5">{desc}</div>
      </div>
      {onClick && <span className="text-st-muted">→</span>}
    </button>
  );
}

/* ────── 多市場 panel ────── */
const GLOBAL_MARKETS = [
  { region: "🇹🇼 台灣", desc: "0050 大盤 + 主流 ETF", tickers: ["0050", "0056", "00878", "00692"] },
  { region: "🇺🇸 美國", desc: "S&P 500 / NASDAQ", tickers: ["SPY", "QQQ", "VTI", "VOO"] },
  { region: "🇯🇵 日本", desc: "日股(避免日圓貶值)", tickers: ["DXJ", "EWJ", "HEWJ"] },
  { region: "🇰🇷 韓國", desc: "memory 漏網最大 alpha", tickers: ["EWY", "FLKR"] },
  { region: "🇮🇳 印度", desc: "新興市場核心", tickers: ["INDA", "EPI"] },
  { region: "🇻🇳 越南", desc: "替代中國工廠", tickers: ["VNM"] },
  { region: "🪙 黃金 / 商品", desc: "抗通膨", tickers: ["GLD", "DBA"] },
];

function GlobalMarketsPanel({ onBack }: { onBack: () => void }) {
  return (
    <div className="space-y-4 pb-4">
      <button onClick={onBack} className="text-teal-300 text-sm flex items-center gap-2">
        <span>←</span> 回 我的
      </button>
      <h2 className="text-2xl font-extrabold text-st-fg">🌍 多市場配置</h2>
      <p className="text-st-muted text-xs">
        記得分散全球 7 區塊 · 別把雞蛋全放台股
      </p>
      <div className="space-y-3">
        {GLOBAL_MARKETS.map((m, i) => (
          <motion.div
            key={m.region}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.05 }}
            className="rounded-st p-4"
            style={{
              background: [
                "radial-gradient(circle at 12% 18%, rgba(255,255,255,0.06), transparent 35%)",
                "linear-gradient(180deg, #1c2028 0%, #16181d 100%)",
              ].join(", "),
              border: "1px solid #3a4150",
              boxShadow: "inset 0 1px 0 rgba(255,255,255,0.08), inset 0 -1px 0 rgba(0,0,0,0.4)",
            }}
          >
            <div className="font-extrabold text-st-fg text-lg">{m.region}</div>
            <div className="text-xs text-st-muted mb-2">{m.desc}</div>
            <div className="flex flex-wrap gap-1.5">
              {m.tickers.map((tk) => (
                <a
                  key={tk}
                  href={`/ticker/${tk}`}
                  className="text-xs font-mono font-bold text-teal-300 bg-teal-500/10 hover:bg-teal-500/20 px-2 py-1 rounded border border-teal-500/30 transition-colors"
                >
                  {tk}
                </a>
              ))}
            </div>
          </motion.div>
        ))}
      </div>
    </div>
  );
}

/* ────── 關於 panel ────── */
const ABOUT_TERMS = [
  { term: "健檢分數", desc: "0-100 分綜合評估個股體質(技術 40% + 籌碼 30% + 基本 20% + 新聞 10%)。70+ 健康、50-69 亞健康、<50 韭菜病。歷史驗證 2020-26 7y backtest:70+ 體質股 60d 平均 +10.93% / win 60.3% / vs 0050 alpha +4.57pp(n=194)。" },
  { term: "稀有度", desc: "用 20 日平均成交額分:LEGENDARY > 50 億、EPIC 10-50 億、RARE 3-10 億、UNCOMMON 0.5-3 億、COMMON < 0.5 億。代表流動性,跟好壞無關。" },
  { term: "VIX", desc: "美股恐慌指數,反映 30 天波動預期。> 30 = 高恐慌、< 18 = 過度樂觀、20-30 = 正常範圍。" },
  { term: "MA200", desc: "200 日移動平均線。股價距 MA200 反映長期趨勢:+30% 過熱、合理區間 ±5%、-15% 低估。" },
  { term: "AB 雙重共識", desc: "外資 + 投信同時大買的訊號。memory 真 alpha:60d +8.78% / t=+3.83 / n=126 OOS PASS。" },
  { term: "量縮漲停", desc: "單日漲停但成交量低於 20 日均量 0.8 倍。memory 真 alpha:20d +4.83% / n=5437。「無量上漲」反映籌碼穩定。" },
  { term: "量縮跌停反彈", desc: "單日跌停但成交量低。memory 真 alpha:20d +7.99% / 5d +4.27% / n=4733。籌碼不亂出反彈機率高。" },
  { term: "行庫共識度反向", desc: "5+ 家公股行庫同買 = 政府護盤股。memory:後續 60d alpha -1.62% / t=-28.46。「越多護盤後續越弱」反直覺真 alpha。" },
  { term: "散戶比例", desc: "持股 < 50 張的散戶合計佔比。比例極端區(極低 = 法人主導 / 極高 = 韭菜聚集)有 alpha,lift +11.3pp。" },
  { term: "月營收 YoY", desc: "本月營收 vs 去年同月年增率。> 30% + 流動性過濾 = memory 真 alpha 60d +3.95% / t=24.19 / n=24K。" },
  { term: "RSI", desc: "0-100 動能指標。> 70 超買 / < 30 超賣 / 30-70 中性。" },
  { term: "KD", desc: "K 與 D 線交叉預測短期反轉。K > D 黃金交叉(看多)、K < D 死亡交叉(看空)。" },
  { term: "本益比 PER", desc: "股價 ÷ EPS。< 15 偏低 / 15-25 合理 / > 30 偏高。台股大盤平均 ~18。" },
  { term: "DXJ vs EWJ", desc: "兩個日本 ETF。DXJ 避險日圓貶值風險,EWJ 完整日股曝險。memory:DXJ 16 年 alpha +12.33% CAGR(+6.40pp/yr vs 0050)。" },
];

function AboutPanel({ onBack }: { onBack: () => void }) {
  return (
    <div className="space-y-4 pb-4">
      <button onClick={onBack} className="text-teal-300 text-sm flex items-center gap-2">
        <span>←</span> 回 我的
      </button>
      <h2 className="text-2xl font-extrabold text-st-fg">❓ 關於韭菜健檢</h2>

      {/* Brand block */}
      <div
        className="rounded-st p-5 hero-halo"
        style={{
          background: "linear-gradient(135deg, #0f766e 0%, #0a1a1f 50%, #16181d 100%)",
          border: "1px solid #2f343d",
          boxShadow: "inset 0 1px 0 rgba(255,255,255,0.1), 0 0 24px rgba(20,184,166,0.12)",
        }}
      >
        <div className="text-3xl">🩺</div>
        <h3 className="text-xl font-extrabold text-st-fg mt-2">買進前,先做一次健檢</h3>
        <p className="text-sm text-teal-200 mt-2 leading-relaxed">
          韭菜不是命,是健檢不夠勤。這不是投顧 App,
          <b className="text-teal-300">不報明牌、不喊飆股</b>,
          只給你買進前該知道的客觀數據。
        </p>
      </div>

      {/* 4 大原則 */}
      <div className="rounded-st p-4" style={{ background: "#16181d", border: "1px solid #2f343d", borderLeft: "3px solid #fbbf24" }}>
        <div className="text-xs text-amber-300 font-bold tracking-widest mb-2">🛡️ 4 大原則</div>
        <ul className="space-y-1.5 text-sm text-st-soft">
          <li>✓ 不報明牌、不給買賣建議</li>
          <li>✓ 不喊飆股、不炒題材</li>
          <li>✓ 所有訊號 backtest + Walk-Forward + MCPT 驗證</li>
          <li>✓ 純客觀資料展示,盈虧自負</li>
        </ul>
      </div>

      {/* 名詞解釋 */}
      <div>
        <h3 className="text-base font-extrabold text-st-fg mb-2">📖 名詞解釋(14 個)</h3>
        <div className="space-y-2">
          {ABOUT_TERMS.map((t, i) => (
            <details
              key={t.term}
              className="rounded-st p-3"
              style={{ background: "#16181d", border: "1px solid #2f343d" }}
            >
              <summary className="cursor-pointer font-bold text-teal-300 text-sm">
                {i + 1}. {t.term}
              </summary>
              <p className="text-xs text-st-soft mt-2 leading-relaxed">{t.desc}</p>
            </details>
          ))}
        </div>
      </div>

      {/* 連結 */}
      <div className="space-y-2 pt-2">
        <a href="https://aaowobbowocc-ai.github.io/leek-check/privacy.html" target="_blank" rel="noreferrer" className="block w-full text-center text-sm text-teal-300 font-bold py-3 rounded-st" style={{ background: "#16181d", border: "1px solid #2f343d" }}>
          🔒 隱私政策
        </a>
        <a href="https://aaowobbowocc-ai.github.io/leek-check/delete-account.html" target="_blank" rel="noreferrer" className="block w-full text-center text-sm text-rose-300 font-bold py-3 rounded-st" style={{ background: "#16181d", border: "1px solid #2f343d" }}>
          🗑️ 帳號刪除說明
        </a>
      </div>

      <p className="text-[10px] text-st-muted text-center pt-2">
        韭菜健檢 v0.2 · aaowobbowocc Apps
      </p>
    </div>
  );
}

/* ────── 自檢 panel ────── */
const SELF_CHECK_QUESTIONS = [
  { q: "你會看 PTT/Dcard 推薦的股票就買嗎?", w: 15 },
  { q: "進場前會看公司財報跟月營收嗎?", w: -10 },
  { q: "你有沒有 all-in 過某一檔股票?", w: 20 },
  { q: "聽到「飆股」「主力」「內線」會心動嗎?", w: 12 },
  { q: "套牢時你會「攤平」拉低成本嗎?", w: 18 },
  { q: "持股有設停損點嗎?", w: -12 },
  { q: "你會看 K 線型態判斷進場嗎?", w: 5 },
  { q: "你的投資組合有 ≥ 3 種資產類別嗎?", w: -15 },
];

function SelfCheckPanel({ onBack }: { onBack: () => void }) {
  const [answers, setAnswers] = useState<Record<number, boolean>>({});
  const [showResult, setShowResult] = useState(false);

  const score = Object.entries(answers).reduce((s, [i, yes]) => {
    return s + (yes ? SELF_CHECK_QUESTIONS[Number(i)].w : 0);
  }, 50);

  const verdict = score >= 80
    ? { label: "重度韭菜病", color: "#f43f5e", desc: "高風險!過度仰賴消息面 + 缺乏紀律,建議從 0050 DCA 開始重建" }
    : score >= 60
    ? { label: "中度韭菜病", color: "#fbbf24", desc: "有改善空間,試著加強財報分析 + 設停損 + 多元配置" }
    : score >= 40
    ? { label: "輕度症狀", color: "#fbbf24", desc: "基本面有概念,但要避免追高與情緒進場" }
    : { label: "免疫!", color: "#5eead4", desc: "你有紀律 + 分析能力,持續保持風險管理" };

  return (
    <div className="space-y-4 pb-4">
      <button onClick={onBack} className="text-teal-300 text-sm flex items-center gap-2">
        <span>←</span> 回 我的
      </button>
      <h2 className="text-2xl font-extrabold text-st-fg">🥬 韭菜病自檢</h2>
      <p className="text-st-muted text-xs">8 題快速問卷,測你的投資紀律</p>

      {!showResult && (
        <div className="space-y-2">
          {SELF_CHECK_QUESTIONS.map((q, i) => (
            <div
              key={i}
              className="rounded-st p-3"
              style={{ background: "#16181d", border: "1px solid #2f343d" }}
            >
              <div className="text-sm text-st-fg font-bold mb-2">
                <span className="text-teal-300 mr-2">Q{i + 1}.</span>
                {q.q}
              </div>
              <div className="flex gap-2">
                <button
                  onClick={() => setAnswers({ ...answers, [i]: true })}
                  className={`flex-1 py-2 rounded text-xs font-bold ${answers[i] === true ? "bg-rose-500/30 border border-rose-400 text-rose-200" : "bg-ink-900 border border-st-border text-st-muted"}`}
                >
                  是
                </button>
                <button
                  onClick={() => setAnswers({ ...answers, [i]: false })}
                  className={`flex-1 py-2 rounded text-xs font-bold ${answers[i] === false ? "bg-teal-500/30 border border-teal-400 text-teal-200" : "bg-ink-900 border border-st-border text-st-muted"}`}
                >
                  否
                </button>
              </div>
            </div>
          ))}
          <Button
            variant="primary"
            size="lg"
            className="w-full mt-4"
            disabled={Object.keys(answers).length < SELF_CHECK_QUESTIONS.length}
            onClick={() => setShowResult(true)}
          >
            {Object.keys(answers).length < SELF_CHECK_QUESTIONS.length
              ? `剩 ${SELF_CHECK_QUESTIONS.length - Object.keys(answers).length} 題`
              : "🩺 看結果"}
          </Button>
        </div>
      )}

      {showResult && (
        <motion.div
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          className="rounded-st p-5 text-center"
          style={{
            background: "#16181d",
            border: `2px solid ${verdict.color}`,
            boxShadow: `0 0 32px ${verdict.color}40`,
          }}
        >
          <div className="text-[10px] text-st-muted tracking-widest font-bold mb-1">韭菜病指數</div>
          <div className="tabular-nums" style={{ fontSize: "4rem", color: verdict.color, fontWeight: 800, lineHeight: 1 }}>
            {Math.max(0, Math.min(100, score))}
          </div>
          <div className="text-xs text-st-muted mb-2">/ 100</div>
          <div className="text-xl font-extrabold mt-2" style={{ color: verdict.color }}>
            {verdict.label}
          </div>
          <p className="text-sm text-st-soft mt-3 leading-relaxed">{verdict.desc}</p>
          <button
            onClick={() => { setAnswers({}); setShowResult(false); }}
            className="mt-5 text-xs text-teal-300 font-bold"
          >
            🔄 重新測一次
          </button>
        </motion.div>
      )}
    </div>
  );
}

function PlaceholderCard({
  title, desc, badge,
}: { title: string; desc: string; badge?: string }) {
  return (
    <div className="bg-ink-900/50 border border-ink-700 rounded-2xl p-4">
      <div className="flex items-start justify-between gap-2">
        <h3 className="font-bold text-white">{title}</h3>
        {badge && (
          <span className="text-[10px] font-bold tracking-wider text-amber-300 bg-amber-500/20 px-2 py-0.5 rounded-full">
            {badge}
          </span>
        )}
      </div>
      <p className="text-sm text-slate-400 mt-2">{desc}</p>
    </div>
  );
}
