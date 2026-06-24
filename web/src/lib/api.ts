const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

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

export interface HealthCheck {
  ticker: string;
  name: string;
  industry: string;
  quote: { price: number; change_pct: number; asof: string };
  health: {
    composite: number;
    verdict: string;
    color: "green" | "teal" | "amber" | "red";
    scores: {
      technical: { score: number; notes: string[] };
      chip: { score: number; notes: string[] };
      fundamental: { score: number; notes: string[] };
      news: { score: number; notes: string[] };
    };
  };
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

export const api = {
  searchTickers: (q: string) => get<TickerInfo[]>(`/api/search?q=${encodeURIComponent(q)}`),
  getQuote: (tk: string) => get<Quote>(`/api/quote/${tk}`),
  getQuotesBatch: (tks: string[]) => get<Quote[]>(`/api/quote/batch?tickers=${tks.join(",")}`),
  getHealthCheck: (tk: string) => get<HealthCheck>(`/api/health-check/${tk}`),
  getStrategyResults: () => get<StrategyResults>("/api/strategy/results"),
};
