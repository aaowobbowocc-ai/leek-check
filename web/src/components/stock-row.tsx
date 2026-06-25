"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useQuery } from "@tanstack/react-query";
import { Stethoscope, Settings as SettingsIcon, Loader2 } from "lucide-react";
import { StockPill } from "@/components/stock-pill";
import { cardTier, chgArrow } from "@/lib/tier";
import { formatNumber } from "@/lib/utils";
import { api, type Quote } from "@/lib/api";

type Props = {
  ticker: string;
  name: string;
  industry: string;
  quote?: Quote;
  hasHolding?: boolean;
  holding?: {
    shares: number;
    cost_per_share: number;
    pnl?: number;
    pnlPct?: number;
  };
  onOpen?: () => void;
  onEdit?: () => void;
  /** 晨報精選 toggle */
  isPicked?: boolean;
  onPin?: () => void;
  /** 加入觀察 toggle(熱門股 / 排行榜用)*/
  isInWatch?: boolean;
  onAddWatch?: () => void;
  defaultExpanded?: boolean;
};

export function StockRow({
  ticker, name, industry, quote, hasHolding, holding, onOpen, onEdit,
  isPicked, onPin, isInWatch, onAddWatch, defaultExpanded = false,
}: Props) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const { light } = cardTier(ticker, industry);
  // 防連點:500ms 內只能 click 一次
  const [pinBusy, setPinBusy] = useState(false);
  const [watchBusy, setWatchBusy] = useState(false);
  const guard = (fn: (() => void) | undefined, setBusy: (b: boolean) => void) => () => {
    if (!fn) return;
    setBusy(true);
    fn();
    setTimeout(() => setBusy(false), 500);
  };

  // Lazy load health-check 只在展開時 fetch
  const { data: hc, isLoading: hcLoading } = useQuery({
    queryKey: ["health-check", ticker],
    queryFn: () => api.getHealthCheck(ticker),
    enabled: expanded,
    staleTime: 5 * 60_000,  // 5 min cache
  });

  return (
    <div className="space-y-1">
      <StockPill
        ticker={ticker}
        name={name}
        industry={industry}
        quote={quote}
        expanded={expanded}
        hasHolding={hasHolding}
        onClick={() => setExpanded(!expanded)}
      />

      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.22, ease: "easeOut" }}
            className="overflow-hidden"
          >
            <div
              className="rounded-st p-3 space-y-3"
              style={{
                background: "#16181d",
                border: "1px solid #2f343d",
                borderLeft: `3px solid ${light}`,
                boxShadow: [
                  "inset 0 1px 0 rgba(255,255,255,0.05)",
                  "inset 0 -1px 0 rgba(0,0,0,0.3)",
                ].join(", "),
              }}
            >
              {/* 第 1 列:產業 + 日期 */}
              <div className="flex items-center justify-between text-[10px] text-st-muted">
                <span>🏷️ {industry || "—"}</span>
                {quote && <span className="tabular-nums">{quote.asof}</span>}
              </div>

              {/* 第 2 列:OHLCV 4 cells */}
              {quote && (
                <div className="grid grid-cols-4 gap-1 text-[10px]">
                  <Mini label="開" value={formatNumber(quote.open, 1)} />
                  <Mini label="高" value={formatNumber(quote.high, 1)} tint="up" />
                  <Mini label="低" value={formatNumber(quote.low, 1)} tint="down" />
                  <Mini label="量" value={fmtVol(quote.volume)} />
                </div>
              )}

              {/* 迷你股價走勢圖(20 日)*/}
              {hc && hc.sparkline.length > 0 && (
                <MiniChart data={hc.sparkline} />
              )}

              {/* 第 3 列:健檢分數 + 4 維度迷你 bar */}
              {hcLoading && (
                <div className="flex items-center gap-2 text-[10px] text-st-muted">
                  <Loader2 className="w-3 h-3 animate-spin" /> 健檢中⋯
                </div>
              )}
              {hc && (
                <HealthMiniRow hc={hc} />
              )}

              {/* 第 4 列:技術 + 基本面 key metrics */}
              {hc && hc.tech && (
                <div className="grid grid-cols-4 gap-1 text-[10px]">
                  <Mini
                    label="距 MA20"
                    value={`${(((hc.tech.price / hc.tech.ma20) - 1) * 100).toFixed(1)}%`}
                    tint={hc.tech.price >= hc.tech.ma20 ? "up" : "down"}
                  />
                  <Mini
                    label="距 MA60"
                    value={`${(((hc.tech.price / hc.tech.ma60) - 1) * 100).toFixed(1)}%`}
                    tint={hc.tech.price >= hc.tech.ma60 ? "up" : "down"}
                  />
                  <Mini
                    label="RSI"
                    value={hc.tech.rsi.toFixed(0)}
                    tint={hc.tech.rsi > 70 ? "up" : hc.tech.rsi < 30 ? "down" : undefined}
                    sub={hc.tech.rsi > 70 ? "超買" : hc.tech.rsi < 30 ? "超賣" : "中性"}
                  />
                  <Mini
                    label="KD"
                    value={`${hc.tech.k.toFixed(0)}/${hc.tech.d.toFixed(0)}`}
                    sub={hc.tech.k > hc.tech.d ? "↑金叉" : "↓死叉"}
                  />
                </div>
              )}

              {/* 第 5 列:基本面 + 法人(如有)*/}
              {hc && (hc.funda?.per != null || hc.funda?.rev_yoy != null || hc.chip) && (
                <div className="grid grid-cols-3 gap-1 text-[10px]">
                  {hc.funda?.per != null && (
                    <Mini label="本益比" value={hc.funda.per.toFixed(1)} />
                  )}
                  {hc.funda?.rev_yoy != null && (
                    <Mini
                      label="月營收 YoY"
                      value={`${hc.funda.rev_yoy >= 0 ? "+" : ""}${hc.funda.rev_yoy.toFixed(1)}%`}
                      tint={hc.funda.rev_yoy >= 0 ? "up" : "down"}
                    />
                  )}
                  {hc.chip && (
                    <Mini
                      label="外資 20d"
                      value={`${hc.chip.foreign_20d >= 0 ? "+" : ""}${(hc.chip.foreign_20d / 1000).toFixed(1)}K`}
                      tint={hc.chip.foreign_20d > 0 ? "up" : hc.chip.foreign_20d < 0 ? "down" : undefined}
                    />
                  )}
                </div>
              )}

              {/* 持股 row(如有)*/}
              {holding && (
                <div
                  className="rounded p-2 grid grid-cols-3 gap-1 text-[10px]"
                  style={{
                    background: "#0f1218",
                    border: "1px solid #2f343d",
                  }}
                >
                  <Mini label="股數" value={holding.shares.toLocaleString()} />
                  <Mini label="成本" value={formatNumber(holding.cost_per_share, 1)} />
                  <Mini
                    label="損益"
                    value={`${(holding.pnl ?? 0) >= 0 ? "+" : ""}${formatNumber(holding.pnl ?? 0, 0)}`}
                    tint={(holding.pnl ?? 0) >= 0 ? "up" : "down"}
                    sub={`${chgArrow(holding.pnlPct ?? 0)} ${(holding.pnlPct ?? 0).toFixed(2)}%`}
                  />
                </div>
              )}

              {/* Action buttons — ⭐ 加觀察 + 📰 加晨報 + 編輯 同一排 */}
              <div className="flex gap-1.5 pt-1">
                {/* 主按鈕:翻開健檢 — frosted glass accent (跟 btn-smart 同風格) */}
                <motion.button
                  onClick={onOpen}
                  whileTap={{ scale: 0.97 }}
                  className="flex-1 relative overflow-hidden rounded-st py-2.5 px-3 text-sm font-bold flex items-center justify-center gap-2 transition-all"
                  style={{
                    background: [
                      "linear-gradient(135deg, color-mix(in srgb, var(--accent) 18%, transparent) 0%, color-mix(in srgb, var(--accent-mid) 10%, transparent) 50%, color-mix(in srgb, var(--accent-deep) 8%, transparent) 100%)",
                    ].join(", "),
                    border: "1px solid color-mix(in srgb, var(--accent) 50%, transparent)",
                    color: "var(--accent)",
                    boxShadow: [
                      "0 0 20px var(--accent-glow)",
                      "inset 0 1px 0 rgba(255,255,255,0.1)",
                      "inset 0 -1px 0 rgba(0,0,0,0.3)",
                    ].join(", "),
                    backdropFilter: "blur(8px)",
                  }}
                >
                  <Stethoscope className="w-4 h-4" strokeWidth={2.5} />
                  <span className="tracking-wide">翻開完整健檢</span>
                  <motion.span
                    className="text-base opacity-70"
                    animate={{ x: [0, 3, 0] }}
                    transition={{ duration: 1.4, repeat: Infinity, ease: "easeInOut" }}
                  >
                    →
                  </motion.span>
                </motion.button>

                {/* 副按鈕:編輯 */}
                {/* ⭐ 加觀察 */}
                {onAddWatch && (
                  <motion.button
                    onClick={guard(onAddWatch, setWatchBusy)}
                    disabled={watchBusy}
                    whileTap={{ scale: 0.9 }}
                    className="rounded-st px-2.5 py-2 flex items-center justify-center text-base disabled:opacity-50"
                    style={{
                      background: isInWatch
                        ? "linear-gradient(180deg, color-mix(in srgb, var(--accent) 40%, transparent), color-mix(in srgb, var(--accent-deep) 30%, transparent))"
                        : "linear-gradient(180deg, #1c2028, #11141a)",
                      border: `1px solid ${isInWatch ? "var(--accent)" : "#2a3340"}`,
                      color: isInWatch ? "var(--accent)" : "#64748b",
                      boxShadow: isInWatch
                        ? "0 0 12px var(--accent-glow), inset 0 1px 0 rgba(255,255,255,0.15)"
                        : "inset 0 1px 0 rgba(255,255,255,0.06)",
                    }}
                    title={isInWatch ? "已在觀察清單(點移除)" : "加入觀察清單"}
                  >
                    {isInWatch ? "★" : "☆"}
                  </motion.button>
                )}
                {/* 📰 加晨報 */}
                {onPin && (
                  <motion.button
                    onClick={guard(onPin, setPinBusy)}
                    disabled={pinBusy}
                    whileTap={{ scale: 0.9 }}
                    className="rounded-st px-2.5 py-2 flex items-center justify-center text-base disabled:opacity-50"
                    style={{
                      background: isPicked
                        ? "linear-gradient(180deg, color-mix(in srgb, var(--accent) 40%, transparent), color-mix(in srgb, var(--accent-deep) 30%, transparent))"
                        : "linear-gradient(180deg, #1c2028, #11141a)",
                      border: `1px solid ${isPicked ? "var(--accent)" : "#2a3340"}`,
                      filter: isPicked ? "none" : "grayscale(1) opacity(0.5)",
                      boxShadow: isPicked
                        ? "0 0 12px var(--accent-glow), inset 0 1px 0 rgba(255,255,255,0.15)"
                        : "inset 0 1px 0 rgba(255,255,255,0.06)",
                    }}
                    title={isPicked ? "已加入晨報精選 (點再次移除)" : "加入晨報精選 (最多 5 檔)"}
                  >
                    📰
                  </motion.button>
                )}
                {onEdit && (
                  <motion.button
                    onClick={onEdit}
                    whileTap={{ scale: 0.96 }}
                    className="rounded-st px-3 text-xs font-semibold text-st-muted flex items-center justify-center gap-1.5"
                    style={{
                      background: "linear-gradient(180deg, #1c2028, #11141a)",
                      border: "1px solid #2a3340",
                      boxShadow: "inset 0 1px 0 rgba(255,255,255,0.08), inset 0 -1px 0 rgba(0,0,0,0.4)",
                    }}
                  >
                    <SettingsIcon className="w-3.5 h-3.5" /> 編輯
                  </motion.button>
                )}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function MiniChart({ data }: { data: number[] }) {
  if (data.length < 2) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const ML = 28, MR = 4, MT = 2, MB = 12;
  const W = 320, H = 70;
  const plotW = W - ML - MR;
  const plotH = H - MT - MB;
  const points = data
    .map((v, i) => `${ML + (i / (data.length - 1)) * plotW},${MT + plotH - ((v - min) / range) * plotH}`)
    .join(" ");
  const first = data[0];
  const last = data[data.length - 1];
  const up = last >= first;
  const stroke = up ? "#ef4444" : "#10b981";
  const fill = up ? "rgba(239,68,68,0.18)" : "rgba(16,185,129,0.18)";
  const chg = ((last / first - 1) * 100);

  return (
    <div
      className="rounded p-2"
      style={{ background: "#0f1218", border: "1px solid #2f343d" }}
    >
      <div className="flex items-center justify-between mb-1 text-[10px]">
        <span className="text-st-muted font-bold tracking-wider">📈 20 日走勢</span>
        <span className="tabular-nums font-bold" style={{ color: stroke }}>
          {up ? "▲" : "▼"} {Math.abs(chg).toFixed(2)}%
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: H }}>
        <defs>
          <linearGradient id="miniGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={fill} />
            <stop offset="100%" stopColor="transparent" />
          </linearGradient>
        </defs>
        {/* Y labels (high / low) */}
        <text x={ML - 4} y={MT + 7} textAnchor="end" className="tabular-nums" fill="#94a3b8" fontSize="8">
          {max.toFixed(1)}
        </text>
        <text x={ML - 4} y={MT + plotH} textAnchor="end" className="tabular-nums" fill="#94a3b8" fontSize="8">
          {min.toFixed(1)}
        </text>
        {/* X labels */}
        <text x={ML} y={H - 3} textAnchor="start" fill="#94a3b8" fontSize="8">
          -20d
        </text>
        <text x={W - MR} y={H - 3} textAnchor="end" fill="#94a3b8" fontSize="8">
          今日
        </text>
        {/* Baseline */}
        <line x1={ML} y1={MT + plotH} x2={W - MR} y2={MT + plotH} stroke="#2f343d" strokeWidth={0.5} />
        {/* Area + line */}
        <polygon
          points={`${ML},${MT + plotH} ${points} ${W - MR},${MT + plotH}`}
          fill="url(#miniGrad)"
        />
        <polyline points={points} stroke={stroke} strokeWidth={1.5} fill="none" strokeLinejoin="round" />
      </svg>
    </div>
  );
}

