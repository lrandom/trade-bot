-- Gold Trading Bot — SQLite Schema
-- Run on every startup via init_db(); all tables use IF NOT EXISTS.
-- All timestamps stored as UTC text (ISO-8601). Convert to ICT only for display.

-- ---------------------------------------------------------------------------
-- signals: LLM-generated trade signals
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS signals (
    id              TEXT PRIMARY KEY,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    mode            TEXT NOT NULL,          -- swing | intraday | scalp
    action          TEXT NOT NULL,          -- BUY | SELL | HOLD
    entry           REAL,
    sl              REAL,
    tp1             REAL,
    tp2             REAL,
    tp3             REAL,
    confidence      INTEGER,                -- 0-100
    trend_bias      TEXT,                   -- bullish | bearish | neutral
    htf_bias        TEXT,                   -- higher-timeframe bias
    reasoning       TEXT,
    status          TEXT DEFAULT 'pending', -- pending | approved | rejected | executed | expired
    telegram_msg_id INTEGER
);

-- ---------------------------------------------------------------------------
-- trades: executed (live or paper) trade records
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trades (
    id              TEXT PRIMARY KEY,
    signal_id       TEXT REFERENCES signals(id),
    opened_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
    closed_at       DATETIME,
    side            TEXT,                   -- LONG | SHORT
    entry           REAL,
    close_price     REAL,
    quantity        REAL,
    leverage        INTEGER,
    pnl             REAL,
    pnl_pct         REAL,
    tp_hit          INTEGER DEFAULT 0,      -- number of TPs hit (0-3)
    status          TEXT DEFAULT 'open',    -- open | closed | stopped | liquidated
    mode            TEXT,
    llm_provider    TEXT
);

-- ---------------------------------------------------------------------------
-- paper_orders: paper-trading order log (more granular than trades)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_orders (
    id              TEXT PRIMARY KEY,       -- UUID assigned by MockTrader
    symbol          TEXT NOT NULL DEFAULT 'XAUUSDT',
    side            TEXT NOT NULL,          -- BUY | SELL
    mode            TEXT NOT NULL,
    entry           REAL,
    stop_loss       REAL,
    tp1             REAL,
    tp2             REAL,
    tp3             REAL,
    size            REAL,                   -- position size in oz
    open_time       DATETIME DEFAULT CURRENT_TIMESTAMP,
    close_time      DATETIME,
    close_price     REAL,
    pnl_usd         REAL,
    pnl_pct         REAL,
    status          TEXT DEFAULT 'open',    -- open | closed | stopped | cancelled | expired
    signal_id       TEXT REFERENCES signals(id),
    llm_provider    TEXT,
    confidence      INTEGER
);

-- ---------------------------------------------------------------------------
-- paper_stats: daily summary of paper trading performance
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_stats (
    date            TEXT NOT NULL,          -- YYYY-MM-DD (UTC)
    mode            TEXT NOT NULL,
    total_trades    INTEGER DEFAULT 0,
    wins            INTEGER DEFAULT 0,
    losses          INTEGER DEFAULT 0,
    win_rate        REAL DEFAULT 0.0,
    profit_factor   REAL DEFAULT 0.0,
    total_pnl_usd   REAL DEFAULT 0.0,
    max_drawdown    REAL DEFAULT 0.0,
    PRIMARY KEY (date, mode)
);

-- ---------------------------------------------------------------------------
-- llm_usage: token and cost tracking per LLM call
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS llm_usage (
    id              TEXT PRIMARY KEY,       -- UUID assigned by cost tracker
    timestamp       DATETIME DEFAULT CURRENT_TIMESTAMP,
    provider        TEXT NOT NULL,          -- anthropic | openai | gemini | deepseek
    model           TEXT NOT NULL,
    call_type       TEXT,                   -- macro | htf | mtf | ltf | signal | management
    trading_mode    TEXT,                   -- swing | intraday | scalp
    input_tokens    INTEGER DEFAULT 0,
    output_tokens   INTEGER DEFAULT 0,
    cost_usd        REAL DEFAULT 0.0,
    signal_id       TEXT REFERENCES signals(id)
);

