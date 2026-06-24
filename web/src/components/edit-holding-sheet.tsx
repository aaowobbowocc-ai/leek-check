"use client";

import { useState, useEffect } from "react";
import { Sheet } from "@/components/ui/sheet";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import type { WatchlistItem } from "@/lib/watchlist";

type Props = {
  item: WatchlistItem | null;
  open: boolean;
  onClose: () => void;
  onSave: (shares: number | null, cost: number | null, entryDate: string | null) => void | Promise<void>;
  onRemove?: () => void | Promise<void>;
};

export function EditHoldingSheet({ item, open, onClose, onSave, onRemove }: Props) {
  const [shares, setShares] = useState("");
  const [cost, setCost] = useState("");
  const [entryDate, setEntryDate] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (item) {
      setShares(item.shares != null ? String(item.shares) : "");
      setCost(item.cost_per_share != null ? String(item.cost_per_share) : "");
      setEntryDate(item.entry_date ?? "");
    }
  }, [item]);

  if (!item) return null;

  const handleSave = async () => {
    setSaving(true);
    try {
      const s = shares.trim() ? Number(shares) : null;
      const c = cost.trim() ? Number(cost) : null;
      const d = entryDate.trim() || null;
      await onSave(s, c, d);
      onClose();
    } finally {
      setSaving(false);
    }
  };

  return (
    <Sheet open={open} onClose={onClose} title={`⚙️ 編輯 ${item.ticker}`}>
      <div className="space-y-4 pb-6">
        <div className="grid grid-cols-2 gap-3">
          <Field label="持股(股)" hint="例:1000 = 1 張">
            <Input
              type="number"
              inputMode="numeric"
              value={shares}
              onChange={(e) => setShares(e.target.value)}
              placeholder="1000"
            />
          </Field>
          <Field label="平均成本" hint="每股價格">
            <Input
              type="number"
              inputMode="decimal"
              value={cost}
              onChange={(e) => setCost(e.target.value)}
              placeholder="500"
            />
          </Field>
        </div>
        <Field label="進場日期(選填)" hint="YYYY-MM-DD">
          <Input
            type="date"
            value={entryDate}
            onChange={(e) => setEntryDate(e.target.value)}
          />
        </Field>

        <div className="bg-brand-500/10 border border-brand-500/30 rounded-xl p-3 text-xs text-brand-200">
          💡 填好之後,觀察 tab 會自動顯示**損益 + 集中度**。空著就只追蹤股價。
        </div>

        <div className="flex gap-2 pt-2">
          <Button variant="primary" size="lg" className="flex-1" onClick={handleSave} disabled={saving}>
            {saving ? "儲存中..." : "💾 儲存"}
          </Button>
          {onRemove && (
            <Button variant="danger" size="lg" onClick={onRemove}>
              🗑️ 移除
            </Button>
          )}
        </div>
      </div>
    </Sheet>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <div>
        <div className="text-xs font-bold text-slate-300">{label}</div>
        {hint && <div className="text-[10px] text-slate-500">{hint}</div>}
      </div>
      {children}
    </div>
  );
}
