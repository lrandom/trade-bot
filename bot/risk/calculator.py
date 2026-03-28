"""bot/risk/calculator.py
------------------------
Position sizing and signal validation functions.

Key rules:
- Fixed fractional sizing: risk a fixed % of balance per trade.
- Position value is capped by ``balance * leverage`` (max leveraged exposure).
- LLM-generated SL must be within ±50 % of the expected ATR-based distance.
- Stale entry signals (entry > 1 % away from current mark price) are rejected.

All functions are pure (no I/O) so they can be tested without a running DB.
"""

from bot.risk.limits import get_risk_config


# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------

def calc_position_size(
    balance: float,
    risk_pct: float,
    entry: float,
    stop_loss: float,
    leverage: int,
) -> float:
    """Return the quantity (contracts / oz) to trade.

    On Binance XAUUSDT perpetual futures, 1 contract ≈ 1 troy oz.

    Formula:
        risk_amount   = balance × (risk_pct / 100)
        sl_pct        = |entry - stop_loss| / entry
        position_val  = risk_amount / sl_pct          # uncapped
        position_val  = min(position_val, balance × leverage)
        quantity      = position_val / entry

    Args:
        balance:   Account equity in USDT.
        risk_pct:  Percentage of balance to risk on this trade (e.g. 1.0 = 1 %).
        entry:     Planned entry price in USDT.
        stop_loss: Stop-loss price in USDT.
        leverage:  Default leverage for position capping.

    Returns:
        Rounded quantity (≥ 0.001 minimum lot), or 0.0 if inputs are invalid.
    """
    if entry <= 0 or stop_loss <= 0 or abs(entry - stop_loss) < 0.01:
        return 0.0

    risk_amount = balance * (risk_pct / 100)
    sl_pct = abs(entry - stop_loss) / entry

    # How large a position would exhaust exactly risk_amount at the SL?
    position_value = risk_amount / sl_pct

    # Hard cap: never exceed the max leveraged notional
    max_position_value = balance * leverage
    position_value = min(position_value, max_position_value)

    quantity = position_value / entry
    return round(max(quantity, 0.001), 3)


# ---------------------------------------------------------------------------
# Signal validation helpers
# ---------------------------------------------------------------------------

def validate_signal_sl(
    entry: float,
    stop_loss: float,
    atr: float,
    atr_multiplier: float,
    tolerance: float = 0.5,
) -> bool:
    """Validate that the LLM-supplied SL distance is close to the ATR target.

    Rejects signals where the stop-loss distance is outside a symmetric
    tolerance band around ``atr × atr_multiplier``:

        low_bound  = expected × (1 - tolerance)
        high_bound = expected × (1 + tolerance)

    A tolerance of 0.5 means ±50 %, i.e. the SL may be between 0.5× and
    1.5× the expected distance.  This catches hallucinated SL levels that
    are either too tight (almost no room) or too wide (risk-sizing explosion).

    Args:
        entry:          Entry price.
        stop_loss:      LLM-provided stop-loss price.
        atr:            Current ATR value for the primary timeframe.
        atr_multiplier: Mode-specific multiplier (1.0 / 1.5 / 2.0).
        tolerance:      Fractional tolerance (default 0.5 = ±50 %).

    Returns:
        True if the SL distance is within the acceptable range.
    """
    if atr <= 0 or entry <= 0 or stop_loss <= 0:
        return False

    expected = atr * atr_multiplier
    actual = abs(entry - stop_loss)

    low_bound = expected * (1 - tolerance)
    high_bound = expected * (1 + tolerance)
    return low_bound <= actual <= high_bound


def validate_signal_prices(
    signal,
    mark_price: float,
    max_entry_deviation: float = 0.01,
) -> bool:
    """Reject stale signals where the entry price drifted far from the market.

    If the LLM analysis was performed several seconds ago the market may have
    moved.  A deviation larger than *max_entry_deviation* (default 1 %) means
    the signal is no longer actionable and should be discarded.

    Args:
        signal:               A ``TradingSignal`` instance.
        mark_price:           Current mark / last-trade price from the exchange.
        max_entry_deviation:  Maximum allowable fractional deviation (default 0.01).

    Returns:
        True if the signal entry is within the acceptable deviation, or if the
        signal is a HOLD (no price check needed).
    """
    if signal.action == "HOLD":
        return True
    if mark_price <= 0:
        # Cannot validate without a reference price; let it through.
        return True

    deviation = abs(signal.entry_price - mark_price) / mark_price
    return deviation <= max_entry_deviation


# ---------------------------------------------------------------------------
# Mode-aware convenience wrapper
# ---------------------------------------------------------------------------

def calc_size_for_mode(
    balance: float,
    mode: str,
    entry: float,
    stop_loss: float,
) -> float:
    """Compute position size using the risk parameters for *mode*.

    Thin wrapper around :func:`calc_position_size` that reads ``risk_pct``
    and ``leverage`` from :mod:`bot.risk.limits`.
    """
    cfg = get_risk_config(mode)
    return calc_position_size(
        balance=balance,
        risk_pct=cfg["risk_pct"],
        entry=entry,
        stop_loss=stop_loss,
        leverage=cfg["leverage"],
    )
