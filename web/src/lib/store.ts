"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { WatchlistItem } from "@/lib/watchlist";

type SessionState = {
  hasHydrated: boolean;
  setHydrated: () => void;
  isGuest: boolean;
  setGuest: (v: boolean) => void;

  /** 訪客 watchlist(只存 localStorage) */
  /** 晨報精選 5 檔(跨裝置共享 — 暫存 localStorage,等 PRO 接 DB)*/
  briefingPicks: string[];
  togglePick: (ticker: string) => void;

  /** 🎨 主題色(電競風格)*/
  accentTheme: "teal" | "cyan" | "purple" | "amber" | "rose" | "rgb";
  setAccent: (t: "teal" | "cyan" | "purple" | "amber" | "rose" | "rgb") => void;

  guestWatchlist: WatchlistItem[];
  addGuestItem: (item: WatchlistItem) => void;
  removeGuestItem: (ticker: string, type: string) => void;
  updateGuestHolding: (
    ticker: string,
    type: string,
    shares: number | null,
    cost: number | null,
    entryDate?: string | null
  ) => void;

  clearGuest: () => void;
};

export const useSession = create<SessionState>()(
  persist(
    (set) => ({
      hasHydrated: false,
      setHydrated: () => set({ hasHydrated: true }),
      isGuest: false,
      setGuest: (v) => set({ isGuest: v }),

      accentTheme: "teal",
      setAccent: (t) => set({ accentTheme: t }),

      briefingPicks: [],
      togglePick: (ticker) =>
        set((s) => {
          if (s.briefingPicks.includes(ticker)) {
            return { briefingPicks: s.briefingPicks.filter((x) => x !== ticker) };
          }
          if (s.briefingPicks.length >= 5) return s;  // 最多 5
          return { briefingPicks: [...s.briefingPicks, ticker] };
        }),

      guestWatchlist: [],
      addGuestItem: (item) =>
        set((s) => {
          const exists = s.guestWatchlist.some(
            (x) => x.ticker === item.ticker && x.type === item.type
          );
          if (exists) return s;
          return { guestWatchlist: [...s.guestWatchlist, item] };
        }),
      removeGuestItem: (ticker, type) =>
        set((s) => ({
          guestWatchlist: s.guestWatchlist.filter(
            (x) => !(x.ticker === ticker && x.type === type)
          ),
        })),
      updateGuestHolding: (ticker, type, shares, cost, entryDate) =>
        set((s) => ({
          guestWatchlist: s.guestWatchlist.map((x) =>
            x.ticker === ticker && x.type === type
              ? {
                  ...x,
                  shares: shares ?? undefined,
                  cost_per_share: cost ?? undefined,
                  entry_date: entryDate ?? x.entry_date,
                }
              : x
          ),
        })),

      clearGuest: () => set({ isGuest: false, guestWatchlist: [] }),
    }),
    {
      name: "leek-check-guest-v1",
      onRehydrateStorage: () => (state) => {
        state?.setHydrated();
      },
    }
  )
);
