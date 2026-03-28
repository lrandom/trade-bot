# Phase 02 — Data Layer

## Context
- Parent plan: [plan.md](plan.md)
- Depends on: [phase-01-project-setup.md](phase-01-project-setup.md)
- Research: [researcher-01-binance-indicators.md](research/researcher-01-binance-indicators.md)
- Blocks: phase-03, phase-04, phase-05

## Overview
- **Date:** 2026-03-27
- **Priority:** P0
- **Status:** pending
- Binance REST + WebSocket integration, 6-timeframe OHLCV fetching, all technical indicators via pandas-ta, macro data (FRED + NewsAPI).

## Key Insights
- **python-binance** not ccxt: direct Futures API, no abstraction overhead
- **pandas-ta** not ta-lib: pure pip install, 200+ indicators including SuperTrend + VWAP
- WebSocket for real-time (free, no rate limit); REST only for historical fetch on startup
- VWAP needs @aggTrade or @kline stream with volume — pandas-ta computes from OHLCV df
- FRED API: use `pandas-datareader`, key series: `DFF` (fed funds rate), `T10Y2Y` (yield spread)
- NewsAPI: 100 req/day free tier — cache results, refresh every 4h max
- Rate limit: REST 1200 req/min weight-tracked; use WebSocket for live prices

## Requirements

**Functional:**
- Fetch OHLCV for 6 timeframes: `1m`, `5m`, `15m`, `1h`, `4h`, `1d`
- Compute indicators per timeframe: EMA20/50/200, SuperTrend(10,3), VWAP, RSI(14), MACD(12,26,9), ATR(14), Bollinger Bands(20,2)
- Detect S/R levels (rolling window peak/trough, min 2 touches)
- WebSocket kline stream for 1m candles (scalp mode real-time)
- FRED: fed funds rate + yield spread on startup + daily refresh
- NewsAPI: top 5 gold-related headlines, refreshed every 4h
- Expose single `MarketSnapshot` dataclass consumed by LLM engine

**Non-functional:**
- Historical fetch: last 200 candles per timeframe (enough for EMA200)
- WebSocket reconnect on disconnect (max 3 retries, exponential backoff)
- All data functions async

## Architecture

```
bot/data/
├── __init__.py
├── binance_client.py     # python-binance AsyncClient wrapper
├── ohlcv.py              # fetch_ohlcv(symbol, interval, limit=200)
├── indicators.py         # compute_indicators(df) → df with all columns
├── support_resistance.py # find_levels(df) → List[float]
├── websocket_feed.py     # start_kline_stream(), CandleBuffer
├── macro.py              # fetch_fred_data(), fetch_news()
└── snapshot.py           # MarketSnapshot dataclass, build_snapshot()
```

### MarketSnapshot dataclass
```python
@dataclass
class MarketSnapshot:
    symbol: str
    timestamp: datetime
    mode: str
    # Per-timeframe dict: {"1m": df, "5m": df, ...}
    timeframes: Dict[str, pd.DataFrame]
    # Latest indicator values per TF
    indicators: Dict[str, Dict[str, float]]
    # Support/Resistance
    support_levels: List[float]
    resistance_levels: List[float]
    # Macro
    fed_funds_rate: float
    yield_spread: float       # T10Y2Y
    news_headlines: List[str] # top 5
    # Current price
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
| create | `bot/data/binance_client.py` | AsyncClient singleton, testnet toggle |
| create | `bot/data/ohlcv.py` | REST OHLCV fetcher |
| create | `bot/data/indicators.py` | pandas-ta computations |
| create | `bot/data/support_resistance.py` | S/R level detection |
| create | `bot/data/websocket_feed.py` | Live kline WebSocket |
| create | `bot/data/macro.py` | FRED + NewsAPI clients |
| create | `bot/data/snapshot.py` | MarketSnapshot builder |

## Implementation Steps

1. **binance_client.py** — Create `get_client()` returning `AsyncClient` singleton. Use `BINANCE_TESTNET=true` to point at testnet. On first call, await `AsyncClient.create(api_key, secret)`.

2. **ohlcv.py** — `fetch_ohlcv(symbol, interval, limit=200) -> pd.DataFrame`:
   - Call `client.futures_klines(symbol=symbol, interval=interval, limit=limit)`
   - Parse columns: `open_time, open, high, low, close, volume` (cast to float)
   - Set `open_time` as DatetimeIndex (UTC)
   - Return df sorted ascending

3. **indicators.py** — `compute_indicators(df: pd.DataFrame) -> pd.DataFrame`:
   ```python
   import pandas_ta as ta
   df['ema_20']  = ta.ema(df['close'], length=20)
   df['ema_50']  = ta.ema(df['close'], length=50)
   df['ema_200'] = ta.ema(df['close'], length=200)
   df['rsi_14']  = ta.rsi(df['close'], length=14)
   macd = ta.macd(df['close'], fast=12, slow=26, signal=9)
   df['macd'] = macd['MACD_12_26_9']
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

