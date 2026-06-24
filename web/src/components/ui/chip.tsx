import { cn } from "@/lib/utils";

type Tone = "default" | "brand" | "amber" | "rose" | "emerald";

const TONE: Record<Tone, string> = {
  default: "bg-ink-800 text-slate-300 border-ink-700",
  brand:   "bg-brand-500/15 text-brand-300 border-brand-500/40",
  amber:   "bg-amber-500/15 text-amber-300 border-amber-500/40",
  rose:    "bg-rose-500/15 text-rose-300 border-rose-500/40",
  emerald: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
};

export function Chip({
  children, tone = "default", className,
}: { children: React.ReactNode; tone?: Tone; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 text-[10px] font-bold tracking-wider px-1.5 py-0.5 rounded border",
        TONE[tone],
        className
      )}
    >
      {children}
    </span>
  );
}

export function ProgressBar({
  value, max = 100, tone = "brand",
}: { value: number; max?: number; tone?: "brand" | "amber" | "rose" | "emerald" }) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  const cls = {
    brand: "from-brand-500 to-brand-300",
    amber: "from-amber-500 to-amber-300",
    rose: "from-rose-500 to-rose-300",
    emerald: "from-emerald-500 to-emerald-300",
  }[tone];
  return (
    <div className="w-full h-1.5 bg-ink-800 rounded-full overflow-hidden">
      <div
        className={`h-full bg-gradient-to-r ${cls} rounded-full transition-all duration-700 ease-out`}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}
