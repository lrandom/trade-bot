"""bot/risk/limits.py
-------------------
Mode-specific risk parameters: leverage, risk percentage, ATR SL multiplier,
max concurrent positions, and minimum confidence threshold.

These values are the single source of truth for position sizing and signal
validation. The RiskEngine (bot/risk/__init__.py) reads from this module.
"""

MODE_RISK_CONFIG: dict[str, dict] = {
    "scalp": {
        "leverage": 10,             # default leverage
        "max_leverage": 20,         # hard cap
        "risk_pct": 0.5,            # % of account balance risked per trade
        "atr_sl_multiplier": 1.0,   # SL distance = ATR × multiplier
        "max_positions": 1,         # max open positions at once
        "min_confidence": 65,       # minimum LLM confidence to act
    },
    "intraday": {
        "leverage": 5,
        "max_leverage": 10,
        "risk_pct": 1.0,
        "atr_sl_multiplier": 1.5,
        "max_positions": 1,
        "min_confidence": 60,
    },
    "swing": {
        "leverage": 3,
        "max_leverage": 5,
        "risk_pct": 1.5,
        "atr_sl_multiplier": 2.0,
        "max_positions": 1,
        "min_confidence": 60,
    },
}


def get_risk_config(mode: str) -> dict:
    """Return the risk configuration dict for *mode*.

    Args:
        mode: One of ``"scalp"``, ``"intraday"``, or ``"swing"``.

    Returns:
        A copy-safe reference to the mode's risk parameter dict.

    Raises:
        ValueError: If *mode* is not a recognised trading mode.
    """
    if mode not in MODE_RISK_CONFIG:
        raise ValueError(
            f"Unknown mode: {mode!r}. Valid modes: {list(MODE_RISK_CONFIG.keys())}"
        )
    return MODE_RISK_CONFIG[mode]


def get_leverage_for_mode(mode: str) -> int:
    """Return the default leverage for *mode*."""
    return get_risk_config(mode)["leverage"]


def get_risk_pct_for_mode(mode: str) -> float:
    """Return the risk percentage per trade for *mode*."""
    return get_risk_config(mode)["risk_pct"]
