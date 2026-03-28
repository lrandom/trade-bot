# Gold Trading Bot (XAUUSDT) - Technical Research Report
**Date:** March 27, 2026 | **Scope:** Binance Futures APIs & Technical Indicators

---

## 1. BINANCE FUTURES API SELECTION

### python-binance vs ccxt

**RECOMMENDATION: python-binance**

| Aspect | python-binance | ccxt |
|--------|---|---|
| **Futures Support** | Full native Binance Futures API | Abstracted (works but less optimized) |
| **Speed** | Direct REST/WebSocket | Higher latency (extra abstraction layer) |
| **Documentation** | Excellent for Binance-specific | Generic, less detailed |
| **Maintenance** | Active, Binance-aligned updates | Community-driven, slower |
| **Learning Curve** | Moderate (Binance-native) | Shallow (generic API) |

**python-binance Pros:** Full rate limit support (1200 req/min), WebSocket streams, margin/leverage, position management. **Cons:** Binance-only.

**ccxt Pros:** Multi-exchange support, standardized interface. **Cons:** Overhead, missing Binance-specific features, slower WebSocket.

---

## 2. KEY BINANCE FUTURES ENDPOINTS (python-binance)

```
REST Endpoints:
- get_klines()                 # OHLCV data (1m to 1M)
- get_open_orders()            # Active orders
- create_order()               # Limit/market/stop-loss
- cancel_order()               # Order management
- get_account()                # Balance, margin level
- get_position()               # Current positions
- get_mark_price()             # Real-time mark price

WebSocket Streams (Recommended):
- @kline_1m, @kline_5m         # Candlestick updates
- @ticker                      # 24h price ticker
- @trade                       # Individual trades (real-time)
- @depth@100ms                 # Order book updates
```

**Rate Limits:** 1200 orders/min, 12,000 requests/min. Use weight headers for precise tracking.

**Error Handling:** Implement exponential backoff (1s → 2s → 4s), retry logic for 418/429 (rate limit). VWAP calculation requires @aggTrade stream (highest precision).

---

## 3. TECHNICAL INDICATORS: LIBRARY COMPARISON

### BEST CHOICE: pandas-ta

| Feature | pandas-ta | ta-lib | TA |
|---------|---|---|---|
| **Installation** | Pure Python (pip) | C compilation required | Basic |
| **Indicators** | 200+ (EMA, RSI, MACD, ATR, BBands, SuperTrend, VWAP) | 150+ | Limited |
| **Performance** | Moderate (numpy/numba) | Fast (C backend) | Slow |
| **Multi-timeframe** | Native support via df.merge() | Manual data handling | Painful |
| **Maintenance** | Active (GitHub stars: 5k+) | Legacy (minimal updates) | Stagnant |

**Installation:** `pip install pandas-ta` (zero dependencies beyond pandas/numpy)

**ta-lib Downsides:** Requires TA-Lib C library compilation (Windows/Mac pain), licensing concerns, slower updates.

**TA Downsides:** Missing modern indicators, unmaintained.

---

## 4. INDICATOR COMPUTATION PATTERNS

### Pandas-ta Implementation
```python
import pandas_ta as ta
import pandas as pd

# Read OHLCV
df = pd.DataFrame(binance_klines)  # close, high, low, volume, time

# Single-timeframe
df['ema_20'] = ta.ema(df['close'], length=20)
df['ema_50'] = ta.ema(df['close'], length=50)
df['ema_200'] = ta.ema(df['close'], length=200)
df['rsi'] = ta.rsi(df['close'], length=14)
df['macd'] = ta.macd(df['close'])['MACD_12_26_9']
df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
df['supertrend'] = ta.supertrend(df['high'], df['low'], df['close'], length=10, multiplier=3)['SUPERT_10_3']
df['vwap'] = ta.vwap(df['high'], df['low'], df['close'], df['volume'])
df['bbands'] = ta.bbands(df['close'], length=20)

# Multi-timeframe (resample + merge)
df_5m = df.resample('5min').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'})
df_5m['ema_50_5m'] = ta.ema(df_5m['close'], 50)
df = df.merge(df_5m[['ema_50_5m']], left_index=True, right_index=True, how='left').ffill()
```

### Support/Resistance Detection
**Simple Algorithm:** Find local peaks/troughs (rolling max/min ± 5 candles), mark as resistance/support if tested 2+ times.

