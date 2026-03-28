"""bot/trader
-------------
Execution engine — abstracts live and paper trading behind a common interface.

Public API
----------
    from bot.trader import BaseTrader, Order, get_trader

    trader = get_trader()          # MockTrader (paper) or RealTrader (live)
    order  = await trader.place_order(signal, size)
    pnl    = await trader.close_position(order.id, close_price)
    positions = await trader.get_open_positions()

Submodules
----------
    bot.trader.base             — BaseTrader ABC and Order dataclass
    bot.trader.factory          — get_trader() / reset_trader()
    bot.trader.mock_trader      — Paper trade implementation (SQLite)
    bot.trader.real_trader      — Live Binance Futures implementation
    bot.trader.trade_executor   — execute_signal(signal_id) high-level flow
    bot.trader.position_monitor — position_monitor_loop() async 30s poller
"""

from bot.trader.base import BaseTrader, Order
from bot.trader.factory import get_trader, reset_trader

__all__ = [
    "BaseTrader",
    "Order",
    "get_trader",
    "reset_trader",
]
