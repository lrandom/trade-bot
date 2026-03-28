import pandas as pd
from bot.data.binance_client import get_client


async def fetch_ohlcv(symbol: str, interval: str, limit: int = 200) -> pd.DataFrame:
    """Fetch OHLCV candlestick data from Binance Futures.

    Args:
        symbol:   Trading pair, e.g. "XAUUSDT".
        interval: Kline interval, e.g. "1m", "5m", "1h", "4h", "1d".
        limit:    Number of candles to fetch (default 200 — enough for EMA200).

    Returns:
        pd.DataFrame with DatetimeIndex (UTC) named "open_time" and float columns:
        open, high, low, close, volume — sorted ascending.
    """
    client = await get_client()
    raw = await client.futures_klines(symbol=symbol, interval=interval, limit=limit)

    # Binance kline response columns (index → name):
    # 0  open_time, 1 open, 2 high, 3 low, 4 close, 5 volume,
    # 6  close_time, 7 quote_asset_volume, 8 num_trades,
    # 9  taker_buy_base, 10 taker_buy_quote, 11 ignore
    df = pd.DataFrame(
        raw,
        columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_asset_volume", "num_trades",
            "taker_buy_base", "taker_buy_quote", "ignore",
        ],
    )

    # Keep only OHLCV columns
    df = df[["open_time", "open", "high", "low", "close", "volume"]].copy()

    # Cast numeric columns to float
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    # Convert open_time (ms epoch) to UTC DatetimeIndex
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df.set_index("open_time", inplace=True)

    # Ensure ascending order
    df.sort_index(inplace=True)

    return df
