"""bot/modes/manager.py
----------------------
DB-backed trading mode and auto-trade flag management.

All state is stored in the ``config`` table so that:
    - mode changes take effect on the next analysis cycle without restart;
    - the last mode survives process crashes / restarts;
    - Telegram commands (``/mode``, ``/auto``) can update state at runtime.

Public API
----------
    from bot.modes.manager import (
        get_current_mode, set_mode,
        get_auto_trade, set_auto_trade,
    )
"""

from loguru import logger

from bot.modes.config import MODES


# ---------------------------------------------------------------------------
# Trading mode
# ---------------------------------------------------------------------------

async def get_current_mode() -> str:
    """Return the active trading mode stored in the DB.

    Falls back to ``"swing"`` if the ``config`` row is absent or NULL
    (defensive default — the schema seeds it to ``"intraday"``).

    Returns:
        One of ``"scalp"``, ``"intraday"``, ``"swing"``.
    """
    from bot.database import db_scalar

    val = await db_scalar("SELECT value FROM config WHERE key='mode'")
    return val or "swing"


async def set_mode(mode: str) -> None:
    """Persist *mode* to the DB and log the change.

    The update takes effect on the next ``run_analysis_cycle`` invocation;
    no scheduler restart is required for interval-based modes (phase-08
    orchestrator re-reads the mode on each tick).

    Args:
        mode: Must be one of the keys in :data:`bot.modes.config.MODES`.

    Raises:
        ValueError: If *mode* is not a recognised trading mode.
    """
    if mode not in MODES:
        raise ValueError(
            f"Unknown mode: {mode!r}. Valid modes: {list(MODES.keys())}"
        )

    from bot.database import db_execute

    await db_execute(
        "UPDATE config SET value=?, updated_at=CURRENT_TIMESTAMP WHERE key='mode'",
        mode,
    )
    logger.info(f"Trading mode changed to: {mode}")


# ---------------------------------------------------------------------------
# Auto-trade flag
# ---------------------------------------------------------------------------

async def get_auto_trade() -> bool:
    """Return True if the bot is configured to place orders automatically.

    When False the bot sends signals to Telegram for manual approval instead
    of placing orders directly on the exchange.

    Returns:
        True if ``auto_trade`` config value equals ``"true"``; False otherwise.
    """
    from bot.database import db_scalar

    val = await db_scalar("SELECT value FROM config WHERE key='auto_trade'")
    return val == "true"


async def set_auto_trade(enabled: bool) -> None:
    """Enable or disable automatic order placement and persist the flag.

    Args:
        enabled: ``True`` to enable auto-trading; ``False`` for manual mode.
    """
    from bot.database import db_execute

    val = "true" if enabled else "false"
    await db_execute(
        "UPDATE config SET value=?, updated_at=CURRENT_TIMESTAMP WHERE key='auto_trade'",
        val,
    )
    logger.info(f"Auto trade set to: {enabled}")


# ---------------------------------------------------------------------------
# Convenience helper
# ---------------------------------------------------------------------------

async def get_mode_config() -> dict:
    """Return the full config dict for the currently active mode.

    Thin wrapper combining :func:`get_current_mode` and
    :func:`bot.modes.config.get_mode_config`.
    """
    from bot.modes.config import get_mode_config as _cfg

    mode = await get_current_mode()
    return _cfg(mode)
