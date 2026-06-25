/**
 * Streamlit 1:1 — 稀有度 tier + 行業 emoji
 * 來源:app/app.py:3115-3147
 */

export type Rarity = "LEGENDARY" | "EPIC" | "RARE" | "UNCOMMON" | "COMMON";

export type TierColors = {
  light: string;   // border + tier label color
  dark: string;    // card background gradient start
  icon: string;
  rarity: Rarity;
};

/** 行業 emoji 對應(streamlit _INDUSTRY_ICON 子集) */
const INDUSTRY_ICON: Record<string, string> = {
  "半導體業":           "🔬",
  "電子工業":           "🔬",
  "電腦及週邊設備業":   "💻",
  "光電業":             "💡",
  "通信網路業":         "📡",
  "電子零組件業":       "🔌",
  "電子通路業":         "🔌",
  "資訊服務業":         "💾",
  "其他電子業":         "🔧",
  "金融保險業":         "🏦",
  "金融業":             "🏦",
  "保險業":             "🛡️",
  "證券業":             "💱",
  "鋼鐵工業":           "⚙️",
  "塑膠工業":           "🛢️",
  "化學工業":           "🧪",
  "生技醫療業":         "💊",
  "食品工業":           "🍱",
  "紡織纖維業":         "🧵",
  "建材營造業":         "🏗️",
  "建材營造":           "🏗️",
  "汽車工業":           "🚗",
  "航運業":             "🚢",
  "觀光事業":           "🏨",
  "貿易百貨業":         "🛍️",
  "電器電纜":           "⚡",
  "玻璃陶瓷":           "🍶",
  "造紙工業":           "📃",
  "橡膠工業":           "🛞",
  "農業科技":           "🌾",
  "油電燃氣業":         "⛽",
  "水泥工業":           "🪨",
};

/** Tier thresholds (20 日平均成交額 億元 / 日) — streamlit:_RARITY_TIERS */
const TIERS: Array<[number, string, string, Rarity]> = [
  [50,  "#fcd34d", "#f59e0b", "LEGENDARY"],
  [10,  "#a78bfa", "#7c3aed", "EPIC"],
  [3,   "#38bdf8", "#0284c7", "RARE"],
  [0.5, "#5eead4", "#0d9488", "UNCOMMON"],
  [0,   "#94a3b8", "#475569", "COMMON"],
];

/** 暫時硬寫常見大票的 avg_value(等 backend 加 endpoint 後拿掉)*/
const KNOWN_AVG_VALUE: Record<string, number> = {
  "2330": 200, "0050": 80, "2454": 60, "2317": 50, "2382": 40,
  "2308": 35, "2603": 30, "2891": 25, "1101": 12, "1216": 12,
  "00878": 15, "00692": 10, "00919": 12, "00713": 8, "0056": 8,
  "2412": 8, "2884": 8, "2885": 7, "2881": 7, "1301": 6,
  "1303": 6, "2002": 5, "2207": 5, "2027": 4, "3008": 4,
  "2618": 4, "2609": 4, "2615": 4, "3711": 4, "2890": 4,
};

export function industryIcon(industry: string, ticker = ""): string {
  if (ticker.startsWith("00") || ticker === "0050") return "🌐";
  return INDUSTRY_ICON[industry || ""] || "📊";
}

export function cardTier(
  ticker: string,
  industry: string,
  avgValueYi?: number
): TierColors {
  const icon = industryIcon(industry, ticker);
  const value = avgValueYi ?? KNOWN_AVG_VALUE[ticker];
  if (value == null) return { light: "#94a3b8", dark: "#475569", icon, rarity: "COMMON" };
  for (const [threshold, light, dark, rarity] of TIERS) {
    if (value >= threshold) return { light, dark, icon, rarity };
  }
  return { light: "#94a3b8", dark: "#475569", icon, rarity: "COMMON" };
}

/** 台股慣例 — 紅漲綠跌(streamlit 一致)*/
export const TW_UP_COLOR = "#ef4444";    // 紅
export const TW_DOWN_COLOR = "#10b981";  // 綠
export const TW_FLAT_COLOR = "#8b92a0";

export function chgColor(chgPct: number): string {
  if (chgPct > 0) return TW_UP_COLOR;
  if (chgPct < 0) return TW_DOWN_COLOR;
  return TW_FLAT_COLOR;
}

export function chgArrow(chgPct: number): string {
  if (chgPct > 0) return "▲";
  if (chgPct < 0) return "▼";
  return "—";
}

/** 熱門股分類(空白搜尋時推薦)*/
export type HotCategory = {
  key: string;
  emoji: string;
  label: string;
  desc: string;
  tickers: string[];
};

export const HOT_STOCK_CATEGORIES: HotCategory[] = [
  {
    key: "weight",
    emoji: "🏆",
    label: "權值股 LEGENDARY",
    desc: "市場最熱、流動性最好的台股",
    tickers: ["2330", "2454", "2317", "2308", "2382"],
  },
  {
    key: "etf_core",
    emoji: "🌐",
    label: "核心 ETF",
    desc: "大盤 + 主流選股 ETF",
    tickers: ["0050", "0056", "00878", "00692", "00919"],
  },
  {
    key: "ai",
    emoji: "🤖",
    label: "AI 概念",
    desc: "AI 伺服器 + 算力族群",
    tickers: ["3231", "2376", "3702", "8069", "2492"],
  },
  {
    key: "finance",
    emoji: "🏦",
    label: "金融股",
    desc: "金控 + 證券,股息族最愛",
    tickers: ["2891", "2884", "2885", "2881", "2890"],
  },
  {
    key: "shipping",
    emoji: "🚢",
    label: "航運股",
    desc: "短線散戶熱門族群",
    tickers: ["2603", "2618", "2609", "2615", "2606"],
  },
];

/** 全部熱門股(batch fetch 用)*/
export const ALL_HOT_TICKERS = Array.from(
  new Set(HOT_STOCK_CATEGORIES.flatMap((c) => c.tickers))
);
