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
    glow: "rgba(20,184,166,0.45)",
    glowSoft: "rgba(20,184,166,0.18)",
    text: "#5eead4",
    textGlow: "rgba(94, 234, 212, 0.6)",
  },
  amber: {
    stroke: "#fbbf24",
    glow: "rgba(245,158,11,0.45)",
    glowSoft: "rgba(245,158,11,0.18)",
    text: "#fbbf24",
    textGlow: "rgba(251, 191, 36, 0.6)",
  },
  rose: {
    stroke: "#f43f5e",
    glow: "rgba(220,38,38,0.45)",
    glowSoft: "rgba(220,38,38,0.18)",
    text: "#f43f5e",
    textGlow: "rgba(244, 63, 94, 0.6)",
  },
} as const;

export function ScoreRing({ score, label, color, size = 140 }: Props) {
  const c = COLOR_MAP[color];
  const radius = size / 2 - 8;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (Math.min(100, Math.max(0, score)) / 100) * circumference;

  return (
    <div
      className="relative flex flex-col items-center justify-center rounded-full"
      style={{
        width: size,
        height: size,
        background: `radial-gradient(circle, ${c.glowSoft} 0%, transparent 65%)`,
        boxShadow: `0 0 32px ${c.glow}, inset 0 0 24px ${c.glowSoft}`,
      }}
    >
      {/* Glow halo ring */}
      <div
        className="absolute rounded-full pointer-events-none"
        style={{
          width: size + 30,
          height: size + 30,
          background: `radial-gradient(circle, ${c.glow} 0%, transparent 55%)`,
          filter: "blur(8px)",
          opacity: 0.5,
        }}
      />
      <svg width={size} height={size} className="absolute inset-0 -rotate-90">
        <circle
          cx={size / 2} cy={size / 2} r={radius}
          fill="none"
          stroke="#2f343d"
          strokeWidth={6}
        />
        <motion.circle
          cx={size / 2} cy={size / 2} r={radius}
          fill="none"
          stroke={c.stroke}
          strokeWidth={7}
          strokeLinecap="round"
          strokeDasharray={circumference}
          initial={{ strokeDashoffset: circumference }}
          animate={{ strokeDashoffset: offset }}
          transition={{ duration: 1.2, ease: "easeOut" }}
          style={{
            filter: `drop-shadow(0 0 6px ${c.stroke}) drop-shadow(0 0 14px ${c.glow})`,
          }}
        />
      </svg>
      <div className="relative z-10 flex flex-col items-center">
        <motion.div
          initial={{ scale: 0.4, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={{ delay: 0.3, duration: 0.5, type: "spring" }}
          className="text-5xl font-extrabold text-white leading-none"
          style={{
            textShadow: `0 0 12px ${c.textGlow}, 0 0 24px ${c.glow}`,
          }}
        >
          {score}
        </motion.div>
        <div className="text-[10px] text-slate-400 mt-1 font-bold tracking-widest">/ 100</div>
        <div
          className="text-sm font-bold mt-1.5 tracking-wider"
          style={{
            color: c.text,
            textShadow: `0 0 8px ${c.textGlow}`,
          }}
        >
          {label}
        </div>
      </div>
    </div>
  );
}
