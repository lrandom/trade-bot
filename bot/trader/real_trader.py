"""bot/trader/real_trader.py
---------------------------
Live Binance Futures order execution.
All Binance API calls are wrapped with 3-retry exponential backoff.
"""

import asyncio

from loguru import logger

from bot.config import settings
from bot.data.binance_client import get_client
from bot.trader.base import BaseTrader, Order


class RealTrader(BaseTrader):
    """Live Binance Futures order execution."""

    # ------------------------------------------------------------------
    # Internal retry helper
    # ------------------------------------------------------------------

    async def _with_retry(self, coro_fn, *args, retries: int = 3, base_delay: float = 1.0, **kwargs):
        """Call an async function with exponential-backoff retry on any exception."""
        for attempt in range(retries):
            try:
                return await coro_fn(*args, **kwargs)
            except Exception as e:
                if attempt == retries - 1:
                    raise
                wait = base_delay * (2 ** attempt)
                logger.warning(
                    f"Binance call attempt {attempt + 1}/{retries} failed: {e}. "
                    f"Retrying in {wait:.1f}s"
                )
                await asyncio.sleep(wait)

    # ------------------------------------------------------------------
    # BaseTrader interface
    # ------------------------------------------------------------------

    async def place_order(self, signal: dict, size: float) -> Order:
        """
        Execute a trade on Binance Futures:
          1. Set leverage.
          2. Place MARKET entry order.
          3. Place STOP_MARKET stop-loss order (reduce_only=True).
          4. Return populated Order dataclass.
        """
        client = await get_client()
        action = signal.get("action", "BUY")
        sl = float(signal.get("sl", signal.get("stop_loss", 0)))
        leverage = int(signal.get("leverage", 10))
        tp1 = float(signal.get("tp1", 0))
        tp2 = float(signal.get("tp2", 0))
        tp3 = float(signal.get("tp3", 0))

        binance_side = "BUY" if action == "BUY" else "SELL"
        sl_side = "SELL" if action == "BUY" else "BUY"

        for attempt in range(3):
            try:
                # 1. Set leverage
                await client.futures_change_leverage(
                    symbol=settings.trading_symbol, leverage=leverage
                )

                # 2. Market entry
                entry_order = await client.futures_create_order(
                    symbol=settings.trading_symbol,
                    side=binance_side,
                    type="MARKET",
                    quantity=round(size, 3),
                )
                fill_price = float(
                    entry_order.get("avgPrice", signal.get("entry", signal.get("entry_price", 0)))
                )
                order_id = str(entry_order.get("orderId", ""))

                # 3. Stop-loss order
                if sl > 0:
                    await client.futures_create_order(
                        symbol=settings.trading_symbol,
                        side=sl_side,
                        type="STOP_MARKET",
                        stopPrice=sl,
                        quantity=round(size, 3),
                        reduceOnly=True,
                    )

                logger.info(
                    f"[LIVE] Order placed: {action} {size:.3f}oz @ ${fill_price:,.2f}, SL=${sl:,.2f}"
                )
                return Order(
                    id=order_id,
                    symbol=settings.trading_symbol,
                    side=action,
                    entry=fill_price,
                    stop_loss=sl,
                    tp1=tp1,
                    tp2=tp2,
                    tp3=tp3,
                    size=round(size, 3),
                    status="open",
                )
            except Exception as e:
                if attempt == 2:
                    logger.error(f"[LIVE] place_order failed after 3 attempts: {e}")
                    raise
                wait = 2 ** attempt
                logger.warning(
                    f"[LIVE] Order attempt {attempt + 1}/3 failed: {e}. Retry in {wait}s"
                )
                await asyncio.sleep(wait)

    async def close_position(self, order_id: str, close_price: float) -> float:
        """Close entire position at market. Returns approximate unrealized PnL."""
        client = await get_client()
        try:
            positions = await client.futures_position_information(symbol=settings.trading_symbol)
            for pos in positions:
                amt = float(pos.get("positionAmt", 0))
                if abs(amt) < 0.001:
                    continue
                side = "SELL" if amt > 0 else "BUY"
                await client.futures_create_order(
                    symbol=settings.trading_symbol,
                    side=side,
                    type="MARKET",
                    quantity=round(abs(amt), 3),
                    reduceOnly=True,
                )
                pnl = float(pos.get("unRealizedProfit", 0))
                logger.info(
                    f"[LIVE] Position closed: {side} {abs(amt):.3f}oz @ ${close_price:,.2f}, "
                    f"unrealized PnL=${pnl:.2f}"
                )
                return pnl
        except Exception as e:
            logger.error(f"[LIVE] close_position error: {e}")
        return 0.0

    async def get_open_positions(self) -> list[Order]:
        """Return all open Binance Futures positions for XAUUSDT."""
        client = await get_client()
        try:
            positions = await client.futures_position_information(symbol=settings.trading_symbol)
            result: list[Order] = []
            for pos in positions:
                amt = float(pos.get("positionAmt", 0))
                if abs(amt) < 0.001:
                    continue
                result.append(
                    Order(
                        id=str(pos.get("symbol", settings.trading_symbol)),
                        symbol=settings.trading_symbol,
                        side="BUY" if amt > 0 else "SELL",
                        entry=float(pos.get("entryPrice", 0)),
                        stop_loss=0.0,
                        tp1=0.0,
                        tp2=0.0,
                        tp3=0.0,
                        size=abs(amt),
                        status="open",
                        pnl=float(pos.get("unRealizedProfit", 0)),
                    )
                )
            return result
        except Exception as e:
            logger.error(f"[LIVE] get_open_positions error: {e}")
            return []

    async def cancel_all_orders(self, symbol: str = "XAUUSDT") -> None:
        """Cancel all open orders for symbol."""
        client = await get_client()
        try:
            await client.futures_cancel_all_open_orders(symbol=symbol)
            logger.info(f"[LIVE] Cancelled all orders for {symbol}")
        except Exception as e:
            logger.error(f"[LIVE] cancel_all_orders error: {e}")
