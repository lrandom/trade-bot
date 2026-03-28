"""bot/telegram — Telegram bot package.

Public API
----------
    from bot.telegram import build_application, start_bot, stop_bot
    from bot.telegram import send_message, send_signal_for_approval
"""

from bot.telegram.bot import build_application, start_bot, stop_bot
from bot.telegram.notifier import send_message, send_signal_for_approval

__all__ = [
    "build_application",
    "start_bot",
    "stop_bot",
    "send_message",
    "send_signal_for_approval",
]
