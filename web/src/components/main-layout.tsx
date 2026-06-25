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
  const picks = useSession((s) => s.briefingPicks);
  const [userId, setUserId] = useState<string | null>(null);
  const [roundupOpen, setRoundupOpen] = useState(false);

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

        {/* 今日 1 行摘要 — 大盤 / 觀察 / 策略 */}
        <TodayDigest
          watchlistTotal={wlSummary?.total ?? 0}
          watchlistUps={wlSummary?.ups ?? 0}
          watchlistDowns={wlSummary?.downs ?? 0}
          strategyHits={totalHits}
        />
      </motion.div>

      {/* ═══════ 🌡️ 大盤即時(TAIEX + 國際 + 商品)═══════ */}
      <GroupHeader emoji="🌡️" label="大盤即時" />
      <MarketDashboardCard />

      {/* ═══════ 🤖 AI 判讀群 ═══════
          (組件已在 MarketDashboardCard 內了:AI 國際情勢 + AI 新聞情緒)
          —— 留在這裡 placeholder 之後 AI 拆出來)
      */}

      {/* ═══════ 📰 新聞群(已在 MarketDashboardCard 內) ═══════ */}

      {/* ═══════ ⭐ 我的觀察 ═══════ */}
      <GroupHeader emoji="⭐" label="我的觀察" />

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
                {wlSummary.bot.map((q: import("@/lib/api").Quote) => (
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

      {/* 🌅 晨報精選(如有 picks)*/}
      {picks.length > 0 && (
        <BriefPicksCard
          picks={picks}
          quotes={wlQuotes}
          onClick={(tk) => router.push(`/ticker/${tk}`)}
        />
      )}

      {/* 🩺 一鍵巡禮 */}
      {wlList.length > 0 && (
        <HealthRoundupCard
          tickers={wlTickers}
          open={roundupOpen}
          onToggle={() => setRoundupOpen(!roundupOpen)}
        />
      )}
    </div>
  );
}

function BriefPicksCard({ picks, quotes, onClick }: {
  picks: string[];
  quotes: import("@/lib/api").Quote[];
  onClick: (tk: string) => void;
}) {
  const qMap = new Map(quotes.map((q) => [q.ticker, q]));
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
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
      <div className="flex items-center gap-2 mb-3">
        <span className="text-2xl">🌅</span>
        <div>
          <div className="text-xs text-amber-300 font-bold tracking-wider">晨報精選</div>
          <div className="font-extrabold text-st-fg text-sm">
            鎖定 {picks.length} 檔每日健檢
          </div>
        </div>
      </div>
      <div className="space-y-1.5">
        {picks.map((tk) => {
          const q = qMap.get(tk);
          if (!q) return (
            <div key={tk} className="text-[10px] text-st-muted px-2">{tk} (載入中⋯)</div>
          );
          const c = chgColor(q.change_pct);
          return (
            <button
              key={tk}
              onClick={() => onClick(tk)}
              className="w-full flex items-center justify-between rounded p-2 hover:bg-white/[0.03] active:scale-[0.98]"
              style={{ background: "#0f1218", border: "1px solid #2f343d" }}
            >
              <div className="flex items-center gap-2 min-w-0">
                <span className="tabular-nums text-xs font-bold text-st-fg">{q.ticker}</span>
                <span className="text-xs text-st-soft truncate">{q.name}</span>
              </div>
              <div className="text-right flex-shrink-0">
                <span className="tabular-nums text-xs font-bold text-st-fg">{q.price.toFixed(2)}</span>
                <span className="tabular-nums text-[10px] font-bold ml-2" style={{ color: c }}>
                  {chgArrow(q.change_pct)} {Math.abs(q.change_pct).toFixed(2)}%
                </span>
              </div>
            </button>
          );
        })}
      </div>
    </motion.div>
  );
}

function HealthRoundupCard({ tickers, open, onToggle }: {
  tickers: string[]; open: boolean; onToggle: () => void;
}) {
  const router = useRouter();
  const results = useQueries(tickers.slice(0, 10), open);
  const loadedCount = results.filter((r) => r.data).length;
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      className="rounded-st p-4"
      style={{
        background: [
          "radial-gradient(circle at 12% 18%, rgba(255,255,255,0.06), transparent 35%)",
          "linear-gradient(180deg, #1c2028 0%, #16181d 50%, #11141a 100%)",
        ].join(", "),
        border: "1px solid #3a4150",
        borderLeft: "3px solid #5eead4",
        boxShadow: "inset 0 1px 0 rgba(255,255,255,0.08), inset 0 -1px 0 rgba(0,0,0,0.4)",
      }}
    >
      <button onClick={onToggle} className="w-full flex items-center gap-2 text-left">
        <span className="text-2xl">🩺</span>
        <div className="flex-1">
          <div className="text-xs text-teal-300 font-bold tracking-wider">一鍵巡禮</div>
          <div className="font-extrabold text-st-fg text-sm">
            {open ? `掃描中 ${loadedCount}/${Math.min(tickers.length, 10)}` : `掃描全部 ${tickers.length} 檔 4 面健檢分數`}
          </div>
        </div>
        <span className="text-teal-300">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="mt-3 grid grid-cols-2 gap-1.5">
          {tickers.slice(0, 10).map((tk, i) => {
            const r = results[i];
            const hc = r.data;
            const score = hc?.health.composite;
            const verdict = score == null ? "—" : score >= 70 ? "健康" : score >= 50 ? "亞健康" : "韭菜病";
            const c = score == null ? "#94a3b8" : score >= 70 ? "#5eead4" : score >= 50 ? "#fbbf24" : "#f43f5e";
            return (
              <button
                key={tk}
                onClick={() => router.push(`/ticker/${tk}`)}
                disabled={!hc}
                className="rounded p-2.5 text-center active:scale-[0.97]"
                style={{ background: "#0f1218", border: `1px solid ${c}40`, borderLeft: `3px solid ${c}` }}
              >
                <div className="tabular-nums text-xs font-bold text-st-fg">{tk}</div>
                {score == null ? (
                  <div className="shimmer h-5 w-12 rounded mx-auto mt-1" />
                ) : (
                  <>
                    <div className="tabular-nums text-lg font-extrabold mt-1" style={{ color: c, lineHeight: 1 }}>
                      {score}
                    </div>
                    <div className="text-[9px] font-bold" style={{ color: c }}>{verdict}</div>
                  </>
                )}
              </button>
            );
          })}
        </div>
      )}
    </motion.div>
  );
}

