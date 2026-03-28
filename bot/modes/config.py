"""Trading mode configuration.

Each mode defines:
    timeframes        — list of kline intervals to fetch/analyse
    primary_tf        — main timeframe for S/R detection and entry signals
    analysis_trigger  — "candle_close" (event-driven) or "interval" (time-driven)
    interval_minutes  — polling interval for "interval" triggers (None for candle_close)
    claude_model      — Claude model ID used for LLM analysis in this mode
    leverage          — default leverage multiplier
    max_leverage      — hard cap on leverage
    risk_pct          — percentage of account equity risked per trade
    atr_sl_mult       — ATR multiplier for stop-loss distance
    min_confidence    — minimum LLM confidence score (0–100) to act on a signal
"""

MODES: dict[str, dict] = {
    "scalp": {
        "timeframes": ["1m", "5m"],
        "primary_tf": "5m",
        "analysis_trigger": "candle_close",
        "interval_minutes": None,
        "claude_model": "claude-haiku-4-5-20251001",
        "leverage": 10,
        "max_leverage": 20,
        "risk_pct": 0.5,
        "atr_sl_mult": 1.0,
        "min_confidence": 65,
    },
    "intraday": {
        "timeframes": ["15m", "1h"],
        "primary_tf": "1h",
        "analysis_trigger": "interval",
        "interval_minutes": 15,
        "claude_model": "claude-sonnet-4-6",
        "leverage": 5,
        "max_leverage": 10,
        "risk_pct": 1.0,
        "atr_sl_mult": 1.5,
        "min_confidence": 60,
    },
    "swing": {
        "timeframes": ["4h", "1d"],
        "primary_tf": "4h",
        "analysis_trigger": "interval",
        "interval_minutes": 240,
        "claude_model": "claude-sonnet-4-6",
        "leverage": 3,
        "max_leverage": 5,
        "risk_pct": 1.5,
        "atr_sl_mult": 2.0,
        "min_confidence": 60,
    },
}


def get_mode_config(mode: str) -> dict:
    """Return the configuration dict for a given trading mode.

    Args:
        mode: One of "scalp", "intraday", "swing".

    Returns:
        Configuration dict for the requested mode.

    Raises:
        ValueError: If the mode name is not recognised.
    """
    if mode not in MODES:
        raise ValueError(
            f"Unknown mode: {mode!r}. Valid modes: {list(MODES.keys())}"
        )
    return MODES[mode]
