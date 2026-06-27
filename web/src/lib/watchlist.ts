"use client";

import { createClient } from "@/lib/supabase/client";

export type WatchlistItem = {
  ticker: string;
  type: string;
  note?: string;
  shares?: number;
  cost_per_share?: number;
  entry_date?: string;
  position?: number;
};

/**
 * 雙模式 watchlist:
 * - 已登入 → Supabase `watchlists` 表(RLS 加密)
 * - 訪客   → localStorage(zustand persist)
 */

export async function loadCloudWatchlist(): Promise<WatchlistItem[]> {
  const sb = createClient();
  const { data, error } = await sb
    .from("watchlists")
    .select("*")
    .order("position", { ascending: true });
  if (error) {
    console.warn("[watchlist] load failed:", error.message);
    return [];
  }
  return (data ?? []).map((r) => ({
    ticker: r.ticker,
    type: r.ticker_type ?? "twse",
    note: r.note ?? "",
    shares: r.shares ?? undefined,
    cost_per_share: r.cost_per_share ?? undefined,
    entry_date: r.entry_date ?? undefined,
    position: r.position ?? undefined,
  }));
}

export async function addCloudTicker(item: WatchlistItem, userId: string) {
  const sb = createClient();
  const { error } = await sb.from("watchlists").upsert(
    {
      user_id: userId,
      ticker: item.ticker,
      ticker_type: item.type,
      note: item.note ?? "",
      shares: item.shares ?? null,
      cost_per_share: item.cost_per_share ?? null,
      entry_date: item.entry_date ?? null,
      position: item.position ?? 0,
    },
    { onConflict: "user_id,ticker,ticker_type" }
  );
  if (error) throw error;
}

export async function removeCloudTicker(ticker: string, type: string) {
  const sb = createClient();
  const { error } = await sb
    .from("watchlists")
    .delete()
    .eq("ticker", ticker)
    .eq("ticker_type", type);
  if (error) throw error;
}

/** 批次更新 cloud watchlist 的 position(拖曳排序用)— upsert by primary key. */
export async function reorderCloudWatchlist(
  items: WatchlistItem[],
  userId: string
) {
  const sb = createClient();
  // 1 次 upsert 全部 — Supabase 支援 array upsert
  const rows = items.map((it, idx) => ({
    user_id: userId,
    ticker: it.ticker,
    ticker_type: it.type,
    position: idx,
  }));
  const { error } = await sb.from("watchlists").upsert(rows, {
    onConflict: "user_id,ticker,ticker_type",
  });
  if (error) throw error;
}

export async function updateCloudHolding(
  ticker: string,
  type: string,
  shares: number | null,
  costPerShare: number | null,
  entryDate?: string | null
) {
  const sb = createClient();
  const { error } = await sb
    .from("watchlists")
    .update({
      shares,
      cost_per_share: costPerShare,
      entry_date: entryDate ?? null,
    })
    .eq("ticker", ticker)
    .eq("ticker_type", type);
  if (error) throw error;
}
