"""bot/risk/circuit_breaker.py
------------------------------
Daily drawdown circuit breaker.

State is stored in the DB ``config`` table under two keys:
    ``daily_pnl``        — running PnL for the current UTC trading day (REAL as TEXT)
    ``circuit_breaker``  — "true" | "false" (TEXT)

Persisting state in the DB ensures the circuit breaker survives process
restarts or crashes.  Only the scheduled midnight reset job clears it;
there is intentionally no Telegram command to reset it (safety measure).

All public functions are async to match the aiosqlite-based DB layer.
"""

from loguru import logger

DAILY_LOSS_LIMIT_PCT: float = 5.0  # trip breaker when loss > 5 % of balance


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------

async def is_circuit_breaker_active() -> bool:
    """Return True if the circuit breaker is currently tripped.

    Reads the ``circuit_breaker`` key from the ``config`` table.
    Returns False when the key is absent or set to any value other than
    ``"true"``.
    """
    from bot.database import db_scalar

    val = await db_scalar("SELECT value FROM config WHERE key='circuit_breaker'")
    return val == "true"


async def get_daily_pnl() -> float:
    """Return the current daily PnL accumulated so far (USDT)."""
    from bot.database import db_scalar

    val = await db_scalar("SELECT value FROM config WHERE key='daily_pnl'")
    try:
        return float(val) if val is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# Trip / check logic
# ---------------------------------------------------------------------------

async def check_and_trip(balance: float) -> bool:
    """Check the daily loss against the limit and trip the breaker if needed.

    If the breaker is already active this function returns immediately without
    a second DB write.

    Args:
        balance: Current account equity in USDT (fetched live from exchange).

    Returns:
        True  — breaker is active (trading must be halted).
        False — daily loss is within limits; trading may continue.
    """
    from bot.database import db_execute

    # Fast path: already tripped
    if await is_circuit_breaker_active():
        return True

    daily_pnl = await get_daily_pnl()
    loss_limit = -(balance * DAILY_LOSS_LIMIT_PCT / 100)

    if daily_pnl < loss_limit:
        await db_execute(
            "UPDATE config SET value='true', updated_at=CURRENT_TIMESTAMP "
            "WHERE key='circuit_breaker'"
        )
        logger.warning(
            f"Circuit breaker TRIPPED: daily_pnl={daily_pnl:.2f} USDT, "
            f"limit={loss_limit:.2f} USDT (balance={balance:.2f})"
        )
        return True

    return False


# ---------------------------------------------------------------------------
# Mutation helpers
# ---------------------------------------------------------------------------

async def update_daily_pnl(pnl_delta: float) -> None:
    """Atomically add *pnl_delta* to the running daily PnL in the DB.

    Uses a single SQL arithmetic update to avoid read-modify-write races
    when multiple coroutines might attempt an update concurrently.

    Args:
        pnl_delta: Realised PnL of the closed trade in USDT (negative = loss).
    """
    from bot.database import db_execute

    await db_execute(
        "UPDATE config "
        "SET value = CAST(ROUND(CAST(value AS REAL) + ?, 2) AS TEXT), "
        "    updated_at = CURRENT_TIMESTAMP "
        "WHERE key = 'daily_pnl'",
        pnl_delta,
    )


async def reset_daily_pnl() -> None:
    """Reset both ``daily_pnl`` and ``circuit_breaker`` to their defaults.

    Intended to be called by the APScheduler cron job at 00:01 UTC every day.
    """
    from bot.database import db_execute

    await db_execute(
        "UPDATE config "
        "SET value = CASE key WHEN 'daily_pnl' THEN '0' ELSE 'false' END, "
        "    updated_at = CURRENT_TIMESTAMP "
        "WHERE key IN ('daily_pnl', 'circuit_breaker')"
    )
    logger.info("Daily PnL and circuit breaker reset for new trading day")
