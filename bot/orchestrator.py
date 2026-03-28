"""bot/orchestrator.py
---------------------
Main event loop orchestration. Wires all components together.

Startup sequence:
    1. Load settings + logger
    2. init_db()
    3. Connect Binance client
    4. Read mode from DB
    5. Build LLMEngine + RiskEngine
    6. Build Telegram Application
    7. Start APScheduler (analysis_job + daily_reset)
    8. Start position_monitor_loop task
    9. If scalp: start websocket task
    10. Start Telegram polling
    11. Await shutdown_event
    12. Graceful shutdown
"""

from __future__ import annotations

import asyncio
import signal
import sys
import uuid
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from bot.config import settings
from bot.data.binance_client import get_client, close_client
from bot.data.macro import get_macro_context
from bot.data.snapshot import build_snapshot
from bot.database import init_db, db_execute, db_fetchone
from bot.filters import FilterChain
from bot.health.monitor import HealthMonitor
from bot.llm.engine import LLMEngine
from bot.llm.models import TradingSignal
from bot.modes.manager import get_current_mode, get_auto_trade
from bot.risk import RiskEngine
from bot.telegram.bot import build_application, start_bot, stop_bot
from bot.telegram.notifier import send_message, send_signal_for_approval
from bot.telegram.formatters import format_signal
from bot.trader.factory import get_trader
from bot.trader.position_monitor import position_monitor_loop
from bot.utils.timezone import fmt_ict, utc_now

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

_shutdown_event: asyncio.Event | None = None
_scheduler: AsyncIOScheduler | None = None
_health_monitor: HealthMonitor | None = None
_ws_task: asyncio.Task | None = None       # track WebSocket task for scalp mode
_current_interval: int | None = None       # track current scheduler interval


def _handle_signal(sig):
    """Called by asyncio loop on SIGINT / SIGTERM."""
    logger.info(f"Received signal {sig.name} — shutting down")
    if _shutdown_event:
        _shutdown_event.set()


# ---------------------------------------------------------------------------
# Analysis cycle
# ---------------------------------------------------------------------------

async def _analysis_job_fn() -> None:
    """Module-level analysis job for APScheduler (interval-based modes)."""
    current_mode = await get_current_mode()
    await _run_analysis_cycle(current_mode)


async def _ws_analysis_task() -> None:
    """WebSocket feed — triggers analysis on every closed 1m candle (scalp mode)."""
    from bot.data.binance_client import get_client
    from bot.data.websocket_feed import CandleBuffer
    client = await get_client()
    buf = CandleBuffer(settings.trading_symbol, "1m")
    buf.on_close = lambda: asyncio.create_task(_run_analysis_cycle("scalp"))
    await buf.start(client)


async def apply_mode_switch(new_mode: str) -> str:
    """Apply scheduler/WebSocket changes immediately after a mode switch.

    Should be called right after set_mode() to sync the scheduler with the
    new mode. Returns a human-readable description of the new interval.
    """
    global _ws_task

    from bot.modes.config import get_mode_config
    mode_cfg = get_mode_config(new_mode)
    analysis_trigger = mode_cfg.get("analysis_trigger", "interval")
    interval_minutes = mode_cfg.get("interval_minutes")

    if _scheduler is None:
        return "Scheduler chưa khởi động"

    if analysis_trigger == "interval" and interval_minutes:
        # Interval-based (swing, intraday): reschedule or add job
        if _scheduler.get_job("analysis"):
            _scheduler.reschedule_job(
                "analysis",
                trigger="interval",
                minutes=interval_minutes,
            )
        else:
            _scheduler.add_job(
                _analysis_job_fn,
                trigger="interval",
                minutes=interval_minutes,
                id="analysis",
                max_instances=1,
                coalesce=True,
            )
        # Stop WebSocket if running (switching away from scalp)
        if _ws_task and not _ws_task.done():
            _ws_task.cancel()
            _ws_task = None
        return f"Phân tích mỗi {interval_minutes} phút"

    elif analysis_trigger == "candle_close":
        # Scalp: remove interval job, start WebSocket
        if _scheduler.get_job("analysis"):
            _scheduler.remove_job("analysis")
        if _ws_task is None or _ws_task.done():
            _ws_task = asyncio.create_task(_ws_analysis_task(), name="websocket")
        return "Real-time (WebSocket 1m candle)"

    return "Mode đã cập nhật"


async def trigger_analysis() -> "TradingSignal | None":
    """Called by /signal Telegram command to run one immediate cycle."""
    mode = await get_current_mode()
    return await _run_analysis_cycle(mode)


