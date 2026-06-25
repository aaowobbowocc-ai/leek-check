"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Stethoscope, Settings as SettingsIcon } from "lucide-react";
import { StockPill } from "@/components/stock-pill";
import { cardTier, chgColor, chgArrow } from "@/lib/tier";
import { formatNumber } from "@/lib/utils";
import type { Quote } from "@/lib/api";

type Props = {
  ticker: string;
  name: string;
  industry: string;
  quote?: Quote;
  hasHolding?: boolean;
  /** 持股資訊(展開時顯示)*/
  holding?: {
    shares: number;
    cost_per_share: number;
    pnl?: number;
    pnlPct?: number;
  };
  /** 點「翻開健檢」按鈕 */
  onOpen?: () => void;
  /** 點「編輯持股」按鈕 — 沒傳就不顯示 */
  onEdit?: () => void;
  /** 預設展開 */
  defaultExpanded?: boolean;
};

/**
 * 3 層 disclosure UX:
 * Level 1 = 膠囊行(StockPill)— 預設只顯示這個
 * Level 2 = 點開後 brief panel(產業 / 開高低 / 持股 / 兩個 action 按鈕)
 * Level 3 = 點 brief panel 的 [翻開健檢] → /ticker/{tk}
 */
export function StockRow({
  ticker, name, industry, quote, hasHolding, holding, onOpen, onEdit,
  defaultExpanded = false,
}: Props) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const { light } = cardTier(ticker, industry);

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
              className="rounded-st p-3 space-y-2"
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
              {/* 產業 + 收盤日期 */}
              <div className="flex items-center justify-between text-[10px] text-st-muted">
                <span>📂 {industry || "—"}</span>
                {quote && <span className="tabular-nums">{quote.asof}</span>}
              </div>

              {/* 開高低 + 量 */}
              {quote && (
                <div className="grid grid-cols-4 gap-1 text-[10px]">
                  <Mini label="開" value={formatNumber(quote.open, 1)} />
                  <Mini label="高" value={formatNumber(quote.high, 1)} tint="up" />
                  <Mini label="低" value={formatNumber(quote.low, 1)} tint="down" />
                  <Mini label="量" value={fmtVol(quote.volume)} />
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

              {/* Action buttons */}
              <div className="flex gap-1.5 pt-1">
                <button
                  onClick={onOpen}
                  className="flex-1 rounded-st py-2 text-xs font-semibold text-teal-300 transition-colors flex items-center justify-center gap-1.5 active:scale-95"
                  style={{
                    background: "linear-gradient(180deg, #1c2028, #11141a)",
                    border: "1px solid #2a3340",
                    boxShadow: "inset 0 1px 0 rgba(255,255,255,0.08), inset 0 -1px 0 rgba(0,0,0,0.4)",
                  }}
                >
                  <Stethoscope className="w-3.5 h-3.5" /> 翻開健檢
                </button>
                {onEdit && (
                  <button
                    onClick={onEdit}
                    className="rounded-st px-3 text-xs font-semibold text-st-muted transition-colors flex items-center justify-center gap-1.5 active:scale-95"
                    style={{
                      background: "linear-gradient(180deg, #1c2028, #11141a)",
                      border: "1px solid #2a3340",
                      boxShadow: "inset 0 1px 0 rgba(255,255,255,0.08), inset 0 -1px 0 rgba(0,0,0,0.4)",
                    }}
                  >
                    <SettingsIcon className="w-3.5 h-3.5" /> 編輯
                  </button>
                )}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
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
