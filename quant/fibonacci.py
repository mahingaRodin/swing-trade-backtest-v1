"""
quant/fibonacci.py

Fib retracement levels from a swing leg, and discount/premium tagging.
"""

from __future__ import annotations

FIB_RATIOS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]


def fib_levels(leg: dict) -> dict:
    """
    leg: output of quant.swings.latest_confirmed_leg()

    For an "up" leg (buy setup): 0% = swing high, 100% = swing low
    (matches how the strategy draws it: retracement % grows as price
    falls back down from the high).
    For a "down" leg (sell setup): 0% = swing low, 100% = swing high.

    Returns {0.0: price, 0.236: price, ..., 1.0: price}
    """
    if leg["direction"] == "up":
        p0, p1 = leg["end_price"], leg["start_price"]  # high -> low
    else:
        p0, p1 = leg["end_price"], leg["start_price"]  # low -> high

    span = p1 - p0
    return {r: p0 + r * span for r in FIB_RATIOS}


def zone(levels: dict, price: float, direction: str) -> str:
    """
    direction: "up" (buy setup) or "down" (sell setup)
    Returns "discount" or "premium".

    Buy setup: discount = below the 50% level (closer to the low).
    Sell setup: premium = above the 50% level (closer to the high) --
    i.e. the zone you WANT for a sell is still called "premium" here,
    named for the strategy's own terminology, not just up/down.
    """
    fifty = levels[0.5]
    if direction == "up":
        return "discount" if price < fifty else "premium"
    else:
        return "premium" if price > fifty else "discount"
