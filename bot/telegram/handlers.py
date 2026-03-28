# bot/telegram/handlers.py
"""All Telegram command and callback handlers."""

from datetime import datetime, timezone

from loguru import logger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot.config import settings
from bot.database import db_execute, db_fetchall, db_fetchone
from bot.telegram.formatters import format_history, format_signal, format_status
from bot.utils.timezone import fmt_ict, month_start_utc, today_utc, utc_now


def _is_authorized(update: Update) -> bool:
    """Check if message is from authorized chat_id."""
    user_id = update.effective_user.id if update.effective_user else None
    chat_id = update.effective_chat.id if update.effective_chat else None
    # Allow both user_id and chat_id to match settings.telegram_chat_id
    auth_id = int(settings.telegram_chat_id)
    return user_id == auth_id or chat_id == auth_id


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    await update.message.reply_text(
        "🤖 Gold Trading Bot active!\n\n"
        "Commands:\n"
        "/signal — trigger analysis\n"
        "/status — current positions\n"
        "/balance — account balance\n"
        "/mode [swing|intraday|scalp] — change mode\n"
        "/auto [on|off] — toggle auto trade\n"
        "/close — close all positions\n"
        "/stop — emergency stop\n"
        "/history — last 10 trades\n"
        "/health — health check\n"
        "/cost — LLM cost today\n"
        "/filter status — filter status"
    )


