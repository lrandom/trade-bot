from collections import deque
from binance import BinanceSocketManager
from typing import Callable
import asyncio
import pandas as pd


class CandleBuffer:
    """Live 1-minute candle buffer backed by a Binance Futures kline WebSocket.

    Usage::

        buffer = CandleBuffer(symbol="XAUUSDT", maxlen=200)
        buffer.set_callback(on_new_candle)   # async callback(df)
        await buffer.start(client)           # starts background stream task

    The callback receives a fresh ``pd.DataFrame`` every time a 1m candle
    closes (``x == True``).  The DataFrame contains up to ``maxlen`` rows with
    columns: open, high, low, close, volume (index = open_time UTC).
    """

    def __init__(self, symbol: str = "XAUUSDT", maxlen: int = 200):
        self.symbol = symbol
        self.buffer: deque = deque(maxlen=maxlen)
        self._callback = None
        self._task: asyncio.Task | None = None
        self.on_close: Callable | None = None  # no-arg callback fired on each closed candle

    # ── Public API ─────────────────────────────────────────────────────────────

    def set_callback(self, callback):
        """Register an async callable(df: pd.DataFrame) invoked on each closed candle."""
        self._callback = callback

    async def start(self, client):
        """Start the background WebSocket stream task."""
        self._task = asyncio.create_task(self._stream(client))

    async def stop(self):
        """Cancel the background stream task."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def get_df(self) -> pd.DataFrame | None:
        """Return current buffer as a DataFrame, or None if fewer than 2 candles."""
        if len(self.buffer) < 2:
            return None
        return pd.DataFrame(list(self.buffer)).set_index("open_time")

    # ── Internal ───────────────────────────────────────────────────────────────

    async def _stream(self, client):
        """Main stream loop with exponential-backoff reconnect (max 3 retries)."""
        bm = BinanceSocketManager(client)
        retries = 0
        max_retries = 3

        while retries < max_retries:
            try:
                async with bm.futures_kline_socket(self.symbol, "1m") as stream:
                    retries = 0  # reset on successful connection
                    async for msg in stream:
                        k = msg.get("data", {}).get("k") or msg.get("k")
                        if k and k.get("x"):  # candle is closed
                            self._on_candle(k)
            except asyncio.CancelledError:
                # Graceful shutdown — do not retry
                raise
            except Exception:
                retries += 1
                wait = 2 ** retries  # 2s, 4s, 8s
                await asyncio.sleep(wait)

    def _on_candle(self, k: dict):
        """Parse a closed kline dict and append to buffer, then fire callback."""
        candle = {
            "open_time": pd.Timestamp(k["t"], unit="ms", tz="UTC"),
            "open": float(k["o"]),
            "high": float(k["h"]),
            "low": float(k["l"]),
            "close": float(k["c"]),
            "volume": float(k["v"]),
        }
        self.buffer.append(candle)

        if self.on_close:
            self.on_close()

        if self._callback and len(self.buffer) >= 20:
            df = pd.DataFrame(list(self.buffer)).set_index("open_time")
            asyncio.create_task(self._callback(df))
