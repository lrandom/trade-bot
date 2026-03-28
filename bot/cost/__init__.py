"""bot/cost
-----------
Cost tracking for LLM API usage, trading fees, and infrastructure.

Public API
----------
    from bot.cost import track_llm_call, track_trade_fee, get_cost_summary, format_cost_report

    # After each LLM call:
    await track_llm_call("anthropic", "claude-sonnet-4-6", "htf", 1200, 350, mode="swing")

    # After each order fill:
    await track_trade_fee(trade_id, "XAUUSDT", "BUY", 0.05, 3300.0, order_type="taker", leverage=10)

    # Reporting:
    summary = await get_cost_summary("2026-03-01", "2026-03-28")
    text    = await format_cost_report(summary)
"""

from bot.cost.tracker import (
    export_csv,
    format_cost_report,
    get_cost_summary,
    get_daily_summary,
    get_mtd_summary,
    set_infra_cost,
    track_llm_call,
    track_trade_fee,
)

__all__ = [
    "track_llm_call",
    "track_trade_fee",
    "get_cost_summary",
    "get_daily_summary",
    "get_mtd_summary",
    "format_cost_report",
    "set_infra_cost",
    "export_csv",
]
