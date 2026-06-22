-- 韭菜健檢 — Supabase Schema (multi-tenant 多使用者)
-- 在 Supabase Dashboard → SQL Editor 跑這個 once

-- ============================
-- 1. watchlists(觀察清單 + 持股)
-- ============================
CREATE TABLE IF NOT EXISTS public.watchlists (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    ticker TEXT NOT NULL,
    ticker_type TEXT NOT NULL DEFAULT 'twse',   -- twse / tpex / emerging
    note TEXT DEFAULT '',
    -- 持股欄位(空=純觀察,有值=記帳模式)
    shares INTEGER,
    cost_per_share NUMERIC(10, 4),
    entry_date DATE,
    position INTEGER DEFAULT 0,                  -- 拖曳排序用
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(user_id, ticker, ticker_type)
);

CREATE INDEX idx_watchlists_user ON public.watchlists(user_id, position);

-- ============================
-- 2. price_alerts(價格警示規則)
-- ============================
CREATE TABLE IF NOT EXISTS public.price_alerts (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    ticker TEXT NOT NULL,
    condition TEXT NOT NULL,        -- 'above' / 'below'
    target_price NUMERIC(10, 4) NOT NULL,
    note TEXT DEFAULT '',
    triggered_at TIMESTAMPTZ,
    triggered_price NUMERIC(10, 4),
    is_read BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_alerts_user_active ON public.price_alerts(user_id) WHERE triggered_at IS NULL;

-- ============================
-- 3. user_settings(個人設定)
-- ============================
CREATE TABLE IF NOT EXISTS public.user_settings (
    user_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    -- 手續費設定
    buy_fee_pct NUMERIC(6, 4) DEFAULT 0.1425,
    sell_fee_pct NUMERIC(6, 4) DEFAULT 0.1425,
    sell_tax_pct NUMERIC(6, 4) DEFAULT 0.3,
    fee_rebate_pct NUMERIC(5, 2) DEFAULT 70.0,
    -- AI 偏好
    default_frame TEXT DEFAULT 'mid',    -- short / mid / long
    default_tone TEXT DEFAULT 'casual',  -- pro / casual / simple
    -- 隱私
    hide_amounts BOOLEAN DEFAULT FALSE,
    -- 其他
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- ============================
-- 4. cash_accounts(存款 / 現金帳戶)
-- ============================
CREATE TABLE IF NOT EXISTS public.cash_accounts (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,                  -- 例: '永豐證券交割戶'
    category TEXT DEFAULT 'cash',        -- cash / broker / wallet / debt
    amount NUMERIC(15, 2) DEFAULT 0,
    currency TEXT DEFAULT 'TWD',
    position INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_cash_user ON public.cash_accounts(user_id, position);

-- ============================
-- 5. portfolio_snapshots(每日淨資產 snapshot — 趨勢圖用)
-- ============================
CREATE TABLE IF NOT EXISTS public.portfolio_snapshots (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    snapshot_date DATE NOT NULL,
    total_cash NUMERIC(15, 2),
    total_holdings_mv NUMERIC(15, 2),
    total_holdings_cost NUMERIC(15, 2),
    net_worth NUMERIC(15, 2),
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(user_id, snapshot_date)
);

CREATE INDEX idx_snapshots_user_date ON public.portfolio_snapshots(user_id, snapshot_date DESC);

-- ============================
-- Row Level Security(RLS)— 確保 user A 看不到 user B 資料
-- ============================
ALTER TABLE public.watchlists ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.price_alerts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cash_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.portfolio_snapshots ENABLE ROW LEVEL SECURITY;

-- 每個 table 同一個 pattern:user 只能 CRUD 自己的 row
CREATE POLICY "user_can_view_own_watchlists" ON public.watchlists
    FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "user_can_insert_own_watchlists" ON public.watchlists
    FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY "user_can_update_own_watchlists" ON public.watchlists
    FOR UPDATE USING (auth.uid() = user_id);
CREATE POLICY "user_can_delete_own_watchlists" ON public.watchlists
    FOR DELETE USING (auth.uid() = user_id);

CREATE POLICY "user_can_view_own_alerts" ON public.price_alerts
    FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "user_can_insert_own_alerts" ON public.price_alerts
    FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY "user_can_update_own_alerts" ON public.price_alerts
    FOR UPDATE USING (auth.uid() = user_id);
CREATE POLICY "user_can_delete_own_alerts" ON public.price_alerts
    FOR DELETE USING (auth.uid() = user_id);

CREATE POLICY "user_can_view_own_settings" ON public.user_settings
    FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "user_can_upsert_own_settings" ON public.user_settings
    FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY "user_can_update_own_settings" ON public.user_settings
    FOR UPDATE USING (auth.uid() = user_id);

CREATE POLICY "user_can_view_own_cash" ON public.cash_accounts
    FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "user_can_insert_own_cash" ON public.cash_accounts
    FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY "user_can_update_own_cash" ON public.cash_accounts
    FOR UPDATE USING (auth.uid() = user_id);
CREATE POLICY "user_can_delete_own_cash" ON public.cash_accounts
    FOR DELETE USING (auth.uid() = user_id);

CREATE POLICY "user_can_view_own_snapshots" ON public.portfolio_snapshots
    FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "user_can_insert_own_snapshots" ON public.portfolio_snapshots
    FOR INSERT WITH CHECK (auth.uid() = user_id);

-- ============================
-- Auto-update updated_at trigger
-- ============================
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER set_watchlists_updated_at BEFORE UPDATE ON public.watchlists
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
CREATE TRIGGER set_settings_updated_at BEFORE UPDATE ON public.user_settings
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
CREATE TRIGGER set_cash_updated_at BEFORE UPDATE ON public.cash_accounts
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