function HealthMiniRow({ hc }: { hc: NonNullable<ReturnType<typeof useQuery<import("@/lib/api").HealthCheck>>["data"]> }) {
  const composite = hc.health.composite;
  const color = composite >= 70 ? "#5eead4" : composite >= 50 ? "#fbbf24" : "#f43f5e";
  const verdict = composite >= 70 ? "健康" : composite >= 50 ? "亞健康" : "韭菜病";

  // 完整 4 維度 + 權重 + 第一條 bullet 解讀
  const dims = [
    { key: "tech", label: "技術面", weight: 40, val: hc.health.scores.technical.score, hint: hc.health.scores.technical.notes[0] ?? "—" },
    { key: "chip", label: "籌碼面", weight: 30, val: hc.health.scores.chip.score, hint: hc.health.scores.chip.notes[0] ?? "—" },
    { key: "funda", label: "基本面", weight: 20, val: hc.health.scores.fundamental.score, hint: hc.health.scores.fundamental.notes[0] ?? "—" },
    { key: "news", label: "新聞面", weight: 10, val: hc.health.scores.news.score, hint: hc.health.scores.news.notes[0] ?? "—" },
  ];

  return (
    <div
      className="rounded p-2.5 space-y-2"
      style={{ background: "#0f1218", border: "1px solid #2f343d" }}
    >
      {/* 上半:總分 + verdict */}
      <div className="flex items-center gap-3">
        <div className="text-center flex-shrink-0 px-1">
          <div className="text-[9px] text-st-muted font-bold tracking-wider">健檢</div>
          <div className="tabular-nums font-extrabold" style={{ fontSize: "1.7rem", color, lineHeight: 1 }}>
            {composite}
          </div>
          <div className="text-[10px] font-bold mt-0.5" style={{ color }}>{verdict}</div>
        </div>
        <div className="flex-1 text-[10px] text-st-muted leading-snug border-l border-st-border pl-3">
          {composite >= 70
            ? "✓ 4 面綜合判讀體質健康,可關注進場時機"
            : composite >= 50
            ? "⚠️ 體質普通,部分維度需注意"
            : "🚨 體質偏弱,留意風險(韭菜病警示)"}
        </div>
      </div>

      {/* 下半:4 維度 — 名稱 + 權重 + 分數 + 第 1 條 bullet */}
      <div className="space-y-1.5 pt-2 border-t border-st-border">
        {dims.map((d) => {
          const dColor = d.val >= 70 ? "#5eead4" : d.val >= 50 ? "#fbbf24" : "#f43f5e";
          return (
            <div key={d.key} className="space-y-0.5">
              <div className="flex items-center gap-1.5">
                <span className="text-[10px] font-bold text-st-soft w-12 flex-shrink-0">
                  {d.label}
                </span>
                <span className="text-[8px] text-st-muted w-7 flex-shrink-0">
                  ({d.weight}%)
                </span>
                <div className="flex-1 h-1 bg-ink-800 rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all duration-700"
                    style={{ width: `${Math.max(2, d.val)}%`, background: dColor }}
                  />
                </div>
                <span className="tabular-nums text-[11px] font-extrabold w-7 text-right flex-shrink-0" style={{ color: dColor }}>
                  {d.val}
                </span>
              </div>
              {d.hint && d.hint !== "—" && (
                <div className="text-[9px] text-st-muted pl-[5.25rem] truncate">
                  {d.hint}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Mini({ label, value, tint, sub }: { label: string; value: string; tint?: "up" | "down"; sub?: string }) {
  const color = tint === "up" ? "#ef4444" : tint === "down" ? "#10b981" : "#fff";
  return (
    <div className="text-center">
      <div className="text-[9px] text-st-muted">{label}</div>
      <div className="tabular-nums font-bold mt-0.5" style={{ color, fontSize: "0.8rem" }}>{value}</div>
      {sub && <div className="tabular-nums text-[8px] mt-0.5" style={{ color }}>{sub}</div>}
    </div>
  );
}

function fmtVol(v: number): string {
  if (v >= 100_000_000) return `${(v / 100_000_000).toFixed(1)}億`;
  if (v >= 10_000) return `${(v / 10_000).toFixed(1)}萬`;
  return v.toLocaleString();
}
