"""bot/trader/base.py
--------------------
Abstract base class for all trader implementations (live and paper).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Order:
    id: str
    symbol: str
    side: str          # BUY | SELL
    entry: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    size: float        # qty in oz / contracts
    status: str        # open | closed | stopped
    pnl: float = 0.0


class BaseTrader(ABC):
    @abstractmethod
    async def place_order(self, signal: dict, size: float) -> Order:
        """Place an order for the given signal and position size."""
        ...

    @abstractmethod
    async def close_position(self, order_id: str, close_price: float) -> float:
        """Close position at close_price. Returns realized PnL."""
        ...

    @abstractmethod
    async def get_open_positions(self) -> list[Order]:
        """Return all currently open positions."""
        ...

    @abstractmethod
    async def cancel_all_orders(self, symbol: str = "XAUUSDT") -> None:
        """Cancel all open orders for the given symbol."""
        ...
