"""
quant/patterns.py

Candle-level pattern rules: bearish/bullish classification, doji exclusion,
the 3-candle retracement run, and the engulfing confirmation candle.

These implement rules #7 and #8 from the spec precisely -- see the doc for
the reasoning. Change the constants here (not scattered in scripts) if you
want to test alternative definitions.
"""

from __future__ import annotations
import pandas as pd

DOJI_BODY_FRACTION = 0.1  # body smaller than 10% of the bar's range = doji, excluded


def candle_kind(o: float, c: float, h: float, l: float) -> str:
    """'bullish', 'bearish', or 'doji'."""
    rng = h - l
    if rng == 0:
        return "doji"
    if abs(c - o) < DOJI_BODY_FRACTION * rng:
        return "doji"
    return "bullish" if c > o else "bearish"


def three_candle_retracement(df: pd.DataFrame, idx: int, want_kind: str) -> bool:
    """
    True if the 3 bars immediately BEFORE idx (idx-3, idx-2, idx-1) are all
    `want_kind` ('bearish' for a buy-setup retracement, 'bullish' for a sell).
    Any doji in that window breaks the run -- no ambiguity allowed in.
    """
    if idx - 3 < 0:
        return False
    for j in range(idx - 3, idx):
        row = df.iloc[j]
        if candle_kind(row["open"], row["close"], row["high"], row["low"]) != want_kind:
            return False
    return True


def is_confirmation(df: pd.DataFrame, idx: int, direction: str) -> bool:
    """
    Engulfing confirmation at bar idx.
    direction: 'buy' -> bullish engulfing, 'sell' -> bearish engulfing.
    """
    if idx - 1 < 0:
        return False
    cur, prev = df.iloc[idx], df.iloc[idx - 1]
    if direction == "buy":
        return (cur["close"] > cur["open"]
                and cur["open"] <= prev["close"]
                and cur["close"] >= prev["open"])
    else:
        return (cur["close"] < cur["open"]
                and cur["open"] >= prev["close"]
                and cur["close"] <= prev["open"])
