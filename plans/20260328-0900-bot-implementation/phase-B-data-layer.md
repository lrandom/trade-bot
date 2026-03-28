# Phase B — Data Layer

## Context
- Parent plan: [plan.md](plan.md)
- Design spec: `plans/20260327-1200-gold-trading-bot/phase-02-data-layer.md`
- Depends on: [phase-A-foundation.md](phase-A-foundation.md)
- Blocks: Phase C (LLM Engine), Phase D (Risk + Modes)

## Overview
- **Date:** 2026-03-28
- **Priority:** P0
- **Status:** pending
- Binance REST + WebSocket client, 6-timeframe OHLCV fetching, all technical indicators via pandas-ta, S/R detection, macro data (FRED + NewsAPI), `MarketSnapshot` dataclass.

## Key Insights
- Use `python-binance` not ccxt — direct Futures API, no abstraction overhead
- `pandas-ta` pure-pip, 200+ indicators including SuperTrend + VWAP
- WebSocket for real-time (scalp); REST only for historical on startup/refresh
- FRED key series: `DFF` (fed funds rate), `T10Y2Y` (10Y-2Y yield spread)
- NewsAPI: 100 req/day free — cache 4h; fallback to empty list on limit
- `asyncio.gather` to fetch all timeframes concurrently — reduces snapshot build time
- `MarketSnapshot` is the single contract between data layer and LLM engine

## Requirements

**Functional:**
- Fetch OHLCV for 6 TFs: `1m`, `5m`, `15m`, `1h`, `4h`, `1d`
- Indicators per TF: EMA20/50/200, RSI(14), MACD(12,26,9), ATR(14), SuperTrend(10,3), VWAP, Bollinger Bands(20,2)
- S/R detection: rolling peak/trough, min 2 touches, 0.2% tolerance
- WebSocket 1m kline stream (scalp mode)
- FRED: fed funds rate + yield spread
- NewsAPI: top 5 headlines, 4h cache
- `MarketSnapshot` dataclass exposed to LLM engine

**Non-functional:**
- 200 candles per TF (enough for EMA200)
- WebSocket reconnect: 3 retries, exponential backoff (1s, 2s, 4s)
- All async

## Architecture

```
bot/data/
├── __init__.py
├── binance_client.py      # AsyncClient singleton, testnet toggle
├── ohlcv.py               # fetch_ohlcv(symbol, interval, limit=200)
├── indicators.py          # compute_indicators(df) → df with all columns
├── support_resistance.py  # find_levels(df) → (supports, resistances)
├── websocket_feed.py      # start_kline_stream(), CandleBuffer
├── macro.py               # fetch_fred_data(), fetch_news() with cache
└── snapshot.py            # MarketSnapshot dataclass, build_snapshot()
```

### MarketSnapshot
```python
@dataclass
class MarketSnapshot:
    symbol: str
    timestamp: datetime          # UTC
    mode: str
    timeframes: dict[str, pd.DataFrame]  # TF → df with indicator columns
    indicators: dict[str, dict[str, float]]  # TF → {col: last_value}
    support_levels: list[float]
    resistance_levels: list[float]
    fed_funds_rate: float | None
    yield_spread: float | None
    news_headlines: list[str]
    mark_price: float
```

### Indicator columns on each df
```
ema_20, ema_50, ema_200
rsi_14
macd, macd_signal, macd_hist
atr_14
supertrend, supertrend_dir   # 1=up, -1=down
vwap
bb_upper, bb_mid, bb_lower
```

## Related Code Files

| Action | File | Description |
|--------|------|-------------|
| create | `bot/data/binance_client.py` | AsyncClient singleton |
| create | `bot/data/ohlcv.py` | REST OHLCV fetcher |
| create | `bot/data/indicators.py` | pandas-ta computations |
| create | `bot/data/support_resistance.py` | S/R level detection |
| create | `bot/data/websocket_feed.py` | Live kline stream + CandleBuffer |
| create | `bot/data/macro.py` | FRED + NewsAPI + cache |
| create | `bot/data/snapshot.py` | MarketSnapshot + build_snapshot() |
| create | `tests/test_indicators.py` | Smoke test indicator columns |

## Implementation Steps

1. **`bot/data/binance_client.py`**:
   - Module-level `_client: AsyncClient | None = None`
   - `async def get_client() -> AsyncClient`: singleton; on first call `await AsyncClient.create(api_key, secret, testnet=settings.BINANCE_TESTNET)`
   - `async def close_client()`: call `_client.close_connection()` during shutdown

2. **`bot/data/ohlcv.py`** — `fetch_ohlcv(symbol, interval, limit=200) -> pd.DataFrame`:
   - Call `client.futures_klines(symbol=symbol, interval=interval, limit=limit)`
   - Columns: `open_time, open, high, low, close, volume` (all float)
   - Set `open_time` as `pd.DatetimeIndex` (UTC, unit=`ms`)
   - Return df sorted ascending, index reset