function useQueries(tickers: string[], enabled: boolean) {
  // Parallel useQuery hooks(simple version — uses TanStack pattern)
  const arr: ReturnType<typeof useQuery<import("@/lib/api").HealthCheck>>[] = [];
  for (const tk of tickers) {
    // eslint-disable-next-line react-hooks/rules-of-hooks
    arr.push(useQuery({
      queryKey: ["health-check", tk],
      queryFn: () => api.getHealthCheck(tk),
      enabled: enabled && !!tk,
      staleTime: 5 * 60_000,
    }));
  }
  return arr;
}

function MarketDashboardCard() {
  const { data, isLoading } = useQuery({
    queryKey: ["market-dashboard"],
    queryFn: () => api.getMarketDashboard(),
    staleTime: 5 * 60_000,
  });

  if (isLoading) {
    return (
      <div className="shimmer rounded-st" style={{ height: 600 }} />
    );
  }
  if (!data) return null;

  return (
    <div className="space-y-3">
      {/* ════════ Group 1: 即時市場 ════════ */}
      {data.taiex && <TaiexHeroCard taiex={data.taiex} />}

      {/* 市場情緒 + 法人(同類:單一數值判讀,並排省空間) */}
      <div className="grid grid-cols-1 gap-3">
        {/* 市場情緒 — VIX + 估值 */}
        <SectionCard emoji="🧠" title="市場情緒" sub="規則化判讀,非預測">
          <div className="grid grid-cols-2 gap-2">
            {data.vix && <VixCard vix={data.vix} />}
            {data.taiex?.ma200_dist_pct != null && <ValuationCard dist={data.taiex.ma200_dist_pct} />}
          </div>
        </SectionCard>

        {/* 三大法人 */}
        {data.institutional && (
          <SectionCard emoji="📊" title="三大法人" sub="全市場 20 日累計(張)">
            <InstitutionalCard inst={data.institutional} />
          </SectionCard>
        )}
      </div>

      {/* 國際市場 + 商品(同類:tile 展示)*/}
      <SectionCard emoji="🌍" title="國際市場連動" sub="台股早盤常跟美股夜盤連動">
        <div className="grid grid-cols-3 gap-1.5 mb-3">
          {[
            { key: "sp500", emoji: "🇺🇸" },
            { key: "nasdaq", emoji: "💻" },
            { key: "sox", emoji: "🔌" },
            { key: "dxy", emoji: "💵" },
            { key: "oil", emoji: "🛢️" },
            { key: "gold", emoji: "🪙" },
            { key: "usdtwd", emoji: "🇹🇼" },
            { key: "btc", emoji: "₿" },
            { key: "eth", emoji: "Ξ" },
          ].map(({ key, emoji }) => {
            const idx = data[key as "sp500"];
            if (!idx) return <SkelTile key={key} />;
            return <IntlTile key={key} idx={idx} emoji={emoji} />;
          })}
        </div>
        {/* 商品 + 日股(視覺整合到國際連動下方)*/}
        <div className="text-[10px] text-st-muted font-bold tracking-wider mb-1.5 mt-3">
          🪙 商品 + 日股
        </div>
        <div className="grid grid-cols-3 gap-1.5">
          {data.silver && <IntlTile idx={data.silver} emoji="🥈" />}
          {data.nikkei && <IntlTile idx={data.nikkei} emoji="🇯🇵" />}
          {data.dxj && <IntlTile idx={data.dxj} emoji="💴" />}
        </div>
        <div className="text-[11px] text-st-muted text-center pt-3 mt-3 border-t border-st-border">
          {data.international_note}
        </div>
      </SectionCard>

      {/* ════════ Group 2: 🤖 智能整理(同類聚合)════════ */}
      <GroupHeader emoji="🤖" label="智能整理" sub="用你選的語氣 × 時間框架" />
      <AiMarketInsightCard dashboard={data} />
      <AiNewsSentimentCard />

      {/* ════════ Group 3: 📰 新聞(同類聚合)════════ */}
      <GroupHeader emoji="📰" label="新聞" sub="Google News · 30 分快取" />
      <WorldNewsCard />
      <MarketNewsCard />

      {/* Disclaimer */}
      <div className="text-[10px] text-st-muted leading-relaxed px-1 pt-4">
        ⚠️ 純客觀數據展示。投資決策請自行判斷或諮詢專業顧問,盈虧自負。
      </div>
    </div>
  );
}

/** 今日摘要 — 晨報頂部一行(大盤 / 觀察 / 策略)*/
function TodayDigest({
  watchlistTotal, watchlistUps, watchlistDowns, strategyHits,
}: {
  watchlistTotal: number; watchlistUps: number; watchlistDowns: number;
  strategyHits: number;
}) {
  const { data: dash } = useQuery({
    queryKey: ["market-dashboard"],
    queryFn: () => api.getMarketDashboard(),
    staleTime: 5 * 60_000,
  });
  const taiexChg = dash?.taiex?.change_pct;
  return (
    <div
      className="mt-3 grid grid-cols-3 gap-1.5 pt-3 border-t border-white/10"
    >
      <Inline
        label="加權指數"
        value={
          taiexChg != null
            ? <span style={{ color: chgColor(taiexChg) }}>
                {chgArrow(taiexChg)} {Math.abs(taiexChg).toFixed(2)}%
              </span>
            : "—"
        }
      />
      <Inline
        label={`觀察 ${watchlistTotal} 檔`}
        value={
          watchlistTotal > 0
            ? <span>
                <span className="text-rose-400">{watchlistUps}</span>
                {" / "}
                <span className="text-emerald-400">{watchlistDowns}</span>
              </span>
            : "—"
        }
      />
      <Inline
        label="策略命中"
        value={
          <span className="text-teal-300">{strategyHits} 檔</span>
        }
      />
    </div>
  );
}

