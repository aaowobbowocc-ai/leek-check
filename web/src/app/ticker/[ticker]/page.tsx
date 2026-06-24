"use client";

import { useParams, useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import {
  ArrowLeft, TrendingUp, TrendingDown, Loader2, Sparkles,
  Stethoscope, BarChart3, Newspaper, Coins, Users, Copy, Check,
} from "lucide-react";
import { useState } from "react";
import { api, type HealthCheck } from "@/lib/api";
import { formatNumber, formatPct } from "@/lib/utils";
import { ScoreRing } from "@/components/ui/score-ring";
import { Chip, ProgressBar } from "@/components/ui/chip";

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
      <main className="min-h-dvh flex items-center justify-center px-6">
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.15 }}
          className="text-center"
        >
          <div className="relative">
            <Stethoscope className="w-12 h-12 text-brand-400 mx-auto mb-3" />
            <Loader2 className="w-6 h-6 animate-spin text-brand-400 mx-auto absolute -bottom-1 -right-2" />
          </div>
          <p className="text-brand-300 text-sm mt-3">{ticker} 健檢中⋯</p>
        </motion.div>
      </main>
    );
  }

  if (error || !data) {
    return (
      <main className="min-h-dvh px-4 pt-12">
        <button onClick={() => router.back()} className="text-brand-400 mb-4 flex items-center gap-2">
          <ArrowLeft className="w-4 h-4" /> 回上一頁
        </button>
        <div className="bg-rose-500/10 border border-rose-500/30 rounded-xl p-4 text-rose-300">
          ⚠️ 找不到 {ticker} 的資料
        </div>
      </main>
    );
  }

  return <HealthCheckView data={data} onBack={() => router.back()} />;
}

const DIM_META = {
  technical: {
    icon: BarChart3, label: "技術面", weight: 40,
    desc: "趨勢 · 量價 · KD · MACD · RSI",
  },
  chip: {
    icon: Users, label: "籌碼面", weight: 30,
    desc: "三大法人 · 散戶比例 · 融資融券",
  },
  fundamental: {
    icon: Coins, label: "基本面", weight: 20,
    desc: "月營收 YoY · 財報 · 本益比",
  },
  news: {
    icon: Newspaper, label: "新聞面", weight: 10,
    desc: "重大新聞 · 市場情緒",
  },
} as const;

