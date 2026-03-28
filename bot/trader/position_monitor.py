"""bot/trader/position_monitor.py
---------------------------------
Async loop that polls open positions every 30 seconds and checks
TP/SL price levels.  For paper trade, all logic is local.
For live mode, position state is reconciled via Binance data.
"""

import asyncio

from loguru import logger

from bot.database import db_execute, db_fetchall


async def position_monitor_loop(shutdown_event: asyncio.Event) -> None:
    """Poll open positions every 30s and check TP/SL hits.

    Designed to run as a background asyncio task.  Stops cleanly when
    shutdown_event is set.
    """
    logger.info("Position monitor started")
    while not shutdown_event.is_set():
        try:
            await _check_positions()
        except Exception as e:
            logger.error(f"position_monitor_loop error: {e}")
        # Wait 30s or until shutdown — whichever comes first
        try:
            await asyncio.wait_for(
                asyncio.shield(shutdown_event.wait()), timeout=30.0
            )
        except asyncio.TimeoutError:
            pass
    logger.info("Position monitor stopped")


async def _check_positions() -> None:
    from bot.config import settings

    if settings.paper_trade:
        await _check_paper_positions()
    else:
        await _check_live_positions()


async def _check_paper_positions() -> None:
    """Check open paper orders against the current Binance mark price."""
    positions = await db_fetchall(
        "SELECT * FROM paper_orders WHERE status = 'open'"
    )
    if not positions:
        return

    current_price = await _fetch_current_price()
    if current_price <= 0:
        return

    for pos in positions:
        pos = dict(pos)
        await _evaluate_paper_position(pos, current_price)


async def _evaluate_paper_position(pos: dict, current_price: float) -> None:
    """Check a single paper position for SL/TP hits and update DB."""
    entry = float(pos.get("entry", 0))
    sl = float(pos.get("stop_loss", 0))
    tp1 = float(pos.get("tp1", 0))
    tp2 = float(pos.get("tp2", 0))
    tp3 = float(pos.get("tp3", 0))
    side = pos.get("side", "BUY")
    order_id = pos.get("id")

    is_buy = side == "BUY"

    hit_sl = (is_buy and sl > 0 and current_price <= sl) or (
        not is_buy and sl > 0 and current_price >= sl
    )
    hit_tp3 = (is_buy and tp3 > 0 and current_price >= tp3) or (
        not is_buy and tp3 > 0 and current_price <= tp3
    )
    hit_tp1 = (is_buy and tp1 > 0 and current_price >= tp1) or (
        not is_buy and tp1 > 0 and current_price <= tp1
    )

    if hit_sl:
        close_px = sl if sl > 0 else current_price
        pnl = _calc_pnl(side, entry, close_px, float(pos.get("size", 1)))
        await db_execute(
            """UPDATE paper_orders
               SET status='stopped', close_price=?, pnl_usd=?, close_time=CURRENT_TIMESTAMP
               WHERE id=?""",
            close_px, pnl, order_id,
        )
        logger.info(
            f"[PAPER] SL hit: {order_id} @ ${close_px:,.2f}, PnL=${pnl:.2f}"
        )
        from bot.telegram.notifier import send_message
        await send_message(
            f"🔴 *SL Hit* — Paper Trade\n"
            f"Order: `{order_id}`\n"
            f"Close: ${close_px:,.2f} | PnL: ${pnl:.2f}"
        )

    elif hit_tp3:
        pnl = _calc_pnl(side, entry, current_price, float(pos.get("size", 1)))
        await db_execute(
            """UPDATE paper_orders
               SET status='closed', close_price=?, pnl_usd=?, close_time=CURRENT_TIMESTAMP
               WHERE id=?""",
            current_price, pnl, order_id,
        )
        logger.info(
            f"[PAPER] TP3 hit: {order_id} @ ${current_price:,.2f}, PnL=${pnl:.2f}"
        )
        from bot.telegram.notifier import send_message
        await send_message(
            f"✅ *TP3 Hit* — Paper Trade\n"
            f"Order: `{order_id}`\n"
            f"Close: ${current_price:,.2f} | PnL: ${pnl:.2f}"
        )

    elif hit_tp1 and pos.get("status") == "open":
        # Partial close at TP1: update status to tp1_hit (position remains open)
        await db_execute(
            "UPDATE paper_orders SET status='tp1_hit' WHERE id=?",
            order_id,
        )
        logger.info(
            f"[PAPER] TP1 hit: {order_id} @ ${current_price:,.2f}"
        )
        from bot.telegram.notifier import send_message
        await send_message(
            f"🟡 *TP1 Hit* — Paper Trade\n"
            f"Order: `{order_id}` @ ${current_price:,.2f}\n"
            f"Position continues toward TP2/TP3."
        )


async def _check_live_positions() -> None:
    """For live mode: Binance handles order fills natively.

    This loop is a safety net — it logs open positions and can be extended
    to reconcile DB state with Binance position data.
    """
    try:
        from bot.trader.factory import get_trader
        trader = get_trader()
        positions = await trader.get_open_positions()
        if positions:
            logger.debug(f"[LIVE] {len(positions)} open position(s) on Binance")
    except Exception as e:
        logger.error(f"[LIVE] _check_live_positions error: {e}")


async def _fetch_current_price() -> float:
    """Fetch the current XAUUSDT mark price from Binance."""
    try:
        from bot.data.binance_client import get_client
        client = await get_client()
        ticker = await client.get_symbol_ticker(symbol="XAUUSDT")
        return float(ticker.get("price", 0))
    except Exception as e:
        logger.warning(f"Could not fetch current price: {e}")
        return 0.0


def _calc_pnl(side: str, entry: float, close_price: float, size: float) -> float:
    """Calculate PnL for a position."""
    if side == "BUY":
        return (close_price - entry) * size
    return (entry - close_price) * size