/** 大盤 mini snapshot — 搜尋頁頂端 + 全 tab 共用 */
function MarketMiniSnapshot() {
  const { data } = useQuery({
    queryKey: ["market-dashboard"],
    queryFn: () => api.getMarketDashboard(),
    staleTime: 5 * 60_000,
  });
  if (!data) return null;
  return (
    <div
      className="rounded-st px-3 py-2.5 flex items-center gap-2 overflow-x-auto"
      style={{
        background: [
          "radial-gradient(circle at 12% 18%, rgba(255,255,255,0.05), transparent 35%)",
          "linear-gradient(180deg, #1c2028 0%, #16181d 50%, #11141a 100%)",
        ].join(", "),
        border: "1px solid #3a4150",
        boxShadow: "inset 0 1px 0 rgba(255,255,255,0.08), inset 0 -1px 0 rgba(0,0,0,0.4)",
      }}
    >
      {[
        { label: "TAIEX", val: data.taiex?.price, pct: data.taiex?.change_pct },
        { label: "VIX", val: data.vix?.price, pct: data.vix?.change_pct },
        { label: "SP500", val: data.sp500?.price, pct: data.sp500?.change_pct },
        { label: "NASDAQ", val: data.nasdaq?.price, pct: data.nasdaq?.change_pct },
        { label: "BTC", val: data.btc?.price, pct: data.btc?.change_pct },
        { label: "USDTWD", val: data.usdtwd?.price, pct: data.usdtwd?.change_pct },
      ].map((it) => {
        if (it.val == null || it.pct == null) return null;
        const c = chgColor(it.pct);
        return (
          <div key={it.label} className="flex-shrink-0 px-2.5 py-1.5 rounded border" style={{
            background: "#0f1218",
            borderColor: c + "40",
            borderLeft: `2px solid ${c}`,
          }}>
            <div className="text-[9px] text-st-muted font-bold">{it.label}</div>
            <div className="text-[10px] font-bold tabular-nums" style={{ color: c }}>
              {chgArrow(it.pct)} {Math.abs(it.pct).toFixed(2)}%
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** 策略今日摘要 banner */
function StrategySummaryBanner({ data }: { data: import("@/lib/api").StrategyResults }) {
  const STRATEGY_META_LOCAL: Record<string, { icon: string; name: string }> = {
    rev_yoy: { icon: "💰", name: "月營收高成長" },
    low_retail: { icon: "👻", name: "散戶極端低位" },
    quiet_limitdown: { icon: "📉", name: "量縮跌停反彈" },
    quiet_limitup: { icon: "📈", name: "量縮漲停" },
    ab_consensus: { icon: "🤝", name: "AB 雙重共識" },
    govbank_reverse: { icon: "🏦", name: "政府行庫反向" },
  };
  // 找出命中最多的 3 個策略
  const top3 = Object.entries(data.strategies)
    .filter(([, h]) => h.length > 0)
    .sort((a, b) => b[1].length - a[1].length)
    .slice(0, 3);
  const totalHits = Object.values(data.strategies).reduce((s, a) => s + a.length, 0);
  return (
    <div
      className="rounded-st p-4"
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
      <div className="flex items-center gap-2 mb-2">
        <span className="text-xl">🎯</span>
        <div>
          <div className="text-xs text-teal-300 font-bold tracking-wider">今日重點</div>
          <div className="font-extrabold text-st-fg text-sm">
            {totalHits} 檔命中 7 種策略
          </div>
        </div>
      </div>
      <div className="grid grid-cols-3 gap-1.5 mt-2">
        {top3.length > 0 ? top3.map(([key, hits]) => {
          const meta = STRATEGY_META_LOCAL[key] ?? { icon: "📊", name: key };
          return (
            <div key={key} className="rounded p-2 text-center" style={{ background: "#0f1218", border: "1px solid #2f343d" }}>
              <div className="text-lg">{meta.icon}</div>
              <div className="text-[10px] text-st-soft truncate mt-1">{meta.name}</div>
              <div className="tabular-nums text-base font-extrabold text-teal-300 mt-0.5">
                {hits.length}
              </div>
              <div className="text-[8px] text-st-muted">檔</div>
            </div>
          );
        }) : (
          <div className="col-span-3 text-xs text-st-muted text-center py-2">
            今日所有策略都沒命中(冷盤 / 沒 setup)
          </div>
        )}
      </div>
    </div>
  );
}

/** 大區塊群組標題(置左,有底色拉長條)*/
function GroupHeader({ emoji, label, sub }: { emoji: string; label: string; sub?: string }) {
  return (
    <div className="flex items-center gap-2.5 pt-4 pb-1 mt-2">
      <span className="text-2xl">{emoji}</span>
      <div className="flex-1">
        <h3 className="text-base font-extrabold text-st-fg tracking-wide">{label}</h3>
        {sub && <p className="text-[10px] text-st-muted">{sub}</p>}
      </div>
      <div
        className="flex-1 h-px ml-2"
        style={{ background: "linear-gradient(90deg, #3a4150, transparent)" }}
      />
    </div>
  );
}

function SectionCard({
  emoji, title, sub, badge, children,
}: { emoji: string; title: string; sub?: string; badge?: string; children: React.ReactNode }) {
  return (
    <div
      className="rounded-st p-4"
      style={{
        background: [
          "radial-gradient(circle at 12% 18%, rgba(255,255,255,0.05), transparent 35%)",
          "linear-gradient(180deg, #1c2028 0%, #16181d 50%, #11141a 100%)",
        ].join(", "),
        border: "1px solid #3a4150",
        boxShadow: "inset 0 1px 0 rgba(255,255,255,0.08), inset 0 -1px 0 rgba(0,0,0,0.4)",
      }}
    >
      <div className="flex items-center gap-2 mb-3">
        <span className="text-xl">{emoji}</span>
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <h3 className="font-extrabold text-st-fg text-sm">{title}</h3>
            {badge && (
              <span className="text-[9px] font-bold tracking-widest text-amber-300 bg-amber-500/15 border border-amber-500/40 px-1.5 py-0.5 rounded">
                {badge}
              </span>
            )}
          </div>
          {sub && <div className="text-[10px] text-st-muted mt-0.5">{sub}</div>}
        </div>
      </div>
      {children}
    </div>
  );
}

function TaiexHeroCard({ taiex }: { taiex: import("@/lib/api").TaiexFull }) {
  const c = chgColor(taiex.change_pct);
  const tempColor = taiex.ma200_dist_pct == null ? "#94a3b8" :
    taiex.ma200_dist_pct > 30 ? "#f43f5e" :
    taiex.ma200_dist_pct > 15 ? "#fbbf24" :
    taiex.ma200_dist_pct > -5 ? "#5eead4" : "#60a5fa";
  return (
    <div
      className="rounded-st p-4 hero-halo"
      style={{
        background: "linear-gradient(135deg, #0f766e 0%, #0a1a1f 50%, #16181d 100%)",
        border: "1px solid #2f343d",
        boxShadow: "inset 0 1px 0 rgba(255,255,255,0.1), 0 0 24px rgba(20,184,166,0.12)",
      }}
    >
      <div className="text-[10px] tracking-widest text-teal-300 font-bold">
        🇹🇼 加權指數 ^TWII · {taiex.asof}
      </div>
      <div className="flex items-end justify-between mt-1 gap-3">
        <div>
          <div className="text-st-fg tabular-nums leading-none" style={{ fontSize: "2.4rem", fontWeight: 800 }}>
            {taiex.price.toLocaleString("zh-TW", { maximumFractionDigits: 0 })}
          </div>
          <div className="tabular-nums font-bold mt-2" style={{ color: c }}>
            {chgArrow(taiex.change_pct)} {Math.abs(taiex.price - taiex.prev_close).toFixed(2)} ({chgArrow(taiex.change_pct) === "▼" ? "" : "+"}{taiex.change_pct.toFixed(2)}%)
          </div>
        </div>
        {taiex.sparkline_30d.length > 0 && (
          <div className="flex-1 max-w-[140px]">
            <MiniSpark data={taiex.sparkline_30d} />
          </div>
        )}
      </div>
      <div className="grid grid-cols-3 gap-2 mt-3 pt-3 border-t border-st-border/50">
        <Inline label="大盤溫度" value={
          <span style={{ color: tempColor }}>
            {taiex.temperature_emoji} {taiex.temperature}
          </span>
        } sub={taiex.ma200_dist_pct != null ? `距 MA200 ${taiex.ma200_dist_pct >= 0 ? "+" : ""}${taiex.ma200_dist_pct.toFixed(1)}%` : ""} />
        <Inline label="20 日" value={taiex.ret_20d != null ? `${taiex.ret_20d >= 0 ? "+" : ""}${taiex.ret_20d.toFixed(2)}%` : "—"} tint={taiex.ret_20d != null && taiex.ret_20d >= 0 ? "up" : "down"} />
        <Inline label="60 日" value={taiex.ret_60d != null ? `${taiex.ret_60d >= 0 ? "+" : ""}${taiex.ret_60d.toFixed(2)}%` : "—"} tint={taiex.ret_60d != null && taiex.ret_60d >= 0 ? "up" : "down"} />
      </div>
    </div>
  );
}

function VixCard({ vix }: { vix: import("@/lib/api").MarketIndex }) {
  const v = vix.price;
  const verdict = v > 30 ? { lbl: "🚨 高恐慌", c: "#f43f5e", desc: "市場恐慌中,留意黑天鵝" } :
                  v > 25 ? { lbl: "😟 警戒", c: "#fbbf24", desc: "波動升高,謹慎為上" } :
                  v < 18 ? { lbl: "😎 過度樂觀", c: "#fbbf24", desc: "市場無感,小心反轉" } :
                  { lbl: "😐 平靜", c: "#5eead4", desc: "市場情緒正常區間" };
  return (
    <div className="rounded p-2.5" style={{ background: "#0f1218", border: `1px solid ${verdict.c}40`, borderLeft: `3px solid ${verdict.c}` }}>
      <div className="text-[10px] text-st-muted">📉 VIX 恐慌指數</div>
      <div className="tabular-nums font-extrabold text-st-fg mt-1" style={{ fontSize: "1.5rem" }}>
        {v.toFixed(2)}
      </div>
      <div className="text-xs font-bold mt-1" style={{ color: verdict.c }}>{verdict.lbl}</div>
      <div className="text-[10px] text-st-muted mt-0.5">{verdict.desc}</div>
    </div>
  );
}

function ValuationCard({ dist }: { dist: number }) {
  const verdict = dist > 30 ? { lbl: "🚨 過熱", c: "#f43f5e", desc: "過去 10 年僅 5% 時間在此區間" } :
                  dist > 15 ? { lbl: "🌡️ 偏熱", c: "#fbbf24", desc: "估值偏高,留意均值回歸" } :
                  dist > -5 ? { lbl: "✅ 合理", c: "#5eead4", desc: "估值在中性區" } :
                  { lbl: "❄️ 偏冷", c: "#60a5fa", desc: "估值低位,可分批進場" };
  return (
    <div className="rounded p-2.5" style={{ background: "#0f1218", border: `1px solid ${verdict.c}40`, borderLeft: `3px solid ${verdict.c}` }}>
      <div className="text-[10px] text-st-muted">📊 大盤估值</div>
      <div className="tabular-nums font-extrabold text-st-fg mt-1" style={{ fontSize: "1.5rem", color: verdict.c }}>
        {dist >= 0 ? "+" : ""}{dist.toFixed(1)}%
      </div>
      <div className="text-xs font-bold mt-1" style={{ color: verdict.c }}>{verdict.lbl}</div>
      <div className="text-[10px] text-st-muted mt-0.5">{verdict.desc}</div>
    </div>
  );
}

function InstitutionalCard({ inst }: { inst: import("@/lib/api").InstitutionalSummary }) {
  const rows = [
    { label: "外資 20d", v: inst.foreign_20d, color: "#5eead4" },
    { label: "投信 20d", v: inst.invtrust_20d, color: "#fbbf24" },
    { label: "自營 20d", v: inst.dealer_20d, color: "#a78bfa" },
  ];
  return (
    <div>
      <div className="grid grid-cols-3 gap-1.5">
        {rows.map(r => (
          <div key={r.label} className="rounded p-2 text-center" style={{ background: "#0f1218", border: "1px solid #2f343d", borderLeft: `3px solid ${r.color}` }}>
            <div className="text-[10px] text-st-muted">{r.label}</div>
            <div className="tabular-nums font-bold mt-0.5" style={{
              fontSize: "0.95rem",
              color: r.v > 0 ? "#ef4444" : r.v < 0 ? "#10b981" : "#fff",
            }}>
              {r.v > 0 ? "▲" : r.v < 0 ? "▼" : "—"} {Math.abs(r.v).toLocaleString("zh-TW")}
            </div>
            <div className="text-[8px] text-st-muted">張</div>
          </div>
        ))}
      </div>
      <div className="text-[11px] text-st-soft text-center mt-3 pt-2 border-t border-st-border">
        {inst.note}
      </div>
    </div>
  );
}

function IntlTile({ idx, emoji }: { idx: import("@/lib/api").MarketIndex; emoji: string }) {
  const c = chgColor(idx.change_pct);
  return (
    <div
      className="rounded p-2 relative overflow-hidden"
      style={{
        // 金屬感:左上反光 + 漸層底
        background: [
          "radial-gradient(circle at 15% 18%, rgba(255,255,255,0.08), transparent 35%)",
          "linear-gradient(180deg, #1a1e25 0%, #14171c 50%, #0e1116 100%)",
        ].join(", "),
        border: "1px solid #2f343d",
        borderLeft: `2px solid ${c}`,
        boxShadow: "inset 0 1px 0 rgba(255,255,255,0.06), inset 0 -1px 0 rgba(0,0,0,0.4)",
      }}
    >
      <div className="flex items-center gap-1 text-[9px] text-st-muted truncate">
        <span>{emoji}</span>
        <span className="font-bold truncate">{idx.name}</span>
      </div>
      <div className="tabular-nums font-extrabold text-st-fg mt-0.5" style={{ fontSize: "0.85rem" }}>
        {idx.price >= 10000 ? idx.price.toLocaleString("zh-TW", { maximumFractionDigits: 0 }) :
         idx.price >= 100 ? idx.price.toFixed(2) :
         idx.price.toFixed(3)}
      </div>
      <div className="tabular-nums text-[10px] font-bold mt-0.5" style={{ color: c }}>
        {chgArrow(idx.change_pct)} {Math.abs(idx.change_pct).toFixed(2)}%
      </div>
    </div>
  );
}

function SkelTile() {
  return (
    <div className="rounded p-2 shimmer" style={{ height: 56, border: "1px solid #2f343d" }} />
  );
}

/* ════════ AI 智能國際情勢 ════════ */
function AiMarketInsightCard({ dashboard }: { dashboard: import("@/lib/api").MarketDashboard }) {
  const [aiText, setAiText] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [style, setStyle] = useState<"neutral" | "pro" | "casual">("neutral");
  const [tf, setTf] = useState<"short" | "mid" | "long">("mid");

  const run = async () => {
    setLoading(true);
    try {
      const intlObj: Record<string, { price: number; change_pct: number }> = {};
      (["sp500", "nasdaq", "sox", "dxy", "oil", "gold", "btc"] as const).forEach(k => {
        const x = dashboard[k];
        if (x) intlObj[k] = { price: x.price, change_pct: x.change_pct };
      });
      const res = await api.aiMarketInsight({
        taiex_price: dashboard.taiex?.price ?? 0,
        taiex_change_pct: dashboard.taiex?.change_pct ?? 0,
        taiex_ma200_dist: dashboard.taiex?.ma200_dist_pct,
        taiex_temperature: dashboard.taiex?.temperature ?? "",
        vix: dashboard.vix?.price,
        intl: intlObj,
        institutional: dashboard.institutional ?? {},
        style, timeframe: tf,
      });
      setAiText(res.text);
    } catch (e) { setAiText(`⚠️ ${(e as Error).message}`); }
    finally { setLoading(false); }
  };

  return (
    <SectionCard emoji="🤖" title="智能國際整理" badge="PRO" sub="整理國際 → 對台股影響">
      <div className="grid grid-cols-2 gap-2 mb-3">
        <StyleSelector value={style} onChange={setStyle} />
        <TimeframeSelector value={tf} onChange={setTf} />
      </div>
      <button
        onClick={run}
        disabled={loading}
        className="btn-smart w-full"
      >
        ✨ <span className="relative z-10">{loading ? "智能整理中⋯" : aiText ? "🔄 重新整理" : "查看整理報告"}</span>
      </button>
      {aiText && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="mt-3 rounded p-3 text-xs text-st-soft whitespace-pre-wrap leading-relaxed"
          style={{ background: "#0f1218", border: "1px solid #2f343d", borderLeft: "3px solid #a78bfa" }}
        >
          {aiText}
        </motion.div>
      )}
    </SectionCard>
  );
}

/* ════════ 世界大事 ════════ */
function WorldNewsCard() {
  const { data, isLoading } = useQuery({
    queryKey: ["news-world"],
    queryFn: () => api.getWorldNews(),
    staleTime: 30 * 60_000,
  });
  return (
    <SectionCard emoji="🗞️" title="世界大事" sub="篩選對台股有影響的國際新聞">
      {isLoading && <div className="shimmer rounded h-24" />}
      {data?.map((cat) => (
        <div key={cat.key} className="mb-3 last:mb-0">
          <div className="text-xs font-bold text-st-fg mb-1.5">{cat.label}</div>
          <div className="space-y-1">
            {cat.items.map((it, i) => (
              <a
                key={i}
                href={it.link}
                target="_blank"
                rel="noreferrer"
                className="block rounded p-2 hover:bg-white/[0.03] text-xs"
                style={{ background: "#0f1218", border: "1px solid #2f343d" }}
              >
                <div className="text-st-soft leading-snug">{it.title}</div>
                <div className="text-[10px] text-st-muted mt-1">{it.source}</div>
              </a>
            ))}
          </div>
        </div>
      ))}
    </SectionCard>
  );
}

/* ════════ AI 新聞情緒 ════════ */
function AiNewsSentimentCard() {
  const { data: news } = useQuery({
    queryKey: ["news-market"],
    queryFn: () => api.getMarketNews(15),
    staleTime: 30 * 60_000,
  });
  const [aiText, setAiText] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [style, setStyle] = useState<"neutral" | "pro" | "casual">("neutral");
  const [tf, setTf] = useState<"short" | "mid" | "long">("short");

  const run = async () => {
    if (!news?.length) return;
    setLoading(true);
    try {
      const res = await api.aiNewsSentiment({
        news_titles: news.map(n => n.title),
        style, timeframe: tf,
      });
      setAiText(res.text);
    } catch (e) { setAiText(`⚠️ ${(e as Error).message}`); }
    finally { setLoading(false); }
  };

  return (
    <SectionCard emoji="🤖" title="智能新聞整理" badge="PRO" sub="自動整理今日大盤新聞情緒">
      <div className="grid grid-cols-2 gap-2 mb-3">
        <StyleSelector value={style} onChange={setStyle} />
        <TimeframeSelector value={tf} onChange={setTf} />
      </div>
      <button
        onClick={run}
        disabled={loading || !news?.length}
        className="btn-smart w-full"
      >
        ✨ <span className="relative z-10">{loading ? "智能整理中⋯" : aiText ? "🔄 重新整理" : `查看新聞情緒(${news?.length ?? 0} 條)`}</span>
      </button>
      {aiText && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}
          className="mt-3 rounded p-3 text-xs text-st-soft whitespace-pre-wrap leading-relaxed"
          style={{ background: "#0f1218", border: "1px solid #2f343d", borderLeft: "3px solid #a78bfa" }}>
          {aiText}
        </motion.div>
      )}
    </SectionCard>
  );
}

/* ════════ 大盤新聞 Google News ════════ */
function MarketNewsCard() {
  const { data, isLoading } = useQuery({
    queryKey: ["news-market-10"],
    queryFn: () => api.getMarketNews(10),
    staleTime: 30 * 60_000,
  });
  return (
    <SectionCard emoji="📰" title="大盤新聞" sub="Google News · 30 分快取">
      {isLoading && <div className="shimmer rounded h-32" />}
      {data && data.length > 0 && (
        <div className="space-y-1">
          {data.map((it, i) => (
            <a key={i} href={it.link} target="_blank" rel="noreferrer"
                className="block rounded p-2 hover:bg-white/[0.03] text-xs"
                style={{ background: "#0f1218", border: "1px solid #2f343d" }}>
              <div className="text-st-soft leading-snug">{it.title}</div>
              <div className="text-[10px] text-st-muted mt-1">📰 {it.source}</div>
            </a>
          ))}
        </div>
      )}
    </SectionCard>
  );
}

/* ════════ Selectors ════════ */
function StyleSelector({ value, onChange }: { value: string; onChange: (v: "neutral" | "pro" | "casual") => void }) {
  return (
    <div>
      <div className="text-[10px] text-st-muted mb-1">語氣</div>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value as "neutral" | "pro" | "casual")}
        className="w-full text-xs rounded-st px-2 py-1.5"
        style={{ background: "#16181d", border: "1px solid #2f343d", color: "#fff" }}
      >
        <option value="neutral">中立白話</option>
        <option value="pro">嚴肅專業</option>
        <option value="casual">輕鬆口語</option>
      </select>
    </div>
  );
}

function TimeframeSelector({ value, onChange }: { value: string; onChange: (v: "short" | "mid" | "long") => void }) {
  return (
    <div>
      <div className="text-[10px] text-st-muted mb-1">時間框架</div>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value as "short" | "mid" | "long")}
        className="w-full text-xs rounded-st px-2 py-1.5"
        style={{ background: "#16181d", border: "1px solid #2f343d", color: "#fff" }}
      >
        <option value="short">短期 (1-4 週)</option>
        <option value="mid">中期 (1-3 月)</option>
        <option value="long">長期 (6-12 月)</option>
      </select>
    </div>
  );
}

