"""main.py — Gold Trading Bot entry point.

Usage:
    python main.py              # Full bot (paper trade by default)
    python main.py --dry-run    # Dry run: print signals, no DB/Telegram
"""

import asyncio
import sys


def main():
    if "--dry-run" in sys.argv:
        _dry_run()
    else:
        from bot.orchestrator import main as orchestrator_main
        asyncio.run(orchestrator_main())


def _dry_run():
    """Layer 1 testing: run one analysis cycle, print result, exit."""
    import asyncio as _asyncio

    async def _run():
        from bot.logger import setup_logger
        setup_logger()

        from bot.database import init_db
        await init_db()

        from bot.data.snapshot import build_snapshot
        from bot.llm.engine import LLMEngine
        from bot.modes.manager import get_current_mode

        mode = await get_current_mode()
        print(f"\n=== DRY RUN | mode={mode} ===\n")

        snapshot = await build_snapshot(mode)
        engine = LLMEngine(mode)
        signal = await engine.generate_signal(snapshot)

        print(f"Action:     {signal.action}")
        print(f"Entry:      {signal.entry_price}")
        print(f"SL:         {signal.stop_loss}")
        print(f"TP1/2/3:    {signal.tp1} / {signal.tp2} / {signal.tp3}")
        print(f"Confidence: {signal.confidence}%")
        print(f"HTF Bias:   {signal.htf_bias}")
        print(f"Reasoning:  {signal.reasoning[:200]}")
        print("\n=== DRY RUN COMPLETE ===\n")

    _asyncio.run(_run())


if __name__ == "__main__":
    main()
