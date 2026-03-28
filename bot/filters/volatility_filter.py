# bot/filters/volatility_filter.py
"""ATR spike detection guard."""

from dataclasses import dataclass, field
from typing import Optional

from loguru import logger

ATR_SPIKE_MULTIPLIERS = {
    "scalp":    2.0,
    "intraday": 2.5,
    "swing":    3.0,
}


@dataclass
class FilterResult:
    passed: bool
    reason: str = ""
    action: str = ""
    spike_ratio: float = 0.0
    conflicts: list = field(default_factory=list)
    adjusted_confidence: int = 0
    dxy_status: str = ""
    silver_aligned: bool = True


class VolatilityFilter:

    async def check_atr_spike(self, mode: str, tf: str = "1h") -> FilterResult:
        """Check if current ATR is spiking relative to 20-bar average."""
        try:
            from bot.data.ohlcv import fetch_ohlcv
            from bot.data.indicators import compute_indicators

            df = await fetch_ohlcv("XAUUSDT", tf, limit=30)
            df = compute_indicators(df)

            # Locate the ATR column produced by pandas-ta (ATRr_14 by default)
            atr_col = None
            if "ATRr_14" in df.columns:
                atr_col = "ATRr_14"
            elif "atr" in df.columns:
                atr_col = "atr"
            else:
                for col in df.columns:
                    if "atr" in col.lower():
                        atr_col = col
                        break

            if not atr_col:
                return FilterResult(passed=True, reason="ATR not available — skip")

            atr_series = df[atr_col].dropna()
            if len(atr_series) < 22:
                return FilterResult(passed=True, reason="Insufficient ATR data")

            current_atr = float(atr_series.iloc[-1])
            avg_atr_20 = float(atr_series.iloc[-21:-1].mean())

            if avg_atr_20 == 0:
                return FilterResult(passed=True, reason="avg_atr=0 — skip")

            spike_ratio = current_atr / avg_atr_20
            threshold = ATR_SPIKE_MULTIPLIERS.get(mode, 2.5)

            if spike_ratio >= threshold:
                return FilterResult(
                    passed=False,
                    reason=(
                        f"ATR spike {spike_ratio:.1f}x "
                        f"(threshold {threshold}x) — likely news event"
                    ),
                    action="PAUSE_AUTO_TRADE",
                    spike_ratio=spike_ratio,
                )

            return FilterResult(passed=True, spike_ratio=spike_ratio)

        except Exception as e:
            logger.warning(f"ATR spike check error: {e} — letting signal pass")
            return FilterResult(passed=True, reason=f"ATR check error: {e}")
