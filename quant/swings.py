"""
quant/swings.py

Fractal swing high/low detection.

A bar at index i is a CONFIRMED swing high once n bars have closed after
it and its high was greater than every high in the n bars on both sides.
"Confirmed" matters: you cannot know bar i is a swing high until n bars
later -- using it earlier is look-ahead bias.
"""

from __future__ import annotations
import pandas as pd


def detect_swings(df: pd.DataFrame, n: int = 2) -> pd.DataFrame:
    """
    Parameters
    ----------
    df : DataFrame with 'high' and 'low' columns, sorted ascending by time.
    n  : number of bars required on each side to confirm a swing (default 2).

    Returns
    -------
    Copy of df with two added boolean columns:
        swing_high -- True if this bar is a confirmed swing high
        swing_low  -- True if this bar is a confirmed swing low
    A swing near the very end of the data (fewer than n bars after it)
    is NOT marked -- there isn't enough future data yet to confirm it.
    """
    out = df.copy()
    highs = out["high"].values
    lows = out["low"].values
    n_bars = len(out)

    swing_high = [False] * n_bars
    swing_low = [False] * n_bars

    for i in range(n, n_bars - n):
        window_high = highs[i - n : i + n + 1]
        window_low = lows[i - n : i + n + 1]
        if highs[i] == window_high.max() and (window_high == highs[i]).sum() == 1:
            swing_high[i] = True
        if lows[i] == window_low.min() and (window_low == lows[i]).sum() == 1:
            swing_low[i] = True

    out["swing_high"] = swing_high
    out["swing_low"] = swing_low
    return out


def latest_confirmed_leg(df_with_swings: pd.DataFrame) -> dict | None:
    """
    Finds the most recent confirmed swing-low-to-swing-high (or high-to-low)
    leg -- i.e. the last two consecutive, alternating confirmed swing points.

    Returns a dict like:
        {
            "direction": "up" or "down",
            "start_time": ..., "start_price": ...,   # older swing point
            "end_time": ...,   "end_price": ...,      # more recent swing point
        }
    or None if fewer than two confirmed swings exist yet.
    """
    swings = df_with_swings[df_with_swings["swing_high"] | df_with_swings["swing_low"]].copy()
    if len(swings) < 2:
        return None

    swings["kind"] = swings.apply(lambda r: "high" if r["swing_high"] else "low", axis=1)
    swings["price"] = swings.apply(lambda r: r["high"] if r["swing_high"] else r["low"], axis=1)

    # walk backwards from the most recent swing, find the first point before it
    # of the OPPOSITE kind -- that pair defines the current leg.
    last = swings.iloc[-1]
    for i in range(len(swings) - 2, -1, -1):
        prev = swings.iloc[i]
        if prev["kind"] != last["kind"]:
            direction = "up" if last["kind"] == "high" else "down"
            return {
                "direction": direction,
                "start_time": prev.name,
                "start_price": prev["price"],
                "end_time": last.name,
                "end_price": last["price"],
            }
    return None
