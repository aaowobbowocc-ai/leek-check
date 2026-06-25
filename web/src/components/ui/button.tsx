"use client";

import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * 統一金屬質感按鈕(對角反光 + inset shadows)
 * 5 個 variant 都對齊 streamlit 色票
 */
const buttonVariants = cva(
  "relative inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-st text-sm font-bold transition-all active:scale-[0.97] disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-300 overflow-hidden",
  {
    variants: {
      variant: {
        // 主按鈕:teal 漸層 + 強烈反光(對應 streamlit gradient #5eead4 → #14b8a6)
        primary:
          "text-ink-950 [background:linear-gradient(105deg,transparent_35%,rgba(255,255,255,0.25)_50%,transparent_65%),linear-gradient(180deg,#5eead4_0%,#14b8a6_60%,#0d9488_100%)] border border-teal-200 shadow-[0_4px_14px_rgba(20,184,166,0.35),inset_0_1px_0_rgba(255,255,255,0.4),inset_0_-1px_0_rgba(0,0,0,0.2),0_0_20px_rgba(94,234,212,0.2)]",
        // 副按鈕:深色金屬
        secondary:
          "text-st-fg [background:linear-gradient(105deg,transparent_35%,rgba(255,255,255,0.06)_50%,transparent_65%),linear-gradient(180deg,#1c2028_0%,#16181d_50%,#11141a_100%)] border border-ink-700 shadow-[inset_0_1px_0_rgba(255,255,255,0.1),inset_0_-1px_0_rgba(0,0,0,0.4)]",
        // 描邊按鈕
        outline:
          "border-2 border-teal-500 text-teal-300 bg-transparent hover:bg-teal-500/10",
        // 文字按鈕
        ghost:
          "text-st-soft hover:bg-white/[0.03] hover:text-st-fg",
        // 危險(賣出 / 刪除)
        danger:
          "text-rose-300 [background:linear-gradient(180deg,rgba(244,63,94,0.15),rgba(244,63,94,0.08))] border border-rose-500/40 shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]",
        // 訪客金色
        guest:
          "text-ink-950 [background:linear-gradient(105deg,transparent_35%,rgba(255,255,255,0.3)_50%,transparent_65%),linear-gradient(180deg,#fcd34d_0%,#f59e0b_60%,#d97706_100%)] border border-amber-200 shadow-[0_4px_14px_rgba(245,158,11,0.35),inset_0_1px_0_rgba(255,255,255,0.4),inset_0_-1px_0_rgba(0,0,0,0.2),0_0_20px_rgba(252,211,77,0.2)]",
      },
      size: {
        sm: "h-9 px-3 text-xs",
        md: "h-11 px-4",
        lg: "h-14 px-6 text-base",
        icon: "h-10 w-10",
      },
    },
    defaultVariants: { variant: "primary", size: "md" },
  }
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        ref={ref}
        className={cn(buttonVariants({ variant, size, className }))}
        {...props}
      />
    );
  }
);
Button.displayName = "Button";