function HealthCheckView({ data, onBack }: { data: HealthCheck; onBack: () => void }) {
  const { ticker, name, industry, quote, health, sparkline, has_full_data } = data;
  const up = quote.change_pct >= 0;

  // 圓環顏色映射 (跟 streamlit 同色階)
  const ringColor = health.composite >= 70
    ? "green" as const
    : health.composite >= 50
    ? "amber" as const
    : "rose" as const;
  const verdictLabel = health.composite >= 70
    ? "健康"
    : health.composite >= 50
    ? "亞健康"
    : "韭菜病";

  return (
    <main className="min-h-dvh pb-12">
      {/* Top bar */}
      <div className="sticky top-0 z-30 bg-ink-950/80 backdrop-blur-md border-b border-ink-800">
        <div style={{ height: "env(safe-area-inset-top)" }} />
        <div className="px-4 py-3 flex items-center gap-2">
          <button
            onClick={onBack}
            className="p-1.5 -ml-1.5 rounded-lg hover:bg-ink-800 active:scale-95 transition"
          >
            <ArrowLeft className="w-5 h-5 text-slate-300" />
          </button>
          <div className="flex-1 min-w-0">
            <div className="text-xs text-brand-300 font-mono">{ticker}</div>
            <div className="font-bold text-white truncate text-sm">{name || ticker}</div>
          </div>
          {industry && (
            <Chip tone="brand">{industry}</Chip>
          )}
        </div>
      </div>

      <div className="px-4 pt-4 space-y-4">
        {/* Quote hero */}
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          className="bg-gradient-to-br from-ink-900 via-ink-900 to-brand-900/30 border border-ink-700 rounded-2xl p-5"
        >
          <div className="flex items-end justify-between gap-4">
            <div>
              <div className="text-[10px] text-slate-500 tracking-wider font-bold">
                收盤價 {quote.asof}
              </div>
              <div className="text-5xl font-extrabold text-white leading-none mt-1">
                {formatNumber(quote.price)}
              </div>
              <div className={`text-sm font-bold flex items-center gap-1 mt-2 ${up ? "text-emerald-400" : "text-rose-400"}`}>
                {up ? <TrendingUp className="w-4 h-4" /> : <TrendingDown className="w-4 h-4" />}
                {formatPct(quote.change_pct)}
              </div>
            </div>
            <div className="flex-1 max-w-[180px]">
              {sparkline.length > 0 && <Sparkline data={sparkline} up={up} />}
            </div>
          </div>
        </motion.div>

        {/* Hero: composite score ring */}
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.08 }}
          className="bg-gradient-to-br from-ink-900 to-ink-950 border border-ink-700 rounded-2xl p-5"
        >
          <div className="flex items-center gap-2 mb-3">
            <Stethoscope className="w-4 h-4 text-brand-400" />
            <h2 className="font-extrabold text-white">健檢分數</h2>
          </div>
          <div className="flex justify-center mb-4">
            <ScoreRing
              score={health.composite}
              label={verdictLabel}
              color={ringColor}
              size={140}
            />
          </div>
          {/* Sub scores quick view */}
          <div className="grid grid-cols-4 gap-2">
            {(["technical", "chip", "fundamental", "news"] as const).map((k) => {
              const meta = DIM_META[k];
              const Icon = meta.icon;
              const sub = health.scores[k];
              return (
                <div key={k} className="bg-ink-950/60 border-l-2 border-brand-500/60 rounded p-2 text-center">
                  <Icon className="w-3.5 h-3.5 text-brand-300 mx-auto" />
                  <div className="text-xl font-extrabold text-white mt-1 leading-none">{sub.score}</div>
                  <div className="text-[9px] text-slate-500 mt-1">{meta.label}</div>
                  <div className="text-[8px] text-slate-600">{meta.weight}%</div>
                </div>
              );
            })}
          </div>
          <p className="text-[10px] text-slate-500 text-center mt-3">
            💡 70+ 健康 / 50-69 亞健康 / &lt;50 韭菜病 · 純客觀數據,不構成投資建議
          </p>
        </motion.div>

        {/* 4 dimension breakdown */}
        <div className="space-y-3">
          <div className="flex items-center gap-2 px-1">
            <BarChart3 className="w-4 h-4 text-brand-400" />
            <h2 className="font-extrabold text-white">4 面詳細分析</h2>
          </div>
          {(["technical", "chip", "fundamental", "news"] as const).map((k, i) => {
            const meta = DIM_META[k];
            const Icon = meta.icon;
            const sub = health.scores[k];
            const tone = sub.score >= 70
              ? "emerald" as const
              : sub.score >= 50
              ? "brand" as const
              : sub.score >= 35
              ? "amber" as const
              : "rose" as const;
            return (
              <motion.div
                key={k}
                initial={{ opacity: 0, x: -8 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: 0.15 + i * 0.05 }}
                className="bg-ink-900/60 border border-ink-700 rounded-2xl p-4"
              >
                <div className="flex items-start justify-between gap-3 mb-2">
                  <div className="flex items-center gap-2">
                    <div className="w-9 h-9 bg-brand-500/15 border border-brand-500/30 rounded-xl flex items-center justify-center">
                      <Icon className="w-4 h-4 text-brand-300" />
                    </div>
                    <div>
                      <div className="font-extrabold text-white text-sm">{meta.label}</div>
                      <div className="text-[10px] text-slate-500">{meta.desc}</div>
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="text-2xl font-extrabold text-white leading-none">{sub.score}</div>
                    <div className="text-[10px] text-slate-500">權重 {meta.weight}%</div>
                  </div>
                </div>
                <ProgressBar value={sub.score} tone={tone} />
                <ul className="mt-3 space-y-1">
                  {sub.notes.map((note, j) => (
                    <li key={j} className="text-sm text-slate-300 leading-relaxed">
                      {note}
                    </li>
                  ))}
                </ul>
              </motion.div>
            );
          })}
        </div>

        {/* AI prompt copy section */}
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.4 }}
        >
          <AiPromptCard data={data} verdict={verdictLabel} />
        </motion.div>

        {/* Disclaimer */}
        {!has_full_data && (
          <div className="bg-amber-500/10 border border-amber-500/30 rounded-xl p-3 text-xs text-amber-200">
            ⚠️ 部分資料用 yfinance 即時抓,可能有誤差;最佳準確度請等本地 cache 同步
          </div>
        )}
        <div className="bg-amber-500/10 border border-amber-500/30 rounded-xl p-3 text-xs text-amber-200">
          ⚠️ 純客觀數據展示 · 不報明牌 · 不指示動作 · 盈虧自負
        </div>
      </div>
    </main>
  );
}