-- ---------------------------------------------------------------------------
-- trading_fees: exchange fee records per trade leg
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trading_fees (
    id              TEXT PRIMARY KEY,       -- UUID assigned by cost tracker
    timestamp       DATETIME DEFAULT CURRENT_TIMESTAMP,
    trade_id        TEXT REFERENCES trades(id),
    symbol          TEXT DEFAULT 'XAUUSDT',
    side            TEXT,                   -- BUY | SELL
    quantity        REAL DEFAULT 0.0,
    price           REAL DEFAULT 0.0,
    notional        REAL DEFAULT 0.0,       -- quantity * price
    order_type      TEXT,                   -- taker | maker
    fee_rate        REAL DEFAULT 0.0,
    fee_asset       TEXT DEFAULT 'USDT',
    fee_amount      REAL DEFAULT 0.0,
    fee_usd         REAL DEFAULT 0.0,
    leverage        INTEGER DEFAULT 1
);

-- ---------------------------------------------------------------------------
-- infra_costs: manual infrastructure cost entries (VPS, API subscriptions…)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS infra_costs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    month           TEXT NOT NULL,          -- YYYY-MM
    category        TEXT NOT NULL,          -- vps | api | other
    description     TEXT,
    cost_usd        REAL DEFAULT 0.0,
    UNIQUE (month, category)
);

-- ---------------------------------------------------------------------------
-- cost_summary: daily rolled-up cost + PnL view (written by cost module)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cost_summary (
    date            TEXT PRIMARY KEY,       -- YYYY-MM-DD (UTC)
    llm_cost_usd    REAL DEFAULT 0.0,
    trading_fee_usd REAL DEFAULT 0.0,
    infra_cost_usd  REAL DEFAULT 0.0,
    total_cost_usd  REAL DEFAULT 0.0,
    trading_pnl_usd REAL DEFAULT 0.0,
    net_profit_usd  REAL DEFAULT 0.0
);

-- ---------------------------------------------------------------------------
-- health_log: periodic health-check snapshots
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS health_log (
    timestamp       DATETIME PRIMARY KEY DEFAULT CURRENT_TIMESTAMP,
    all_ok          INTEGER NOT NULL DEFAULT 1,  -- 1 = healthy, 0 = degraded
    details         TEXT,                         -- JSON blob of component statuses
    uptime_seconds  INTEGER DEFAULT 0
);

-- ---------------------------------------------------------------------------
-- bot_events: structured event log for auditing and alerting
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bot_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       DATETIME DEFAULT CURRENT_TIMESTAMP,
    event_type      TEXT NOT NULL,          -- startup | shutdown | error | trade | signal | alert
    component       TEXT,                   -- orchestrator | llm | trader | telegram …
    message         TEXT,
    severity        TEXT DEFAULT 'info'     -- debug | info | warning | error | critical
);

-- ---------------------------------------------------------------------------
-- filter_log: signal filter decisions for post-analysis
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS filter_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       DATETIME DEFAULT CURRENT_TIMESTAMP,
    signal_id       TEXT REFERENCES signals(id),
    filter_type     TEXT NOT NULL,          -- spike | dxy | silver | confidence | composite
    passed          INTEGER NOT NULL,       -- 1 = passed, 0 = blocked
    reason          TEXT,
    spike_ratio     REAL,
    dxy_status      TEXT,
    silver_aligned  INTEGER,               -- 1 | 0 | NULL
    confidence_adj  INTEGER,               -- adjustment applied
    original_conf   INTEGER,
    adjusted_conf   INTEGER
);

-- ---------------------------------------------------------------------------
-- config: mutable runtime settings stored in DB (overrides .env at runtime)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS config (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Default config rows (INSERT OR IGNORE preserves manual changes on restart)
INSERT OR IGNORE INTO config (key, value, updated_at) VALUES ('mode',            'swing',  CURRENT_TIMESTAMP);
INSERT OR IGNORE INTO config (key, value, updated_at) VALUES ('auto_trade',      'false',  CURRENT_TIMESTAMP);
INSERT OR IGNORE INTO config (key, value, updated_at) VALUES ('daily_pnl',       '0',      CURRENT_TIMESTAMP);
INSERT OR IGNORE INTO config (key, value, updated_at) VALUES ('circuit_breaker', 'false',  CURRENT_TIMESTAMP);
INSERT OR IGNORE INTO config (key, value, updated_at) VALUES ('paper_trade',     'true',   CURRENT_TIMESTAMP);
