"""bot/llm/prompts.py
---------------------
Prompt builder functions for all 6 LLM analysis steps.

Each function returns a (system, user) tuple of strings ready to pass
to ``BaseLLMProvider.complete()`` or ``BaseLLMProvider.complete_structured()``.

Steps:
    1. build_macro_prompt   — macro-economic bias
    2. build_htf_prompt     — higher timeframe structure (W1 / D1 / H4)
    3. build_mtf_prompt     — medium timeframe confirmation (H4 / H1)
    4. build_ltf_prompt     — lower timeframe entry trigger (M15 / H1)
    5. build_signal_prompt  — final signal generation (tool_use)
    6. build_management_prompt — open position management (tool_use)
"""

from bot.data.snapshot import MarketSnapshot
from bot.llm.models import HTFAnalysis, LTFAnalysis, MTFAnalysis, MacroAnalysis


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _asset_label(symbol: str) -> str:
    """Return a human-friendly asset name from a Binance Futures symbol."""
    base = symbol.upper().replace("USDT", "").replace("BUSD", "").replace("PERP", "")
    return {
        "XAU": "Gold", "BTC": "Bitcoin", "ETH": "Ethereum",
        "BNB": "BNB", "SOL": "Solana", "XRP": "XRP",
    }.get(base, base)


def _safe_float(value, precision: int = 2) -> str:
    """Format a numeric value safely, returning 'N/A' for None."""
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.{precision}f}"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_tf_section(snapshot: MarketSnapshot, tf: str) -> str:
    """Build a compact indicator + last-5-candles summary for one timeframe.

    Args:
        snapshot: The full market snapshot.
        tf:       Timeframe key, e.g. ``"1h"`` or ``"4h"``.

    Returns:
        Multi-line string section ready to embed in a prompt.
    """
    ind = snapshot.indicators.get(tf, {})
    df = snapshot.timeframes.get(tf)

    last5 = ""
    if df is not None and len(df) >= 5:
        rows = df[["open", "high", "low", "close"]].tail(5)
        last5 = "\n".join(
            f"  {i}: O={r['open']:.2f} H={r['high']:.2f} "
            f"L={r['low']:.2f} C={r['close']:.2f}"
            for i, r in rows.iterrows()
        )
    elif df is not None and len(df) > 0:
        rows = df[["open", "high", "low", "close"]].tail(len(df))
        last5 = "\n".join(
            f"  {i}: O={r['open']:.2f} H={r['high']:.2f} "
            f"L={r['low']:.2f} C={r['close']:.2f}"
            for i, r in rows.iterrows()
        )

    supertrend_label = "UP" if ind.get("supertrend_dir", 0) == 1 else "DOWN"

    return (
        f"### {tf.upper()}\n"
        f"EMA20/50/200: {_safe_float(ind.get('ema_20'))}/"
        f"{_safe_float(ind.get('ema_50'))}/{_safe_float(ind.get('ema_200'))}\n"
        f"RSI(14): {_safe_float(ind.get('rsi_14'), 1)} | "
        f"MACD hist: {_safe_float(ind.get('macd_hist'), 4)}\n"
        f"SuperTrend: {supertrend_label} | ATR(14): {_safe_float(ind.get('atr_14'))}\n"
        f"VWAP: {_safe_float(ind.get('vwap'))}\n"
        f"Last 5 candles:\n{last5}"
    )


# ---------------------------------------------------------------------------
# Prompt 1 — Macro Analysis
# ---------------------------------------------------------------------------

def build_macro_prompt(snapshot: MarketSnapshot) -> tuple[str, str]:
    """Macro-economic directional bias prompt.

    Args:
        snapshot: Full market snapshot (uses fed_funds_rate, yield_spread,
                  news_headlines, mark_price, timestamp).

    Returns:
        (system, user) prompt strings.
    """
    asset = _asset_label(snapshot.symbol)
    pair = snapshot.symbol.replace("USDT", "")
    system = (
        f"You are a {asset} market macro analyst. Analyze macroeconomic data and "
        f"give directional bias for {pair}. Be concise and decisive.\n\n"
        "Respond with EXACTLY this format:\n"
        "BIAS: [BULLISH|BEARISH|NEUTRAL]\n"
        "CONFIDENCE: [0-100]\n"
        "RISKS: [bullet 1] | [bullet 2] | [bullet 3]"
    )

    headlines = (
        "\n".join(f"- {h}" for h in snapshot.news_headlines[:5])
        or "- No headlines available"
    )
    fed_rate = (
        f"{snapshot.fed_funds_rate}%" if snapshot.fed_funds_rate is not None else "N/A"
    )
    yield_spread = (
        f"{snapshot.yield_spread}%" if snapshot.yield_spread is not None else "N/A"
    )

    user = (
        f"## Macro Data ({snapshot.timestamp.strftime('%Y-%m-%d')})\n"
        f"Fed Funds Rate: {fed_rate}\n"
        f"10Y-2Y Treasury Spread: {yield_spread}\n"
        f"Current {pair}: {snapshot.mark_price:.2f}\n\n"
        f"## Recent Headlines:\n"
        f"{headlines}\n\n"
        f"Analyze and provide BIAS, CONFIDENCE, RISKS."
    )
    return system, user


