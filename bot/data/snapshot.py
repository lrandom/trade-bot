from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
import asyncio
import pandas as pd

from bot.data.ohlcv import fetch_ohlcv
from bot.data.indicators import compute_indicators
from bot.data.support_resistance import find_levels
from bot.data.macro import fetch_fred_data, fetch_news
from bot.data.binance_client import get_client
from bot.modes.config import MODES
from bot.config import settings


@dataclass
class MarketSnapshot:
    """Consolidated market view consumed by the LLM engine.

    Attributes:
        symbol:            Trading symbol, e.g. "XAUUSDT".
        timestamp:         UTC datetime when this snapshot was built.
        mode:              Active trading mode: "scalp" | "intraday" | "swing".
        timeframes:        Raw OHLCV + indicator DataFrames keyed by interval
                           string (e.g. "1h", "4h").
        indicators:        Latest indicator values per timeframe, e.g.
                           ``{"1h": {"ema_20": 2350.5, "rsi_14": 62.1, ...}}``.
        support_levels:    Detected support prices (ascending).
        resistance_levels: Detected resistance prices (ascending).
        fed_funds_rate:    Latest DFF value from FRED (or None if unavailable).
        yield_spread:      Latest T10Y2Y value from FRED (or None if unavailable).
        news_headlines:    Up to 5 recent gold-related news headlines.
        mark_price:        Binance Futures mark price at snapshot time.
        primary_tf:        Primary timeframe used for S/R detection and signal
                           generation for the active mode.
    """

    symbol: str
    timestamp: datetime
    mode: str
    timeframes: Dict[str, pd.DataFrame]
    indicators: Dict[str, Dict[str, float]]
    support_levels: List[float] = field(default_factory=list)
    resistance_levels: List[float] = field(default_factory=list)
    fed_funds_rate: Optional[float] = None
    yield_spread: Optional[float] = None
    news_headlines: List[str] = field(default_factory=list)
    mark_price: float = 0.0
    primary_tf: str = "1h"


# Indicator column names extracted per timeframe
_INDICATOR_COLS = [
    "ema_20", "ema_50", "ema_200",
    "rsi_14",
    "macd", "macd_signal", "macd_hist",
    "atr_14",
    "supertrend", "supertrend_dir",
    "vwap",
    "bb_upper", "bb_mid", "bb_lower",
]


async def build_snapshot(mode: str) -> MarketSnapshot:
    """Fetch all market data and build a complete MarketSnapshot.

    Steps:
        1. Resolve timeframes for the given mode (plus fixed HTF set for LLM
           context: 1w, 1d, 4h, 1h, 15m).
        2. Fetch OHLCV for all timeframes concurrently.
        3. Compute indicators on each DataFrame.
        4. Detect S/R levels from the primary timeframe.
        5. Fetch mark price from Binance Futures.
        6. Fetch macro data (FRED + NewsAPI) — both results are cached.
        7. Extract the latest row of each indicator column per timeframe.

    Args:
        mode: One of "scalp", "intraday", "swing".

    Returns:
        Populated MarketSnapshot instance.

    Raises:
        KeyError: If ``mode`` is not defined in MODES config.
    """
    mode_cfg = MODES[mode]
    tfs: list[str] = mode_cfg["timeframes"]
    primary_tf: str = mode_cfg["primary_tf"]

    # Include a standard higher-timeframe set for LLM multi-TF analysis
    htf_set = ["1w", "1d", "4h", "1h", "15m"]
    all_tfs = list(dict.fromkeys(tfs + htf_set))  # deduplicate, preserve order

    client = await get_client()

    # ── Fetch all TFs concurrently ────────────────────────────────────────────
    fetch_tasks = [fetch_ohlcv(settings.trading_symbol, tf) for tf in all_tfs]
    fetch_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

    timeframes: Dict[str, pd.DataFrame] = {}
    for tf, result in zip(all_tfs, fetch_results):
        if isinstance(result, Exception):
            continue  # skip failed TFs silently; LLM will note absence
        timeframes[tf] = compute_indicators(result)

    # ── S/R from primary TF ───────────────────────────────────────────────────
    support: List[float] = []
    resistance: List[float] = []
    if primary_tf in timeframes:
        support, resistance = find_levels(timeframes[primary_tf])

    # ── Mark price ────────────────────────────────────────────────────────────
    mark_price = 0.0
    try:
        ticker = await client.futures_mark_price(symbol=settings.trading_symbol)
        mark_price = float(ticker["markPrice"])
    except Exception:
        pass

    # ── Macro data (both calls are independently cached) ──────────────────────
    macro, news = await asyncio.gather(fetch_fred_data(), fetch_news())

    # ── Extract latest indicator values per TF ────────────────────────────────
    indicators: Dict[str, Dict[str, float]] = {}
    for tf, df in timeframes.items():
        if df is None or len(df) == 0:
            continue
        row = df.iloc[-1]
        indicators[tf] = {
            col: (float(row[col]) if pd.notna(row.get(col)) else None)
            for col in _INDICATOR_COLS
            if col in df.columns
        }

    # ── Build snapshot ────────────────────────────────────────────────────────
    from bot.utils.timezone import utc_now

    return MarketSnapshot(
        symbol=settings.trading_symbol,
        timestamp=utc_now(),
        mode=mode,
        timeframes=timeframes,
        indicators=indicators,
        support_levels=support,
        resistance_levels=resistance,
        fed_funds_rate=macro.get("fed_rate"),
        yield_spread=macro.get("yield_spread"),
        news_headlines=news,
        mark_price=mark_price,
        primary_tf=primary_tf,
    )
