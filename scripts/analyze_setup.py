"""
scripts/analyze_setup.py

Reads a TradingView CSV export, finds the most recent confirmed swing leg,
computes Fib levels, checks if price is in discount/premium, finds nearby
support/resistance, and reports candidate entry/stop/target levels at
1:2 and 1:3 risk:reward.

This is a READ of current structure, not a backtest and not a signal to
trade. It tells you what the rules say about where price is right now --
you still need the confirmation candle to actually exist before entering
(see quant/patterns.py, not yet built).

Usage:
    python scripts/analyze_setup.py --file "t-view/BITSTAMP_GBPUSD, 240_b9403.csv"
"""

import argparse
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quant.data import load_tradingview_csv
from quant.swings import detect_swings, latest_confirmed_leg
from quant.fibonacci import fib_levels, zone
from quant.levels import find_levels


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True, help="Path to TradingView CSV export")
    parser.add_argument("--swing-n", type=int, default=2, help="Bars each side to confirm a swing")
    parser.add_argument("--sl-buffer-atr", type=float, default=0.25, help="Stop-loss buffer, in ATR multiples")
    args = parser.parse_args()

    df = load_tradingview_csv(args.file)
    df = detect_swings(df, n=args.swing_n)

    leg = latest_confirmed_leg(df)
    if leg is None:
        print("Not enough confirmed swing points yet to define a leg. Need more data or a smaller --swing-n.")
        return

    levels = fib_levels(leg)
    current_price = df["close"].iloc[-1]
    current_zone = zone(levels, current_price, leg["direction"])
    sr_levels = find_levels(df)

    from quant.levels import atr as atr_fn
    current_atr = atr_fn(df).dropna().iloc[-1]

    print("=" * 60)
    print(f"Most recent confirmed leg: {leg['direction'].upper()}")
    print(f"  {leg['start_time']}  ->  {leg['start_price']:.5f}")
    print(f"  {leg['end_time']}    ->  {leg['end_price']:.5f}")
    print()
    print("Fibonacci levels:")
    for r, p in levels.items():
        marker = "  <- 50% (discount/premium line)" if r == 0.5 else ""
        print(f"  {r*100:5.1f}%   {p:.5f}{marker}")
    print()
    print(f"Current price: {current_price:.5f}  -> currently in the {current_zone.upper()} zone")
    print()
    print("Nearby support/resistance (>=2 confirmed touches):")
    if not sr_levels:
        print("  None found with current tolerance -- try a smaller/larger --swing-n or check more history.")
    for lvl in sr_levels[:5]:
        print(f"  {lvl['kind']:10s} {lvl['price']:.5f}  ({lvl['touches']} touches)")

    setup_direction = "buy" if leg["direction"] == "up" and current_zone == "discount" else \
                       "sell" if leg["direction"] == "down" and current_zone == "premium" else None

    print()
    print("=" * 60)
    if setup_direction is None:
        print("Price is NOT currently in the zone this strategy looks for "
              "(need discount zone on an up-leg, or premium zone on a down-leg).")
        print("No entry/stop/target computed -- this is not a setup right now.")
        return

    supports = [l for l in sr_levels if l["kind"] == "support"]
    resistances = [l for l in sr_levels if l["kind"] == "resistance"]

    if setup_direction == "buy":
        support_below = max([l["price"] for l in supports if l["price"] < current_price], default=None)
        target = max([l["price"] for l in resistances if l["price"] > current_price], default=leg["start_price"])
        entry = current_price
        if support_below is None:
            print("No confirmed support below current price found -- cannot place a stop-loss safely. "
                  "This is exactly why we don't trade without confluence.")
            return
        stop = support_below - current_atr * args.sl_buffer_atr
        risk = entry - stop
        tp2 = entry + 2 * risk
        tp3 = entry + 3 * risk
    else:
        resistance_above = min([l["price"] for l in resistances if l["price"] > current_price], default=None)
        target = min([l["price"] for l in supports if l["price"] < current_price], default=leg["start_price"])
        entry = current_price
        if resistance_above is None:
            print("No confirmed resistance above current price found -- cannot place a stop-loss safely.")
            return
        stop = resistance_above + current_atr * args.sl_buffer_atr
        risk = stop - entry
        tp2 = entry - 2 * risk
        tp3 = entry - 3 * risk

    print(f"Candidate {setup_direction.upper()} setup (IF a confirmation candle forms here -- check the chart):")
    print(f"  Entry (next bar open, approx current close): {entry:.5f}")
    print(f"  Stop loss (beyond nearest S/R + buffer):      {stop:.5f}")
    print(f"  Risk (R):                                     {risk:.5f}")
    print(f"  Take profit @ 1:2 R:R:                        {tp2:.5f}")
    print(f"  Take profit @ 1:3 R:R:                        {tp3:.5f}")
    print(f"  Structural target (prior opposite swing/S-R): {target:.5f}")
    print()
    print("Reminder: this is a READ of current structure using historical rules -- "
          "it is not a guarantee price will do anything. Wait for the actual "
          "confirmation candle to close before treating this as a real signal.")


if __name__ == "__main__":
    main()