# ---------------------------------------------------------------------------
# Prompt 2 — HTF Analysis (W1 / D1 / H4)
# ---------------------------------------------------------------------------

def build_htf_prompt(snapshot: MarketSnapshot) -> tuple[str, str]:
    """Higher timeframe structural analysis prompt.

    Args:
        snapshot: Full market snapshot (uses W1, D1, H4 OHLCV + indicators,
                  support_levels, resistance_levels, mark_price).

    Returns:
        (system, user) prompt strings.
    """
    asset = _asset_label(snapshot.symbol)
    pair = snapshot.symbol.replace("USDT", "")
    system = (
        f"You are a {asset} futures analyst specializing in higher timeframe structure. "
        f"Identify the DOMINANT WAVE and directional bias for {pair}. "
        "This bias GATES all lower timeframe entries — be decisive.\n\n"
        "Respond with EXACTLY this format:\n"
        "HTF_BIAS: [BUY-ONLY|SELL-ONLY|NEUTRAL]\n"
        "WAVE_POSITION: [description]\n"
        "KEY_SUPPORT: [price1],[price2]\n"
        "KEY_RESISTANCE: [price1],[price2]\n"
        "INVALIDATION: [price]\n"
        "CONFIDENCE: [0-100]"
    )

    w1 = _fmt_tf_section(snapshot, "1w")
    d1 = _fmt_tf_section(snapshot, "1d")
    h4 = _fmt_tf_section(snapshot, "4h")

    sup_str = str(snapshot.support_levels[:2]) if snapshot.support_levels else "[]"
    res_str = str(snapshot.resistance_levels[:2]) if snapshot.resistance_levels else "[]"

    user = (
        f"## {pair} Higher Timeframe Analysis\n"
        f"Current price: {snapshot.mark_price:.2f}\n\n"
        f"{w1}\n\n"
        f"{d1}\n"
        f"Key S/R detected: Support {sup_str} | Resistance {res_str}\n\n"
        f"{h4}\n\n"
        f"Identify HTF_BIAS, WAVE_POSITION, KEY_SUPPORT, KEY_RESISTANCE, "
        f"INVALIDATION, CONFIDENCE."
    )
    return system, user


# ---------------------------------------------------------------------------
# Prompt 3 — MTF Analysis (H4 / H1)
# ---------------------------------------------------------------------------

def build_mtf_prompt(
    snapshot: MarketSnapshot, htf: HTFAnalysis
) -> tuple[str, str]:
    """Medium timeframe confirmation prompt.

    Args:
        snapshot: Full market snapshot (uses H4, H1 OHLCV + indicators).
        htf:      Result of the HTF analysis step.

    Returns:
        (system, user) prompt strings.
    """
    system = (
        "You are a gold futures analyst focused on medium-timeframe wave structure. "
        "Given HTF bias, identify whether price is in a PULLBACK or IMPULSE "
        "and locate the optimal entry zone.\n\n"
        "Respond with EXACTLY this format:\n"
        "CONFIRMS_HTF: [YES|NO]\n"
        "STRUCTURE: [PULLBACK|IMPULSE|CONSOLIDATION]\n"
        "ENTRY_ZONE: [low_price]-[high_price]\n"
        "REASONING: [1-2 sentences]"
    )

    h4 = _fmt_tf_section(snapshot, "4h")
    h1 = _fmt_tf_section(snapshot, "1h")

    user = (
        f"## HTF Context\n"
        f"HTF Bias: {htf.htf_bias} | Confidence: {htf.confidence}%\n"
        f"Wave: {htf.wave_position}\n"
        f"Key Support: {htf.key_support} | Resistance: {htf.key_resistance}\n"
        f"Invalidation: {htf.invalidation}\n\n"
        f"{h4}\n\n"
        f"{h1}\n\n"
        f"Does MTF confirm HTF bias? Identify STRUCTURE and ENTRY_ZONE."
    )
    return system, user


# ---------------------------------------------------------------------------
# Prompt 4 — LTF Analysis (M15 / H1)
# ---------------------------------------------------------------------------

def build_ltf_prompt(
    snapshot: MarketSnapshot, htf: HTFAnalysis, mtf: MTFAnalysis
) -> tuple[str, str]:
    """Lower timeframe entry trigger prompt.

    Args:
        snapshot: Full market snapshot (uses M15, H1 OHLCV + indicators).
        htf:      Result of the HTF analysis step.
        mtf:      Result of the MTF analysis step.

    Returns:
        (system, user) prompt strings.
    """
    system = (
        "You are a precision entry specialist for gold futures. "
        "Given confirmed HTF and MTF alignment, identify whether there is a "
        "valid ENTRY TRIGGER on the lower timeframe. Be strict — only confirm "
        "if the trigger is clear.\n\n"
        "Respond with EXACTLY this format:\n"
        "ENTRY_TRIGGER: [YES|NO]\n"
        "CANDLE_PATTERN: [pattern or 'none']\n"
        "ENTRY_PRICE: [price]\n"
        "REASONING: [1-2 sentences]"
    )

    m15 = _fmt_tf_section(snapshot, "15m")
    h1 = _fmt_tf_section(snapshot, "1h")

    user = (
        f"## Context\n"
        f"HTF Bias: {htf.htf_bias} | MTF Structure: {mtf.structure}\n"
        f"Entry Zone: {mtf.entry_zone_low:.2f}-{mtf.entry_zone_high:.2f}\n"
        f"Current price: {snapshot.mark_price:.2f}\n\n"
        f"{m15}\n\n"
        f"{h1}\n\n"
        f"Is there a valid entry trigger? "
        f"Identify ENTRY_TRIGGER, CANDLE_PATTERN, ENTRY_PRICE."
    )
    return system, user