function Sparkline({ data, up }: { data: number[]; up: boolean }) {
  if (data.length < 2) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const W = 180, H = 56;
  const points = data
    .map((v, i) => `${(i / (data.length - 1)) * W},${H - ((v - min) / range) * H}`)
    .join(" ");
  const stroke = up ? "#10b981" : "#f43f5e";
  const fill = up ? "rgba(16,185,129,0.15)" : "rgba(244,63,94,0.15)";
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-12">
      <defs>
        <linearGradient id="spark-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={fill} />
          <stop offset="100%" stopColor="transparent" />
        </linearGradient>
      </defs>
      <polygon
        points={`0,${H} ${points} ${W},${H}`}
        fill="url(#spark-grad)"
      />
      <polyline points={points} stroke={stroke} strokeWidth={1.8} fill="none" strokeLinejoin="round" />
    </svg>
  );
}

function AiPromptCard({ data, verdict }: { data: HealthCheck; verdict: string }) {
  const [copied, setCopied] = useState(false);
  const { ticker, name, industry, quote, health } = data;
  const prompt = buildPrompt(ticker, name, industry, quote, health, verdict);

  const copy = async () => {
    await navigator.clipboard.writeText(prompt);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="bg-gradient-to-br from-purple-500/10 to-brand-500/10 border border-purple-500/30 rounded-2xl p-4">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-purple-300" />
          <h3 className="font-bold text-white text-sm">🤖 想要 AI 白話解讀?</h3>
        </div>
      </div>
      <p className="text-xs text-slate-400 mb-3 leading-relaxed">
        複製這份健檢資料貼給 Claude / ChatGPT / Gemini,
        他們會用韭菜健檢風格寫白話評估,不報明牌、不喊飆股。
      </p>
      <button
        onClick={copy}
        className="w-full flex items-center justify-center gap-2 bg-purple-500/20 hover:bg-purple-500/30 active:scale-[0.98] border border-purple-500/40 text-purple-200 font-semibold text-sm rounded-xl py-3 transition"
      >
        {copied ? <Check className="w-4 h-4 text-emerald-300" /> : <Copy className="w-4 h-4" />}
        {copied ? "已複製,貼給 AI 對話" : "📋 複製健檢 prompt"}
      </button>
    </div>
  );
}

function buildPrompt(
  tk: string, name: string, industry: string,
  quote: { price: number; change_pct: number; asof: string },
  health: HealthCheck["health"], verdict: string
): string {
  const dim = (k: keyof typeof health.scores, label: string) => {
    const s = health.scores[k];
    return `${label} ${s.score}/100\n${s.notes.map((n) => `  • ${n}`).join("\n")}`;
  };
  return `請幫我做韭菜健檢:

【標的】${tk} ${name || ""} (${industry || "—"})
【目前報價】NT$ ${quote.price.toFixed(2)} (${quote.change_pct >= 0 ? "+" : ""}${quote.change_pct.toFixed(2)}%) · 收盤 ${quote.asof}

${dim("technical", "【技術面】")}

${dim("chip", "【籌碼面】")}

${dim("fundamental", "【基本面】")}

${dim("news", "【新聞面】")}

【健檢分數】${health.composite}/100 (${verdict})
  • 技術 ${health.scores.technical.score}/100
  • 籌碼 ${health.scores.chip.score}/100
  • 基本 ${health.scores.fundamental.score}/100
  • 新聞 ${health.scores.news.score}/100

請用「韭菜健檢」風格幫我:
1. 🩺 技術面健檢 (白話 2-3 句)
2. 🩺 籌碼面健檢 (白話 2-3 句)
3. 🩺 基本面健檢 (白話 2-3 句)
4. 🚨 綜合判斷 + 韭菜病風險警示

規則:
- 不報明牌、不給買賣建議、純客觀判讀
- 直接從第 1 點開始,不要開場白、不要結尾贅述
`;
}
