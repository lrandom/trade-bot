# bot/telegram/formatters.py
"""Signal/trade/status message formatters. Pure functions, no I/O."""

from bot.utils.timezone import fmt_ict, utc_now


def format_signal(signal: dict) -> str:
    """Format a TradingSignal dict into Telegram message text."""
    # signal dict keys: action, entry_price, stop_loss, tp1, tp2, tp3, confidence, htf_bias, reasoning, mode
    action = signal.get("action", "HOLD")
    entry = signal.get("entry_price", 0)
    sl = signal.get("stop_loss", 0)
    tp1 = signal.get("tp1", 0)
    tp2 = signal.get("tp2", 0)
    tp3 = signal.get("tp3", 0)
    confidence = signal.get("confidence", 0)
    bias = signal.get("htf_bias", "N/A")
    reasoning = signal.get("reasoning", "")
    mode = signal.get("mode", "swing").upper()

    # Truncate reasoning to 300 chars to stay within Telegram 4096-char limit
    if reasoning and len(reasoning) > 300:
        reasoning = reasoning[:297] + "..."

    # Calculate percentages from entry
    def pct(target):
        if not entry or not target:
            return "N/A"
        diff = (target - entry) / entry * 100
        return f"{diff:+.2f}%"

    return (
        f"📊 *XAUUSD Signal — {mode}*\n\n"
        f"Action:     `{action}`\n"
        f"Entry:      `${entry:,.2f}`\n"
        f"SL:         `${sl:,.2f}` ({pct(sl)})\n"
        f"TP1:        `${tp1:,.2f}` ({pct(tp1)})\n"
        f"TP2:        `${tp2:,.2f}` ({pct(tp2)})\n"
        f"TP3:        `${tp3:,.2f}` ({pct(tp3)})\n"
        f"Confidence: `{confidence}%`\n"
        f"Bias:       `{bias}`\n\n"
        f"*Reasoning:*\n{reasoning}"
    )


def format_status(positions: list, mode: str, auto_trade: bool, paper: bool) -> str:
    """Format current bot status message."""
    if not positions:
        pos_text = "No open positions"
    else:
        lines = []
        for p in positions:
            side = p.get("side", "?")
            entry = p.get("entry", 0)
            pnl = p.get("pnl", 0)
            pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
            lines.append(f"  {side} @ ${entry:,.2f} | PnL: {pnl_str}")
        pos_text = "\n".join(lines)

    auto_str = "ON ✅" if auto_trade else "OFF ⛔"
    paper_str = "PAPER 📄" if paper else "LIVE 🔴"

    return (
        f"🤖 *Bot Status* — {fmt_ict(utc_now(), '%H:%M VN')}\n\n"
        f"Mode:       `{mode.upper()}`\n"
        f"Auto Trade: `{auto_str}`\n"
        f"Trading:    `{paper_str}`\n\n"
        f"*Positions:*\n{pos_text}"
    )


def format_history(trades: list) -> str:
    """Format last 10 trades for /history command."""
    if not trades:
        return "No closed trades yet."
    lines = ["📈 *Trade History (last 10)*\n"]
    for t in trades:
        side = t.get("side", "?")
        entry = t.get("entry", 0)
        close = t.get("close_price", 0)
        pnl = t.get("pnl", 0)
        emoji = "✅" if pnl > 0 else "❌"
        pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
        lines.append(f"{emoji} {side} ${entry:,.0f}→${close:,.0f} | {pnl_str}")
    return "\n".join(lines)