# ---------------------------------------------------------------------------
# Prompt 5 — Signal Generation (tool_use)
# ---------------------------------------------------------------------------

def build_signal_prompt(
    snapshot: MarketSnapshot,
    macro: MacroAnalysis,
    htf: HTFAnalysis,
    mtf: MTFAnalysis,
    ltf: LTFAnalysis,
) -> tuple[str, str]:
    """Final signal generation prompt — all 3 TF aligned.

    Uses ``generate_trading_signal`` tool for guaranteed JSON output.

    Args:
        snapshot: Full market snapshot.
        macro:    Macro analysis result.
        htf:      HTF analysis result.
        mtf:      MTF analysis result.
        ltf:      LTF analysis result.

    Returns:
        (system, user) prompt strings.
    """
    from bot.modes.config import MODES

    mode_cfg = MODES.get(snapshot.mode, {})
    atr = snapshot.indicators.get(snapshot.primary_tf, {}).get("atr_14") or 0.0
    sl_mult = mode_cfg.get("atr_sl_mult", 1.5)
    sl_dist = atr * sl_mult

    asset = _asset_label(snapshot.symbol)
    system = (
        f"You are a {asset} futures signal generator. All three timeframes "
        "(HTF/MTF/LTF) confirmed alignment. Generate precise entry parameters. "
        "Direction is LOCKED by HTF bias — do not contradict it. "
        "Use generate_trading_signal tool."
    )

    user = (
        f"## Confirmed Analysis\n"
        f"Mode: {snapshot.mode} | Price: {snapshot.mark_price:.2f}\n"
        f"ATR({snapshot.primary_tf}): {_safe_float(atr)} | "
        f"SL dist ({sl_mult}x ATR): {sl_dist:.2f}\n\n"
        f"Macro: {macro.bias} ({macro.confidence}%) — {macro.risks[:100]}\n"
        f"HTF ({htf.htf_bias}): {htf.wave_position}\n"
        f"MTF ({mtf.structure}): entry zone "
        f"{mtf.entry_zone_low:.2f}-{mtf.entry_zone_high:.2f}\n"
        f"LTF trigger: {ltf.candle_pattern} @ {ltf.entry_price:.2f}\n"
        f"Support: {htf.key_support} | Resistance: {htf.key_resistance}\n\n"
        f"Generate signal. Action MUST match HTF bias ({htf.htf_bias}).\n"
        f"SL: near key S/R or {sl_dist:.2f} from entry.\n"
        f"TP1/TP2/TP3: use resistance/support levels."
    )
    return system, user


# ---------------------------------------------------------------------------
# Prompt 6 — Trade Management (tool_use)
# ---------------------------------------------------------------------------

def build_management_prompt(
    position: dict, snapshot: MarketSnapshot
) -> tuple[str, str]:
    """Open position management prompt.

    Uses ``manage_trade`` tool for guaranteed JSON output.

    Args:
        position: Dict with keys: side, entry, pnl_pct, stop_loss, tp1, tp2,
                  tp3, duration.
        snapshot: Current market snapshot.

    Returns:
        (system, user) prompt strings.
    """
    asset = _asset_label(snapshot.symbol)
    system = (
        f"You are a {asset} futures trade manager. Evaluate whether to hold, exit, "
        "or adjust SL. Capital preservation is primary. Use manage_trade tool."
    )

    ind = snapshot.indicators.get(snapshot.primary_tf, {})
    supertrend_label = "UP" if ind.get("supertrend_dir", 0) == 1 else "DOWN"

    user = (
        f"## Open Position\n"
        f"Side: {position.get('side')} | "
        f"Entry: {position.get('entry')} | "
        f"PnL: {position.get('pnl_pct', 0):.2f}%\n"
        f"SL: {position.get('stop_loss')} | "
        f"TP1: {position.get('tp1')} | "
        f"TP2: {position.get('tp2')} | "
        f"TP3: {position.get('tp3')}\n"
        f"Duration: {position.get('duration', 'unknown')}\n\n"
        f"## Current Market\n"
        f"Mark Price: {snapshot.mark_price:.2f}\n"
        f"RSI: {_safe_float(ind.get('rsi_14'), 1)} | "
        f"SuperTrend: {supertrend_label}\n"
        f"ATR: {_safe_float(ind.get('atr_14'))}\n\n"
        f"Decide: HOLD, EXIT, or ADJUST_SL."
    )
    return system, user