4. **support_resistance.py** — `find_levels(df, window=10, min_touches=2, price_tolerance=0.002) -> (List[float], List[float])`:
   - Resistance: rolling max of `high` over `window` candles, center=True
   - Support: rolling min of `low` over `window` candles, center=True
   - Count "touches" — price within `price_tolerance` (0.2%) of level
   - Return only levels with >= `min_touches`
   - Return `(support_list, resistance_list)` sorted ascending

5. **websocket_feed.py** — `CandleBuffer` class:
   - Holds last 200 `1m` candles as deque
   - `start_kline_stream(symbol, callback)` — uses `BinanceSocketManager` + `futures_kline_socket`
   - On each closed candle: append to buffer, recompute indicators, call `callback(df)`
   - Reconnect on `ConnectionClosedError` with exponential backoff (1s, 2s, 4s, max 3 retries)

6. **macro.py**:
   - `fetch_fred_data() -> dict`: use `pandas_datareader.data.DataReader(['DFF','T10Y2Y'], 'fred', start, end)`; return latest values as `{'fed_rate': float, 'yield_spread': float}`
   - `fetch_news(query='gold XAUUSD', page_size=5) -> List[str]`: call NewsAPI `/v2/everything`; return list of headline strings; cache result with timestamp, skip refetch if < 4h old

7. **snapshot.py** — `async build_snapshot(mode: str) -> MarketSnapshot`:
   - Determine which timeframes to fetch based on mode (see phase-05)
   - Fetch OHLCV for all timeframes concurrently with `asyncio.gather`
   - Compute indicators on each df
   - Find S/R from the primary timeframe df
   - Fetch macro (from cache if fresh)
   - Fetch mark price via `client.futures_mark_price(symbol='XAUUSDT')`
   - Build and return `MarketSnapshot`

8. Write unit tests in `tests/test_indicators.py` — create synthetic OHLCV df, assert indicator columns exist and have no NaN in last row after 200 bars

## Todo

- [ ] `binance_client.py` singleton with testnet support
- [ ] `ohlcv.py` — fetch + parse
- [ ] `indicators.py` — all 9 indicator groups
- [ ] `support_resistance.py` — peak/trough detection
- [ ] `websocket_feed.py` — CandleBuffer + reconnect
- [ ] `macro.py` — FRED + NewsAPI with caching
- [ ] `snapshot.py` — async build_snapshot
- [ ] Unit test indicators on synthetic data

## Success Criteria
- `build_snapshot('intraday')` returns valid `MarketSnapshot` with all indicator columns populated
- WebSocket receives live 1m candles and calls callback within 1s of candle close
- FRED fetch returns non-None `fed_rate` and `yield_spread`
- No NaN in last row of indicator columns (on 200-bar df)

## Risk Assessment
| Risk | Impact | Mitigation |
|------|--------|------------|
| pandas-ta column name changes | Medium | Pin version; unit test column names |
| Binance testnet downtime | Medium | Toggle to mainnet, use paper mode |
| NewsAPI 100/day limit exceeded | Low | 4h cache; fallback to empty list |
| WebSocket disconnect | High | Exponential backoff reconnect |
| FRED API key missing | Low | Graceful degradation — return `None`, LLM prompt notes unavailability |

## Security Considerations
- API keys loaded only from `Settings`, never hardcoded
- Testnet mode default (`BINANCE_TESTNET=true`) prevents accidental live trading during dev
- NewsAPI key in `.env` only

## Next Steps
- Phase 03: `MarketSnapshot` consumed by LLM prompts
- Phase 05: `build_snapshot` called with mode-specific TF list
