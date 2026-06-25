"use client";

import { useParams, useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { useState } from "react";
import { ArrowLeft, Stethoscope, Copy, Check } from "lucide-react";
import { api, type HealthCheck } from "@/lib/api";
import { formatNumber, formatPct } from "@/lib/utils";
import { ScoreRing } from "@/components/ui/score-ring";
import { StCard, StHeader, StCaption } from "@/components/ui/st-card";
import { PriceChart, RevenueBarChart, ChipStackedBar, TechGrid, FundaGrid } from "@/components/charts";

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
    return <HealthSkeleton ticker={ticker} />;
  }

  if (error || !data) {
    return (
      <main className="min-h-dvh px-4 pt-12">
        <BackBtn onClick={() => router.back()} />
        <StCard className="mt-4">
          <div className="text-rose-400">⚠️ 找不到 {ticker} 的資料</div>
        </StCard>
      </main>
    );
  }

  return <HealthCheckView data={data} onBack={() => router.back()} />;
}

function BackBtn({ onClick }: { onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="text-teal-300 flex items-center gap-2 text-sm active:opacity-60"
    >
      <ArrowLeft className="w-4 h-4" /> 回上一頁
    </button>
  );
}

function HealthCheckView({ data, onBack }: { data: HealthCheck; onBack: () => void }) {
  const { ticker, name, industry, quote, health, sparkline } = data;
  const up = quote.change_pct >= 0;
  const composite = health.composite;

  const ringColor = composite >= 70 ? "green" : composite >= 50 ? "amber" : "rose";
  const verdict = composite >= 70 ? "健康" : composite >= 50 ? "亞健康" : "韭菜病";

  // Sub score 對應 streamlit 三項(技術/籌碼/基本),新聞獨立顯示
  const sub = health.scores;

  return (
    <main className="min-h-dvh pb-12 bg-st-bg">
      {/* Top sticky bar */}
      <div className="sticky top-0 z-30 bg-st-bg/95 backdrop-blur-md border-b border-st-border">
        <div style={{ height: "env(safe-area-inset-top)" }} />
        <div className="px-4 py-3 flex items-center gap-2">
          <button
            onClick={onBack}
            className="p-1.5 -ml-1.5 rounded hover:bg-white/5 active:scale-95 transition"
          >
            <ArrowLeft className="w-5 h-5 text-st-soft" />
          </button>
          <div className="flex-1 min-w-0">
            <div className="text-xs text-teal-300 font-mono">{ticker}</div>
            <div className="font-bold text-st-fg truncate text-sm">{name || ticker}</div>
          </div>
        </div>
      </div>

      <div className="px-4 pt-4 space-y-4">
        {/* Hero quote card — halo + tabular-nums + live dot (台股紅漲綠跌) */}
        <StCard variant="hero" className="hero-halo">
          <div className="flex items-center justify-between text-[10px] text-teal-300 font-bold tracking-[0.2em]">
            <span>{industry || "—"} · 收盤 {quote.asof}</span>
            <span className="flex items-center gap-1.5 normal-case tracking-normal text-emerald-300">
              <span className="live-dot" /> Live
            </span>
          </div>
          <div className="flex items-end justify-between gap-4 mt-1">
            <div>
              <div
                className="text-st-fg leading-none tabular-nums"
                style={{ fontSize: "3rem", fontWeight: 800 }}
              >
                {formatNumber(quote.price)}
              </div>
              <div
                className="font-bold mt-2 tabular-nums"
                style={{
                  color: up ? "#ef4444" : "#10b981",
                  fontSize: "1rem",
                }}
              >
                {up ? "▲" : "▼"} {formatPct(Math.abs(quote.change_pct), false)} ({formatPct(quote.change_pct)})
              </div>
            </div>
            <div className="flex-1 max-w-[160px]">
              {sparkline.length > 0 && <Sparkline data={sparkline} up={up} />}
            </div>
          </div>
        </StCard>

        {/* 健檢分數區 — 1:1 抄 streamlit */}
        <StCard>
          <StHeader emoji="🩺" title="健檢分數" />
          {/* Ring 置中 */}
          <div className="flex justify-center mb-4">
            <ScoreRing
              score={composite}
              label={verdict}
              color={ringColor}
              size={130}
            />
          </div>
          {/* 3 個分項橫排 — streamlit 用 grid-template-columns: repeat(3,1fr) */}
          <div className="grid grid-cols-3 gap-2">
            <SubScoreCard emoji="📈" label="技術" score={sub.technical.score} weight="40%" />
            <SubScoreCard emoji="📊" label="籌碼" score={sub.chip.score} weight="30%" />
            <SubScoreCard emoji="💰" label="基本" score={sub.fundamental.score} weight="30%" />
          </div>
          <StCaption className="text-center mt-3">
            💡 70+ 健康 / 50-69 亞健康 / &lt;50 韭菜病 · 純客觀數據,不構成投資建議
          </StCaption>
        </StCard>

        {/* 📈 60 日股價圖 + MA(主圖)*/}
        {data.ohlcv_60d.length > 0 && (
          <StCard>
            <StHeader emoji="📈" title="60 日股價趨勢" />
            <PriceChart bars={data.ohlcv_60d} />
          </StCard>
        )}

        {/* 📋 技術面數值 grid */}
        {data.tech && (
          <StCard>
            <StHeader emoji="📋" title="技術指標" sub="均線多空 + KD + RSI" />
            <TechGrid tech={data.tech} />
            <div className="mt-3 space-y-1.5 pt-3 border-t border-st-border">
              {sub.technical.notes.map((n, i) => (
                <div key={i} className="text-xs text-st-soft">{n}</div>
              ))}
            </div>
          </StCard>
        )}

        {/* 📊 法人籌碼 */}
        {data.chip && (
          <StCard>
            <StHeader emoji="📊" title="法人籌碼 20 日" sub="外資 / 投信 / 自營商 net buy" />
            <ChipStackedBar
              foreign={data.chip.foreign_20d}
              invtrust={data.chip.invtrust_20d}
              dealer={data.chip.dealer_20d}
            />
            <div className="mt-3 space-y-1.5 pt-3 border-t border-st-border">
              {sub.chip.notes.map((n, i) => (
                <div key={i} className="text-xs text-st-soft">{n}</div>
              ))}
            </div>
          </StCard>
        )}

        {/* 💰 基本面 + 月營收圖 */}
        <StCard>
          <StHeader emoji="💰" title="基本面" sub="估值 + 月營收成長" />
          <FundaGrid funda={data.funda} />
          {data.funda.rev_history && data.funda.rev_history.length > 0 && (
            <div className="mt-3 pt-3 border-t border-st-border">
              <div className="text-[10px] text-st-muted mb-2">📊 月營收 YoY 12 期</div>
              <RevenueBarChart data={data.funda.rev_history} />
            </div>
          )}
          <div className="mt-3 space-y-1.5 pt-3 border-t border-st-border">
            {sub.fundamental.notes.map((n, i) => (
              <div key={i} className="text-xs text-st-soft">{n}</div>
            ))}
          </div>
        </StCard>

        {/* 📰 新聞面(暫 placeholder)*/}
        <StCard>
          <StHeader emoji="📰" title="新聞面" sub="近期新聞 + 市場情緒" />
          <div className="space-y-1.5">
            {sub.news.notes.map((n, i) => (
              <div key={i} className="text-xs text-st-soft">{n}</div>
            ))}
          </div>
        </StCard>

        {/* AI prompt (streamlit expander 翻成 card) */}
        <AiPromptCard data={data} verdict={verdict} />

        {/* Disclaimer */}
        <div className="rounded-st border border-amber-300/40 bg-amber-300/10 p-3 text-xs text-amber-300">
          ⚠️ 純客觀數據展示 · 不報明牌 · 不指示動作 · 盈虧自負
        </div>
      </div>
    </main>
  );
}

