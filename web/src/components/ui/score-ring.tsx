"use client";

import { motion } from "framer-motion";

type Props = {
  score: number;
  label: string;
  color: "green" | "amber" | "rose";
  size?: number;
};

/**
 * 完全照搬 streamlit app/app.py:6447-6455 那顆圓環
 * - 130px 容器
 * - 3px solid border
 * - background rgba(20,184,166,0.18)
 * - box-shadow 0 0 24px ring_bg
 * - 中央 2.4rem 分數 + /100 + tier label
 */
const COLOR_MAP = {
  green: {
    border: "#5eead4",
    bg:     "rgba(20,184,166,0.18)",
    label:  "#5eead4",
    shadow: "0 0 24px rgba(20,184,166,0.18)",
  },
  amber: {
    border: "#fbbf24",
    bg:     "rgba(245,158,11,0.18)",
    label:  "#fbbf24",
    shadow: "0 0 24px rgba(245,158,11,0.18)",
  },
  rose: {
    border: "#f43f5e",
    bg:     "rgba(220,38,38,0.18)",
    label:  "#f43f5e",
    shadow: "0 0 24px rgba(220,38,38,0.18)",
  },
} as const;

export function ScoreRing({ score, label, color, size = 130 }: Props) {
  const c = COLOR_MAP[color];
  return (
    <motion.div
      initial={{ scale: 0.8, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      transition={{ duration: 0.4, type: "spring", stiffness: 200 }}
      style={{
        width: size,
        height: size,
        borderRadius: "50%",
        background: c.bg,
        border: `3px solid ${c.border}`,
        boxShadow: c.shadow,
      }}
      className="flex flex-col items-center justify-center"
    >
      <div
        style={{ fontSize: "2.4rem", color: "#fff", fontWeight: 800, lineHeight: 1 }}
      >
        {score}
      </div>
      <div style={{ fontSize: "0.7rem", color: "#94a3b8", marginTop: 2 }}>
        / 100
      </div>
      <div
        style={{
          fontSize: "0.85rem",
          color: c.label,
          marginTop: 4,
          fontWeight: 700,
        }}
      >
        {label}
      </div>
    </motion.div>
  );
}