```python
def find_levels(df, window=10, min_tests=2):
    df['peak'] = df['high'].rolling(window, center=True).max()
    df['valley'] = df['low'].rolling(window, center=True).min()
    # Filter touching events, return significant levels
```

---

## 5. MACRO DATA SOURCES

### FRED API (Federal Reserve Economic Data)
**URL:** https://fred.stlouisfed.org/api/
**Free Tier:** Unlimited API calls (key required, instant registration)
**Key Series:**
- `DFF` - Effective Federal Funds Rate
- `T10Y2Y` - 10-Year minus 2-Year Treasury Spread
- `UNRATE` - Unemployment Rate

**Python:** `pip install pandas-datareader` → `DataReader('DFF', 'fred', start, end)`

**Advantages:** Official, zero cost, no rate limit.

### DXY (US Dollar Index) Sources
**Problem:** DXY is proprietary (ICE), no free official API.

**Alternatives:**
1. **scrape investing.com** (unreliable, breaks with DOM changes)
2. **Alpha Vantage** (free tier: 5 calls/min, $20/mo for DXY)
3. **Binance Spot USDT pairs** (proxy: EURUSD, JPYUSD from klines)
4. **Commodities APIs** (Finnhub free tier: limited tickers)

**RECOMMENDATION:** Use Fed rate + Treasury spread as proxy (gold inverse correlation with DXY ~80%). Skip direct DXY if budget-constrained.

### News APIs
| Source | Free Tier | Rate Limit | Gold Coverage |
|--------|---|---|---|
| NewsAPI | 100 req/day | 1 req/sec | Moderate |
| GNews | 100 req/day | 30 req/min | Moderate |
| Polygon.io | 5 calls/min | — | Good (crypto-focused) |

**Recommendation:** Polygon.io (crypto-native) or skip news for MVP (use only technical + macro).

---

## 6. SCHEDULING TASKS: APScheduler vs asyncio

### RECOMMENDATION: APScheduler (for gold bot)

| Aspect | APScheduler | asyncio |
|--------|---|---|
| **Interval Jobs** | Simple: `scheduler.add_job(func, 'interval', minutes=5)` | Complex: manual asyncio.sleep() loops |
| **Cron Support** | Yes ('cron' trigger, e.g., '0 9 * * MON') | No native |
| **Persistence** | SQLAlchemy/JSON store across restarts | Ephemeral |
| **Error Handling** | Automatic retry policies, max_instances | Manual try/except |
| **CPU Overhead** | Minimal (threaded executor) | Minimal (single event loop) |
| **WebSocket Coexistence** | Requires ThreadPoolExecutor (separate thread) | Native async integration |

**APScheduler Pros:** Simpler syntax, job queuing, less boilerplate.
**asyncio Pros:** Lighter memory (single event loop), better for pure async code.

### Hybrid Pattern (GOLD BOT IDEAL)
```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
scheduler = AsyncIOScheduler()
scheduler.add_job(async_indicator_calc, 'interval', minutes=5)
scheduler.start()  # Runs inside asyncio event loop
```

This avoids thread overhead while maintaining simplicity.

---

## 7. CRITICAL INTEGRATION PATTERNS

**Binance WebSocket + Indicator Compute (5min bars):**
- Stream: `@kline_5m` for candlestick closes
- Compute: EMA/RSI/ATR on each candle close
- Schedule: APScheduler fires indicator calc, triggers signal logic
- Order: Use `create_order(symbol, side, quantity)` from REST endpoint

**Rate Limit Strategy:**
- WebSocket: FREE (no rate limit)
- REST: 1 account request/sec, batch orders, use position size from account cache

---

## UNRESOLVED QUESTIONS

1. **Leverage/Margin:** Will bot use 1x (spot-equivalent) or leverage (2-5x)? Changes risk management significantly.
2. **Backtest Framework:** Use Backtrader, VectorBT, or rolling historical window? Not covered here.
3. **Order Types:** Market fills vs limit orders? Slippage tolerance?
4. **Gold Micro Contract:** XAUUSDT contract size? (Verify Binance docs for position sizing math.)
5. **News Integration ROI:** Worth maintenance cost vs raw technical+macro?
6. **Capital Requirements:** Min. deposit for leverage tier? Binance Futures KYC levels?

---

**Sources:**
- python-binance GitHub (main library for Futures)
- pandas-ta GitHub (indicator suite)
- FRED API documentation
- APScheduler official docs
- Binance Futures API reference (2024-2025)
