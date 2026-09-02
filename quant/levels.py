"""
quant/levels.py

Clusters confirmed swing points into support/resistance levels.
A level needs >=2 touches within `tolerance` to count -- a single
swing point is not "support," it's just a swing.
"""

from __future__ import annotations
import pandas as pd


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def find_levels(df_with_swings: pd.DataFrame, tolerance_atr_mult: float = 0.15) -> list[dict]:
    """
    Returns a list of {"price": float, "touches": int, "kind": "support"/"resistance"}
    sorted by touch count descending. `price` is the average of the clustered points.
    """
    atr_series = atr(df_with_swings)
    current_atr = atr_series.dropna().iloc[-1] if atr_series.notna().any() else df_with_swings["close"].std() * 0.01
    tolerance = current_atr * tolerance_atr_mult

    swing_lows = df_with_swings.loc[df_with_swings["swing_low"], "low"].tolist()
    swing_highs = df_with_swings.loc[df_with_swings["swing_high"], "high"].tolist()

    def cluster(points: list[float], kind: str) -> list[dict]:
        points = sorted(points)
        clusters: list[list[float]] = []
        for p in points:
            if clusters and abs(p - clusters[-1][-1]) <= tolerance:
                clusters[-1].append(p)
            else:
                clusters.append([p])
        return [
            {"price": sum(c) / len(c), "touches": len(c), "kind": kind}
            for c in clusters if len(c) >= 2
        ]

    levels = cluster(swing_lows, "support") + cluster(swing_highs, "resistance")
    return sorted(levels, key=lambda l: -l["touches"])
