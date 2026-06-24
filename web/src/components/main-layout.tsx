"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Home, Star, Search, TrendingUp, Radio, User, Ghost, Wallet
} from "lucide-react";
import { useSession } from "@/lib/store";
import { Button } from "@/components/ui/button";
import { useRouter } from "next/navigation";

type Tab = "brief" | "watch" | "search" | "rank" | "scan" | "me";

const TABS: { id: Tab; icon: typeof Home; label: string }[] = [
  { id: "brief", icon: Home, label: "晨報" },
  { id: "watch", icon: Star, label: "觀察" },
  { id: "search", icon: Search, label: "搜尋" },
  { id: "scan", icon: Radio, label: "策略" },
  { id: "me", icon: User, label: "我的" },
];

export function MainLayout() {
  const [active, setActive] = useState<Tab>("brief");
  const isGuest = useSession((s) => s.isGuest);
  const clearGuest = useSession((s) => s.clearGuest);
  const router = useRouter();

  return (
    <div className="min-h-dvh pb-[calc(72px+env(safe-area-inset-bottom))]">
      {/* Top status bar safe area */}
      <div style={{ height: "env(safe-area-inset-top)" }} className="bg-ink-950" />

      {/* Guest banner */}
      {isGuest && (
        <motion.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          className="bg-amber-500/10 border-b border-amber-500/30 px-4 py-2 flex items-center justify-between"
        >
          <div className="flex items-center gap-2 text-amber-300 text-sm">
            <Ghost className="w-4 h-4" />
            訪客模式 · 資料只存裝置
          </div>
          <Button
            size="sm"
            variant="outline"
            onClick={() => { clearGuest(); router.push("/login"); }}
          >
            註冊保存
          </Button>
        </motion.div>
      )}

      {/* Content area */}
      <main className="px-4 pt-4 animate-page-in">
        <AnimatePresence mode="wait">
          <motion.div
            key={active}
            initial={{ opacity: 0, x: 8 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -8 }}
            transition={{ duration: 0.2 }}
          >
            {active === "brief" && <BriefPanel />}
            {active === "watch" && <WatchPanel />}
            {active === "search" && <SearchPanel />}
            {active === "scan" && <ScanPanel />}
            {active === "me" && <MePanel />}
          </motion.div>
        </AnimatePresence>
      </main>

      {/* Bottom tab bar */}
      <nav className="fixed bottom-0 inset-x-0 bg-ink-950/95 backdrop-blur-md border-t border-ink-700 pb-[env(safe-area-inset-bottom)]">
        <div className="flex justify-around">
          {TABS.map(({ id, icon: Icon, label }) => {
            const isActive = active === id;
            return (
              <button
                key={id}
                onClick={() => setActive(id)}
                className="flex-1 flex flex-col items-center gap-1 py-3 relative"
              >
                <Icon
                  className={`w-5 h-5 transition-colors ${isActive ? "text-brand-400" : "text-slate-500"}`}
                />
                <span
                  className={`text-[10px] font-semibold transition-colors ${isActive ? "text-brand-300" : "text-slate-500"}`}
                >
                  {label}
                </span>
                {isActive && (
                  <motion.div
                    layoutId="active-tab-pill"
                    className="absolute top-0 left-1/2 -translate-x-1/2 w-8 h-0.5 bg-brand-400 rounded-full"
                  />
                )}
              </button>
            );
          })}
        </div>
      </nav>
    </div>
  );
}

function BriefPanel() {
  return (
    <div className="space-y-4">
      <div className="bg-gradient-to-br from-brand-700/40 to-ink-900 border border-ink-700 rounded-2xl p-5">
        <div className="text-xs tracking-widest text-brand-300 font-bold">
          {new Date().toLocaleDateString("zh-TW", { dateStyle: "long" })}
        </div>
        <h2 className="text-2xl font-extrabold text-white mt-1">
          🌅 早安,今日市場健檢
        </h2>
        <p className="text-brand-200 text-sm mt-2">
          開盤前 5 分鐘看一眼 · 盤後分析,不適合盤中即時下單
        </p>
      </div>

      <PlaceholderCard
        title="📡 [Paper] 策略訊號"
        desc="從 1958 檔台股掃出今日命中"
        badge="即將上線"
      />
      <PlaceholderCard
        title="🩺 觀察清單巡禮"
        desc="一鍵掃描所有持股 4 面健檢分數"
        badge="即將上線"
      />
      <PlaceholderCard
        title="🌡️ 大盤狀態"
        desc="TAIEX / VIX / 集中度 / 法人動向"
        badge="即將上線"
      />
    </div>
  );
}

function WatchPanel() {
  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-extrabold text-white">⭐ 觀察清單</h2>
      <p className="text-slate-400 text-sm">卡牌風格 · 集中度警示 · 一鍵健檢</p>
      <PlaceholderCard
        title="開發中"
        desc="這個 tab 重做中,先去 search 找股票"
        badge="WIP"
      />
    </div>
  );
}

function SearchPanel() {
  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-extrabold text-white">🔍 搜尋</h2>
      <PlaceholderCard
        title="開發中"
        desc="輸入股票代碼或公司名查 4 面健檢"
        badge="WIP"
      />
    </div>
  );
}

function ScanPanel() {
  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-extrabold text-white">📡 策略掃描</h2>
      <PlaceholderCard
        title="7 個真 alpha 策略"
        desc="月營收 YoY / 散戶極端 / 量縮跌停反彈 / ..."
        badge="WIP"
      />
    </div>
  );
}

function MePanel() {
  const isGuest = useSession((s) => s.isGuest);
  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-extrabold text-white">👤 我的</h2>
      {isGuest ? (
        <div className="bg-amber-500/10 border border-amber-500/30 rounded-xl p-4">
          <p className="text-amber-300 text-sm">
            👻 訪客模式 — 資料只存裝置,清快取就消失。註冊即可永久雲端同步。
          </p>
        </div>
      ) : (
        <PlaceholderCard
          title="帳號 & 設定"
          desc="登出 / 變更密碼 / 刪除帳號"
          badge="WIP"
        />
      )}
    </div>
  );
}

function PlaceholderCard({
  title, desc, badge,
}: { title: string; desc: string; badge?: string }) {
  return (
    <div className="bg-ink-900/50 border border-ink-700 rounded-2xl p-4">
      <div className="flex items-start justify-between gap-2">
        <h3 className="font-bold text-white">{title}</h3>
        {badge && (
          <span className="text-[10px] font-bold tracking-wider text-amber-300 bg-amber-500/20 px-2 py-0.5 rounded-full">
            {badge}
          </span>
        )}
      </div>
      <p className="text-sm text-slate-400 mt-2">{desc}</p>
    </div>
  );
}