/** Sub-card — streamlit border-left:3px solid #5eead4 + 置中 */
function SubScoreCard({ emoji, label, score, weight }: { emoji: string; label: string; score: number; weight: string }) {
  return (
    <div
      style={{
        background: "#16181d",
        padding: "10px 8px",
        borderRadius: 8,
        borderLeft: "3px solid #5eead4",
        textAlign: "center",
      }}
    >
      <div style={{ fontSize: "0.7rem", color: "#94a3b8" }}>{emoji} {label}</div>
      <div style={{ fontSize: "1.4rem", color: "#fff", fontWeight: 700 }}>{score}</div>
      <div style={{ fontSize: "0.65rem", color: "#64748b" }}>{weight}</div>
    </div>
  );
}

function DimSection({ emoji, label, weight, score, notes }: { emoji: string; label: string; weight: string; score: number; notes: string[] }) {
  return (
    <div>
      <div className="flex items-baseline justify-between mb-2 pb-2 border-b border-st-border">
        <h4 className="font-bold text-st-fg">
          {emoji} {label} <span className="text-xs text-st-muted ml-1">權重 {weight}</span>
        </h4>
        <div className="text-2xl font-extrabold text-teal-300 leading-none">{score}</div>
      </div>
      <ul className="space-y-1.5">
        {notes.map((n, i) => (
          <li key={i} className="text-sm text-st-soft leading-relaxed">{n}</li>
        ))}
      </ul>
    </div>
  );
}

