"use client";

import { motion } from "framer-motion";

type Props = {
  score: number;
  label: string;
  color: "green" | "amber" | "rose";
  size?: number;
};

const COLOR_MAP = {
  green: { stroke: "#5eead4", glow: "rgba(20,184,166,0.35)", text: "#5eead4" },
  amber: { stroke: "#fbbf24", glow: "rgba(245,158,11,0.35)", text: "#fbbf24" },
  rose:  { stroke: "#f43f5e", glow: "rgba(220,38,38,0.35)", text: "#f43f5e" },
} as const;

export function ScoreRing({ score, label, color, size = 140 }: Props) {
  const c = COLOR_MAP[color];
  const radius = size / 2 - 6;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (Math.min(100, Math.max(0, score)) / 100) * circumference;

  return (
    <div
      className="relative flex flex-col items-center justify-center rounded-full"
      style={{
        width: size, height: size,
        background: `radial-gradient(circle, ${c.glow} 0%, transparent 70%)`,
      }}
    >
      <svg width={size} height={size} className="absolute inset-0 -rotate-90">
        <circle
          cx={size / 2} cy={size / 2} r={radius}
          fill="none"
          stroke="#2f343d"
          strokeWidth={4}
        />
        <motion.circle
          cx={size / 2} cy={size / 2} r={radius}
          fill="none"
          stroke={c.stroke}
          strokeWidth={5}
          strokeLinecap="round"
          strokeDasharray={circumference}
          initial={{ strokeDashoffset: circumference }}
          animate={{ strokeDashoffset: offset }}
          transition={{ duration: 1, ease: "easeOut" }}
          style={{ filter: `drop-shadow(0 0 8px ${c.glow})` }}
        />
      </svg>
      <div className="relative z-10 flex flex-col items-center">
        <motion.div
          initial={{ scale: 0.5, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={{ delay: 0.3, duration: 0.4 }}
          className="text-4xl font-extrabold text-white leading-none"
        >
          {score}
        </motion.div>
        <div className="text-[10px] text-slate-500 mt-0.5">/ 100</div>
        <div
          className="text-sm font-bold mt-1"
          style={{ color: c.text }}
        >
          {label}
        </div>
      </div>
    </div>
  );
}
