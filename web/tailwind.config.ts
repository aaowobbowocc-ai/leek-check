import type { Config } from "tailwindcss";

/**
 * 🎨 韭菜健檢 Design Tokens — 鎖死 streamlit 既有色票
 * (從 app/app.py 萃取,不可改)
 */
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // ── Streamlit 直接對應 ──
        st: {
          bg:     "#16181d",
          border: "#2f343d",
          fg:     "#ffffff",
          muted:  "#94a3b8",
          soft:   "#cbd5e1",
        },
        // ── brand 別名(= teal,讓既有 brand-* class 不失效)──
        brand: {
          200: "#99f6e4", 300: "#5eead4", 400: "#2dd4bf",
          500: "#14b8a6", 600: "#0d9488", 700: "#0f766e",
        },
        // ── ink 階梯(深色背景階)──
        ink: {
          700: "#2a3340",
          800: "#1a1d24",
          900: "#16181d",
          950: "#0f1218",
        },
        // ── Brand teal(健康 + 主品牌) ──
        teal: {
          50:  "#f0fdfa", 100: "#ccfbf1", 200: "#99f6e4",
          300: "#5eead4",   // ★ streamlit 主色
          400: "#2dd4bf",
          500: "#14b8a6",   // ★ button gradient end
          600: "#0d9488",
          700: "#0f766e",   // ★ hero gradient start
          800: "#115e59", 900: "#134e4a",
        },
        // ── 警示色階(streamlit 確切值)──
        amber: { 300: "#fbbf24" },   // 亞健康
        rose:  { 400: "#f43f5e" },   // 韭菜病 / 下跌
        green: { 400: "#34d399" },   // 上漲
      },
      // ── Streamlit card gradient ──
      backgroundImage: {
        "st-card": "linear-gradient(135deg, #1f2937 0%, #1a1f27 100%)",
        "st-hero": "linear-gradient(135deg, #0f766e 0%, #0a1a1f 50%, #16181d 100%)",
        "st-btn":  "linear-gradient(135deg, #5eead4 0%, #14b8a6 100%)",
      },
      // ── 邊框圓角(streamlit 全用 14)──
      borderRadius: {
        st: "14px",
      },
      // ── Inner shadow + glow(streamlit 用 box-shadow:0 0 24px)──
      boxShadow: {
        "st-ring-teal":  "0 0 24px rgba(20,184,166,0.35)",
        "st-ring-amber": "0 0 24px rgba(245,158,11,0.35)",
        "st-ring-rose":  "0 0 24px rgba(220,38,38,0.35)",
      },
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', '"Noto Sans TC"', 'sans-serif'],
      },
      animation: {
        "fade-in":  "fadeIn 0.3s ease-out",
        "slide-up": "slideUp 0.4s ease-out",
      },
      keyframes: {
        fadeIn:  { "0%": { opacity: "0" }, "100%": { opacity: "1" } },
        slideUp: {
          "0%":   { opacity: "0", transform: "translateY(12px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
    },
  },
  plugins: [],
};

export default config;
