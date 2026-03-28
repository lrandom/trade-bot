import time
from datetime import datetime, timedelta
from bot.config import settings

# ── In-memory caches ──────────────────────────────────────────────────────────
_macro_cache: dict = {"data": None, "ts": 0.0}
_news_cache: dict = {"data": [], "ts": 0.0}

MACRO_TTL = 3600 * 24   # 24 hours — FRED data changes slowly
NEWS_TTL = 3600 * 4     # 4 hours — respect NewsAPI 100 req/day free tier


# ── FRED (Federal Reserve Economic Data) ─────────────────────────────────────

async def fetch_fred_data() -> dict:
    """Fetch macroeconomic indicators from FRED via pandas-datareader.

    Series fetched:
        - ``DFF``    — Effective Federal Funds Rate (daily)
        - ``T10Y2Y`` — 10-Year minus 2-Year Treasury Yield Spread

    Results are cached for 24 hours.  On failure, returns ``None`` values so
    callers can degrade gracefully without raising an exception.

    Returns:
        dict with keys ``fed_rate`` and ``yield_spread`` (floats or None).
    """
    if time.time() - _macro_cache["ts"] < MACRO_TTL and _macro_cache["data"]:
        return _macro_cache["data"]

    try:
        import pandas_datareader.data as web

        end = datetime.today()
        start = end - timedelta(days=30)
        df = web.DataReader(["DFF", "T10Y2Y"], "fred", start, end)

        result = {
            "fed_rate": float(df["DFF"].dropna().iloc[-1]),
            "yield_spread": float(df["T10Y2Y"].dropna().iloc[-1]),
        }
        _macro_cache["data"] = result
        _macro_cache["ts"] = time.time()
        return result

    except Exception:
        # Return cached stale data if available, else None values
        if _macro_cache["data"]:
            return _macro_cache["data"]
        return {"fed_rate": None, "yield_spread": None}


# ── NewsAPI ───────────────────────────────────────────────────────────────────

async def fetch_news(
    query: str = "gold XAUUSD price",
    page_size: int = 5,
) -> list[str]:
    """Fetch top gold-related news headlines from NewsAPI.

    Results are cached for 4 hours to stay within the 100 req/day free tier.
    Falls back to an empty list if the API key is not configured or on error.

    Args:
        query:     NewsAPI search query string.
        page_size: Maximum number of articles to return (default 5).

    Returns:
        List of headline strings (may be empty on error or missing API key).
    """
    if time.time() - _news_cache["ts"] < NEWS_TTL and _news_cache["data"]:
        return _news_cache["data"]

    if not settings.news_api_key:
        return []

    try:
        from newsapi import NewsApiClient

        client = NewsApiClient(api_key=settings.news_api_key)
        resp = client.get_everything(
            q=query,
            language="en",
            sort_by="publishedAt",
            page_size=page_size,
        )
        headlines = [a["title"] for a in resp.get("articles", [])]
        _news_cache["data"] = headlines
        _news_cache["ts"] = time.time()
        return headlines

    except Exception:
        # Return stale cache if available
        if _news_cache["data"]:
            return _news_cache["data"]
        return []


# ── Unified context builder ───────────────────────────────────────────────────

async def get_macro_context() -> dict:
    """Fetch all macro data and return as a unified context dict.

    Used by the LLM engine to inject macro context into prompts.

    Returns:
        dict with keys: ``fred_rates`` (dict), ``news_headlines`` (list[str]),
        ``timestamp_ict`` (str).
    """
    from bot.utils.timezone import utc_now, fmt_ict

    fred = await fetch_fred_data()
    news = await fetch_news()

    return {
        "fred_rates": fred,
        "news_headlines": news,
        "timestamp_ict": fmt_ict(utc_now()),
    }
