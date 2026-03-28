# bot/telegram/notifier.py
"""Notification sender — wraps Telegram Application.bot.send_message()."""

from telegram import Bot
from telegram.constants import ParseMode
from loguru import logger

_bot: Bot | None = None
_chat_id: str | None = None


def init_notifier(bot: Bot, chat_id: str) -> None:
    """Called once at startup."""
    global _bot, _chat_id
    _bot = bot
    _chat_id = str(chat_id)


async def send_message(text: str, parse_mode=ParseMode.MARKDOWN) -> None:
    """Send plain text message."""
    if not _bot:
        logger.warning("Notifier not initialized — skipping message")
        return
    try:
        await _bot.send_message(chat_id=_chat_id, text=text, parse_mode=parse_mode)
    except Exception as e:
        logger.error(f"Telegram send_message error: {e}")


async def send_signal_for_approval(text: str, signal_id: str):
    """Send signal with Approve/Reject inline keyboard. Returns message_id."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    if not _bot:
        return None
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve_{signal_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject_{signal_id}"),
        ]
    ])
    try:
        msg = await _bot.send_message(
            chat_id=_chat_id, text=text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )
        return msg.message_id
    except Exception as e:
        logger.error(f"Telegram send_signal error: {e}")
        return None