async def _run_analysis_cycle(mode: str) -> "TradingSignal | None":
    """Full analysis pipeline: filter → snapshot → LLM → risk → save/execute.

    Returns the TradingSignal produced (including HOLD), or None if blocked
    by a pre-LLM filter (ATR spike, etc.).
    """
    logger.info(f"=== Analysis cycle start | mode={mode} ===")

    try:
        # Phase 12: Pre-LLM filter (ATR spike check)
        filter_chain = FilterChain()
        atr_result = await filter_chain.vol_filter.check_atr_spike(mode)
        if not atr_result.passed:
            logger.info(f"ATR filter blocked — {atr_result.reason}")
            return None

        # Build market snapshot
        snapshot = await build_snapshot(mode)

        # Run LLM chain
        engine = LLMEngine(mode)
        signal: TradingSignal = await engine.generate_signal(snapshot)

        if signal.action == "HOLD":
            logger.info(f"Signal: HOLD — {signal.reasoning[:80]}")
            return signal

        logger.info(f"Signal: {signal.action} @ {signal.entry_price} (conf={signal.confidence}%)")

        # Phase 12: Post-LLM correlation filter
        signal_dict = {
            "action": signal.action,
            "confidence": signal.confidence,
            "id": "",
        }
        corr_result = await filter_chain.corr_filter.check_correlation(signal_dict)
        if not corr_result.passed:
            logger.info(f"Correlation filter blocked — {corr_result.reason}")
            signal.reasoning = f"[Corr filter] {corr_result.reason}"
            return signal
        # Adjust confidence from correlation
        adjusted_confidence = corr_result.adjusted_confidence or signal.confidence

        # Risk check
        risk_engine = RiskEngine()
        balance = await _get_balance()
        approved, reason = await risk_engine.pre_trade_check(signal, mode, balance)
        if not approved:
            logger.info(f"Risk check rejected: {reason}")
            signal.reasoning = f"[Risk] {reason}"
            return signal

        # Save signal to DB
        signal_id = await _save_signal(signal, mode, adjusted_confidence)

        # Cycle context for enriched Telegram message
        cycle = filter_chain.get_cycle_context()
        if asyncio.iscoroutine(cycle):
            cycle = await cycle

        # Execute or send for approval
        auto = await get_auto_trade()
        signal_dict_full = {
            "action": signal.action,
            "entry_price": signal.entry_price,
            "stop_loss": signal.stop_loss,
            "tp1": signal.tp1,
            "tp2": signal.tp2,
            "tp3": signal.tp3,
            "confidence": adjusted_confidence,
            "htf_bias": signal.htf_bias,
            "reasoning": signal.reasoning,
            "mode": mode,
        }

        if auto:
            logger.info(f"Auto trade: executing {signal.action} signal {signal_id}")
            from bot.trader.trade_executor import execute_signal
            success = await execute_signal(signal_id)
            if success:
                await send_message(
                    f"🤖 *Auto Trade Executed*\n"
                    f"{signal.action} @ ${signal.entry_price:,.2f}\n"
                    f"Confidence: {adjusted_confidence}%"
                )
        else:
            # Signal mode: send for Telegram approval
            msg_text = format_signal(signal_dict_full)
            msg_id = await send_signal_for_approval(msg_text, signal_id)
            if msg_id:
                await db_execute(
                    "UPDATE signals SET status = 'pending' WHERE id = ?",
                    (signal_id,)
                )
            logger.info(f"Signal sent for approval: {signal_id}")

        return signal

    except Exception as e:
        logger.exception(f"Analysis cycle error: {e}")
        # Don't crash main loop
        return None