3. **`bot/data/indicators.py`** — `compute_indicators(df) -> pd.DataFrame`:
   ```python
   import pandas_ta as ta

   df['ema_20']  = ta.ema(df['close'], length=20)
   df['ema_50']  = ta.ema(df['close'], length=50)
   df['ema_200'] = ta.ema(df['close'], length=200)
   df['rsi_14']  = ta.rsi(df['close'], length=14)

   macd = ta.macd(df['close'], fast=12, slow=26, signal=9)
   df['macd']        = macd['MACD_12_26_9']
   df['macd_signal'] = macd['MACDs_12_26_9']
   df['macd_hist']   = macd['MACDh_12_26_9']

   df['atr_14'] = ta.atr(df['high'], df['low'], df['close'], length=14)

   st = ta.supertrend(df['high'], df['low'], df['close'], length=10, multiplier=3.0)
   df['supertrend']     = st['SUPERT_10_3.0']
   df['supertrend_dir'] = st['SUPERTd_10_3.0']

   df['vwap'] = ta.vwap(df['high'], df['low'], df['close'], df['volume'])

   bb = ta.bbands(df['close'], length=20, std=2)
   df['bb_upper'] = bb['BBU_20_2.0']
   df['bb_mid']   = bb['BBM_20_2.0']
   df['bb_lower'] = bb['BBL_20_2.0']

   return df
   ```

4. **`bot/data/support_resistance.py`** — `find_levels(df, window=10, min_touches=2, price_tolerance=0.002)`:
   - Resistance: rolling max of `high` over `window` bars (center=True) — collect unique peaks
   - Support: rolling min of `low` over `window` bars (center=True) — collect unique troughs
   - Count touches: price within `price_tolerance` (0.2%) of level
   - Return only levels with `>= min_touches` touches
   - Return `(sorted_supports, sorted_resistances)` both ascending

5. **`bot/data/websocket_feed.py`** — `CandleBuffer`:
   - `deque(maxlen=200)` of 1m candles
   - `async def start_kline_stream(symbol: str, callback: Callable)`:
     - Use `BinanceSocketManager(client).futures_kline_socket(symbol, '1m')`
     - On each message: if `msg['k']['x']` is True (candle closed), update buffer, call `callback(df)`
     - Wrap in while loop with `try/except` for reconnect; backoff: 1s, 2s, 4s; max 3 retries then log critical

6. **`bot/data/macro.py`**:
   - `fetch_fred_data() -> dict`: use `pandas_datareader.data.DataReader(['DFF','T10Y2Y'], 'fred', start, end)`. Return `{'fed_rate': float, 'yield_spread': float}`. Catch exceptions → return `{'fed_rate': None, 'yield_spread': None}`
   - `fetch_news(query='gold XAUUSD', page_size=5) -> list[str]`: call NewsAPI. Cache result + timestamp. If cache < 4h old return cached. Return list of title strings.
   - Module-level `_macro_cache = {'data': None, 'fetched_at': None, 'ttl_seconds': 14400}`

7. **`bot/data/snapshot.py`** — `async build_snapshot(mode: str) -> MarketSnapshot`:
   - `from bot.modes.config import MODES` — get `timeframes` list for mode
   - `asyncio.gather(*[fetch_ohlcv('XAUUSDT', tf) for tf in timeframes])` — concurrent fetch
   - `compute_indicators(df)` on each result
   - `find_levels(df)` on the primary TF df
   - `fetch_fred_data()` + `fetch_news()` (use cache)
   - `client.futures_mark_price(symbol='XAUUSDT')` for `mark_price`
   - Build `indicators` dict: `{tf: {col: df[col].iloc[-1] for col in indicator_cols}}`
   - Return `MarketSnapshot(...)` with `timestamp=utc_now()`

8. **`tests/test_indicators.py`** — smoke test:
   - Generate synthetic 200-row OHLCV df with random walk prices
   - Call `compute_indicators(df)`
   - Assert all expected columns exist
   - Assert last row has no NaN in any indicator column

## Todo

- [ ] `binance_client.py` singleton with testnet support
- [ ] `ohlcv.py` fetch + parse to DataFrame
- [ ] `indicators.py` all 9 indicator groups
- [ ] `support_resistance.py` peak/trough detection
- [ ] `websocket_feed.py` CandleBuffer + reconnect loop
- [ ] `macro.py` FRED + NewsAPI + 4h cache
- [ ] `snapshot.py` build_snapshot() async
- [ ] `tests/test_indicators.py` smoke test
- [ ] Verify no NaN in last row of 200-bar df

## Success Criteria
- `build_snapshot('intraday')` returns valid `MarketSnapshot` with all indicator columns populated
- WebSocket receives 1m candles and calls callback within 1s of candle close
- FRED returns non-None fed_rate and yield_spread
- `indicators['1h']['atr_14']` is a float on 200-bar df
- NewsAPI cache: second call within 4h returns same result without HTTP request

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| pandas-ta column name changes | Medium | Pin version; assert column names in test |
| Binance testnet rate limits | Medium | Use 200 limit (1 REST call per TF), not polling |
| NewsAPI 100/day limit exceeded | Low | 4h cache; fallback empty list |
| WebSocket disconnect during fast market | High | Exponential backoff reconnect; fallback to REST polling |
| FRED API unavailable | Low | Graceful degradation — None values; LLM prompt notes missing |

## Security Considerations
- API keys via `Settings` only
- Testnet=true default prevents accidental live trading during dev
- WebSocket callback should not block — wrap in `asyncio.create_task()`

## Next Steps
- Phase C: `MarketSnapshot` consumed by LLM prompts
- Phase D: ATR value from snapshot used by risk engine SL validation