function Inline({ label, value, sub, tint }: {
  label: string; value: React.ReactNode; sub?: string; tint?: "up" | "down";
}) {
  const c = tint === "up" ? "#ef4444" : tint === "down" ? "#10b981" : "#fff";
  return (
    <div className="text-center">
      <div className="text-[10px] text-st-muted">{label}</div>
      <div className="tabular-nums font-bold mt-0.5" style={{ color: c, fontSize: "0.85rem" }}>
        {value}
      </div>
      {sub && <div className="text-[9px] text-st-muted mt-0.5 tabular-nums">{sub}</div>}
    </div>
  );
}

function MiniSpark({ data }: { data: number[] }) {
  if (data.length < 2) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const W = 140, H = 50;
  const points = data.map((v, i) => `${(i / (data.length - 1)) * W},${H - ((v - min) / range) * H}`).join(" ");
  const up = data[data.length - 1] >= data[0];
  const stroke = up ? "#ef4444" : "#10b981";
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-12">
      <polyline points={points} stroke={stroke} strokeWidth={1.5} fill="none" strokeLinejoin="round" />
    </svg>
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

      {/* 大盤 mini snapshot — 不在搜尋時顯示 */}
      {!isSearching && <MarketMiniSnapshot />}

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
  const { data, isLoading } = useQuery({
    queryKey: ["strategy-results"],
    queryFn: () => api.getStrategyResults(),
    staleTime: 60_000,
  });

  return (
    <div className="space-y-3 pb-4">
      <div>
        <h2 className="text-2xl font-extrabold text-st-fg">📡 策略掃描</h2>
        <p className="text-st-muted text-xs mt-1">
          {data
            ? `${data.fresh ? "✓ 資料新鮮" : "⚠️ 資料過期"} · 更新於 ${new Date(data.updated_at).toLocaleString("zh-TW", { hour: "2-digit", minute: "2-digit", month: "2-digit", day: "2-digit" })}`
            : "讀取中⋯"}
          {data && ` · 共 ${Object.values(data.strategies).reduce((s, a) => s + a.length, 0)} 檔命中`}
        </p>
      </div>

      {/* 📊 今日策略摘要 banner */}
      {data && <StrategySummaryBanner data={data} />}

      {/* 正向 / 反向 大分類 */}
      {data && Object.entries(STRATEGY_DIRECTIONS).map(([dirKey, dir]) => {
        const dirHits = dir.groups.reduce((s, g) =>
          s + g.strategies.reduce((ss, k) => ss + (data.strategies[k]?.length ?? 0), 0), 0);
        return (
          <div key={dirKey} className="space-y-2">
            {/* 大分類 header(正向 / 反向)*/}
            <div
              className="rounded-st px-3 py-2.5 flex items-center gap-2 mt-3"
              style={{
                background: `linear-gradient(90deg, ${dir.tint}15, transparent)`,
                border: `1px solid ${dir.tint}40`,
                borderLeft: `4px solid ${dir.tint}`,
              }}
            >
              <span className="text-xl">{dir.emoji}</span>
              <div className="flex-1">
                <div className="font-extrabold text-st-fg text-sm" style={{ color: dir.tint }}>
                  {dir.label}
                </div>
                <div className="text-[10px] text-st-muted">{dir.desc}</div>
              </div>
              <span className="text-xs font-bold tabular-nums" style={{ color: dir.tint }}>
                {dirHits} 檔
              </span>
            </div>

            {/* 子分類(基本/籌碼/量價)*/}
            {dir.groups.map((grp, gi) => {
              const inGrp = grp.strategies.filter(k => data.strategies[k]);
              if (inGrp.length === 0) return null;
              return (
                <div key={`${dirKey}-${gi}`} className="space-y-1.5">
                  <div className="flex items-center gap-1.5 px-1 mt-2">
                    <span className="text-sm">{grp.emoji}</span>
                    <h4 className="text-xs font-bold text-st-soft">{grp.label}</h4>
                    <span className="text-[10px] text-st-muted">· {grp.desc}</span>
                  </div>
                  {inGrp.map(key => (
                    <StrategyCollapsible key={key} strategyKey={key} hits={data.strategies[key]} />
                  ))}
                </div>
              );
            })}
          </div>
        );
      })}
      {isLoading && (
        <>
          <div className="shimmer h-16 rounded-st" />
          <div className="shimmer h-16 rounded-st" />
          <div className="shimmer h-16 rounded-st" />
        </>
      )}
    </div>
  );
}

