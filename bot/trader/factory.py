"""bot/trader/factory.py
-----------------------
Trader factory — returns MockTrader (paper) or RealTrader (live)
based on settings.paper_trade.
"""

from bot.config import settings
from bot.trader.base import BaseTrader

_trader: BaseTrader | None = None


def get_trader() -> BaseTrader:
    """Return the singleton trader instance.

    - paper_trade=True  → MockTrader (no Binance API calls)
    - paper_trade=False → RealTrader (live Binance Futures)
    """
    global _trader
    if _trader is None:
        if settings.paper_trade:
            from bot.trader.mock_trader import MockTrader
            _trader = MockTrader()
        else:
            from bot.trader.real_trader import RealTrader
            _trader = RealTrader()
    return _trader


def reset_trader() -> None:
    """Reset the trader singleton (useful for tests and mode switches)."""
    global _trader
    _trader = None
