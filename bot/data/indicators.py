import pandas as pd
import pandas_ta as ta


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all technical indicators on an OHLCV DataFrame.

    Adds the following columns in-place and returns the modified df:
        ema_20, ema_50, ema_200
        rsi_14
        macd, macd_signal, macd_hist
        atr_14
        supertrend, supertrend_dir   (1 = uptrend, -1 = downtrend)
        vwap
        bb_upper, bb_mid, bb_lower

    NaN handling:
        - Forward-fill (ffill) is applied to all new indicator columns so that
          a partial NaN window doesn't leave the last row unusable.
        - Any remaining NaN in indicator columns after ffill is left as-is
          (callers can dropna if they need a fully clean row).

    Args:
        df: DataFrame with columns open, high, low, close, volume and a
            DatetimeIndex.  Minimum 200 rows recommended for EMA200.

    Returns:
        Same DataFrame object with indicator columns appended.
    """
    df = df.copy()

    # ── EMAs ──────────────────────────────────────────────────────────────────
    df["ema_20"] = ta.ema(df["close"], length=20)
    df["ema_50"] = ta.ema(df["close"], length=50)
    df["ema_200"] = ta.ema(df["close"], length=200)

    # ── RSI ───────────────────────────────────────────────────────────────────
    df["rsi_14"] = ta.rsi(df["close"], length=14)

    # ── MACD ─────────────────────────────────────────────────────────────────
    macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
    if macd is not None and not macd.empty:
        df["macd"] = macd["MACD_12_26_9"]
        df["macd_signal"] = macd["MACDs_12_26_9"]
        df["macd_hist"] = macd["MACDh_12_26_9"]
    else:
        df["macd"] = float("nan")
        df["macd_signal"] = float("nan")
        df["macd_hist"] = float("nan")

    # ── ATR ───────────────────────────────────────────────────────────────────
    df["atr_14"] = ta.atr(df["high"], df["low"], df["close"], length=14)

    # ── SuperTrend ────────────────────────────────────────────────────────────
    st = ta.supertrend(df["high"], df["low"], df["close"], length=10, multiplier=3.0)
    if st is not None and not st.empty:
        st_col  = next((c for c in st.columns if c.startswith("SUPERT_") and not c.startswith("SUPERTd") and not c.startswith("SUPERTl") and not c.startswith("SUPERTs")), None)
        std_col = next((c for c in st.columns if c.startswith("SUPERTd_")), None)
        df["supertrend"]     = st[st_col]  if st_col  else float("nan")
        df["supertrend_dir"] = st[std_col] if std_col else float("nan")
    else:
        df["supertrend"] = float("nan")
        df["supertrend_dir"] = float("nan")

    # ── VWAP ─────────────────────────────────────────────────────────────────
    # pandas_ta vwap resets at each session anchor (midnight UTC by default)
    df["vwap"] = ta.vwap(df["high"], df["low"], df["close"], df["volume"])

    # ── Bollinger Bands ───────────────────────────────────────────────────────
    # Column name format varies by pandas_ta version (e.g. "BBU_20_2.0" vs "BBU_20_2")
    bb = ta.bbands(df["close"], length=20, std=2.0)
    if bb is not None and not bb.empty:
        upper_col = next((c for c in bb.columns if c.startswith("BBU_")), None)
        mid_col   = next((c for c in bb.columns if c.startswith("BBM_")), None)
        lower_col = next((c for c in bb.columns if c.startswith("BBL_")), None)
        df["bb_upper"] = bb[upper_col] if upper_col else float("nan")
        df["bb_mid"]   = bb[mid_col]   if mid_col   else float("nan")
        df["bb_lower"] = bb[lower_col] if lower_col else float("nan")
    else:
        df["bb_upper"] = float("nan")
        df["bb_mid"] = float("nan")
        df["bb_lower"] = float("nan")

    # ── NaN handling ──────────────────────────────────────────────────────────
    indicator_cols = [
        "ema_20", "ema_50", "ema_200",
        "rsi_14",
        "macd", "macd_signal", "macd_hist",
        "atr_14",
        "supertrend", "supertrend_dir",
        "vwap",
        "bb_upper", "bb_mid", "bb_lower",
    ]
    df[indicator_cols] = df[indicator_cols].ffill()

    return df