type StratGroup = { emoji: string; label: string; desc: string; tint: string; strategies: string[] };

/** 第一層:正向 vs 反向;第二層:基本/籌碼/量價 */
const STRATEGY_DIRECTIONS: Record<string, {
  emoji: string;
  label: string;
  desc: string;
  tint: string;
  groups: StratGroup[];
}> = {
  long: {
    emoji: "🟢",
    label: "正向訊號",
    desc: "可考慮買進(memory 真 alpha 驗證)",
    tint: "#5eead4",
    groups: [
      { emoji: "💰", label: "基本面", desc: "看公司營運實力", tint: "#5eead4", strategies: ["rev_yoy"] },
      { emoji: "📊", label: "籌碼面", desc: "看誰在買", tint: "#5eead4", strategies: ["low_retail", "ab_consensus"] },
      { emoji: "📈", label: "量價", desc: "看 K 線與成交量", tint: "#5eead4", strategies: ["limitdown_bounce", "limitup_quiet"] },
    ],
  },
  short: {
    emoji: "🔴",
    label: "反向訊號",
    desc: "避開或減碼(memory 真 alpha 反向)",
    tint: "#f43f5e",
    groups: [
      { emoji: "📊", label: "籌碼異常", desc: "韭菜聚集 / 政府護盤後弱", tint: "#f43f5e", strategies: ["high_retail", "govbank_reverse"] },
    ],
  },
};

