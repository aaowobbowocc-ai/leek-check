"use client";

import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { api, type Quote } from "@/lib/api";
import type { WatchlistItem } from "@/lib/watchlist";

/** 今日大盤晨報(公版,所有 user 同一份)+ 個人化按鈕 */
export function DailyBriefCard({
  picks,
  watchlist,
  quotes,
}: {
  picks: string[];
  watchlist: WatchlistItem[];
  quotes: Quote[];
}) {
  const [expandMarket, setExpandMarket] = useState(true);
  const [expandNews, setExpandNews] = useState(false);
  const [personalText, setPersonalText] = useState<string | null>(null);
  const [personalLoading, setPersonalLoading] = useState(false);

  // 抓公版 daily brief(cache 命中 100%,秒回)
  const briefQ = useQuery({
    queryKey: ["daily-brief"],
    queryFn: () => api.getDailyBrief(),
    staleTime: 5 * 60_000,
  });
  const brief = briefQ.data;

  const quoteMap = useMemo(() => {
    const m = new Map<string, Quote>();
    quotes.forEach((q) => m.set(q.ticker, q));
    return m;
  }, [quotes]);

  const genPersonal = async () => {
    setPersonalLoading(true);
    try {
      // 拉 dashboard + healthcheck picks(平行)
      const [dashboard, ...healths] = await Promise.all([
        api.getMarketDashboard(),
        ...picks.map((tk) => api.getHealthCheck(tk).catch(() => null)),
      ]);

      const picksData = healths.filter(Boolean).map((h) => ({
        ticker: h!.ticker,
        name: h!.name,
        price: h!.quote.price,
        change_pct: h!.quote.change_pct,
        composite: h!.health.composite,
        rev_yoy: h!.funda?.rev_yoy,
        rsi: h!.tech?.rsi,
      }));

      const holdings = watchlist
        .filter((it) => it.shares && it.cost_per_share)
        .map((it) => {
          const q = quoteMap.get(it.ticker);
          const costIncl = it.cost_per_share! * 1.001425;
          const currPrice = q?.price ?? costIncl;
          const pnlPct = (currPrice / costIncl - 1) * 100;
          return {
            ticker: it.ticker,
            name: q?.name ?? "",
            shares: it.shares,
            cost_per_share: it.cost_per_share,
            current_price: currPrice,
            pnl_pct: pnlPct,
          };
        });

      const wlData = watchlist
        .filter((it) => !it.shares || !it.cost_per_share)
        .slice(0, 10)
        .map((it) => {
          const q = quoteMap.get(it.ticker);
          return {
            ticker: it.ticker,
            name: q?.name ?? "",
            price: q?.price ?? 0,
            change_pct: q?.change_pct ?? 0,
          };
        });

      const res = await api.aiPersonalBrief({
        picks: picksData,
        holdings,
        watchlist: wlData,
        dashboard,
        style: "neutral",
        timeframe: "mid",
      });
      setPersonalText(res.text);
    } catch (e) {
      setPersonalText(`⚠️ 產生失敗:${(e as Error).message}`);
    } finally {
      setPersonalLoading(false);
    }
  };

  const hasAny = brief?.market_insight || brief?.news_sentiment;

  const slotLabel = (() => {
    if (!brief?.slot) return "";
    if (brief.slot.endsWith("morning")) return "🌅 早盤前(07:30 版)";
    if (brief.slot.endsWith("noon")) return "☀️ 盤後(14:00 版)";
    return "🌙 夜盤(20:30 版)";
  })();

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      className="rounded-st p-4"
      style={{
        background: [
          "radial-gradient(circle at 12% 18%, rgba(94,234,212,0.08), transparent 40%)",
          "linear-gradient(180deg, #1c2028 0%, #16181d 60%, #11141a 100%)",
        ].join(", "),
        border: "1px solid #3a4150",
        borderLeft: "3px solid var(--accent)",
        boxShadow: "inset 0 1px 0 rgba(255,255,255,0.06), 0 0 24px var(--accent-glow)",
      }}
    >
      <div className="flex items-center gap-2 mb-3">
        <span className="text-2xl">📖</span>
        <div className="flex-1">
          <div className="text-xs font-bold tracking-wider" style={{ color: "var(--accent)" }}>
            今日大盤晨報
          </div>
          <div className="text-[10px] text-st-muted">{slotLabel}</div>
        </div>
        {briefQ.isLoading && (
          <div className="text-[10px] text-st-muted">載入中...</div>
        )}
      </div>

      {!hasAny && !briefQ.isLoading && (
        <div className="text-xs text-st-muted text-center py-3">
          晨報生成中,首個 user 觸發後全 App 共用 · 下次刷新試試
        </div>
      )}

      {/* 🌍 大盤 / 國際情勢 */}
      {brief?.market_insight && (
        <div className="mb-3">
          <button
            onClick={() => setExpandMarket(!expandMarket)}
            className="w-full flex items-center gap-2 text-left"
          >
            <span className="text-sm">🌍</span>
            <span className="text-xs font-bold text-st-fg flex-1">國際情勢與大盤位階</span>
            <span className="text-[10px] text-st-muted">{expandMarket ? "▲" : "▼"}</span>
          </button>
          <AnimatePresence>
            {expandMarket && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                className="overflow-hidden"
              >
                <div className="text-xs text-st-soft whitespace-pre-wrap leading-relaxed mt-2 pl-6 border-l border-st-border">
                  {brief.market_insight}
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      )}

      {/* 📰 新聞情緒 */}
      {brief?.news_sentiment && (
        <div className="mb-3">
          <button
            onClick={() => setExpandNews(!expandNews)}
            className="w-full flex items-center gap-2 text-left"
          >
            <span className="text-sm">📰</span>
            <span className="text-xs font-bold text-st-fg flex-1">今日新聞情緒</span>
            <span className="text-[10px] text-st-muted">{expandNews ? "▲" : "▼"}</span>
          </button>
          <AnimatePresence>
            {expandNews && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                className="overflow-hidden"
              >
                <div className="text-xs text-st-soft whitespace-pre-wrap leading-relaxed mt-2 pl-6 border-l border-st-border">
                  {brief.news_sentiment}
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      )}

      {/* ✨ 個人化按鈕 */}
      <button
        onClick={genPersonal}
        disabled={personalLoading || (picks.length === 0 && watchlist.length === 0)}
        className="btn-smart w-full"
      >
        ✨{" "}
        <span className="relative z-10">
          {personalLoading
            ? "整理中..."
            : personalText
            ? "🔄 重新產生個人化報告"
            : picks.length === 0 && watchlist.length === 0
            ? "先加觀察 / 晨報 才能個人化"
            : "產生我的個人化晨報"}
        </span>
      </button>

      {/* 個人化結果 */}
      {personalText && (
        <motion.div
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          className="mt-3 rounded p-3 text-xs text-st-soft whitespace-pre-wrap leading-relaxed"
          style={{
            background: "linear-gradient(135deg, color-mix(in srgb, var(--accent) 12%, transparent), transparent)",
            border: "1px solid color-mix(in srgb, var(--accent) 30%, transparent)",
          }}
        >
          <div className="text-[10px] font-bold mb-2" style={{ color: "var(--accent)" }}>
            🎯 你的個人化晨報
          </div>
          {personalText}
        </motion.div>
      )}
    </motion.div>
  );
}
