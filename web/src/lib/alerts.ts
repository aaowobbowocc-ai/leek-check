"use client";

import { createClient } from "@/lib/supabase/client";

export type PriceAlert = {
  id: number;
  ticker: string;
  condition: "above" | "below";
  target_price: number;
  note: string;
  triggered_at: string | null;
  triggered_price: number | null;
  is_read: boolean;
  created_at: string;
};

export async function loadAlerts(): Promise<PriceAlert[]> {
  const sb = createClient();
  const { data, error } = await sb
    .from("price_alerts")
    .select("*")
    .order("created_at", { ascending: false });
  if (error) {
    console.warn("[alerts] load:", error.message);
    return [];
  }
  return (data ?? []) as PriceAlert[];
}

export async function loadActiveAlerts(ticker?: string): Promise<PriceAlert[]> {
  const sb = createClient();
  let q = sb.from("price_alerts").select("*").is("triggered_at", null);
  if (ticker) q = q.eq("ticker", ticker);
  const { data, error } = await q.order("created_at", { ascending: false });
  if (error) return [];
  return (data ?? []) as PriceAlert[];
}

export async function createAlert(
  userId: string,
  ticker: string,
  condition: "above" | "below",
  target_price: number,
  note = ""
) {
  const sb = createClient();
  const { error } = await sb.from("price_alerts").insert({
    user_id: userId,
    ticker,
    condition,
    target_price,
    note,
  });
  if (error) throw error;
}

export async function deleteAlert(id: number) {
  const sb = createClient();
  const { error } = await sb.from("price_alerts").delete().eq("id", id);
  if (error) throw error;
}

export async function markAlertRead(id: number) {
  const sb = createClient();
  await sb.from("price_alerts").update({ is_read: true }).eq("id", id);
}
