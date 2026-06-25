"use client";

import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * 按鈕設計:frosted glass + glow + tier color border
 * 不再實心填滿,改成「玻璃 + 發光描邊」感
 */
const buttonVariants = cva(
  "relative inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-st text-sm font-bold transition-all active:scale-[0.97] disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-300 overflow-hidden backdrop-blur-sm",
  {
    variants: {
      variant: {
        // 🟢 主按鈕:teal 玻璃 + glow 邊框
        primary:
          "text-teal-200 border border-teal-400/50 " +
          "[background:linear-gradient(135deg,rgba(94,234,212,0.18)_0%,rgba(20,184,166,0.10)_50%,rgba(15,118,110,0.08)_100%)] " +
          "shadow-[0_0_24px_rgba(94,234,212,0.18),inset_0_1px_0_rgba(255,255,255,0.1),inset_0_-1px_0_rgba(0,0,0,0.3)] " +
          "hover:shadow-[0_0_32px_rgba(94,234,212,0.3),inset_0_1px_0_rgba(255,255,255,0.15),inset_0_-1px_0_rgba(0,0,0,0.3)] " +
          "hover:border-teal-300 hover:text-teal-100",
        // ⚪ 副按鈕:深色玻璃
        secondary:
          "text-st-soft border border-ink-700 " +
          "[background:linear-gradient(135deg,rgba(40,45,56,0.6)_0%,rgba(22,24,29,0.5)_100%)] " +
          "shadow-[inset_0_1px_0_rgba(255,255,255,0.05),inset_0_-1px_0_rgba(0,0,0,0.3)] " +
          "hover:border-ink-700/80 hover:text-st-fg",
        // 描邊按鈕:純邊框
        outline:
          "border-2 border-teal-400/60 text-teal-300 bg-transparent " +
          "hover:bg-teal-500/10 hover:border-teal-300",
        // 文字按鈕
        ghost:
          "text-st-soft border border-transparent " +
          "hover:bg-white/[0.04] hover:text-st-fg",
        // 🔴 危險(賣出/刪除):rose 玻璃
        danger:
          "text-rose-200 border border-rose-400/50 " +
          "[background:linear-gradient(135deg,rgba(244,63,94,0.15)_0%,rgba(190,18,60,0.08)_100%)] " +
          "shadow-[0_0_20px_rgba(244,63,94,0.15),inset_0_1px_0_rgba(255,255,255,0.06)] " +
          "hover:border-rose-300 hover:shadow-[0_0_28px_rgba(244,63,94,0.25)]",
        // 🟡 訪客:amber 玻璃
        guest:
          "text-amber-200 border border-amber-400/50 " +
          "[background:linear-gradient(135deg,rgba(252,211,77,0.18)_0%,rgba(217,119,6,0.10)_100%)] " +
          "shadow-[0_0_24px_rgba(252,211,77,0.18),inset_0_1px_0_rgba(255,255,255,0.1),inset_0_-1px_0_rgba(0,0,0,0.3)] " +
          "hover:border-amber-300 hover:shadow-[0_0_32px_rgba(252,211,77,0.3)]",
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
