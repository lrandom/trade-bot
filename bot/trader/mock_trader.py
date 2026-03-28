"""bot/trader/mock_trader.py
---------------------------
Paper trade implementation — writes to the paper_orders table in SQLite.
No Binance API calls are made; PnL is calculated from price data only.
"""

import uuid

from loguru import logger

from bot.config import settings
from bot.database import db_execute, db_fetchall, db_fetchone
from bot.trader.base import BaseTrader, Order


class MockTrader(BaseTrader):
    """Paper trade mock — same interface as RealTrader, zero real money risk."""

    async def place_order(self, signal: dict, size: float) -> Order:
        order_id = str(uuid.uuid4())
        action = signal.get("action", "BUY")
        entry = float(signal.get("entry", signal.get("entry_price", 0)))
        sl = float(signal.get("sl", signal.get("stop_loss", 0)))
        tp1 = float(signal.get("tp1", 0))
        tp2 = float(signal.get("tp2", 0))
        tp3 = float(signal.get("tp3", 0))
        mode = signal.get("mode", "swing")
        signal_id = signal.get("id", "")

        await db_execute(
            """INSERT INTO paper_orders
               (id, signal_id, symbol, side, entry, stop_loss, tp1, tp2, tp3, size, mode, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')""",
            order_id, signal_id, settings.trading_symbol, action, entry, sl, tp1, tp2, tp3, size, mode,
        )

        logger.info(
            f"[PAPER] Order placed: {action} {size:.4f}oz @ ${entry:,.2f}, "
            f"SL=${sl:,.2f}, TP3=${tp3:,.2f}"
        )

        from bot.telegram.notifier import send_message
        await send_message(
            f"📄 *Paper Order Placed*\n"
            f"{action} {size:.4f}oz @ ${entry:,.2f}\n"
            f"SL: ${sl:,.2f} | TP3: ${tp3:,.2f}",
        )

        return Order(
            id=order_id,
            symbol=settings.trading_symbol,
            side=action,
            entry=entry,
            stop_loss=sl,
            tp1=tp1,
            tp2=tp2,
            tp3=tp3,
            size=size,
            status="open",
        )

    async def close_position(self, order_id: str, close_price: float) -> float:
        """Close a paper position at close_price, record PnL, return realized PnL."""
        row = await db_fetchone("SELECT * FROM paper_orders WHERE id = ?", order_id)
        if not row:
            return 0.0
        row = dict(row)
        entry = float(row.get("entry", 0))
        size = float(row.get("size", 0))
        side = row.get("side", "BUY")

        pnl = (
            (close_price - entry) * size
            if side == "BUY"
            else (entry - close_price) * size
        )

        await db_execute(
            """UPDATE paper_orders
               SET status='closed', close_price=?, pnl_usd=?, close_time=CURRENT_TIMESTAMP
               WHERE id=?""",
            close_price, pnl, order_id,
        )

        logger.info(
            f"[PAPER] Position closed: {order_id} @ ${close_price:,.2f}, PnL=${pnl:.2f}"
        )
        return pnl

    async def get_open_positions(self) -> list[Order]:
        """Return all open paper positions."""
        rows = await db_fetchall("SELECT * FROM paper_orders WHERE status = 'open'")
        if not rows:
            return []
        result: list[Order] = []
        for r in rows:
            r = dict(r)
            result.append(
                Order(
                    id=str(r["id"]),
                    symbol=r.get("symbol", settings.trading_symbol),
                    side=r.get("side", "BUY"),
                    entry=float(r.get("entry", 0)),
                    stop_loss=float(r.get("stop_loss", 0)),
                    tp1=float(r.get("tp1", 0)),
                    tp2=float(r.get("tp2", 0)),
                    tp3=float(r.get("tp3", 0)),
                    size=float(r.get("size", 0)),
                    status="open",
                )
            )
        return result

    async def cancel_all_orders(self, symbol: str = "XAUUSDT") -> None:
        """Cancel all open paper orders for symbol."""
        await db_execute(
            "UPDATE paper_orders SET status='cancelled' WHERE status='open' AND symbol=?",
            symbol,
        )
        logger.info(f"[PAPER] Cancelled all open paper orders for {symbol}")
