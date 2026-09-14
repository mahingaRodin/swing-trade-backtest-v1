"""
scripts/scan_setups.py

Scans an ENTIRE historical CSV for every occurrence of the swing setup:
  confirmed swing leg -> 3-candle retracement into discount/premium ->
  confirmation candle -> entry at next open -> SL beyond nearest S/R ->
  TP at 1:2 R.

For every match, it also plays the trade forward bar-by-bar to see whether
SL or TP was hit first (or neither, within a time-stop window), and prints
a summary: win rate, average R, expectancy. This IS a real backtest, in
the sense of "what would have happened" -- it is NOT evidence the strategy
is profitable going forward. Read the numbers as a starting hypothesis
test, not a verdict.

Usage:
    python scripts/scan_setups.py --file t-view_data_v2.csv
"""

import argparse
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from quant.data import load_tradingview_csv
from quant.swings import detect_swings
from quant.fibonacci import fib_levels, zone
from quant.levels import find_levels, atr
from quant.patterns import three_candle_retracement, is_confirmation
from quant.config import load_config


def leg_confirmed_by(swings_list, cutoff_idx):
    """Latest confirmed alternating swing-high/swing-low pair, using only
    swing points whose original index <= cutoff_idx (i.e. known by then)."""
    known = [s for s in swings_list if s["idx"] <= cutoff_idx]
    if len(known) < 2:
        return None
    last = known[-1]
    for i in range(len(known) - 2, -1, -1):
        prev = known[i]
        if prev["kind"] != last["kind"]:
            direction = "up" if last["kind"] == "high" else "down"
            return {
                "direction": direction,
                "start_price": prev["price"],
                "end_price": last["price"],
                "start_idx": prev["idx"],
                "end_idx": last["idx"],
            }
    return None


