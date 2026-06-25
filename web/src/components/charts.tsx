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
  // 軸標保留 margin
  const W = 320;
  const H = 160;
  const ML = 36;  // Y 軸左邊空間
  const MR = 4;
  const MT = 4;
  const MB = 22;  // X 軸下方空間
  const plotW = W - ML - MR;
  const plotH = H - MT - MB;

  const closes = bars.map((b) => b.close);
  const ma20s = bars.map((b) => b.ma20).filter((v) => v > 0);
  const ma60s = bars.map((b) => b.ma60).filter((v) => v > 0);
  const allVals = [...closes, ...ma20s, ...ma60s];
  const min = Math.min(...allVals);
  const max = Math.max(...allVals);
  const range = max - min || 1;
  const mid = (min + max) / 2;

  const toXY = (v: number, i: number) => {
    const x = ML + (i / (bars.length - 1)) * plotW;
    const y = MT + plotH - ((v - min) / range) * plotH;
    return `${x},${y}`;
  };

  const closePath = bars.map((b, i) => toXY(b.close, i)).join(" ");
  const ma20Path = bars.map((b, i) => (b.ma20 > 0 ? toXY(b.ma20, i) : null)).filter(Boolean).join(" ");
  const ma60Path = bars.map((b, i) => (b.ma60 > 0 ? toXY(b.ma60, i) : null)).filter(Boolean).join(" ");

  const last = bars[bars.length - 1];
  const first = bars[0];
  const up = last.close >= first.close;
  const stroke = up ? "#ef4444" : "#10b981";
  const fill = up ? "rgba(239,68,68,0.12)" : "rgba(16,185,129,0.12)";

  // Y 軸 3 個刻度(min / mid / max)
  const yTicks = [
    { v: max, y: MT },
    { v: mid, y: MT + plotH / 2 },
    { v: min, y: MT + plotH },
  ];
  // X 軸 4 個刻度(start / 1/3 / 2/3 / end)
  const xIdx = [0, Math.floor(bars.length / 3), Math.floor((bars.length * 2) / 3), bars.length - 1];
  const xTicks = xIdx.map((i) => ({
    date: bars[i].date.slice(5),  // MM-DD
    x: ML + (i / (bars.length - 1)) * plotW,
  }));

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: H }}>
        <defs>
          <linearGradient id="priceFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={fill} />
            <stop offset="100%" stopColor="transparent" />
          </linearGradient>
        </defs>

        {/* Y axis grid lines + labels */}
        {yTicks.map((t, i) => (
          <g key={i}>
            <line
              x1={ML} y1={t.y} x2={W - MR} y2={t.y}
              stroke="#2f343d" strokeWidth={0.5} strokeDasharray="2,3"
            />
            <text
              x={ML - 4} y={t.y + 3}
              textAnchor="end"
              className="tabular-nums"
              fill="#94a3b8" fontSize="9"
            >
              {t.v.toFixed(1)}
            </text>
          </g>
        ))}

        {/* X axis labels */}
        {xTicks.map((t, i) => (
          <text
            key={i}
            x={t.x} y={H - 6}
            textAnchor={i === 0 ? "start" : i === xTicks.length - 1 ? "end" : "middle"}
            className="tabular-nums"
            fill="#94a3b8" fontSize="9"
          >
            {t.date}
          </text>
        ))}

        {/* Plot area border */}
        <line x1={ML} y1={MT + plotH} x2={W - MR} y2={MT + plotH} stroke="#2f343d" strokeWidth={0.8} />

        {/* 收盤 area fill */}
        <polygon
          points={`${ML},${MT + plotH} ${closePath} ${W - MR},${MT + plotH}`}
          fill="url(#priceFill)"
        />
        {/* MA60 黃線 */}
        {ma60Path && (
          <polyline points={ma60Path} stroke="#fbbf24" strokeWidth={1} fill="none" strokeDasharray="3,2" opacity={0.85} />
        )}
        {/* MA20 藍線 */}
        {ma20Path && (
          <polyline points={ma20Path} stroke="#60a5fa" strokeWidth={1.2} fill="none" opacity={0.9} />
        )}
        {/* 收盤線 */}
        <polyline points={closePath} stroke={stroke} strokeWidth={1.8} fill="none" strokeLinejoin="round" />
      </svg>
      <div className="flex items-center justify-end gap-3 text-[10px] text-st-muted">
        <span className="flex items-center gap-1">
          <span className="w-3 h-0.5" style={{ background: stroke }} /> 收盤
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-0.5 bg-blue-400" /> MA20
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-px bg-amber-300" /> MA60
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
  const H = 140;
  const ML = 30;
  const MR = 4;
  const MT = 4;
  const MB = 22;
  const plotW = W - ML - MR;
  const plotH = H - MT - MB;
  const BAR_GAP = 2;
  const yoys = data.map((d) => d.yoy);
  const absMax = Math.max(...yoys.map(Math.abs), 10);
  const barW = plotW / data.length - BAR_GAP;
  const zeroY = MT + plotH / 2;

  // Y 軸 3 個刻度
  const yTicks = [
    { v: absMax, y: MT },
    { v: 0, y: zeroY },
    { v: -absMax, y: MT + plotH },
  ];
  // X 軸 4 個月份標
  const xIdx = [0, Math.floor(data.length / 3), Math.floor((data.length * 2) / 3), data.length - 1];

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: H }}>
        {/* Y axis */}
        {yTicks.map((t, i) => (
          <g key={i}>
            <line
              x1={ML} y1={t.y} x2={W - MR} y2={t.y}
              stroke={t.v === 0 ? "#475569" : "#2f343d"}
              strokeWidth={t.v === 0 ? 0.8 : 0.5}
              strokeDasharray={t.v === 0 ? "0" : "2,3"}
            />
            <text
              x={ML - 4} y={t.y + 3}
              textAnchor="end"
              className="tabular-nums"
              fill="#94a3b8" fontSize="9"
            >
              {t.v >= 0 ? "+" : ""}{t.v.toFixed(0)}%
            </text>
          </g>
        ))}

        {/* Bars */}
        {data.map((d, i) => {
          const x = ML + i * (barW + BAR_GAP);
          const h = (Math.abs(d.yoy) / absMax) * (plotH / 2);
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

        {/* X labels */}
        {xIdx.map((i, n) => {
          const x = ML + i * (barW + BAR_GAP) + barW / 2;
          return (
            <text
              key={n}
              x={x} y={H - 6}
              textAnchor="middle"
              className="tabular-nums"
              fill="#94a3b8" fontSize="9"
            >
              {data[i].month.split("/")[1]}/{data[i].month.split("/")[0].slice(-2)}
            </text>
          );
        })}
      </svg>
    </div>
  );
}

/* ────────────────────────────────────────
   HealthScanGrid — 體質掃描 4 維度連續方向圖
   ──────────────────────────────────────── */
export function HealthScanGrid({
  rev12, eps, gpm, current,
}: {
  rev12?: RevHistory[];           // 月營收 12 期(我們有)
  eps?: { period: string; value: number }[] | null;
  gpm?: { period: string; value: number }[] | null;
  current?: { period: string; value: number }[] | null;
}) {
  const dims: Array<{ key: string; emoji: string; label: string; data: { period: string; value: number }[] | undefined | null; unit: string }> = [
    {
      key: "rev",
      emoji: "📈",
      label: "接單能力",
      data: rev12?.map((r) => ({ period: r.month, value: r.yoy })),
      unit: "%",
    },
    { key: "eps", emoji: "💵", label: "獲利能力", data: eps, unit: "" },
    { key: "gpm", emoji: "🛠️", label: "經營能力", data: gpm, unit: "%" },
    { key: "current", emoji: "🏦", label: "償債能力", data: current, unit: "倍" },
  ];

  return (
    <div className="grid grid-cols-2 gap-2">
      {dims.map((d) => {
        const data = d.data ?? [];
        const last6 = data.slice(-6);
        const direction = last6.length >= 2
          ? (last6[last6.length - 1].value >= last6[0].value ? "up" : "down")
          : "flat";
        const arrows = last6.map((p, i) => {
          if (i === 0) return "·";
          const prev = last6[i - 1].value;
          if (p.value > prev) return "↑";
          if (p.value < prev) return "↓";
          return "→";
        });
        const c = direction === "up" ? "#ef4444" : direction === "down" ? "#10b981" : "#94a3b8";
        return (
          <div
            key={d.key}
            className="rounded p-2.5"
            style={{ background: "#0f1218", border: "1px solid #2f343d", borderLeft: `3px solid ${c}` }}
          >
            <div className="text-[10px] text-st-muted font-bold tracking-wider">
              {d.emoji} {d.label}
            </div>
            {last6.length > 0 ? (
              <>
                <div className="tabular-nums font-extrabold mt-1" style={{ fontSize: "0.95rem", color: c }}>
                  {arrows.join(" ")}
                </div>
                <div className="tabular-nums text-[10px] text-st-soft">
                  最新 {last6[last6.length - 1].value.toFixed(2)}{d.unit}
                </div>
              </>
            ) : (
              <div className="text-[10px] text-st-muted mt-1">資料不足</div>
            )}
          </div>
        );
      })}
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
