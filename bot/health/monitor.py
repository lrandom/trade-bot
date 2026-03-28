# bot/health/monitor.py
"""Health monitor: periodic heartbeat + component checks."""

import asyncio
import json
import time

from loguru import logger

from bot.database import db_execute, db_scalar, db_fetchone
from bot.health.models import ComponentStatus, HealthStatus
from bot.utils.timezone import utc_now, fmt_ict

_start_time = time.time()


class HealthMonitor:

    def __init__(self, scheduler=None, silent: bool = True):
        self.scheduler = scheduler
        self.silent = silent  # if True, only alert on issues

    async def heartbeat(self) -> None:
        """Run every 5 minutes via APScheduler."""
        status = await self._check_all()

        # Log to DB
        await db_execute(
            "INSERT OR REPLACE INTO health_log (timestamp, all_ok, details, uptime_seconds) "
            "VALUES (CURRENT_TIMESTAMP, ?, ?, ?)",
            status.ok,
            json.dumps(status.details),
            int(status.uptime_seconds),
        )

        if not status.ok:
            # Alert on any component failure
            from bot.telegram.notifier import send_message
            failed = [c.name for c in status.components if not c.ok]
            await send_message(
                f"⚠️ WARNING — {fmt_ict(utc_now(), '%H:%M VN')}\n"
                f"Components down: {', '.join(failed)}\n"
                "Action: Auto trade PAUSED"
            )
            # Pause auto trade
            await db_execute(
                "INSERT OR REPLACE INTO config (key, value, updated_at) "
                "VALUES ('auto_trade', 'false', CURRENT_TIMESTAMP)"
            )

        elif not self.silent:
            # Verbose mode — ping even when OK
            from bot.telegram.notifier import send_message
            from bot.config import settings
            from bot.modes.manager import get_current_mode
            mode = await get_current_mode()
            uptime_h = int(status.uptime_seconds // 3600)
            uptime_m = int((status.uptime_seconds % 3600) // 60)
            await send_message(
                f"✅ Bot Alive — {fmt_ict(utc_now(), '%H:%M VN')}\n"
                f"Uptime: {uptime_h}h {uptime_m}m\n"
                f"{status.summary_text()}\n"
                f"Mode: {mode} | Paper: {'ON' if settings.paper_trade else 'OFF'}"
            )

    async def get_health_report(self) -> str:
        """Return formatted health report for /health command."""
        try:
            import psutil
            proc = psutil.Process()
            mem_mb = proc.memory_info().rss / 1024 / 1024
            cpu = proc.cpu_percent(interval=0.1)
        except Exception:
            mem_mb = 0.0
            cpu = 0.0

        status = await self._check_all()
        uptime = int(time.time() - _start_time)
        uptime_h = uptime // 3600
        uptime_m = (uptime % 3600) // 60
        uptime_s = uptime % 60

        from bot.config import settings
        from bot.modes.manager import get_current_mode, get_auto_trade
        mode = await get_current_mode()
        auto = await get_auto_trade()

        # Last signal
        last_sig = await db_fetchone(
            "SELECT action, confidence, created_at FROM signals ORDER BY created_at DESC LIMIT 1"
        )
        last_sig_text = "None"
        if last_sig:
            last_sig_text = f"{last_sig['action']} (conf: {last_sig['confidence']}%)"

        return (
            f"🏥 *Health Report* — {fmt_ict(utc_now(), '%H:%M VN')}\n\n"
            f"*System:*\n"
            f"  Uptime: `{uptime_h}h {uptime_m}m {uptime_s}s`\n"
            f"  Memory: `{mem_mb:.0f} MB`\n"
            f"  CPU:    `{cpu:.1f}%`\n\n"
            f"*Components:*\n{status.summary_text()}\n\n"
            f"*Trading:*\n"
            f"  Mode:       `{mode}`\n"
            f"  Paper:      `{'ON' if settings.paper_trade else 'OFF'}`\n"
            f"  Auto trade: `{'ON' if auto else 'OFF'}`\n"
            f"  Last signal: `{last_sig_text}`\n\n"
            f"Overall: {'✅ All OK' if status.ok else '❌ Issues detected'}"
        )

    async def _check_all(self) -> HealthStatus:
        results = await asyncio.gather(
            self._check_binance(),
            self._check_db(),
            self._check_scheduler(),
            return_exceptions=True,
        )
        components = []
        for r in results:
            if isinstance(r, Exception):
                components.append(ComponentStatus("unknown", ok=False, error=str(r)))
            else:
                components.append(r)
        return HealthStatus(
            components=components,
            uptime_seconds=time.time() - _start_time,
        )

    async def _check_binance(self) -> ComponentStatus:
        start = time.time()
        try:
            from bot.data.binance_client import get_client
            client = await get_client()
            from bot.config import settings
            await client.get_symbol_ticker(symbol=settings.trading_symbol)
            latency = (time.time() - start) * 1000
            return ComponentStatus("binance", ok=True, latency_ms=latency)
        except Exception as e:
            return ComponentStatus("binance", ok=False, error=str(e)[:50])

    async def _check_db(self) -> ComponentStatus:
        start = time.time()
        try:
            await db_scalar("SELECT 1")
            latency = (time.time() - start) * 1000
            return ComponentStatus("db", ok=True, latency_ms=latency)
        except Exception as e:
            return ComponentStatus("db", ok=False, error=str(e)[:50])

    async def _check_scheduler(self) -> ComponentStatus:
        if self.scheduler is None:
            return ComponentStatus("scheduler", ok=True)
        try:
            jobs = self.scheduler.get_jobs()
            ok = len(jobs) > 0 and all(j.next_run_time is not None for j in jobs)
            return ComponentStatus("scheduler", ok=ok)
        except Exception as e:
            return ComponentStatus("scheduler", ok=False, error=str(e)[:50])
