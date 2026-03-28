import pandas as pd


def find_levels(
    df: pd.DataFrame,
    window: int = 10,
    min_touches: int = 2,
    price_tolerance: float = 0.002,
) -> tuple[list[float], list[float]]:
    """Detect support and resistance levels from OHLCV data.

    Algorithm:
        1. Rolling max of ``high`` (center=True) identifies resistance pivots.
        2. Rolling min of ``low``  (center=True) identifies support pivots.
        3. Candidate levels are the unique pivot prices rounded to a grid
           defined by ``price_tolerance``.
        4. Only levels with at least ``min_touches`` touches are kept.
           A touch is when any candle's high (for resistance) or low (for
           support) comes within ``price_tolerance`` (0.2% by default) of
           the level.

    Args:
        df:              OHLCV DataFrame — must have ``high`` and ``low`` columns.
        window:          Rolling window size for pivot detection (default 10).
        min_touches:     Minimum number of price touches to qualify (default 2).
        price_tolerance: Fractional tolerance for touch detection (default 0.002
                         = 0.2%).

    Returns:
        Tuple ``(support_list, resistance_list)`` where each list contains
        qualified levels sorted ascending.
    """
    if df is None or len(df) < window * 2:
        return [], []

    highs = df["high"]
    lows = df["low"]

    # ── Identify pivot highs (resistance) ────────────────────────────────────
    rolling_max = highs.rolling(window=window, center=True).max()
    # A pivot high is a candle whose high equals the rolling max at that point
    pivot_highs = highs[highs == rolling_max].dropna()

    # ── Identify pivot lows (support) ─────────────────────────────────────────
    rolling_min = lows.rolling(window=window, center=True).min()
    pivot_lows = lows[lows == rolling_min].dropna()

    def _count_touches(level: float, series: pd.Series) -> int:
        """Count how many values in series are within tolerance of level."""
        lower = level * (1 - price_tolerance)
        upper = level * (1 + price_tolerance)
        return int(((series >= lower) & (series <= upper)).sum())

    def _cluster_levels(pivot_series: pd.Series) -> list[float]:
        """
        Collapse nearby pivots into a single representative level.
        Two pivots are merged if they are within price_tolerance of each other.
        Returns a sorted list of cluster centre prices.
        """
        sorted_pivots = sorted(pivot_series.values)
        clusters: list[list[float]] = []
        for price in sorted_pivots:
            placed = False
            for cluster in clusters:
                ref = cluster[0]
                if abs(price - ref) / ref <= price_tolerance:
                    cluster.append(price)
                    placed = True
                    break
            if not placed:
                clusters.append([price])
        return [sum(c) / len(c) for c in clusters]

    # ── Cluster and filter resistance ─────────────────────────────────────────
    resistance_levels: list[float] = []
    if len(pivot_highs) > 0:
        for level in _cluster_levels(pivot_highs):
            if _count_touches(level, highs) >= min_touches:
                resistance_levels.append(round(level, 4))

    # ── Cluster and filter support ────────────────────────────────────────────
    support_levels: list[float] = []
    if len(pivot_lows) > 0:
        for level in _cluster_levels(pivot_lows):
            if _count_touches(level, lows) >= min_touches:
                support_levels.append(round(level, 4))

    return sorted(support_levels), sorted(resistance_levels)
