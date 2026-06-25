"use client";

import { motion } from "framer-motion";
import { cardTier, chgColor, chgArrow } from "@/lib/tier";
import { formatNumber } from "@/lib/utils";
import type { Quote } from "@/lib/api";

type Props = {
  ticker: string;
  name: string;
  industry: string;
  quote?: Quote;
  avgValueYi?: number;
  rankMedal?: string;
  onClick?: () => void;
};

/**
 * 1:1 抄 streamlit app/app.py:3403-3425 那張卡:
 * - linear-gradient(155deg, {dark} 0%, #1a1d24 50%, #16181d 100%)
 * - border 2px solid {light}
 * - box-shadow: 0 4px 14px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.08)
 * - 3 欄 flex:Icon+Tier / 代碼+名+業 / 價格+漲跌
 * - 台股紅漲綠跌
 */
export function StockCard({
  ticker, name, industry, quote, avgValueYi, rankMedal, onClick,
}: Props) {
  const { light, dark, icon, rarity } = cardTier(ticker, industry, avgValueYi);
  const isLegendary = rarity === "LEGENDARY";
  const chg = quote ? quote.change_pct : 0;
  const c = chgColor(chg);
  const arrow = chgArrow(chg);
  const priceStr = quote ? formatNumber(quote.price) : "—";
  const chgStr = quote
    ? `${arrow} ${Math.abs(quote.price - quote.prev_close).toFixed(2)} (${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%)`
    : "—";

  return (
    <motion.button
      onClick={onClick}
      whileTap={{ scale: 0.98 }}
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      className={`w-full text-left rounded-st flex items-center gap-3.5 px-4 py-3.5 relative overflow-hidden mb-1.5 ${isLegendary ? "legendary-border" : ""}`}
      style={
        isLegendary
          ? {
              boxShadow: [
                "0 4px 14px rgba(0,0,0,0.4)",
                "inset 0 1px 0 rgba(255,255,255,0.18)",
                "inset 0 -1px 0 rgba(0,0,0,0.4)",
                "0 0 24px rgba(252, 211, 77, 0.15)",
              ].join(", "),
            }
          : {
              background: [
                // 對角反光帶
                "linear-gradient(105deg, transparent 35%, rgba(255,255,255,0.06) 50%, transparent 65%)",
                // 頂部弧光
                "radial-gradient(ellipse 80% 50% at 50% 0%, rgba(255,255,255,0.08), transparent 65%)",
                // 底色(155° tier 漸層)
                `linear-gradient(155deg, ${dark} 0%, #1a1d24 50%, #16181d 100%)`,
              ].join(", "),
              border: `1px solid ${light}`,
              boxShadow: [
                "0 4px 14px rgba(0,0,0,0.45)",
                "inset 0 1px 0 rgba(255,255,255,0.18)",
                "inset 0 -1px 0 rgba(0,0,0,0.5)",
                "inset 0 0 0 1px rgba(255,255,255,0.04)",
              ].join(", "),
            }
      }
    >
      {/* 左:icon + tier */}
      <div className="flex-shrink-0 text-center min-w-[60px]">
        <div style={{ fontSize: "2rem", lineHeight: 1 }}>{icon}</div>
        <div
          className="mt-1 inline-block"
          style={{
            background: "rgba(0,0,0,0.45)",
            padding: "2px 6px",
            borderRadius: 5,
            fontSize: "0.55rem",
            color: light,
            letterSpacing: 1,
            fontWeight: 700,
          }}
        >
          {rarity}
        </div>
      </div>

      {/* 中:代碼 + 名 + 業 */}
      <div className="flex-1 min-w-0">
        {rankMedal && (
          <div style={{ fontSize: "0.7rem", color: "#cbd5e1", marginBottom: 2 }}>
            <span
              style={{
                background: "rgba(0,0,0,0.45)",
                padding: "2px 8px",
                borderRadius: 5,
                fontWeight: 700,
              }}
            >
              {rankMedal}
            </span>
          </div>
        )}
        <div
          style={{
            fontSize: "1.4rem",
            color: "#fff",
            fontWeight: 800,
            letterSpacing: 1,
            lineHeight: 1,
          }}
        >
          {ticker}
        </div>
        <div
          className="truncate"
          style={{
            fontSize: "0.9rem",
            color: "#e4e6eb",
            marginTop: 3,
            fontWeight: 600,
          }}
        >
          {name || ticker}
        </div>
        <div
          className="truncate"
          style={{ fontSize: "0.7rem", color: light, marginTop: 2 }}
        >
          {industry || "—"}
        </div>
      </div>

      {/* 右:價格 + 漲跌 */}
      <div className="text-right flex-shrink-0 min-w-[90px]">
        <div
          className="tabular-nums"
          style={{
            fontSize: "1.4rem",
            color: c,
            fontWeight: 700,
            lineHeight: 1,
          }}
        >
          {priceStr}
        </div>
        <div
          className="whitespace-nowrap"
          style={{ fontSize: "0.75rem", color: c, marginTop: 3 }}
        >
          {chgStr}
        </div>
      </div>
    </motion.button>
  );
}
