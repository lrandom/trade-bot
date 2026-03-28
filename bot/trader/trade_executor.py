"""bot/trader/trade_executor.py
-------------------------------
High-level signal execution: fetch from DB, validate with RiskEngine,
place order via trader, save to trades table.
"""

import uuid
from types import SimpleNamespace

from loguru import logger

from bot.database import db_execute, db_fetchone
from bot.risk import RiskEngine
from bot.trader.factory import get_trader


async def execute_signal(signal_id: str) -> bool:
    """
    Fetch signal from DB, validate with RiskEngine, place order, save trade record.

    Returns True if executed successfully, False otherwise.
    """
    signal = await db_fetchone("SELECT * FROM signals WHERE id = ?", signal_id)
    if not signal:
        logger.error(f"Signal {signal_id} not found in DB")
        return False

    signal = dict(signal)

    # Guard: only process pending or approved signals
    if signal.get("status") not in ("pending", "approved"):
        logger.info(f"Signal {signal_id} already processed: {signal.get('status')}")
        return False

    # Build a minimal object that satisfies RiskEngine.pre_trade_check interface
    signal_obj = SimpleNamespace(
        action=signal.get("action", "HOLD"),
        confidence=int(signal.get("confidence") or 0),
        entry_price=float(signal.get("entry") or 0),
        stop_loss=float(signal.get("sl") or 0),
        mark_price=0.0,  # not available at DB read time; stale-price check skipped
    )

    balance = await _get_balance()
    mode = signal.get("mode", "swing")

    risk_engine = RiskEngine()
    approved, reason = await risk_engine.pre_trade_check(signal_obj, mode, balance)
    if not approved:
        logger.warning(f"Risk check FAILED for signal {signal_id}: {reason}")
        await db_execute(
            "UPDATE signals SET status = 'rejected' WHERE id = ?", signal_id
        )
        return False

    entry = float(signal.get("entry") or 0)
    sl = float(signal.get("sl") or 0)

    size = risk_engine.calc_size(balance, mode, entry, sl)
    if size <= 0:
        logger.warning(f"Invalid position size {size} for signal {signal_id}")
        await db_execute(
            "UPDATE signals SET status = 'rejected' WHERE id = ?", signal_id
        )
        return False

    trader = get_trader()
    try:
        order = await trader.place_order(signal, size)

        trade_id = str(uuid.uuid4())
        leverage = int(signal.get("leverage") or 10)

        await db_execute(
            """INSERT INTO trades
               (id, signal_id, side, entry, quantity, leverage, status, mode)
               VALUES (?, ?, ?, ?, ?, ?, 'open', ?)""",
            trade_id,
            signal_id,
            signal.get("action"),
            entry,
            size,
            leverage,
            mode,
        )
        await db_execute(
            "UPDATE signals SET status = 'executed' WHERE id = ?", signal_id
        )

        logger.info(
            f"Trade executed: trade_id={trade_id}, signal_id={signal_id}, "
            f"side={order.side}, entry=${entry:,.2f}, size={size:.3f}oz"
        )
        return True

    except Exception as e:
        logger.error(f"execute_signal error for {signal_id}: {e}")
        await db_execute(
            "UPDATE signals SET status = 'error' WHERE id = ?", signal_id
        )
        return False


async def _get_balance() -> float:
    """Return account balance — paper default or live Binance balance."""
    from bot.config import settings

    if settings.paper_trade:
        return 10_000.0

    try:
        from bot.data.binance_client import get_client

        client = await get_client()
        account = await client.futures_account()
        return float(account.get("availableBalance", 10_000.0))
    except Exception as e:
        logger.warning(f"Could not fetch Binance balance: {e} — using $10,000 default")
        return 10_000.0
