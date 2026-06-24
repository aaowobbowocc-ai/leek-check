"use client";

import { motion } from "framer-motion";

type Props = {
  score: number;
  label: string;
  color: "green" | "amber" | "rose";
  size?: number;
};

const COLOR_MAP = {
  green: {
    stroke: "#5eead4",
    bg:     "rgba(20,184,166,0.18)",
    label:  "#5eead4",
    glow:   "rgba(94,234,212,0.5)",
  },
  amber: {
    stroke: "#fbbf24",
    bg:     "rgba(245,158,11,0.18)",
    label:  "#fbbf24",
    glow:   "rgba(251,191,36,0.5)",
  },
  rose: {
    stroke: "#f43f5e",
    bg:     "rgba(220,38,38,0.18)",
    label:  "#f43f5e",
    glow:   "rgba(244,63,94,0.5)",
  },
} as const;

/**
 * Streamlit 1:1 + drop-shadow filter polish(來自 Robinhood / Preline 研究)
 * - 130px 容器 + 3px solid border + 0 0 24px box-shadow
 * - 改用 SVG circle + dashoffset 動畫(可控進度條視覺)
 * - drop-shadow filter on stroke = 質感升級關鍵
 */
export function ScoreRing({ score, label, color, size = 130 }: Props) {
  const c = COLOR_MAP[color];
  const radius = size / 2 - 6;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (Math.min(100, Math.max(0, score)) / 100) * circumference;

  return (
    <motion.div
      initial={{ scale: 0.7, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      transition={{ duration: 0.5, type: "spring", stiffness: 220 }}
      className="relative flex flex-col items-center justify-center"
      style={{
        width: size,
        height: size,
        borderRadius: "50%",
        background: c.bg,
        boxShadow: `0 0 32px ${c.bg}, inset 0 0 16px ${c.bg}`,
      }}
    >
      {/* SVG ring with drop-shadow */}
      <svg
        width={size}
        height={size}
        className="absolute inset-0 -rotate-90 pointer-events-none"
        style={{ filter: `drop-shadow(0 0 6px ${c.glow})` }}
      >
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="#2f343d"
          strokeWidth={3}
        />
        <motion.circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={c.stroke}
          strokeWidth={3}
          strokeLinecap="round"
          strokeDasharray={circumference}
          initial={{ strokeDashoffset: circumference }}
          animate={{ strokeDashoffset: offset }}
          transition={{ duration: 1, ease: "easeOut", delay: 0.2 }}
        />
      </svg>

      {/* Center content */}
      <div className="relative z-10 flex flex-col items-center pointer-events-none">
        <div
          className="tabular-nums"
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
            textShadow: `0 0 8px ${c.glow}`,
          }}
        >
          {label}
        </div>
      </div>
    </motion.div>
  );
}
