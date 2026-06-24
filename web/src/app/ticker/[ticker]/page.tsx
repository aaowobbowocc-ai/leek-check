"use client";

import { useParams, useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { ArrowLeft, TrendingUp, TrendingDown, Loader2 } from "lucide-react";
import { api, type HealthCheck } from "@/lib/api";
import { formatNumber, formatPct } from "@/lib/utils";

export default function TickerPage() {
  const params = useParams<{ ticker: string }>();
  const router = useRouter();
  const ticker = params.ticker;

  const { data, isLoading, error } = useQuery({
    queryKey: ["health-check", ticker],
    queryFn: () => api.getHealthCheck(ticker),
    enabled: !!ticker,
  });

  if (isLoading) {
    return (
      <div className="min-h-dvh flex items-center justify-center">
        <div className="text-center">
          <Loader2 className="w-8 h-8 animate-spin text-brand-400 mx-auto mb-3" />
          <p className="text-brand-300 text-sm">{ticker} 健檢中⋯</p>
        </div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="min-h-dvh px-4 pt-12">
        <button onClick={() => router.back()} className="text-brand-400 mb-4 flex items-center gap-2">
          <ArrowLeft className="w-4 h-4" /> 回上一頁
        </button>
        <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 text-red-300">
          ⚠️ 找不到 {ticker} 的資料
        </div>
      </div>
    );
  }

  return <HealthCheckView data={data} onBack={() => router.back()} />;
}

function HealthCheckView({ data, onBack }: { data: HealthCheck; onBack: () => void }) {
  const { ticker, name, industry, quote, health, sparkline, has_full_data } = data;
  const up = quote.change_pct >= 0;
  const colorMap = {
    green: "from-emerald-500/30 to-emerald-700/10 border-emerald-500/40 text-emerald-300",
    teal: "from-brand-500/30 to-brand-700/10 border-brand-500/40 text-brand-300",
    amber: "from-amber-500/30 to-amber-700/10 border-amber-500/40 text-amber-300",
    red: "from-red-500/30 to-red-700/10 border-red-500/40 text-red-300",
  } as const;
  const verdictColor = colorMap[health.color];

  return (
    <main className="min-h-dvh pb-12 px-4 pt-[max(16px,env(safe-area-inset-top))]">
      {/* Header */}
      <button
        onClick={onBack}
        className="text-slate-400 hover:text-brand-300 mb-4 mt-2 flex items-center gap-2 text-sm"
      >
        <ArrowLeft className="w-4 h-4" /> 回上一頁
      </button>

      {/* Quote card */}
      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        className="bg-ink-900/60 border border-ink-700 rounded-2xl p-5 mb-4"
      >
        <div className="flex items-start justify-between mb-3">
          <div>
            <div className="text-xs text-brand-300 font-bold tracking-wider">{ticker}</div>
            <h1 className="text-2xl font-extrabold text-white mt-1">{name || ticker}</h1>
            <div className="text-xs text-slate-500 mt-1">{industry}</div>
          </div>
          <div className="text-right">
            <div className="text-3xl font-extrabold text-white">
              {formatNumber(quote.price)}
            </div>
            <div className={`text-sm font-bold flex items-center gap-1 justify-end mt-1 ${up ? "text-emerald-400" : "text-red-400"}`}>
              {up ? <TrendingUp className="w-4 h-4" /> : <TrendingDown className="w-4 h-4" />}
              {formatPct(quote.change_pct)}
            </div>
          </div>
        </div>
        <div className="text-xs text-slate-500">收盤 {quote.asof}</div>
        {sparkline.length > 0 && (
          <Sparkline data={sparkline} up={up} />
        )}
      </motion.div>

      {/* 4 面健檢綜合分 */}
      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.1 }}
        className={`bg-gradient-to-br ${verdictColor} border rounded-2xl p-5 mb-4 text-center`}
      >
        <div className="text-xs tracking-widest font-bold mb-1 opacity-80">
          4 面健檢綜合分
        </div>
        <div className="text-6xl font-extrabold text-white mt-1">
          {health.composite}
        </div>
        <div className="text-xl font-bold mt-2 opacity-90">{health.verdict}</div>
        {!has_full_data && (
          <div className="text-[10px] mt-3 opacity-60">
            ⚠️ 部分資料用 yfinance 即時抓,可能誤差
          </div>
        )}
      </motion.div>

      {/* 4 dimension breakdown */}
      <div className="grid grid-cols-1 gap-3 mb-6">
        {(
          [
            { key: "technical", label: "📈 技術面", weight: "40%", data: health.scores.technical },
            { key: "chip", label: "📊 籌碼面", weight: "30%", data: health.scores.chip },
            { key: "fundamental", label: "💰 基本面", weight: "20%", data: health.scores.fundamental },
            { key: "news", label: "📰 新聞面", weight: "10%", data: health.scores.news },
          ] as const
        ).map(({ key, label, weight, data: d }, i) => (
          <motion.div
            key={key}
            initial={{ opacity: 0, x: -8 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.2 + i * 0.05 }}
            className="bg-ink-900/50 border border-ink-700 rounded-xl p-4"
          >
            <div className="flex items-center justify-between mb-2">
              <div>
                <span className="font-bold text-white">{label}</span>
                <span className="text-[10px] text-slate-500 ml-2">權重 {weight}</span>
              </div>
              <ScorePill score={d.score} />
            </div>
            <ul className="space-y-1 mt-2">
              {d.notes.map((note, j) => (
                <li key={j} className="text-sm text-slate-300">
                  {note}
                </li>
              ))}
            </ul>
          </motion.div>
        ))}
      </div>

      {/* Disclaimer */}
      <div className="bg-amber-500/10 border border-amber-500/30 rounded-xl p-3 text-xs text-amber-200">
        ⚠️ 純客觀數據展示 · 不報明牌 · 不指示動作 · 盈虧自負
      </div>
    </main>
  );
}

function ScorePill({ score }: { score: number }) {
  let bg = "bg-amber-500/20 text-amber-300";
  if (score >= 70) bg = "bg-emerald-500/20 text-emerald-300";
  else if (score >= 50) bg = "bg-brand-500/20 text-brand-300";
  else if (score < 35) bg = "bg-red-500/20 text-red-300";
  return (
    <div className={`px-3 py-1 rounded-full text-sm font-extrabold ${bg}`}>
      {score}
    </div>
  );
}

function Sparkline({ data, up }: { data: number[]; up: boolean }) {
  if (data.length < 2) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const W = 280;
  const H = 50;
  const points = data
    .map((v, i) => `${(i / (data.length - 1)) * W},${H - ((v - min) / range) * H}`)
    .join(" ");
  const stroke = up ? "#10b981" : "#ef4444";
  return (
    <div className="mt-3">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-12">
        <polyline points={points} stroke={stroke} strokeWidth={2} fill="none" strokeLinejoin="round" />
      </svg>
    </div>
  );
}