function Sparkline({ data, up }: { data: number[]; up: boolean }) {
  if (data.length < 2) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const W = 160, H = 56;
  const points = data
    .map((v, i) => `${(i / (data.length - 1)) * W},${H - ((v - min) / range) * H}`)
    .join(" ");
  // 台股紅漲綠跌
  const stroke = up ? "#ef4444" : "#10b981";
  const fill = up ? "rgba(239,68,68,0.18)" : "rgba(16,185,129,0.18)";
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-12">
      <defs>
        <linearGradient id="spark" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={fill} />
          <stop offset="100%" stopColor="transparent" />
        </linearGradient>
      </defs>
      <polygon points={`0,${H} ${points} ${W},${H}`} fill="url(#spark)" />
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
    <StCard>
      <StHeader emoji="🤖" title="想看白話解讀?" sub="複製這份資料貼給 Claude / ChatGPT / Gemini" />
      <button
        onClick={copy}
        className="w-full flex items-center justify-center gap-2 bg-teal-300/10 hover:bg-teal-300/20 active:scale-[0.98] border border-teal-300/40 text-teal-300 font-semibold text-sm rounded-st py-3 transition"
      >
        {copied ? <Check className="w-4 h-4 text-green-400" /> : <Copy className="w-4 h-4" />}
        {copied ? "已複製,貼給 AI 對話" : "📋 複製健檢 prompt"}
      </button>
    </StCard>
  );
}

function HealthSkeleton({ ticker }: { ticker: string }) {
  return (
    <main className="min-h-dvh pb-12 bg-st-bg">
      <div className="sticky top-0 z-30 bg-st-bg/95 backdrop-blur-md border-b border-st-border">
        <div style={{ height: "env(safe-area-inset-top)" }} />
        <div className="px-4 py-3 flex items-center gap-2">
          <Stethoscope className="w-5 h-5 text-teal-300" />
          <div className="text-xs text-teal-300 font-mono">{ticker} · 健檢中⋯</div>
        </div>
      </div>
      <div className="px-4 pt-4 space-y-4">
        <div className="shimmer rounded-st h-32" />
        <div className="rounded-st border border-st-border p-5">
          <div className="shimmer h-5 w-24 rounded mb-4" />
          <div className="flex justify-center mb-4">
            <div className="shimmer rounded-full" style={{ width: 130, height: 130 }} />
          </div>
          <div className="grid grid-cols-3 gap-2">
            <div className="shimmer h-16 rounded" />
            <div className="shimmer h-16 rounded" />
            <div className="shimmer h-16 rounded" />
          </div>
        </div>
        <div className="shimmer rounded-st h-48" />
        <div className="shimmer rounded-st h-48" />
      </div>
    </main>
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
