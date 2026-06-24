import { create } from "zustand";
import { persist } from "zustand/middleware";

type SessionState = {
  isGuest: boolean;
  setGuest: (v: boolean) => void;
  guestWatchlist: string[];
  addGuestTicker: (tk: string) => void;
  removeGuestTicker: (tk: string) => void;
  clearGuest: () => void;
};

export const useSession = create<SessionState>()(
  persist(
    (set) => ({
      isGuest: false,
      setGuest: (v) => set({ isGuest: v }),
      guestWatchlist: [],
      addGuestTicker: (tk) =>
        set((s) => ({
          guestWatchlist: s.guestWatchlist.includes(tk)
            ? s.guestWatchlist
            : [...s.guestWatchlist, tk],
        })),
      removeGuestTicker: (tk) =>
        set((s) => ({ guestWatchlist: s.guestWatchlist.filter((x) => x !== tk) })),
      clearGuest: () => set({ isGuest: false, guestWatchlist: [] }),
    }),
    { name: "leek-check-guest-v1" }
  )
);
