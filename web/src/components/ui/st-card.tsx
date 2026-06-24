import { cn } from "@/lib/utils";

type Props = React.HTMLAttributes<HTMLDivElement> & {
  variant?: "card" | "hero" | "sub";
  glow?: "teal" | "amber" | "rose" | null;
};

/**
 * 統一 streamlit 卡片樣式 — 三種變體:
 * - card  → linear-gradient(135deg, #1f2937 0%, #1a1f27 100%) + border #2f343d
 * - hero  → linear-gradient(135deg, #0f766e 0%, #0a1a1f 50%, #16181d 100%)
 * - sub   → solid #16181d + border-left 3px teal(放在卡內當小方塊用)
 */
export function StCard({
  variant = "card",
  glow = null,
  className,
  children,
  ...rest
}: Props) {
  const base = "rounded-st border";
  const variantCls = {
    card: "bg-st-card border-st-border p-5",
    hero: "bg-st-hero border-st-border p-5",
    sub:  "bg-st-bg border-st-border border-l-[3px] !border-l-teal-300 p-3 text-center",
  }[variant];
  const glowCls = glow === "teal"  ? "shadow-st-ring-teal"
                : glow === "amber" ? "shadow-st-ring-amber"
                : glow === "rose"  ? "shadow-st-ring-rose"
                : "";
  return (
    <div className={cn(base, variantCls, glowCls, className)} {...rest}>
      {children}
    </div>
  );
}

/** Streamlit ### header — 大字 emoji + 標題 */
export function StHeader({
  emoji, title, sub,
}: { emoji?: string; title: string; sub?: string }) {
  return (
    <div className="mb-3 mt-2">
      <h3 className="text-lg font-extrabold text-st-fg">
        {emoji && <span className="mr-1.5">{emoji}</span>}
        {title}
      </h3>
      {sub && <p className="text-xs text-st-muted mt-1">{sub}</p>}
    </div>
  );
}

/** Streamlit caption — 灰字小提示 */
export function StCaption({
  children, className,
}: { children: React.ReactNode; className?: string }) {
  return (
    <p className={cn("text-xs text-st-muted leading-relaxed", className)}>
      {children}
    </p>
  );
}