async def cmd_signal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Trigger immediate analysis cycle."""
    if not _is_authorized(update):
        return
    await update.message.reply_text("🔍 Triggering analysis... Please wait.")
    # The orchestrator exposes a function to run one analysis cycle.
    # Imported lazily to avoid circular imports.
    try:
        from bot.orchestrator import trigger_analysis
        await trigger_analysis()
    except ImportError:
        await update.message.reply_text("⚠️ Analysis engine not ready yet.")
    except Exception as e:
        logger.error(f"cmd_signal error: {e}")
        await update.message.reply_text(f"❌ Analysis error: {e}")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    try:
        from bot.modes.manager import get_current_mode, get_auto_trade
        mode = await get_current_mode()
        auto = await get_auto_trade()
        # Get open positions from paper_orders or trades table
        positions = await db_fetchall(
            "SELECT side, entry, status FROM paper_orders WHERE status = 'open' ORDER BY open_time DESC LIMIT 5"
        )
        pos_list = [dict(p) for p in positions]
        text = format_status(pos_list, mode, auto, settings.paper_trade)
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"cmd_status error: {e}")
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    if settings.paper_trade:
        await update.message.reply_text("📄 Paper trade mode — no real balance.\nUse /status for paper PnL.")
        return
    try:
        from bot.data.binance_client import get_client
        client = await get_client()
        account = await client.futures_account()
        balance = float(account.get("totalWalletBalance", 0))
        unrealized = float(account.get("totalUnrealizedProfit", 0))
        available = float(account.get("availableBalance", 0))
        text = (
            f"💰 *Account Balance*\n\n"
            f"Total:     `${balance:,.2f}`\n"
            f"Unrealized: `${unrealized:+,.2f}`\n"
            f"Available: `${available:,.2f}`"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ Balance error: {e}")


async def cmd_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    from bot.modes.manager import get_current_mode, set_mode
    args = context.args
    if not args:
        mode = await get_current_mode()
        await update.message.reply_text(
            f"Current mode: `{mode}`\nUsage: /mode [swing|intraday|scalp]",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    new_mode = args[0].lower()
    try:
        await set_mode(new_mode)
        await update.message.reply_text(
            f"✅ Mode changed to `{new_mode}`",
            parse_mode=ParseMode.MARKDOWN,
        )
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}")


async def cmd_auto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    from bot.modes.manager import get_auto_trade, set_auto_trade
    args = context.args
    if not args:
        auto = await get_auto_trade()
        state = "ON" if auto else "OFF"
        await update.message.reply_text(
            f"Auto trade: `{state}`\nUsage: /auto [on|off]",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    val = args[0].lower()
    if val == "on":
        if not settings.paper_trade:
            # Confirm live auto trade — require explicit second argument
            await update.message.reply_text(
                "⚠️ This enables LIVE auto trading with real money!\nSend `/auto on confirm` to proceed.",
                parse_mode=ParseMode.MARKDOWN,
            )
            if len(args) < 2 or args[1] != "confirm":
                return
        await set_auto_trade(True)
        await update.message.reply_text("✅ Auto trade ENABLED")
    elif val == "off":
        await set_auto_trade(False)
        await update.message.reply_text("✅ Auto trade DISABLED")
    else:
        await update.message.reply_text("Usage: /auto [on|off]")


async def cmd_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    await update.message.reply_text("🔴 Closing all positions...")
    try:
        from bot.trader.factory import get_trader
        trader = get_trader()
        positions = await trader.get_open_positions()
        if not positions:
            await update.message.reply_text("No open positions to close.")
            return
        for pos in positions:
            # pos is an Order dataclass — access .id and .entry directly
            await trader.close_position(pos.id, pos.entry)
        await update.message.reply_text(f"✅ Closed {len(positions)} position(s).")
    except Exception as e:
        await update.message.reply_text(f"❌ Close error: {e}")


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Emergency stop — set circuit_breaker in DB."""
    if not _is_authorized(update):
        return
    try:
        await db_execute(
            "INSERT OR REPLACE INTO config (key, value, updated_at) VALUES ('circuit_breaker', 'true', CURRENT_TIMESTAMP)"
        )
        await db_execute(
            "INSERT OR REPLACE INTO config (key, value, updated_at) VALUES ('auto_trade', 'false', CURRENT_TIMESTAMP)"
        )
        await update.message.reply_text(
            "🚨 *EMERGENCY STOP ACTIVATED*\n\nCircuit breaker set. Auto trade disabled.\nUse /auto on to resume.",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Stop error: {e}")


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    try:
        rows = await db_fetchall(
            """SELECT side, entry, close_price, pnl FROM trades
               WHERE status = 'closed' ORDER BY closed_at DESC LIMIT 10"""
        )
        trades = [dict(r) for r in (rows or [])]
        if not trades:
            # Fallback to paper_orders (uses pnl_usd and close_time columns)
            rows = await db_fetchall(
                """SELECT side, entry, close_price, pnl_usd AS pnl FROM paper_orders
                   WHERE status IN ('closed', 'stopped') ORDER BY close_time DESC LIMIT 10"""
            )
            trades = [dict(r) for r in (rows or [])]
        text = format_history(trades)
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ History error: {e}")


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """On-demand health report."""
    if not _is_authorized(update):
        return
    try:
        from bot.health.monitor import HealthMonitor
        monitor = HealthMonitor()
        text = await monitor.get_health_report()
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ Health check error: {e}")


async def cmd_filter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show filter status (/filter status)."""
    if not _is_authorized(update):
        return
    args = context.args
    if not args or args[0] != "status":
        await update.message.reply_text("Usage: /filter status")
        return
    try:
        import asyncio
        from bot.filters import FilterChain
        from bot.modes.manager import get_current_mode
        mode = await get_current_mode()
        chain = FilterChain()
        atr_result = await chain.vol_filter.check_atr_spike(mode)
        dxy = await chain.corr_filter.get_dxy_proxy_trend()
        cycle = await chain.get_cycle_context()

        status_line = (
            "✅ All filters PASS"
            if atr_result.passed
            else f"⚠️ ATR blocked ({atr_result.spike_ratio:.1f}x)"
        )
        text = (
            f"🔍 *Filter Status* — {fmt_ict(utc_now(), '%H:%M VN')}\n\n"
            f"ATR Spike Guard:\n"
            f"  Ratio: `{atr_result.spike_ratio:.2f}x` {'✅' if atr_result.passed else '❌'}\n\n"
            f"DXY (EURUSD proxy): `{dxy}`\n\n"
            f"Cycle: `{cycle.get('month')}` — {cycle.get('seasonal_bias')} seasonal\n"
            f"Session: `{cycle.get('session')}`\n\n"
            f"*Status:* {status_line}"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ Filter status error: {e}")


async def cmd_cost(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cost report.

    Usage:
      /cost                          — today's costs
      /cost today                    — today's costs
      /cost mtd                      — month-to-date
      /cost from YYYY-MM-DD          — from date to today
      /cost from YYYY-MM-DD to YYYY-MM-DD   — custom range
      /cost llm                      — LLM breakdown (MTD)
      /cost set vps 10               — record VPS cost $10/month
      /cost set domain 1.5           — record domain cost $1.5/month
      /cost export                   — export CSV
    """
    if not _is_authorized(update):
        return

    from bot.cost.tracker import (
        export_csv,
        format_cost_report,
        get_cost_summary,
        get_daily_summary,
        get_mtd_summary,
        set_infra_cost,
    )

    args = context.args or []

    def _validate_date(s: str) -> None:
        try:
            datetime.strptime(s, "%Y-%m-%d")
        except ValueError:
            raise ValueError(f"Invalid date format: `{s}`. Use YYYY-MM-DD")

    try:
        match args:
            case [] | ["today"]:
                summary = await get_daily_summary()
                text = await format_cost_report(summary)
                await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

            case ["mtd"]:
                summary = await get_mtd_summary()
                text = await format_cost_report(summary)
                await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

            case ["from", d1, "to", d2]:
                _validate_date(d1)
                _validate_date(d2)
                summary = await get_cost_summary(d1, d2)
                text = await format_cost_report(summary)
                await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

            case ["from", d1]:
                _validate_date(d1)
                summary = await get_cost_summary(d1, today_utc())
                text = await format_cost_report(summary)
                await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

            case ["llm"]:
                summary = await get_mtd_summary()
                by_model = summary.get("by_model", [])
                if not by_model:
                    await update.message.reply_text("No LLM usage recorded this month.")
                    return
                lines = [
                    f"📡 *LLM Breakdown — MTD*\n"
                    f"Period: `{month_start_utc()}` → `{today_utc()}`\n"
                ]
                for m in by_model:
                    cost = float(m.get("cost") or 0)
                    calls = int(m.get("calls") or 0)
                    in_tok = int(m.get("in_tok") or 0)
                    out_tok = int(m.get("out_tok") or 0)
                    lines.append(
                        f"  `{m['model']}`: {calls} calls | "
                        f"{in_tok:,} in / {out_tok:,} out | `${cost:.4f}`"
                    )
                lines.append(f"\n*Total LLM:* `${summary['llm_total']:.4f}`")
                await update.message.reply_text(
                    "\n".join(lines), parse_mode=ParseMode.MARKDOWN
                )

            case ["set", category, amount_str]:
                try:
                    amount = float(amount_str)
                except ValueError:
                    await update.message.reply_text(f"❌ Invalid amount: `{amount_str}`")
                    return
                await set_infra_cost(category, amount)
                await update.message.reply_text(
                    f"✅ Infra cost saved: `{category}` = `${amount:.2f}`/month",
                    parse_mode=ParseMode.MARKDOWN,
                )

            case ["export"]:
                csv_path = await export_csv()
                await update.message.reply_document(
                    document=open(csv_path, "rb"),
                    filename="llm_usage_export.csv",
                )

            case _:
                await update.message.reply_text(
                    "💰 *Cost Command Help*\n\n"
                    "/cost — today\n"
                    "/cost mtd — month-to-date\n"
                    "/cost from YYYY-MM-DD — from date to today\n"
                    "/cost from YYYY-MM-DD to YYYY-MM-DD — custom range\n"
                    "/cost llm — LLM model breakdown (MTD)\n"
                    "/cost set vps 10 — set VPS cost $10/month\n"
                    "/cost export — download CSV",
                    parse_mode=ParseMode.MARKDOWN,
                )
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"cmd_cost error: {e}")
        await update.message.reply_text(f"❌ Cost error: {e}")


async def callback_approve_reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline keyboard Approve/Reject button presses."""
    query = update.callback_query
    await query.answer()

    if not _is_authorized(update):
        return

    data = query.data  # "approve_{signal_id}" or "reject_{signal_id}"
    parts = data.split("_", 1)
    if len(parts) != 2:
        return
    action, signal_id = parts[0], parts[1]

    if action == "approve":
        try:
            await db_execute(
                "UPDATE signals SET status = 'approved' WHERE id = ?", signal_id
            )
            await query.edit_message_text(
                query.message.text + "\n\n✅ *Approved — executing...*",
                parse_mode=ParseMode.MARKDOWN,
            )
            # Trigger execution
            try:
                from bot.trader.trade_executor import execute_signal
                await execute_signal(signal_id)
            except Exception as e:
                logger.error(f"Execution after approve failed: {e}")
        except Exception as e:
            logger.error(f"Approve callback error: {e}")
            await query.edit_message_text(f"❌ Approve error: {e}")

    elif action == "reject":
        await db_execute(
            "UPDATE signals SET status = 'rejected' WHERE id = ?", signal_id
        )
        await query.edit_message_text(
            query.message.text + "\n\n❌ *Rejected*",
            parse_mode=ParseMode.MARKDOWN,
        )
