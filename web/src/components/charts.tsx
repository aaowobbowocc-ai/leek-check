"use client";

import { motion } from "framer-motion";
import type { OhlcvBar, RevHistory } from "@/lib/api";
import { formatNumber } from "@/lib/utils";

/* ────────────────────────────────────────
   PriceChart — 60 日收盤 + MA20 + MA60 overlay
   ──────────────────────────────────────── */
export function PriceChart({ bars }: { bars: OhlcvBar[] }) {
  if (bars.length < 2) {
    return (
      <div className="h-40 flex items-center justify-center text-st-muted text-xs">
        資料不足
      </div>
    );
  }
  const W = 320;
  const H = 140;
  const PAD = 4;
  const closes = bars.map((b) => b.close);
  const ma20s = bars.map((b) => b.ma20).filter((v) => v > 0);
  const ma60s = bars.map((b) => b.ma60).filter((v) => v > 0);
  const allVals = [...closes, ...ma20s, ...ma60s];
  const min = Math.min(...allVals);
  const max = Math.max(...allVals);
  const range = max - min || 1;

  const toXY = (v: number, i: number) =>
    `${PAD + (i / (bars.length - 1)) * (W - PAD * 2)},${H - PAD - ((v - min) / range) * (H - PAD * 2)}`;

  const closePath = bars.map((b, i) => toXY(b.close, i)).join(" ");
  const ma20Path = bars.map((b, i) => (b.ma20 > 0 ? toXY(b.ma20, i) : null)).filter(Boolean).join(" ");
  const ma60Path = bars.map((b, i) => (b.ma60 > 0 ? toXY(b.ma60, i) : null)).filter(Boolean).join(" ");

  const last = bars[bars.length - 1];
  const first = bars[0];
  const up = last.close >= first.close;
  const stroke = up ? "#ef4444" : "#10b981";
  const fill = up ? "rgba(239,68,68,0.12)" : "rgba(16,185,129,0.12)";

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-40" preserveAspectRatio="none">
        <defs>
          <linearGradient id="priceFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={fill} />
            <stop offset="100%" stopColor="transparent" />
          </linearGradient>
        </defs>
        {/* 收盤 area fill */}
        <polygon
          points={`${PAD},${H - PAD} ${closePath} ${W - PAD},${H - PAD}`}
          fill="url(#priceFill)"
        />
        {/* MA60 (黃線) */}
        {ma60Path && (
          <polyline points={ma60Path} stroke="#fbbf24" strokeWidth={1} fill="none" strokeDasharray="3,2" opacity={0.8} />
        )}
        {/* MA20 (藍線) */}
        {ma20Path && (
          <polyline points={ma20Path} stroke="#60a5fa" strokeWidth={1.2} fill="none" opacity={0.85} />
        )}
        {/* 收盤線 */}
        <polyline points={closePath} stroke={stroke} strokeWidth={1.8} fill="none" strokeLinejoin="round" />
      </svg>
      <div className="flex items-center justify-end gap-3 text-[10px] text-st-muted mt-1">
        <span className="flex items-center gap-1">
          <span className="w-3 h-0.5" style={{ background: stroke }} /> 收盤
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-0.5 bg-blue-400" /> MA20
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-0.5 bg-amber-300 border-dashed" style={{ borderTop: "1px dashed #fbbf24" }} /> MA60
        </span>
      </div>
    </div>
  );
}

/* ────────────────────────────────────────
   RevenueBarChart — 12 月 YoY bar
   ──────────────────────────────────────── */
export function RevenueBarChart({ data }: { data: RevHistory[] }) {
  if (data.length < 2) {
    return (
      <div className="h-32 flex items-center justify-center text-st-muted text-xs">
        資料不足
      </div>
    );
  }
  const W = 320;
  const H = 110;
  const PAD = 4;
  const BAR_GAP = 2;
  const yoys = data.map((d) => d.yoy);
  const absMax = Math.max(...yoys.map(Math.abs), 10);
  const barW = (W - PAD * 2) / data.length - BAR_GAP;
  const zeroY = H / 2;

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-28">
        {/* zero line */}
        <line x1={PAD} y1={zeroY} x2={W - PAD} y2={zeroY} stroke="#2f343d" strokeWidth={1} strokeDasharray="2,2" />
        {data.map((d, i) => {
          const x = PAD + i * (barW + BAR_GAP);
          const h = (Math.abs(d.yoy) / absMax) * (H / 2 - PAD);
          const y = d.yoy >= 0 ? zeroY - h : zeroY;
          const color = d.yoy >= 0 ? "#ef4444" : "#10b981";
          return (
            <motion.rect
              key={d.month}
              x={x}
              y={y}
              width={barW}
              height={h}
              fill={color}
              opacity={0.85}
              initial={{ height: 0, y: zeroY }}
              animate={{ height: h, y }}
              transition={{ delay: i * 0.03, duration: 0.4 }}
            />
          );
        })}
      </svg>
      <div className="flex justify-between text-[9px] text-st-muted px-1 mt-1">
        <span>{data[0].month}</span>
        <span>{data[data.length - 1].month}</span>
      </div>
    </div>
  );
}

/* ────────────────────────────────────────
   ChipStackedBar — 法人 3 色橫條
   ──────────────────────────────────────── */
