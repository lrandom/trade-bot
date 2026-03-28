"""bot/cost/tracker.py
---------------------
LLM API usage, trading fee, and infrastructure cost tracking.

All writes are async and non-blocking so they never delay the trading pipeline.
"""

import uuid
from datetime import datetime, timezone

from loguru import logger

from bot.cost.pricing import BINANCE_FEES, calc_llm_cost
from bot.database import db_execute, db_fetchall, db_scalar
from bot.utils.timezone import days_in_month, days_in_range, today_utc, month_start_utc


# ---------------------------------------------------------------------------
# LLM tracking
# ---------------------------------------------------------------------------

async def track_llm_call(
    provider: str,
    model: str,
    call_type: str,      # macro | htf | mtf | ltf | signal | management
    input_tokens: int,
    output_tokens: int,
    mode: str = "",
    signal_id: str | None = None,
) -> None:
    """Record an LLM API call with token counts and calculated cost.

    Silently swallows DB errors so a tracking failure never crashes the bot.
    """
    try:
        cost_usd = calc_llm_cost(provider, model, input_tokens, output_tokens)
        await db_execute(
            """INSERT INTO llm_usage
               (id, provider, model, call_type, input_tokens, output_tokens, cost_usd, trading_mode, signal_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            str(uuid.uuid4()),
            provider,
            model,
            call_type,
            input_tokens,
            output_tokens,
            cost_usd,
            mode,
            signal_id,
        )
    except Exception as e:
        logger.warning(f"track_llm_call failed (non-fatal): {e}")


# ---------------------------------------------------------------------------
# Trading fee tracking
# ---------------------------------------------------------------------------

async def track_trade_fee(
    trade_id: str,
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    order_type: str = "taker",   # maker | taker
    leverage: int = 1,
) -> float:
    """Record a trading fee entry and return the fee in USD.

    Silently swallows DB errors so a tracking failure never crashes the bot.
    """
    try:
        fee_rate = BINANCE_FEES.get(order_type, BINANCE_FEES["taker"])
        notional = quantity * price
        fee_usd = notional * fee_rate

        await db_execute(
            """INSERT INTO trading_fees
               (id, trade_id, symbol, side, quantity, price, notional, order_type, fee_rate, fee_usd, leverage)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            str(uuid.uuid4()),
            trade_id,
            symbol,
            side,
            quantity,
            price,
            notional,
            order_type,
            fee_rate,
            fee_usd,
            leverage,
        )
        return fee_usd
    except Exception as e:
        logger.warning(f"track_trade_fee failed (non-fatal): {e}")
        return 0.0


# ---------------------------------------------------------------------------
# Infrastructure cost management
# ---------------------------------------------------------------------------

async def set_infra_cost(category: str, amount_usd: float) -> None:
    """Upsert a monthly infra cost entry for the current month.

    Category is typically 'vps', 'domain', or 'other'.
    """
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    await db_execute(
        """INSERT INTO infra_costs (month, category, description, cost_usd)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(month, category) DO UPDATE SET cost_usd = excluded.cost_usd,
               description = excluded.description""",
        month, category, category, amount_usd,
    )
    logger.info(f"Infra cost set: {category} = ${amount_usd:.2f}/month ({month})")


async def _get_monthly_infra_total(year_month: str) -> float:
    """Return total infra cost for the given YYYY-MM month."""
    val = await db_scalar(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM infra_costs WHERE month = ?",
        year_month,
    )
    return float(val or 0.0)


# ---------------------------------------------------------------------------
# Cost summaries
# ---------------------------------------------------------------------------

