# bot/filters/__init__.py
"""Filter chain: runs ATR spike guard + correlation check before LLM."""

import uuid

from loguru import logger

from bot.database import db_execute
from bot.filters.correlation_filter import CorrelationFilter
from bot.filters.cycle_context import format_cycle_for_prompt, get_cycle_context
from bot.filters.volatility_filter import FilterResult, VolatilityFilter


class FilterChain:
    def __init__(self):
        self.vol_filter = VolatilityFilter()
        self.corr_filter = CorrelationFilter()

    async def run(self, mode: str, signal: dict | None = None) -> FilterResult:
        """
        Run full filter chain.
        If signal is None, only run ATR spike check (pre-LLM gate).
        If signal provided, also run correlation check.
        Returns FilterResult — if passed=False, skip LLM call.
        """
        # Gate 1: ATR spike
        atr_result = await self.vol_filter.check_atr_spike(mode)

        if not atr_result.passed:
            await self._log_block(mode, "atr_spike", atr_result, signal)
            from bot.telegram.notifier import send_message
            await send_message(
                f"⚡ *Volatility Alert* — ATR spike {atr_result.spike_ratio:.1f}x\n"
                f"Auto trade paused. Threshold: {atr_result.spike_ratio:.1f}x"
            )
            # Pause auto trade
            await db_execute(
                "INSERT OR REPLACE INTO config (key, value, updated_at) "
                "VALUES ('auto_trade', 'false', CURRENT_TIMESTAMP)"
            )
            return atr_result

        # Gate 2: Correlation (only if signal provided)
        if signal is not None:
            corr_result = await self.corr_filter.check_correlation(signal)
            if not corr_result.passed:
                await self._log_block(mode, "correlation", corr_result, signal)
                return corr_result
            # Update signal confidence and metadata in-place
            signal["confidence"] = corr_result.adjusted_confidence
            signal["dxy_status"] = corr_result.dxy_status
            signal["silver_aligned"] = corr_result.silver_aligned
            signal["filter_conflicts"] = corr_result.conflicts
            return corr_result

        return FilterResult(passed=True, spike_ratio=atr_result.spike_ratio)

    async def get_cycle_context(self) -> dict:
        return get_cycle_context()

    async def _log_block(
        self, mode: str, filter_type: str, result: FilterResult, signal: dict | None
    ) -> None:
        signal_id = signal.get("id", "") if signal else ""
        try:
            await db_execute(
                """INSERT INTO filter_log
                   (id, signal_id, filter_type, passed, reason, spike_ratio,
                    dxy_status, silver_aligned, confidence_adj,
                    original_conf, adjusted_conf)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                str(uuid.uuid4()),
                signal_id,
                filter_type,
                int(result.passed),
                result.reason,
                result.spike_ratio,
                result.dxy_status,
                int(result.silver_aligned),
                0,
                signal.get("confidence", 0) if signal else 0,
                result.adjusted_confidence,
            )
        except Exception as e:
            logger.warning(f"filter_log write error: {e}")


__all__ = ["FilterChain", "FilterResult"]