def simulate_outcome(df, entry_idx, entry, stop, target, direction, max_bars=60,
                      slippage_price=0.0, fee_pct=0.0):
    """
    Walk forward from entry_idx, applying slippage on both fills and a
    round-trip fee, both expressed back in R terms so they're comparable
    across trades of different price distances.

    slippage_price: fixed price units of adverse slippage, applied once on
                     entry and once on the exit fill (worst case: both directions).
    fee_pct:         fraction (e.g. 0.0002 = 0.02%) of notional, charged on
                     BOTH the entry and the exit -- a full round trip.

    Returns ('WIN'|'LOSS'|'OPEN', R_multiple_after_costs, bars_held).
    """
    risk = abs(entry - stop)
    if risk == 0:
        return "SKIP", 0.0, 0

    # entry fill is worse than the signal price by slippage_price
    entry_filled = entry + slippage_price if direction == "buy" else entry - slippage_price
    fee_r = (entry_filled * fee_pct * 2) / risk  # both legs charged, converted to R

    for k in range(entry_idx, min(entry_idx + max_bars, len(df))):
        bar = df.iloc[k]
        if direction == "buy":
            if bar["low"] <= stop:
                exit_filled = stop - slippage_price
                r = (exit_filled - entry_filled) / risk - fee_r
                return "LOSS", r, k - entry_idx
            if bar["high"] >= target:
                exit_filled = target - slippage_price
                r = (exit_filled - entry_filled) / risk - fee_r
                return "WIN", r, k - entry_idx
        else:
            if bar["high"] >= stop:
                exit_filled = stop + slippage_price
                r = (entry_filled - exit_filled) / risk - fee_r
                return "LOSS", r, k - entry_idx
            if bar["low"] <= target:
                exit_filled = target + slippage_price
                r = (entry_filled - exit_filled) / risk - fee_r
                return "WIN", r, k - entry_idx
    return "OPEN", 0.0, max_bars


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=None, help="Path to a YAML config (e.g. config/base.yaml) -- "
                                                          "sets defaults for the flags below, which can still "
                                                          "be overridden individually on the command line.")
    parser.add_argument("--file", required=True)
    parser.add_argument("--swing-n", type=int, default=5)
    parser.add_argument("--sl-buffer-atr", type=float, default=0.25)
    parser.add_argument("--sl-tolerance-atr", type=float, default=0.15,
                         help="Support/resistance clustering tolerance, in ATR multiples")
    parser.add_argument("--rr-target", type=float, default=2.0, help="Reward:risk multiple for TP")
    parser.add_argument("--max-hold-bars", type=int, default=60)
    parser.add_argument("--slippage-price", type=float, default=0.0,
                         help="Adverse slippage in raw price units, e.g. 0.0002 for GBPUSD ~2 pips")
    parser.add_argument("--fee-pct", type=float, default=0.0,
                         help="Fee as a fraction of notional PER SIDE, e.g. 0.0002 for 0.02%%")
    parser.add_argument("--out", default="research/scan_results.csv")
    parser.add_argument("--start-date", default=None, help="Only use bars from this date onward (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=None, help="Only use bars up to this date (YYYY-MM-DD)")

    # two-pass parse: if --config is given, its values become the defaults,
    # then any flags explicitly passed on the command line still win.
    known_args, _ = parser.parse_known_args()
    if known_args.config:
        cfg = load_config(known_args.config)
        key_map = {  # yaml key -> argparse dest
            "swing_n": "swing_n", "sl_buffer_atr": "sl_buffer_atr",
            "sl_tolerance_atr": "sl_tolerance_atr", "rr_target": "rr_target",
            "max_hold_bars": "max_hold_bars", "slippage_price": "slippage_price",
            "fee_pct": "fee_pct",
        }
        defaults = {key_map[k]: v for k, v in cfg.items() if k in key_map}
        parser.set_defaults(**defaults)

    args = parser.parse_args()

    df = load_tradingview_csv(args.file)
    if args.start_date:
        df = df[df.index >= pd.Timestamp(args.start_date, tz="UTC")]
    if args.end_date:
        df = df[df.index <= pd.Timestamp(args.end_date, tz="UTC")]
    df = detect_swings(df, n=args.swing_n)
    atr_series = atr(df)

    swings_list = []
    for i, (ts, row) in enumerate(df.iterrows()):
        if row["swing_high"]:
            swings_list.append({"idx": i, "kind": "high", "price": row["high"], "time": ts})
        if row["swing_low"]:
            swings_list.append({"idx": i, "kind": "low", "price": row["low"], "time": ts})

    results = []
    for c in range(args.swing_n + 4, len(df) - 1):
        cutoff = c - args.swing_n  # swings are only "known" n bars after they occur
        leg = leg_confirmed_by(swings_list, cutoff)
        if leg is None:
            continue

        direction = "buy" if leg["direction"] == "up" else "sell"
        want_retracement_kind = "bearish" if direction == "buy" else "bullish"

        if not three_candle_retracement(df, c, want_retracement_kind):
            continue
        if not is_confirmation(df, c, direction):
            continue

        levels = fib_levels(leg)
        retracement_close = df.iloc[c - 1]["close"]
        if zone(levels, retracement_close, leg["direction"]) != ("discount" if direction == "buy" else "premium"):
            continue

        entry_idx = c + 1
        entry = df.iloc[entry_idx]["open"]
        history_slice = df.iloc[:c + 1]
        history_slice = history_slice.assign(swing_high=df["swing_high"].iloc[:c + 1],
                                              swing_low=df["swing_low"].iloc[:c + 1])
        sr_levels = find_levels(history_slice, tolerance_atr_mult=args.sl_tolerance_atr)
        current_atr = atr_series.iloc[c] if pd.notna(atr_series.iloc[c]) else df["close"].iloc[:c+1].std() * 0.01

        if direction == "buy":
            supports = [l["price"] for l in sr_levels if l["kind"] == "support" and l["price"] < entry]
            if not supports:
                continue
            stop = max(supports) - current_atr * args.sl_buffer_atr
            risk = entry - stop
            if risk <= 0:
                continue
            target = entry + args.rr_target * risk
        else:
            resistances = [l["price"] for l in sr_levels if l["kind"] == "resistance" and l["price"] > entry]
            if not resistances:
                continue
            stop = min(resistances) + current_atr * args.sl_buffer_atr
            risk = stop - entry
            if risk <= 0:
                continue
            target = entry - args.rr_target * risk

        outcome, r_multiple, bars_held = simulate_outcome(
            df, entry_idx, entry, stop, target, direction, max_bars=args.max_hold_bars,
            slippage_price=args.slippage_price, fee_pct=args.fee_pct
        )
        if outcome == "SKIP":
            continue

        results.append({
            "confirmation_time": df.index[c],
            "direction": direction,
            "entry": entry,
            "stop": stop,
            "target": target,
            "risk_price": risk,
            "outcome": outcome,
            "r_multiple": r_multiple,
            "bars_held": bars_held,
        })

    out_df = pd.DataFrame(results)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    out_df.to_csv(args.out, index=False)

    print("=" * 60)
    print(f"Scanned {len(df)} bars, found {len(out_df)} setups matching the rules.")
    if len(out_df) == 0:
        print("No setups found -- try a different --swing-n or check the rule logic against a manual example.")
        return

    closed = out_df[out_df["outcome"].isin(["WIN", "LOSS"])]
    wins = closed[closed["outcome"] == "WIN"]
    losses = closed[closed["outcome"] == "LOSS"]
    still_open = out_df[out_df["outcome"] == "OPEN"]

    win_rate = len(wins) / len(closed) if len(closed) else float("nan")
    avg_r = closed["r_multiple"].mean() if len(closed) else float("nan")
    avg_win_r = wins["r_multiple"].mean() if len(wins) else float("nan")
    avg_loss_r = losses["r_multiple"].mean() if len(losses) else float("nan")
    expectancy = avg_r
    gross_profit = wins["r_multiple"].sum()
    gross_loss = abs(losses["r_multiple"].sum())
    profit_factor = gross_profit / gross_loss if gross_loss else float("inf")

    print(f"By direction: {out_df['direction'].value_counts().to_dict()}")
    print(f"Closed trades: {len(closed)}  (still open / hit time-stop: {len(still_open)})")
    print(f"Win rate:   {win_rate:.1%}")
    print(f"Avg R (closed trades): {avg_r:.2f}")
    print(f"Avg win / loss: {avg_win_r:.2f}R / {avg_loss_r:.2f}R")
    print(f"Profit factor: {profit_factor:.2f}")
    print(f"Expectancy: {expectancy:.2f}R per trade")
    print()
    print(f"Full trade log written to: {args.out}")
    print()
    print(f"Assumptions: slippage={args.slippage_price}, fee={args.fee_pct:.4%} per side, "
          f"SL buffer={args.sl_buffer_atr}x ATR, R:R target={args.rr_target}.")
    print("Reminder: this is what these exact rules did on this exact historical "
          "file, under these assumptions. It is not a claim about future performance.")


if __name__ == "__main__":
    main()
