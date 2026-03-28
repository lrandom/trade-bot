# bot/telegram/bot.py
"""Build and return the Telegram Application instance."""

from telegram.ext import Application, CommandHandler, CallbackQueryHandler
from bot.config import settings
from bot.telegram import handlers
from bot.telegram.notifier import init_notifier
from loguru import logger


def build_application() -> Application:
    """Build the Application, register handlers. Call once at startup."""
    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .build()
    )

    # Register command handlers
    app.add_handler(CommandHandler("start",   handlers.cmd_start))
    app.add_handler(CommandHandler("signal",  handlers.cmd_signal))
    app.add_handler(CommandHandler("status",  handlers.cmd_status))
    app.add_handler(CommandHandler("balance", handlers.cmd_balance))
    app.add_handler(CommandHandler("mode",    handlers.cmd_mode))
    app.add_handler(CommandHandler("auto",    handlers.cmd_auto))
    app.add_handler(CommandHandler("close",   handlers.cmd_close))
    app.add_handler(CommandHandler("stop",    handlers.cmd_stop))
    app.add_handler(CommandHandler("history", handlers.cmd_history))
    app.add_handler(CommandHandler("health",  handlers.cmd_health))
    app.add_handler(CommandHandler("filter",  handlers.cmd_filter))
    app.add_handler(CommandHandler("cost",    handlers.cmd_cost))

    # Inline keyboard callback (Approve / Reject buttons on signal messages)
    app.add_handler(CallbackQueryHandler(handlers.callback_approve_reject))

    logger.info("Telegram application built with all handlers registered")
    return app


async def start_bot(app: Application) -> None:
    """Initialize bot, set up notifier, start polling."""
    await app.initialize()
    await app.start()
    init_notifier(app.bot, settings.telegram_chat_id)
    await app.updater.start_polling(drop_pending_updates=True)
    logger.info("Telegram bot polling started")


async def stop_bot(app: Application) -> None:
    """Graceful shutdown."""
    await app.updater.stop()
    await app.stop()
    await app.shutdown()
    logger.info("Telegram bot stopped")