async def _save_signal(signal: TradingSignal, mode: str, adjusted_confidence: int) -> str:
    """Save TradingSignal to signals table. Returns signal ID."""
    signal_id = str(uuid.uuid4())
    await db_execute(
        """INSERT INTO signals
           (id, mode, action, entry, sl, tp1, tp2, tp3, confidence, trend_bias, reasoning, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
        (
            signal_id, mode, signal.action,
            signal.entry_price, signal.stop_loss,
            signal.tp1, signal.tp2, signal.tp3,
            adjusted_confidence, signal.htf_bias,
            signal.reasoning,
        )
    )
    return signal_id


async def _get_balance() -> float:
    """Fetch account balance — paper uses default, live fetches from Binance."""
    if settings.paper_trade:
        return 10_000.0
    try:
        client = await get_client()
        account = await client.futures_account()
        return float(account.get("availableBalance", 10_000))
    except Exception as e:
        logger.warning(f"Balance fetch error: {e} — using 10000 default")
        return 10_000.0


# ---------------------------------------------------------------------------
# Daily reset
# ---------------------------------------------------------------------------

async def _daily_pnl_reset() -> None:
    """Reset daily PnL and circuit breaker at 00:01 UTC."""
    from bot.risk.circuit_breaker import reset_daily_pnl
    await reset_daily_pnl()
    logger.info("Daily PnL reset complete")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    """Main coroutine — initializes all components and runs the event loop."""
    global _shutdown_event, _scheduler, _health_monitor

    # Logger is already configured in bot/logger.py via module import
    from bot.logger import setup_logger
    setup_logger()

    _shutdown_event = asyncio.Event()

    # Register signal handlers (Unix only)
    if sys.platform != "win32":
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _handle_signal, sig)

    # ----------------------------------------------------------------
    # Startup
    # ----------------------------------------------------------------
    logger.info("==============================================")
    logger.info("        GOLD TRADING BOT — STARTING          ")
    logger.info("==============================================")

    try:
        # 1. Init DB
        await init_db()
        logger.info("Database initialized")

        # 2. Connect Binance
        client = await get_client()
        logger.info(f"Binance client connected (testnet={settings.binance_testnet})")

        # 3. Read current mode
        mode = await get_current_mode()
        auto = await get_auto_trade()
        logger.info(f"Mode: {mode} | Auto trade: {auto} | Paper: {settings.paper_trade}")

        # 4. Print startup banner
        from bot.modes.config import get_mode_config
        mode_cfg = get_mode_config(mode)
        logger.info(
            f"Mode config: interval={mode_cfg.get('interval_minutes')}min | "
            f"model={mode_cfg.get('claude_model')} | "
            f"leverage={mode_cfg.get('leverage')}x"
        )

        # 5. Build Telegram app
        tg_app = build_application()
        await start_bot(tg_app)
        logger.info("Telegram bot polling started")

        # 6. APScheduler
        _scheduler = AsyncIOScheduler(timezone="UTC")

        # Analysis job
        analysis_trigger = mode_cfg.get("analysis_trigger", "interval")
        interval_minutes = mode_cfg.get("interval_minutes", 240)

        if analysis_trigger == "interval" and interval_minutes:
            _scheduler.add_job(
                _analysis_job_fn,
                trigger="interval",
                minutes=interval_minutes,
                id="analysis",
                max_instances=1,
                coalesce=True,
            )
            logger.info(f"Analysis job scheduled every {interval_minutes} minutes")

        # Daily reset at 00:01 UTC
        _scheduler.add_job(
            _daily_pnl_reset,
            trigger="cron",
            hour=0, minute=1,
            id="daily_reset",
        )

        _scheduler.start()
        logger.info("APScheduler started")

        # 7. Health monitor
        _health_monitor = HealthMonitor(scheduler=_scheduler, silent=not settings.health_verbose)

        # Register heartbeat in scheduler
        _scheduler.add_job(
            _health_monitor.heartbeat,
            trigger="interval",
            minutes=settings.health_interval_min,
            id="heartbeat",
        )
        logger.info(f"Health monitor heartbeat every {settings.health_interval_min} minutes")

        # 8. Background tasks
        tasks = []

        # Position monitor (always running)
        monitor_task = asyncio.create_task(
            position_monitor_loop(_shutdown_event),
            name="position_monitor",
        )
        tasks.append(monitor_task)

        # WebSocket feed for scalp mode
        if mode == "scalp":
            _ws_task = asyncio.create_task(_ws_analysis_task(), name="websocket")
            tasks.append(_ws_task)
            logger.info("WebSocket feed started for scalp mode")

        # ----------------------------------------------------------------
        # Startup notification
        # ----------------------------------------------------------------
        paper_str = "PAPER 📄" if settings.paper_trade else "LIVE 🔴"
        auto_str = "ON" if auto else "OFF"
        await send_message(
            f"🟢 *Bot Started* — {fmt_ict(utc_now(), '%H:%M VN %d/%m/%Y')}\n"
            f"Mode: `{mode.upper()}` | Auto: `{auto_str}` | {paper_str}\n"
            f"Testnet: `{settings.binance_testnet}`"
        )

        logger.info("============================================")
        logger.info(f"  Bot running | mode={mode} | paper={settings.paper_trade}")
        logger.info("  Press Ctrl+C to stop")
        logger.info("============================================")

        # ----------------------------------------------------------------
        # Wait for shutdown
        # ----------------------------------------------------------------
        await _shutdown_event.wait()

    except Exception as e:
        logger.exception(f"Fatal error in main(): {e}")
    finally:
        # ----------------------------------------------------------------
        # Graceful shutdown
        # ----------------------------------------------------------------
        logger.info("Shutting down...")

        # Cancel background tasks
        for task in tasks if "tasks" in dir() else []:
            task.cancel()

        if "tasks" in dir() and tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # Stop scheduler
        if _scheduler and _scheduler.running:
            _scheduler.shutdown(wait=False)

        # Stop Telegram
        if "tg_app" in dir():
            try:
                await send_message(f"🔴 *Bot Stopped* — {fmt_ict(utc_now(), '%H:%M VN')}")
            except Exception:
                pass
            await stop_bot(tg_app)

        # Close Binance connection
        try:
            await close_client()
        except Exception:
            pass

        logger.info("Shutdown complete")
