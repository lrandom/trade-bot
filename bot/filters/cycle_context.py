# bot/filters/cycle_context.py
"""Seasonal + weekday cycle context for Gold."""

from bot.utils.timezone import utc_now, to_ict, session_label

SEASONAL = {
    1:  ("STRONG",  "Chinese New Year + India wedding demand"),
    2:  ("STRONG",  "Chinese New Year peak"),
    3:  ("NEUTRAL", "Post-CNY normalization"),
    4:  ("NEUTRAL", "Quiet period"),
    5:  ("NEUTRAL", "Pre-summer"),
    6:  ("WEAK",    "Summer doldrums — low volume"),
    7:  ("WEAK",    "Summer doldrums"),
    8:  ("WEAK",    "Summer doldrums — watch for reversal"),
    9:  ("STRONG",  "India festive season begins (Navratri, Dussehra)"),
    10: ("STRONG",  "Diwali demand + year-end positioning"),
    11: ("STRONG",  "Year-end buying, geopolitical hedge"),
    12: ("STRONG",  "Holiday buying, portfolio rebalancing"),
}

DOW_NOTES = {
    0: "Monday — continuation from last week's trend",
    1: "Tuesday — usually trend day",
    2: "Wednesday — FOMC/CPI often released, high volatility risk",
    3: "Thursday — post-news follow-through or reversal",
    4: "Friday — position squaring, beware false breakouts",
    5: "Saturday — market closed",
    6: "Sunday — gap risk on open",
}


def get_cycle_context() -> dict:
    """Return cycle context dict for injection into LLM prompts."""
    now_utc = utc_now()
    now_ict = to_ict(now_utc)

    month = now_ict.month
    dow = now_ict.weekday()
    seasonal_bias, seasonal_note = SEASONAL[month]

    return {
        "month":           now_ict.strftime("%B %Y"),
        "seasonal_bias":   seasonal_bias,
        "seasonal_note":   seasonal_note,
        "day_of_week":     DOW_NOTES[dow],
        "session":         session_label(now_utc.hour),
        "is_high_vol_day": dow == 2,  # Wednesday
    }


def format_cycle_for_prompt(ctx: dict) -> str:
    """Format cycle context as text for LLM prompt injection."""
    return (
        f"## Cycle Context\n"
        f"Month: {ctx['month']} → Seasonal: {ctx['seasonal_bias']} ({ctx['seasonal_note']})\n"
        f"Today: {ctx['day_of_week']}\n"
        f"Session: {ctx['session']}"
    )
