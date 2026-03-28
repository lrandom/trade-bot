"""tests/test_risk.py
---------------------
Unit tests for the risk management module (Phase 04).

Tests cover:
    - Position sizing math (calc_position_size)
    - SL validation against ATR (validate_signal_sl)
    - Stale entry price rejection (validate_signal_prices)

All tests are synchronous and pure — no DB or network I/O required.
"""

import pytest

from bot.risk.calculator import (
    calc_position_size,
    validate_signal_prices,
    validate_signal_sl,
)


# ---------------------------------------------------------------------------
# calc_position_size
# ---------------------------------------------------------------------------

class TestCalcPositionSize:
    def test_basic_intraday(self):
        """Standard intraday parameters should return a positive, sane quantity."""
        # balance=$10 000, risk=1 %, entry=$3 300, SL=$3 280 (20 pts), leverage=5×
        # risk_amount = $100
        # sl_pct = 20 / 3300 ≈ 0.006061
        # position_value = 100 / 0.006061 ≈ $16 500
        # max_pos_val = 10 000 × 5 = $50 000  → not hit
        # quantity = 16 500 / 3300 = 5.0
        qty = calc_position_size(
            balance=10_000, risk_pct=1.0, entry=3_300, stop_loss=3_280, leverage=5
        )
        assert qty > 0

        # Verify the math explicitly
        sl_pct = 20 / 3_300
        expected_val = 100 / sl_pct
        expected_qty = round(expected_val / 3_300, 3)
        assert abs(qty - expected_qty) < 0.01

    def test_zero_sl_distance(self):
        """SL equal to entry must return 0.0 (would cause division by zero)."""
        qty = calc_position_size(10_000, 1.0, 3_300, 3_300, 5)
        assert qty == 0.0

    def test_negligible_sl_distance(self):
        """SL closer than 0.01 to entry must return 0.0."""
        qty = calc_position_size(10_000, 1.0, 3_300, 3_300.005, 5)
        assert qty == 0.0

    def test_leverage_caps_position(self):
        """A very tight SL should not produce a position larger than balance×leverage."""
        # SL = 1 pt away from entry → position_value would be enormous if uncapped
        qty = calc_position_size(
            balance=10_000, risk_pct=1.0, entry=3_300, stop_loss=3_299, leverage=5
        )
        max_notional = 10_000 * 5  # $50 000
        assert qty * 3_300 <= max_notional + 1  # +1 for floating-point rounding

    def test_minimum_quantity_floor(self):
        """Quantity should never be below 0.001."""
        # Very small balance, large SL distance
        qty = calc_position_size(
            balance=10, risk_pct=0.5, entry=3_300, stop_loss=3_000, leverage=3
        )
        assert qty >= 0.001

    def test_invalid_entry_zero(self):
        """Entry price of 0 should return 0.0."""
        assert calc_position_size(10_000, 1.0, 0, 3_280, 5) == 0.0

    def test_invalid_sl_zero(self):
        """Stop-loss of 0 should return 0.0."""
        assert calc_position_size(10_000, 1.0, 3_300, 0, 5) == 0.0

    def test_scalp_parameters(self):
        """Scalp mode (risk=0.5 %, leverage=10) should produce smaller positions."""
        qty_scalp = calc_position_size(10_000, 0.5, 3_300, 3_295, 10)
        qty_swing = calc_position_size(10_000, 1.5, 3_300, 3_295, 3)
        # Scalp has lower risk_pct but higher leverage — both should be positive
        assert qty_scalp > 0
        assert qty_swing > 0


# ---------------------------------------------------------------------------
# validate_signal_sl
# ---------------------------------------------------------------------------

