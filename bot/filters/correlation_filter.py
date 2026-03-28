# bot/filters/correlation_filter.py
"""DXY proxy (EURUSD) + Silver correlation check."""

from dataclasses import dataclass

from loguru import logger

from bot.filters.volatility_filter import FilterResult


@dataclass
class CorrelationResult:
    asset: str
    trend: str          # UP | DOWN | UNKNOWN
    aligned: bool
    rsi: float
    confidence_adj: int


class CorrelationFilter:

    async def get_dxy_proxy_trend(self) -> str:
        """
        EURUSD 4H — proxy for DXY (57.6% weight).
        EURUSD up  → DXY weak   → Gold BULLISH
        EURUSD down → DXY strong → Gold BEARISH
        """
        try:
            from bot.data.ohlcv import fetch_ohlcv
            import pandas_ta as ta

            df = await fetch_ohlcv("EURUSDT", "4h", limit=55)
            ema20 = ta.ema(df["close"], length=20).iloc[-1]
            ema50 = ta.ema(df["close"], length=50).iloc[-1]
            price = df["close"].iloc[-1]

            if price > ema20 > ema50:
                return "DXY_WEAK"
            elif price < ema20 < ema50:
                return "DXY_STRONG"
            else:
                return "DXY_NEUTRAL"

        except Exception as e:
            logger.warning(f"DXY proxy error: {e} — returning NEUTRAL")
            return "DXY_NEUTRAL"

    async def get_silver_alignment(self, gold_signal_action: str) -> CorrelationResult:
        """Silver (XAGUSDT) 1H alignment check."""
        try:
            from bot.data.ohlcv import fetch_ohlcv
            import pandas_ta as ta

            df = await fetch_ohlcv("XAGUSDT", "1h", limit=25)
            silver_rsi = float(ta.rsi(df["close"], length=14).iloc[-1])
            silver_ema20 = float(ta.ema(df["close"], length=20).iloc[-1])
            silver_price = float(df["close"].iloc[-1])
            silver_trend = "UP" if silver_price > silver_ema20 else "DOWN"

            gold_direction = "UP" if gold_signal_action == "BUY" else "DOWN"
            aligned = silver_trend == gold_direction

            return CorrelationResult(
                asset="XAGUSDT",
                trend=silver_trend,
                aligned=aligned,
                rsi=silver_rsi,
                confidence_adj=+10 if aligned else -15,
            )

        except Exception as e:
            logger.warning(f"Silver check error: {e} — returning neutral")
            return CorrelationResult(
                asset="XAGUSDT",
                trend="UNKNOWN",
                aligned=True,
                rsi=50.0,
                confidence_adj=0,
            )

    async def check_correlation(self, signal: dict) -> FilterResult:
        """Run DXY + Silver checks. Adjust confidence or block on major conflicts."""
        gold_action = signal.get("action", "BUY")
        original_conf = signal.get("confidence", 60)

        dxy = await self.get_dxy_proxy_trend()
        silver = await self.get_silver_alignment(gold_action)

        conflicts = []
        confidence_adj = 0

        # DXY conflict check
        if gold_action == "BUY" and dxy == "DXY_STRONG":
            conflicts.append("DXY strong — conflicts with BUY")
            confidence_adj -= 20
        elif gold_action == "SELL" and dxy == "DXY_WEAK":
            conflicts.append("DXY weak — conflicts with SELL")
            confidence_adj -= 20

        # Silver alignment
        confidence_adj += silver.confidence_adj
        if not silver.aligned:
            conflicts.append(f"Silver diverging ({silver.trend})")

        new_confidence = max(0, original_conf + confidence_adj)

        # Block if multiple conflicts AND confidence very low
        if len(conflicts) >= 2 and new_confidence < 40:
            return FilterResult(
                passed=False,
                reason=f"Correlation conflicts: {', '.join(conflicts)}",
                adjusted_confidence=new_confidence,
                dxy_status=dxy,
                silver_aligned=silver.aligned,
            )

        return FilterResult(
            passed=True,
            conflicts=conflicts,
            adjusted_confidence=new_confidence,
            dxy_status=dxy,
            silver_aligned=silver.aligned,
        )
