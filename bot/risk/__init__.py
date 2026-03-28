"""bot/risk
----------
Risk management package.

Public API
----------
    from bot.risk import RiskEngine

    engine = RiskEngine()
    approved, reason = await engine.pre_trade_check(signal, mode="intraday", balance=10_000)
    if approved:
        qty = engine.calc_size(10_000, "intraday", entry=3300, stop_loss=3280)

Submodules
----------
    bot.risk.limits          — MODE_RISK_CONFIG and accessor helpers
    bot.risk.calculator      — Position sizing and SL / price validation
    bot.risk.circuit_breaker — Daily PnL tracking and circuit-breaker logic
"""

from loguru import logger

from bot.risk.calculator import (
    calc_position_size,
    calc_size_for_mode,
    validate_signal_prices,
    validate_signal_sl,
)
from bot.risk.circuit_breaker import check_and_trip, is_circuit_breaker_active
from bot.risk.limits import get_risk_config

__all__ = [
    "RiskEngine",
    # re-exports for convenience
    "calc_position_size",
    "calc_size_for_mode",
    "validate_signal_sl",
    "validate_signal_prices",
    "check_and_trip",
    "is_circuit_breaker_active",
    "get_risk_config",
]


class RiskEngine:
    """Orchestrates all pre-trade risk checks and position-sizing calculations.

    Designed to be instantiated once at startup and reused across the
    async event loop.  All DB-touching methods are async coroutines.
    """

    # ------------------------------------------------------------------
    # Pre-trade gate
    # ------------------------------------------------------------------

    async def pre_trade_check(
        self,
        signal,
        mode: str,
        balance: float,
    ) -> tuple[bool, str]:
        """Run all pre-trade risk checks and return an approval decision.

        Checks performed (in order):
            1. Signal is not HOLD.
            2. Circuit breaker is not active (daily loss limit not hit).
            3. LLM confidence meets the mode minimum.
            4. Entry price is not stale vs. mark price (if ``signal.mark_price``
               is available — optional attribute; skipped if absent).

        Note: The max-concurrent-positions check is intentionally left to the
        caller (phase-07 execution engine) which queries the DB for open trades
        and passes the count in before calling this method.

        Args:
            signal:  A ``TradingSignal`` instance from ``bot.llm.models``.
            mode:    Active trading mode (``"scalp"`` / ``"intraday"`` / ``"swing"``).
            balance: Current account equity in USDT (fetched live, never cached).

        Returns:
            ``(True, "OK")`` if all checks pass, or
            ``(False, "<reason>")`` if any check fails.
        """
        # 1. HOLD signals are not actionable
        if signal.action == "HOLD":
            return False, "Signal is HOLD"

        cfg = get_risk_config(mode)

        # 2. Circuit breaker (DB-backed; survives restarts)
        if await check_and_trip(balance):
            return False, "Circuit breaker active — daily loss limit reached"

        # 3. Confidence threshold
        if signal.confidence < cfg["min_confidence"]:
            return (
                False,
                f"Confidence {signal.confidence} below threshold {cfg['min_confidence']}",
            )

        # 4. Stale entry price check (optional — only if mark_price is set)
        mark_price: float = getattr(signal, "mark_price", 0.0) or 0.0
        if mark_price > 0 and not validate_signal_prices(signal, mark_price):
            deviation_pct = abs(signal.entry_price - mark_price) / mark_price * 100
            return (
                False,
                f"Entry price {signal.entry_price} is {deviation_pct:.2f}% from "
                f"mark price {mark_price} — signal is stale",
            )

        logger.debug(
            f"pre_trade_check PASSED | mode={mode} action={signal.action} "
            f"confidence={signal.confidence} balance={balance:.2f}"
        )
        return True, "OK"

    # ------------------------------------------------------------------
    # Position sizing
    # ------------------------------------------------------------------

    def calc_size(
        self,
        balance: float,
        mode: str,
        entry: float,
        stop_loss: float,
    ) -> float:
        """Return the position quantity using the mode's risk parameters.

        Delegates to :func:`bot.risk.calculator.calc_position_size` with
        ``risk_pct`` and ``leverage`` from :mod:`bot.risk.limits`.

        Args:
            balance:   Account equity in USDT.
            mode:      Active trading mode.
            entry:     Planned entry price.
            stop_loss: Stop-loss price.

        Returns:
            Quantity in contracts (≥ 0.001), or 0.0 on invalid inputs.
        """
        cfg = get_risk_config(mode)
        return calc_position_size(
            balance=balance,
            risk_pct=cfg["risk_pct"],
            entry=entry,
            stop_loss=stop_loss,
            leverage=cfg["leverage"],
        )

    # ------------------------------------------------------------------
    # SL validation
    # ------------------------------------------------------------------

    def validate_sl(self, signal, atr: float, mode: str) -> bool:
        """Return True if the signal's SL distance is within the ATR tolerance.

        Uses the mode's ``atr_sl_multiplier`` from :mod:`bot.risk.limits`.

        Args:
            signal: A ``TradingSignal`` instance.
            atr:    Current ATR value for the mode's primary timeframe.
            mode:   Active trading mode.

        Returns:
            True if the SL distance is valid; False if it should be rejected.
        """
        cfg = get_risk_config(mode)
        return validate_signal_sl(
            entry=signal.entry_price,
            stop_loss=signal.stop_loss,
            atr=atr,
            atr_multiplier=cfg["atr_sl_multiplier"],
        )
