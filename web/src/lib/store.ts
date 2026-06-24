"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { WatchlistItem } from "@/lib/watchlist";

type SessionState = {
  isGuest: boolean;
  setGuest: (v: boolean) => void;

  /** 訪客 watchlist(只存 localStorage) */
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
      isGuest: false,
      setGuest: (v) => set({ isGuest: v }),

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
    { name: "leek-check-guest-v1" }
  )
);
