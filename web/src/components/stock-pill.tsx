"use client";

import { motion } from "framer-motion";
import { cardTier, chgColor, chgArrow } from "@/lib/tier";
import { formatNumber } from "@/lib/utils";
import type { Quote } from "@/lib/api";
import { ChevronDown } from "lucide-react";

type Props = {
  ticker: string;
  name: string;
  industry: string;
  quote?: Quote;
  expanded?: boolean;
  hasHolding?: boolean;
  onClick?: () => void;
};

/**
 * 緊湊膠囊列(Level 1)— 金屬質感 + 銳利邊框
 *
 * 金屬效果 = 3 層 inset shadow + tier 漸層左邊條:
 *   inset 0 1px 0 rgba(255,255,255,0.08)  ← 頂高光(metal highlight)
 *   inset 0 -1px 0 rgba(0,0,0,0.45)       ← 底陰影(metal depth)
 *   inset 0 0 0 1px rgba(255,255,255,0.04) ← 內框微亮
 *
 * 銳利 = 1px solid 不模糊 + 不用 box-shadow blur
 */
export function StockPill({
  ticker, name, industry, quote, expanded, hasHolding, onClick,
}: Props) {
  const { light, dark, icon, rarity } = cardTier(ticker, industry);
  const chg = quote ? quote.change_pct : 0;
  const c = chgColor(chg);
  const arrow = chgArrow(chg);
  const isLeg = rarity === "LEGENDARY";

  return (
    <motion.button
      onClick={onClick}
      whileTap={{ scale: 0.99 }}
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      className="w-full text-left rounded-st flex items-center gap-3 px-3 py-2.5 relative overflow-hidden transition-colors"
      style={{
        background: `linear-gradient(180deg, #1c2028 0%, #16181d 50%, #11141a 100%)`,
        border: `1px solid ${expanded ? light : "#3a4150"}`,
        boxShadow: [
          "inset 0 1px 0 rgba(255,255,255,0.08)",
          "inset 0 -1px 0 rgba(0,0,0,0.45)",
          "inset 0 0 0 1px rgba(255,255,255,0.02)",
        ].join(", "),
      }}
    >
      {/* 左側 tier 金屬色條(linear gradient = 金屬光澤)*/}
      <div
        className="absolute left-0 top-0 bottom-0 w-1"
        style={{
          background: `linear-gradient(180deg, ${light} 0%, ${dark} 50%, ${light} 100%)`,
          boxShadow: `0 0 4px ${light}88, inset -1px 0 0 rgba(0,0,0,0.3)`,
        }}
      />

      {/* LEGENDARY 上方金屬光帶 */}
      {isLeg && (
        <div
          className="absolute top-0 left-1 right-0 h-px"
          style={{
            background: `linear-gradient(90deg, transparent, ${light}, transparent)`,
            opacity: 0.85,
          }}
        />
      )}

      {/* Icon + ticker + name */}
      <div className="flex items-center gap-2.5 flex-1 min-w-0 pl-1.5">
        <span style={{ fontSize: "1.2rem", lineHeight: 1 }}>{icon}</span>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <span
              className="tabular-nums"
              style={{
                fontSize: "0.95rem",
                color: "#fff",
                fontWeight: 700,
                letterSpacing: 0.5,
              }}
            >
              {ticker}
            </span>
            {hasHolding && (
              <span
                className="text-[9px] font-bold tracking-widest"
                style={{ color: light }}
              >
                · 持股
              </span>
            )}
          </div>
          <div
            className="truncate"
            style={{ fontSize: "0.72rem", color: "#94a3b8", marginTop: 1 }}
          >
            {name || industry || ticker}
          </div>
        </div>
      </div>

      {/* 價 + % */}
      <div className="text-right flex-shrink-0">
        {quote ? (
          <>
            <div
              className="tabular-nums"
              style={{ fontSize: "0.95rem", color: c, fontWeight: 700, lineHeight: 1 }}
            >
              {formatNumber(quote.price)}
            </div>
            <div
              className="tabular-nums whitespace-nowrap"
              style={{ fontSize: "0.7rem", color: c, marginTop: 2 }}
            >
              {arrow} {Math.abs(chg).toFixed(2)}%
            </div>
          </>
        ) : (
          <div className="shimmer h-4 w-14 rounded" />
        )}
      </div>

      {/* Expand chevron */}
      <motion.div
        animate={{ rotate: expanded ? 180 : 0 }}
        transition={{ duration: 0.2 }}
        className="flex-shrink-0 ml-1"
      >
        <ChevronDown className="w-3.5 h-3.5 text-st-muted" />
      </motion.div>
    </motion.button>
  );
}
