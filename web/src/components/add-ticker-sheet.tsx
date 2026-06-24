"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Plus, Search } from "lucide-react";
import { Sheet } from "@/components/ui/sheet";
import { Input } from "@/components/ui/input";
import { api, type TickerInfo } from "@/lib/api";

type Props = {
  open: boolean;
  onClose: () => void;
  onPick: (item: TickerInfo) => void | Promise<void>;
  existingKeys: Set<string>;
};

export function AddTickerSheet({ open, onClose, onPick, existingKeys }: Props) {
  const [q, setQ] = useState("");
  const { data: results, isFetching } = useQuery({
    queryKey: ["search", q],
    queryFn: () => api.searchTickers(q),
    enabled: q.length >= 1,
    staleTime: 300_000,
  });

  return (
    <Sheet open={open} onClose={onClose} title="➕ 加入觀察清單">
      <div className="space-y-3 pb-8">
        <Input
          autoFocus
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="例:2330 / 台積電 / 0050"
        />
        {q.length >= 1 && isFetching && (
          <div className="flex items-center gap-2 text-sm text-slate-500 px-1">
            <Search className="w-4 h-4 animate-pulse" /> 搜尋中⋯
          </div>
        )}
        <div className="space-y-2 max-h-[60dvh] overflow-y-auto">
          {(results ?? []).map((r) => {
            const key = `${r.ticker}-${r.type}`;
            const already = existingKeys.has(key);
            return (
              <button
                key={key}
                disabled={already}
                onClick={async () => {
                  await onPick(r);
                  setQ("");
                }}
                className="w-full text-left bg-ink-800/50 border border-ink-700 hover:bg-ink-700/80 disabled:opacity-40 disabled:cursor-not-allowed rounded-xl p-3 transition-colors flex items-center justify-between active:scale-[0.98]"
              >
                <div>
                  <div className="font-bold text-white">{r.name || r.ticker}</div>
                  <div className="text-xs text-slate-500">{r.industry || "—"}</div>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-brand-300 font-mono text-sm">{r.ticker}</span>
                  {already ? (
                    <span className="text-[10px] text-slate-500">已加</span>
                  ) : (
                    <Plus className="w-5 h-5 text-brand-400" />
                  )}
                </div>
              </button>
            );
          })}
          {q.length >= 1 && !isFetching && results?.length === 0 && (
            <div className="text-sm text-slate-500 text-center py-6">
              找不到「{q}」相關股票
            </div>
          )}
        </div>
      </div>
    </Sheet>
  );
}