const STRATEGY_META: Record<string, { icon: string; name: string; alpha: string; frame: string; color: string }> = {
  rev_yoy: { icon: "💰", name: "月營收 YoY 高成長", alpha: "+5.10%", frame: "60d", color: "#5eead4" },
  low_retail: { icon: "👻", name: "散戶比例極端低位", alpha: "+11.3pp", frame: "20d", color: "#5eead4" },
  high_retail: { icon: "⚠️", name: "散戶比例極端高位", alpha: "Avoid", frame: "—", color: "#fbbf24" },
  limitdown_bounce: { icon: "📉", name: "量縮跌停反彈", alpha: "+4.27%", frame: "5d", color: "#5eead4" },
  limitup_quiet: { icon: "📈", name: "量縮漲停", alpha: "+4.83%", frame: "20d", color: "#5eead4" },
  ab_consensus: { icon: "🤝", name: "AB 雙重共識", alpha: "+8.78%", frame: "60d", color: "#5eead4" },
  govbank_reverse: { icon: "🏦", name: "政府行庫反向", alpha: "+1.62pp", frame: "60d", color: "#5eead4" },
};

function StrategyCollapsible({
  strategyKey, hits,
}: { strategyKey: string; hits: import("@/lib/api").StrategyHit[] }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const meta = STRATEGY_META[strategyKey] ?? { icon: "📊", name: strategyKey, alpha: "—", frame: "—", color: "#94a3b8" };
  const hasHits = hits.length > 0;

  // 展開時 batch fetch quotes 顯示漲跌
  const tks = hits.map(h => h.ticker);
  const { data: quotes } = useQuery({
    queryKey: ["quotes-batch", [...tks].sort()],
    queryFn: () => tks.length ? api.getQuotesBatch(tks) : Promise.resolve([]),
    enabled: open && tks.length > 0,
    staleTime: 60_000,
  });
  const qMap = new Map((quotes ?? []).map(q => [q.ticker, q]));

  return (
    <div
      className="rounded-st overflow-hidden"
      style={{
        background: [
          "radial-gradient(circle at 12% 18%, rgba(255,255,255,0.05), transparent 35%)",
          "linear-gradient(180deg, #1c2028 0%, #16181d 50%, #11141a 100%)",
        ].join(", "),
        border: "1px solid #3a4150",
        borderLeft: `3px solid ${hasHits ? meta.color : "#475569"}`,
        boxShadow: "inset 0 1px 0 rgba(255,255,255,0.08), inset 0 -1px 0 rgba(0,0,0,0.4)",
      }}
    >
      {/* Header — 一行 pill 樣式 */}
      <button
        onClick={() => hasHits && setOpen(!open)}
        disabled={!hasHits}
        className="w-full px-3 py-3 flex items-center gap-3 active:scale-[0.99] transition-transform disabled:opacity-70"
      >
        <span className="text-2xl flex-shrink-0">{meta.icon}</span>
        <div className="flex-1 min-w-0 text-left">
          <div className="font-extrabold text-st-fg text-sm truncate">{meta.name}</div>
          <div className="text-[10px] tabular-nums" style={{ color: meta.color }}>
            alpha {meta.alpha} · {meta.frame}
          </div>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <span
            className="text-[10px] font-bold tabular-nums px-2 py-1 rounded"
            style={{
              background: hasHits ? `${meta.color}20` : "#1e293b",
              color: hasHits ? meta.color : "#64748b",
              border: `1px solid ${hasHits ? meta.color + "60" : "#334155"}`,
            }}
          >
            {hasHits ? `${hits.length} 檔` : "0"}
          </span>
          {hasHits && (
            <motion.span
              animate={{ rotate: open ? 180 : 0 }}
              transition={{ duration: 0.2 }}
              className="text-st-muted text-sm"
            >
              ▼
            </motion.span>
          )}
        </div>
      </button>

      {/* Body — 展開時的命中列 */}
      <AnimatePresence initial={false}>
        {open && hasHits && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.22, ease: "easeOut" }}
            className="overflow-hidden"
          >
            <div className="px-3 pb-3 pt-1 space-y-1 border-t border-st-border">
              {hits.map((h) => {
                const q = qMap.get(h.ticker);
                const c = q ? chgColor(q.change_pct) : "#94a3b8";
                return (
                  <button
                    key={h.ticker}
                    onClick={() => router.push(`/ticker/${h.ticker}`)}
                    className="w-full text-left rounded flex items-center justify-between gap-2 px-2.5 py-2 hover:bg-white/[0.03] active:scale-[0.98]"
                    style={{ background: "#0f1218", border: "1px solid #2a3340" }}
                  >
                    <div className="flex items-center gap-2 min-w-0 flex-1">
                      <span className="tabular-nums text-xs font-bold flex-shrink-0" style={{ color: meta.color }}>{h.ticker}</span>
                      <span className="text-xs text-st-soft truncate">{h.name}</span>
                    </div>
                    <div className="flex items-center gap-2 flex-shrink-0 text-right">
                      {/* 當日漲跌 */}
                      {q && (
                        <div className="tabular-nums text-[10px] font-bold" style={{ color: c }}>
                          {chgArrow(q.change_pct)} {Math.abs(q.change_pct).toFixed(2)}%
                        </div>
                      )}
                      {/* 策略指標 */}
                      {h.metric != null && (
                        <div
                          className="text-[10px] tabular-nums font-bold px-1.5 py-0.5 rounded"
                          style={{
                            color: meta.color,
                            background: `${meta.color}15`,
                            border: `1px solid ${meta.color}30`,
                          }}
                        >
                          {h.metric.toFixed(1)}
                        </div>
                      )}
                    </div>
                  </button>
                );
              })}
              <div className="text-[10px] text-st-muted text-center pt-1">
                共 {hits.length} 檔
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
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

      {/* 📊 活動統計 */}
      <UserStatsCard isGuest={isGuest} />

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
          desc="晨報精選 5 檔 + 觀察清單一鍵巡禮 + 無限智能整理"
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

/** 用戶活動統計卡 */
function UserStatsCard({ isGuest }: { isGuest: boolean }) {
  const guestList = useSession((s) => s.guestWatchlist);
  const picks = useSession((s) => s.briefingPicks);
  const [userId, setUserId] = useState<string | null>(null);
  useEffect(() => {
    if (isGuest) return;
    createClient().auth.getUser().then(({ data }) => setUserId(data.user?.id ?? null));
  }, [isGuest]);
  const cloudQ = useQuery({
    queryKey: ["watchlist-cloud", userId],
    queryFn: () => import("@/lib/watchlist").then(m => m.loadCloudWatchlist()),
    enabled: !isGuest && !!userId,
    staleTime: 60_000,
  });
  const list = isGuest ? guestList : (cloudQ.data ?? []);
  const holdingsCount = list.filter(x => x.shares && x.cost_per_share).length;

  return (
    <div
      className="rounded-st p-4"
      style={{
        background: [
          "radial-gradient(circle at 12% 18%, rgba(255,255,255,0.06), transparent 35%)",
          "linear-gradient(180deg, #1c2028 0%, #16181d 50%, #11141a 100%)",
        ].join(", "),
        border: "1px solid #3a4150",
        borderLeft: "3px solid #5eead4",
        boxShadow: "inset 0 1px 0 rgba(255,255,255,0.08), inset 0 -1px 0 rgba(0,0,0,0.4)",
      }}
    >
      <div className="text-xs text-teal-300 font-bold tracking-widest mb-3">📊 我的使用統計</div>
      <div className="grid grid-cols-3 gap-2">
        <Inline label="觀察追蹤" value={`${list.length}`} sub="檔" />
        <Inline label="持股檔數" value={`${holdingsCount}`} sub="檔" />
        <Inline label="晨報精選" value={`${picks.length}/5`} sub="檔" />
      </div>
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