async def get_cost_summary(date_from: str, date_to: str) -> dict:
    """Return cost breakdown for a date range.

    Args:
        date_from: 'YYYY-MM-DD' start date (inclusive).
        date_to:   'YYYY-MM-DD' end date (inclusive).

    Returns:
        Dict with keys: llm_total, fee_total, infra_total, total_cost,
        trading_pnl, net_profit, llm_calls, by_model, days,
        cost_per_day, net_per_day, cost_pnl_ratio, start, end.
    """
    start_str = f"{date_from} 00:00:00"
    end_str = f"{date_to} 23:59:59"

    llm_cost = float(
        await db_scalar(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_usage "
            "WHERE timestamp BETWEEN ? AND ?",
            start_str, end_str,
        ) or 0.0
    )

    fee_cost = float(
        await db_scalar(
            "SELECT COALESCE(SUM(fee_usd), 0) FROM trading_fees "
            "WHERE timestamp BETWEEN ? AND ?",
            start_str, end_str,
        ) or 0.0
    )

    llm_calls = int(
        await db_scalar(
            "SELECT COUNT(*) FROM llm_usage WHERE timestamp BETWEEN ? AND ?",
            start_str, end_str,
        ) or 0
    )

    trading_pnl = float(
        await db_scalar(
            "SELECT COALESCE(SUM(pnl), 0) FROM trades "
            "WHERE closed_at BETWEEN ? AND ?",
            start_str, end_str,
        ) or 0.0
    )

    # Per-model breakdown
    model_rows = await db_fetchall(
        """SELECT model, COUNT(*) as calls,
                  COALESCE(SUM(input_tokens), 0) as in_tok,
                  COALESCE(SUM(output_tokens), 0) as out_tok,
                  COALESCE(SUM(cost_usd), 0) as cost
           FROM llm_usage
           WHERE timestamp BETWEEN ? AND ?
           GROUP BY model
           ORDER BY cost DESC""",
        start_str, end_str,
    )

    # Prorated infra cost for the date range
    year_month = date_from[:7]  # YYYY-MM
    total_infra_monthly = await _get_monthly_infra_total(year_month)
    num_days = days_in_range(date_from, date_to)
    month_days = days_in_month(year_month)
    infra_prorated = total_infra_monthly * (num_days / month_days)

    total_cost = llm_cost + fee_cost + infra_prorated
    net_profit = trading_pnl - total_cost

    return {
        "llm_total": round(llm_cost, 4),
        "fee_total": round(fee_cost, 4),
        "infra_total": round(infra_prorated, 4),
        "total_cost": round(total_cost, 4),
        "trading_pnl": round(trading_pnl, 4),
        "net_profit": round(net_profit, 4),
        "llm_calls": llm_calls,
        "by_model": [dict(r) for r in (model_rows or [])],
        "days": num_days,
        "cost_per_day": round(total_cost / num_days, 4) if num_days else 0.0,
        "net_per_day": round(net_profit / num_days, 4) if num_days else 0.0,
        "cost_pnl_ratio": round(total_cost / trading_pnl, 4) if trading_pnl else None,
        "start": date_from,
        "end": date_to,
    }


async def get_daily_summary(date: str | None = None) -> dict:
    """Return cost summary for a single day (defaults to today UTC)."""
    d = date or today_utc()
    return await get_cost_summary(d, d)


async def get_mtd_summary() -> dict:
    """Return month-to-date cost summary (from the 1st of this month to today)."""
    return await get_cost_summary(month_start_utc(), today_utc())


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

async def format_cost_report(summary: dict) -> str:
    """Format a cost summary dict as a Telegram-friendly Markdown message."""
    start = summary.get("start", "")
    end = summary.get("end", "")
    days = summary.get("days", 1)

    period = (
        f"`{start}`" if start == end else f"`{start}` → `{end}` ({days} days)"
    )

    lines = [
        f"💰 *Cost Report*",
        f"Period: {period}\n",
        f"📡 LLM API:      `${summary['llm_total']:.4f}`",
        f"📊 Trading Fees: `${summary['fee_total']:.4f}`",
        f"🖥️  Infra:        `${summary['infra_total']:.4f}`",
        f"💸 Total Cost:   `${summary['total_cost']:.4f}`",
        f"📈 Trading PnL:  `${summary['trading_pnl']:.4f}`",
        f"─────────────────────",
    ]

    net = summary.get("net_profit", 0)
    net_emoji = "✅" if net >= 0 else "❌"
    lines.append(f"{net_emoji} Net Profit:   `${net:.4f}`\n")

    lines.append(f"LLM Calls: `{summary['llm_calls']}`")
    if days > 1:
        lines.append(f"Cost/day:  `${summary.get('cost_per_day', 0):.4f}`")
        if summary.get("cost_pnl_ratio") is not None:
            lines.append(
                f"Cost/PnL:  `{summary['cost_pnl_ratio'] * 100:.1f}%` "
                f"(${summary['cost_pnl_ratio']:.3f} per $1 profit)"
            )

    by_model = summary.get("by_model", [])
    if by_model:
        lines.append("\n*By Model:*")
        for m in by_model:
            cost = float(m.get("cost") or 0)
            calls = int(m.get("calls") or 0)
            lines.append(f"  `{m['model']}`: {calls} calls → ${cost:.4f}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

async def export_csv(date_from: str | None = None, date_to: str | None = None) -> str:
    """Export llm_usage to a CSV file and return the file path.

    Falls back to all-time if no date range is given.
    """
    import csv
    import os
    import tempfile

    if date_from and date_to:
        start_str = f"{date_from} 00:00:00"
        end_str = f"{date_to} 23:59:59"
        rows = await db_fetchall(
            "SELECT * FROM llm_usage WHERE timestamp BETWEEN ? AND ? ORDER BY timestamp",
            start_str, end_str,
        )
    else:
        rows = await db_fetchall("SELECT * FROM llm_usage ORDER BY timestamp")

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, newline="", encoding="utf-8"
    )
    try:
        if rows:
            writer = csv.DictWriter(tmp, fieldnames=dict(rows[0]).keys())
            writer.writeheader()
            for r in rows:
                writer.writerow(dict(r))
    finally:
        tmp.close()

    logger.info(f"Cost CSV exported to {tmp.name} ({len(rows)} rows)")
    return tmp.name
