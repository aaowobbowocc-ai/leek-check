// 支援 2 個變數名稱(都是 user 可能設的)
const BASE = process.env.NEXT_PUBLIC_API_URL
  ?? process.env.NEXT_PUBLIC_API_BASE_URL
  ?? "http://localhost:8000";

export interface Quote {
  ticker: string;
  name: string;
  industry: string;
  price: number;
  prev_close: number;
  change_pct: number;
  open: number;
  high: number;
  low: number;
  volume: number;
  asof: string;
}

export interface TickerInfo {
  ticker: string;
  name: string;
  industry: string;
  type: string;
}

export interface OhlcvBar {
  date: string;
  open: number; high: number; low: number; close: number;
  volume: number;
  ma20: number; ma60: number;
}

export interface RevHistory {
  month: string;
  rev_yi: number;
  yoy: number;
}

export interface HealthCheck {
  ticker: string;
  name: string;
  industry: string;
  quote: {
    price: number; prev_close: number; change_pct: number; asof: string;
    open: number; high: number; low: number; volume: number;
  };
  health: {
    composite: number;
    verdict: string;
    color: "green" | "teal" | "amber" | "rose";
    scores: {
      technical: { score: number; notes: string[] };
      chip: { score: number; notes: string[] };
      fundamental: { score: number; notes: string[] };
      news: { score: number; notes: string[] };
    };
  };
  tech: {
    price: number; ma5: number; ma20: number; ma60: number; ma200: number;
    rsi: number; k: number; d: number;
  } | null;
  chip: {
    foreign_20d: number; invtrust_20d: number; dealer_20d: number;
  } | null;
  funda: {
    per?: number; pbr?: number; yield?: number; rev_yoy?: number;
    rev_history?: RevHistory[];
  };
  ohlcv_60d: OhlcvBar[];
  sparkline: number[];
  has_full_data: boolean;
}

export interface StrategyHit {
  ticker: string;
  name: string;
  industry: string;
  metric: number | null;
  extra: Record<string, unknown>;
}

export interface StrategyResults {
  updated_at: string;
  age_hours: number;
  fresh: boolean;
  strategies: Record<string, StrategyHit[]>;
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) {
    throw new Error(`API ${path} → ${res.status}`);
  }
  return res.json();
}

export interface AiExplainIn {
  ticker: string;
  name: string;
  industry: string;
  price: number;
  change_pct: number;
  composite: number;
  verdict: string;
  tech: Record<string, unknown> | null;
  chip: Record<string, unknown> | null;
  funda: Record<string, unknown> | null;
  style?: "neutral" | "pro" | "casual";
  timeframe?: "short" | "mid" | "long";
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const txt = await res.text().catch(() => "");
    throw new Error(`API ${path} → ${res.status} ${txt}`);
  }
  return res.json();
}

export interface MarketIndex {
  symbol: string;
  name: string;
  price: number;
  change_pct: number;
  asof: string;
}
export interface TaiexFull {
  price: number;
  prev_close: number;
  change_pct: number;
  asof: string;
  ma200_dist_pct: number | null;
  ret_20d: number | null;
  ret_60d: number | null;
  sparkline_30d: number[];
  temperature: string;
  temperature_emoji: string;
}

export interface InstitutionalSummary {
  foreign_20d: number;
  invtrust_20d: number;
  dealer_20d: number;
  note: string;
}

export interface MarketDashboard {
  taiex: TaiexFull | null;
  vix: MarketIndex | null;
  sp500: MarketIndex | null;
  nasdaq: MarketIndex | null;
  sox: MarketIndex | null;
  dxy: MarketIndex | null;
  dxj: MarketIndex | null;
  nikkei: MarketIndex | null;
  gold: MarketIndex | null;
  oil: MarketIndex | null;
  silver: MarketIndex | null;
  usdtwd: MarketIndex | null;
  btc: MarketIndex | null;
  eth: MarketIndex | null;
  institutional: InstitutionalSummary | null;
  international_note: string;
}

export interface RankItem {
  ticker: string;
  name: string;
  industry: string;
  price: number;
  change_pct: number;
  volume: number;
  composite?: number | null;
  verdict?: string | null;
}

export interface RankOut {
  type: string;
  items: RankItem[];
}

export const api = {
  searchTickers: (q: string) => get<TickerInfo[]>(`/api/search?q=${encodeURIComponent(q)}`),
  getQuote: (tk: string) => get<Quote>(`/api/quote/${tk}`),
  getQuotesBatch: (tks: string[]) => get<Quote[]>(`/api/quote/batch?tickers=${tks.join(",")}`),
  getHealthCheck: (tk: string) => get<HealthCheck>(`/api/health-check/${tk}`),
  getStrategyResults: () => get<StrategyResults>("/api/strategy/results"),
  aiExplain: (body: AiExplainIn) => post<{ text: string; model: string }>("/api/ai/explain", body),
  getMarketDashboard: () => get<MarketDashboard>("/api/market/dashboard"),
  getRanking: (by: "up" | "down" | "volume" | "health", limit = 20) =>
    get<RankOut>(`/api/ranking?by=${by}&limit=${limit}`),
  getWorldNews: () => get<NewsCategory[]>("/api/news/world"),
  getMarketNews: (limit = 10) => get<NewsItem[]>(`/api/news/market?limit=${limit}`),
  getTickerNews: (ticker: string, name = "") =>
    get<NewsItem[]>(`/api/news/ticker/${ticker}?name=${encodeURIComponent(name)}`),
  aiMarketInsight: (body: Record<string, unknown>) =>
    post<{ text: string; model: string }>("/api/ai/market-insight", body),
  aiNewsSentiment: (body: { news_titles: string[]; style?: string; timeframe?: string }) =>
    post<{ text: string; model: string }>("/api/ai/news-sentiment", body),
};

export interface NewsItem {
  title: string;
  source: string;
  link: string;
  published: string;
}

export interface NewsCategory {
  key: string;
  label: string;
  items: NewsItem[];
}