class TestValidateSignalSL:
    """ATR = 20, multiplier = 1.5 → expected SL distance = 30.
    Tolerance 50 % → valid range [15, 45].
    """

    ATR = 20.0
    MULT = 1.5
    ENTRY = 3_300.0

    def test_exact_expected_distance(self):
        """SL exactly at expected distance (30 pts) must pass."""
        assert validate_signal_sl(self.ENTRY, 3_270, self.ATR, self.MULT) is True

    def test_within_tolerance_lower_bound(self):
        """SL at lower bound (15 pts = 50 % of 30) must pass."""
        assert validate_signal_sl(self.ENTRY, 3_285, self.ATR, self.MULT) is True

    def test_within_tolerance_upper_bound(self):
        """SL at upper bound (45 pts = 150 % of 30) must pass."""
        assert validate_signal_sl(self.ENTRY, 3_255, self.ATR, self.MULT) is True

    def test_sl_too_wide(self):
        """SL at 100 pts (> 45 pt upper bound) must be rejected."""
        assert validate_signal_sl(self.ENTRY, 3_200, self.ATR, self.MULT) is False

    def test_sl_too_tight(self):
        """SL at 5 pts (< 15 pt lower bound) must be rejected."""
        assert validate_signal_sl(self.ENTRY, 3_295, self.ATR, self.MULT) is False

    def test_zero_atr(self):
        """ATR of 0 is invalid — must return False."""
        assert validate_signal_sl(self.ENTRY, 3_270, atr=0, atr_multiplier=self.MULT) is False

    def test_zero_entry(self):
        """Entry of 0 is invalid — must return False."""
        assert validate_signal_sl(0, 3_270, self.ATR, self.MULT) is False

    def test_zero_sl(self):
        """SL of 0 is invalid — must return False."""
        assert validate_signal_sl(self.ENTRY, 0, self.ATR, self.MULT) is False

    def test_swing_multiplier(self):
        """Swing mode uses ATR × 2.0; valid range should scale accordingly."""
        # ATR=20, mult=2.0, expected=40, valid=[20, 60]
        assert validate_signal_sl(3_300, 3_260, atr=20, atr_multiplier=2.0) is True
        assert validate_signal_sl(3_300, 3_200, atr=20, atr_multiplier=2.0) is False

    def test_scalp_multiplier(self):
        """Scalp mode uses ATR × 1.0; valid range [10, 30] for ATR=20."""
        assert validate_signal_sl(3_300, 3_280, atr=20, atr_multiplier=1.0) is True
        assert validate_signal_sl(3_300, 3_100, atr=20, atr_multiplier=1.0) is False


# ---------------------------------------------------------------------------
# validate_signal_prices
# ---------------------------------------------------------------------------

class TestValidateSignalPrices:
    """Entry within 1 % of mark price → valid; beyond 1 % → stale."""

    def _make_signal(self, action: str, entry_price: float):
        from bot.llm.models import TradingSignal

        return TradingSignal(
            action=action,
            entry_price=entry_price,
            stop_loss=entry_price - 20,
            tp1=entry_price + 40,
            tp2=entry_price + 60,
            tp3=entry_price + 100,
            htf_bias="BUY-ONLY",
            confidence=75,
            reasoning="test signal",
        )

    def test_fresh_signal_within_tolerance(self):
        """0.09 % deviation should be accepted."""
        s = self._make_signal("BUY", 3_300)
        assert validate_signal_prices(s, mark_price=3_303) is True

    def test_stale_signal_exceeds_tolerance(self):
        """3 % deviation should be rejected."""
        s = self._make_signal("BUY", 3_300)
        assert validate_signal_prices(s, mark_price=3_200) is False

    def test_hold_always_passes(self):
        """HOLD signals bypass the price check entirely."""
        from bot.llm.models import TradingSignal

        s = TradingSignal.hold("test hold")
        assert validate_signal_prices(s, mark_price=3_000) is True

    def test_zero_mark_price_passes(self):
        """If mark price is unavailable (0), the check is skipped."""
        s = self._make_signal("SELL", 3_300)
        assert validate_signal_prices(s, mark_price=0) is True

    def test_exactly_at_boundary(self):
        """Exactly 1 % deviation is within the limit (inclusive)."""
        s = self._make_signal("BUY", 3_300)
        mark = 3_300 * (1 - 0.01)  # exactly 1 % below
        assert validate_signal_prices(s, mark_price=mark) is True

    def test_just_over_boundary(self):
        """Just over 1 % deviation should be rejected."""
        s = self._make_signal("BUY", 3_300)
        mark = 3_300 * (1 - 0.011)  # 1.1 % below
        assert validate_signal_prices(s, mark_price=mark) is False

    def test_sell_signal_deviation(self):
        """SELL signals are subject to the same price staleness check."""
        s = self._make_signal("SELL", 3_300)
        assert validate_signal_prices(s, mark_price=3_302) is True
        assert validate_signal_prices(s, mark_price=3_100) is False