export function ChipStackedBar({
  foreign, invtrust, dealer,
}: { foreign: number; invtrust: number; dealer: number }) {
  const total = Math.abs(foreign) + Math.abs(invtrust) + Math.abs(dealer);
  if (total === 0) {
    return <div className="text-xs text-st-muted">最近 20 日法人無動作</div>;
  }
  const items = [
    { label: "外資", net: foreign, color: "#5eead4" },
    { label: "投信", net: invtrust, color: "#fbbf24" },
    { label: "自營", net: dealer, color: "#a78bfa" },
  ];
  return (
    <div className="space-y-2">
      {items.map((it) => {
        const isUp = it.net > 0;
        const sign = isUp ? "+" : "";
        return (
          <div key={it.label}>
            <div className="flex items-center justify-between text-[11px] mb-1">
              <span className="text-st-soft font-bold">{it.label}</span>
              <span
                className="tabular-nums font-bold"
                style={{ color: isUp ? "#ef4444" : it.net < 0 ? "#10b981" : "#94a3b8" }}
              >
                {sign}{it.net.toLocaleString()} 張
              </span>
            </div>
            <div className="h-1.5 bg-ink-800 rounded-full overflow-hidden">
              <motion.div
                className="h-full rounded-full"
                initial={{ width: 0 }}
                animate={{ width: `${Math.min(100, (Math.abs(it.net) / Math.max(...items.map((x) => Math.abs(x.net)), 1)) * 100)}%` }}
                transition={{ duration: 0.5, ease: "easeOut" }}
                style={{ background: it.color }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ────────────────────────────────────────
   TechGrid — MA + KD + RSI 數值
   ──────────────────────────────────────── */
export function TechGrid({ tech }: { tech: NonNullable<import("@/lib/api").HealthCheck["tech"]> }) {
  const cells: { label: string; value: string; hint?: string; tint?: "up" | "down" | "neutral" }[] = [
    { label: "MA5", value: formatNumber(tech.ma5), tint: tech.price >= tech.ma5 ? "up" : "down" },
    { label: "MA20", value: formatNumber(tech.ma20), tint: tech.price >= tech.ma20 ? "up" : "down" },
    { label: "MA60", value: formatNumber(tech.ma60), tint: tech.price >= tech.ma60 ? "up" : "down" },
    { label: "MA200", value: formatNumber(tech.ma200), tint: tech.price >= tech.ma200 ? "up" : "down" },
    { label: "RSI(14)", value: tech.rsi.toFixed(1), hint: tech.rsi > 70 ? "超買" : tech.rsi < 30 ? "超賣" : "中性" },
    { label: "K(9)", value: tech.k.toFixed(1), hint: tech.k > tech.d ? "↑ 金叉" : "↓ 死叉" },
    { label: "D(9)", value: tech.d.toFixed(1) },
    { label: "成交", value: "—" },
  ];
  return (
    <div className="grid grid-cols-4 gap-1.5">
      {cells.map((c) => (
        <div
          key={c.label}
          className="rounded p-2 text-center"
          style={{
            background: "#0f1218",
            border: "1px solid #2f343d",
          }}
        >
          <div className="text-[9px] text-st-muted font-bold tracking-wider">{c.label}</div>
          <div
            className="tabular-nums font-extrabold mt-1"
            style={{
              fontSize: "0.9rem",
              color: c.tint === "up" ? "#ef4444" : c.tint === "down" ? "#10b981" : "#fff",
            }}
          >
            {c.value}
          </div>
          {c.hint && (
            <div className="text-[8px] text-st-muted mt-0.5">{c.hint}</div>
          )}
        </div>
      ))}
    </div>
  );
}

/* ────────────────────────────────────────
   FundaGrid — PER / PBR / yield / 月營收 YoY
   ──────────────────────────────────────── */
export function FundaGrid({ funda }: { funda: import("@/lib/api").HealthCheck["funda"] }) {
  const cells = [
    {
      label: "本益比 PER",
      value: funda.per != null ? funda.per.toFixed(1) : "—",
      hint: funda.per != null
        ? (funda.per < 15 ? "低估" : funda.per > 30 ? "偏高" : "合理")
        : undefined,
    },
    {
      label: "股價淨值比",
      value: funda.pbr != null ? funda.pbr.toFixed(2) : "—",
    },
    {
      label: "現金殖利率",
      value: funda.yield != null ? `${funda.yield.toFixed(2)}%` : "—",
      tint: (funda.yield ?? 0) > 4 ? "up" : undefined,
    },
    {
      label: "月營收 YoY",
      value: funda.rev_yoy != null ? `${funda.rev_yoy >= 0 ? "+" : ""}${funda.rev_yoy.toFixed(1)}%` : "—",
      tint: (funda.rev_yoy ?? 0) > 0 ? "up" : (funda.rev_yoy ?? 0) < 0 ? "down" : undefined,
    },
  ];
  return (
    <div className="grid grid-cols-2 gap-1.5">
      {cells.map((c) => (
        <div
          key={c.label}
          className="rounded p-2.5"
          style={{
            background: "#0f1218",
            border: "1px solid #2f343d",
          }}
        >
          <div className="text-[10px] text-st-muted">{c.label}</div>
          <div
            className="tabular-nums font-extrabold mt-1"
            style={{
              fontSize: "1.1rem",
              color: c.tint === "up" ? "#ef4444" : c.tint === "down" ? "#10b981" : "#fff",
            }}
          >
            {c.value}
          </div>
          {c.hint && <div className="text-[9px] text-amber-300 mt-0.5">{c.hint}</div>}
        </div>
      ))}
    </div>
  );
}
