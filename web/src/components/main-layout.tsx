"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useQuery } from "@tanstack/react-query";
import {
  Sunrise, Star, Search, Radio, User, Ghost, LogOut, Flame, X,
} from "lucide-react";
import { useSession } from "@/lib/store";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useRouter } from "next/navigation";
import { api, type Quote } from "@/lib/api";
import { WatchPanel } from "@/components/watch-panel";
import { StockCard } from "@/components/stock-card";
import { createClient } from "@/lib/supabase/client";
import { HOT_STOCK_CATEGORIES, ALL_HOT_TICKERS } from "@/lib/tier";

type Tab = "brief" | "watch" | "search" | "scan" | "me";

const TABS: { id: Tab; icon: typeof Star; label: string }[] = [
  { id: "brief", icon: Sunrise, label: "晨報" },
  { id: "watch", icon: Star, label: "觀察" },
  { id: "search", icon: Search, label: "搜尋" },
  { id: "scan", icon: Radio, label: "策略" },
  { id: "me", icon: User, label: "我的" },
];

export function MainLayout() {
  const [active, setActive] = useState<Tab>("watch");
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
            {active === "brief" && <BriefPanel />}
            {active === "watch" && <WatchPanel />}
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

function BriefPanel() {
  const hour = new Date().getHours();
  const greeting = hour < 12 ? "🌅 早安" : hour < 18 ? "☀️ 午安" : "🌙 晚安";
  return (
    <div className="space-y-4">
      <div className="bg-gradient-to-br from-brand-700/40 to-ink-900 border border-ink-700 rounded-2xl p-5">
        <div className="text-xs tracking-widest text-brand-300 font-bold">
          {new Date().toLocaleDateString("zh-TW", { dateStyle: "long" })}
        </div>
        <h2 className="text-2xl font-extrabold text-white mt-1">
          {greeting},今日市場健檢
        </h2>
        <p className="text-brand-200 text-sm mt-2">
          開盤前 5 分鐘看一眼 · 盤後分析,不適合盤中即時下單
        </p>
      </div>
      <PlaceholderCard
        title="📡 [Paper] 策略訊號"
        desc="從 1958 檔台股掃出今日命中,即將整合"
        badge="WIP"
      />
      <PlaceholderCard
        title="🩺 觀察清單巡禮"
        desc="一鍵掃描所有持股 4 面健檢分數,即將整合"
        badge="WIP"
      />
      <PlaceholderCard
        title="🌡️ 大盤狀態"
        desc="TAIEX / VIX / 集中度 / 法人動向"
        badge="WIP"
      />
    </div>
  );
}

function SearchPanel() {
  const [q, setQ] = useState("");
  const router = useRouter();
  const isSearching = q.trim().length >= 1;

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
              <StockCard
                key={r.ticker}
                ticker={r.ticker}
                name={r.name}
                industry={r.industry}
                quote={hotQuoteMap[r.ticker]}
                onClick={() => router.push(`/ticker/${r.ticker}`)}
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

      {/* 熱門股(空白搜尋時顯示)*/}
      {!isSearching && (
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
                        className="shimmer rounded-st h-[78px]"
                      />
                    );
                  }
                  return (
                    <StockCard
                      key={tk}
                      ticker={tk}
                      name={q?.name ?? ""}
                      industry={q?.industry ?? ""}
                      quote={q}
                      onClick={() => router.push(`/ticker/${tk}`)}
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

  useState(() => {
    const sb = createClient();
    sb.auth.getUser().then(({ data }) => setEmail(data.user?.email ?? null));
  });

  return (
    <div className="space-y-4 pb-4">
      <h2 className="text-2xl font-extrabold text-white">👤 我的</h2>
      {isGuest ? (
        <div className="bg-amber-500/10 border border-amber-500/30 rounded-2xl p-5">
          <div className="flex items-center gap-2 mb-2">
            <Ghost className="w-5 h-5 text-amber-400" />
            <h3 className="font-bold text-white">訪客模式</h3>
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
        <div className="bg-ink-900/60 border border-ink-700 rounded-2xl p-5">
          <div className="text-xs text-slate-400 font-bold tracking-widest">
            已登入
          </div>
          <div className="text-white font-bold mt-1">{email ?? "—"}</div>
        </div>
      )}
      <PlaceholderCard
        title="🔔 通知設定"
        desc="集中度警示、晨報推播"
        badge="WIP"
      />
      <PlaceholderCard
        title="💎 升級 PRO"
        desc="解鎖晨報精選 5 檔 + 觀察清單一鍵巡禮"
        badge="WIP"
      />
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
