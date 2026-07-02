"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { api } from "@/lib/api";

const TIME_OPTIONS = [
  { key: 5, label: "5D" },
  { key: 20, label: "1M" },
  { key: 60, label: "3M" },
  { key: 120, label: "6M" },
  { key: 250, label: "1Y" },
] as const;

const MODE_OPTIONS = [
  { key: "line", label: "折線" },
  { key: "candle", label: "K線" },
] as const;

type Mode = typeof MODE_OPTIONS[number]["key"];

/** 進階 chart — 時間尺度 5D/1M/3M/6M/1Y × 折線/K線 切換 */
export function AdvancedChart({ ticker }: { ticker: string }) {
  const [days, setDays] = useState<number>(60);
  const [mode, setMode] = useState<Mode>("line");

  const q = useQuery({
    queryKey: ["ohlcv", ticker, days],
    queryFn: () => api.getOhlcv(ticker, days),
    staleTime: 30 * 60_000,
  });

  const bars = q.data?.bars ?? [];

  return (
    <div>
      {/* toggles */}
      <div className="flex items-center gap-2 mb-3">
        <div className="flex rounded overflow-hidden" style={{ border: "1px solid #2f343d" }}>
          {TIME_OPTIONS.map(({ key, label }) => (
            <button
              key={key}
              onClick={() => setDays(key)}
              className="px-2.5 py-1 text-[11px] font-bold tabular-nums transition-colors"
              style={{
                background: days === key ? "color-mix(in srgb, var(--accent) 25%, #0f1218)" : "#0f1218",
                color: days === key ? "var(--accent)" : "#94a3b8",
                borderRight: "1px solid #2f343d",
              }}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="flex rounded overflow-hidden ml-auto" style={{ border: "1px solid #2f343d" }}>
          {MODE_OPTIONS.map(({ key, label }) => (
            <button
              key={key}
              onClick={() => setMode(key)}
              className="px-2.5 py-1 text-[11px] font-bold transition-colors"
              style={{
                background: mode === key ? "color-mix(in srgb, var(--accent) 25%, #0f1218)" : "#0f1218",
                color: mode === key ? "var(--accent)" : "#94a3b8",
                borderRight: "1px solid #2f343d",
              }}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* chart */}
      {q.isLoading && <div className="shimmer h-56 rounded" />}
      {!q.isLoading && bars.length >= 2 && (
        <motion.div
          key={`${days}-${mode}`}
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.25 }}
        >
          {mode === "line" ? <LineChart bars={bars} /> : <CandleChart bars={bars} />}
        </motion.div>
      )}
      {!q.isLoading && bars.length < 2 && (
        <div className="text-xs text-st-muted text-center py-8">
          資料抓取中或找不到 {ticker} 的 K 線
        </div>
      )}
    </div>
  );
}

type Bar = { date: string; open: number; high: number; low: number; close: number; volume: number };

function LineChart({ bars }: { bars: Bar[] }) {
  const W = 640, H = 220, PAD_L = 40, PAD_R = 8, PAD_T = 10, PAD_B = 28;
  const closes = bars.map((b) => b.close);
  const min = Math.min(...bars.map((b) => b.low));
  const max = Math.max(...bars.map((b) => b.high));
  const range = max - min || 1;
  const chartW = W - PAD_L - PAD_R;
  const chartH = H - PAD_T - PAD_B;
  const xAt = (i: number) => PAD_L + (i / (bars.length - 1)) * chartW;
  const yAt = (v: number) => PAD_T + (1 - (v - min) / range) * chartH;
  const up = closes[closes.length - 1] >= closes[0];
  const stroke = up ? "#ef4444" : "#10b981";
  const points = closes.map((v, i) => `${xAt(i)},${yAt(v)}`).join(" ");

  const grid = [0, 0.25, 0.5, 0.75, 1].map((r) => min + range * r);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" preserveAspectRatio="none">
      <defs>
        <linearGradient id="line-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={stroke} stopOpacity="0.25" />
          <stop offset="100%" stopColor={stroke} stopOpacity="0" />
        </linearGradient>
      </defs>
      {/* 網格 */}
      {grid.map((v, i) => (
        <g key={i}>
          <line x1={PAD_L} y1={yAt(v)} x2={W - PAD_R} y2={yAt(v)} stroke="#2a3340" strokeDasharray="2 3" />
          <text x={PAD_L - 4} y={yAt(v) + 3} textAnchor="end" fontSize="9" fill="#64748b">
            {v.toFixed(v > 100 ? 0 : 1)}
          </text>
        </g>
      ))}
      {/* 折線 + 漸層底 */}
      <polyline
        points={`${xAt(0)},${yAt(min)} ${points} ${xAt(bars.length - 1)},${yAt(min)}`}
        fill="url(#line-grad)"
        stroke="none"
      />
      <polyline points={points} stroke={stroke} strokeWidth="1.5" fill="none" strokeLinejoin="round" />
      {/* x label 首尾 */}
      <text x={PAD_L} y={H - 12} fontSize="9" fill="#64748b">{bars[0].date.slice(5)}</text>
      <text x={W - PAD_R} y={H - 12} textAnchor="end" fontSize="9" fill="#64748b">{bars[bars.length - 1].date.slice(5)}</text>
    </svg>
  );
}

function CandleChart({ bars }: { bars: Bar[] }) {
  const W = 640, H = 220, PAD_L = 40, PAD_R = 8, PAD_T = 10, PAD_B = 28;
  const min = Math.min(...bars.map((b) => b.low));
  const max = Math.max(...bars.map((b) => b.high));
  const range = max - min || 1;
  const chartW = W - PAD_L - PAD_R;
  const chartH = H - PAD_T - PAD_B;
  const yAt = (v: number) => PAD_T + (1 - (v - min) / range) * chartH;
  const step = chartW / bars.length;
  const bodyW = Math.max(1.5, step * 0.6);

  const grid = [0, 0.25, 0.5, 0.75, 1].map((r) => min + range * r);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" preserveAspectRatio="none">
      {/* 網格 */}
      {grid.map((v, i) => (
        <g key={i}>
          <line x1={PAD_L} y1={yAt(v)} x2={W - PAD_R} y2={yAt(v)} stroke="#2a3340" strokeDasharray="2 3" />
          <text x={PAD_L - 4} y={yAt(v) + 3} textAnchor="end" fontSize="9" fill="#64748b">
            {v.toFixed(v > 100 ? 0 : 1)}
          </text>
        </g>
      ))}
      {/* K 線 */}
      {bars.map((b, i) => {
        const cx = PAD_L + step * i + step / 2;
        const up = b.close >= b.open;
        const color = up ? "#ef4444" : "#10b981";
        const bodyTop = yAt(Math.max(b.open, b.close));
        const bodyBottom = yAt(Math.min(b.open, b.close));
        const bodyH = Math.max(0.6, bodyBottom - bodyTop);
        return (
          <g key={i}>
            {/* 上下影線 */}
            <line
              x1={cx} y1={yAt(b.high)}
              x2={cx} y2={yAt(b.low)}
              stroke={color} strokeWidth="1"
            />
            {/* 實體 */}
            <rect
              x={cx - bodyW / 2}
              y={bodyTop}
              width={bodyW}
              height={bodyH}
              fill={color}
              stroke={color}
            />
          </g>
        );
      })}
      <text x={PAD_L} y={H - 12} fontSize="9" fill="#64748b">{bars[0].date.slice(5)}</text>
      <text x={W - PAD_R} y={H - 12} textAnchor="end" fontSize="9" fill="#64748b">{bars[bars.length - 1].date.slice(5)}</text>
    </svg>
  );
}
