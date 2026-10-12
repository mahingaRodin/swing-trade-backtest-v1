r"""Run portfolio-risk and spread-sensitivity scenarios on scanner setups.

Example:
    python scripts\backtest_portfolio.py --file "t-view\BITSTAMP_GBPUSD, 240_e573d.csv" `
        --setups research\after_time_stop.csv
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from quant.config import load_config
from quant.data import load_tradingview_csv
from quant.portfolio import portfolio_metrics, run_portfolio_backtest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=os.path.join("config", "base.yaml"))
    parser.add_argument("--file", required=True, help="TradingView OHLCV CSV")
    parser.add_argument("--setups", required=True, help="CSV written by scan_setups.py")
    parser.add_argument("--out-dir", default=os.path.join("research", "portfolio"))
    parser.add_argument(
        "--spread-pips",
        nargs="+",
        type=float,
        default=[0.5, 1.0, 2.0],
        help="Full assumed GBP/USD spread widths in pips (default: 0.5 1 2)",
    )
    parser.add_argument("--pip-size", type=float, default=0.0001)
    parser.add_argument("--initial-equity", type=float, default=10_000.0)
    parser.add_argument("--trade-risk-pct", type=float, default=0.01)
    parser.add_argument("--portfolio-risk-pct", type=float, default=0.01)
    parser.add_argument("--min-units", type=int, default=1_000)
    parser.add_argument("--unit-increment", type=int, default=1_000)
    parser.add_argument("--scenario-name", default="full")
    parser.add_argument("--signal-start-date", default=None, help="Filter setups from this date, inclusive (YYYY-MM-DD)")
    parser.add_argument("--signal-end-date", default=None, help="Filter setups through this date, inclusive (YYYY-MM-DD)")
    parser.add_argument("--data-end-date", default=None, help="Truncate market bars at this date, inclusive (YYYY-MM-DD)")
    args = parser.parse_args()

    if args.pip_size <= 0 or not args.spread_pips or any(pip < 0 for pip in args.spread_pips):
        parser.error("pip size must be positive and spread values must be non-negative")

    config = load_config(args.config)
    bars = load_tradingview_csv(args.file)
    if args.data_end_date:
        data_end = pd.Timestamp(args.data_end_date, tz="UTC") + pd.Timedelta(days=1)
        bars = bars[bars.index < data_end]
        if bars.empty:
            parser.error("No price bars remain after applying --data-end-date.")
    setups = pd.read_csv(args.setups, parse_dates=["confirmation_time"])
    setups["confirmation_time"] = pd.to_datetime(setups["confirmation_time"], utc=True)
    if args.signal_start_date:
        start = pd.Timestamp(args.signal_start_date, tz="UTC")
        setups = setups[setups["confirmation_time"] >= start]
    if args.signal_end_date:
        end_exclusive = pd.Timestamp(args.signal_end_date, tz="UTC") + pd.Timedelta(days=1)
        setups = setups[setups["confirmation_time"] < end_exclusive]
    if setups.empty:
        parser.error("No setups remain after applying the signal-date filters.")

    scenario_dir = os.path.join(args.out_dir, args.scenario_name)
    os.makedirs(scenario_dir, exist_ok=True)

    summaries = []
    for spread_pips in args.spread_pips:
        spread_price = spread_pips * args.pip_size
        trades, events, equity = run_portfolio_backtest(
            bars,
            setups,
            initial_equity=args.initial_equity,
            trade_risk_pct=args.trade_risk_pct,
            portfolio_risk_pct=args.portfolio_risk_pct,
            spread_price=spread_price,
            slippage_price=float(config["slippage_price"]),
            fee_pct=float(config["fee_pct"]),
            max_hold_bars=int(config["max_hold_bars"]),
            min_units=args.min_units,
            unit_increment=args.unit_increment,
        )
        metrics = portfolio_metrics(trades, equity, args.initial_equity)
        label = f"{spread_pips:g}pip".replace(".", "_")
        assumptions = {
            "spread_pips": spread_pips,
            "spread_price": spread_price,
            "slippage_per_fill_price": float(config["slippage_price"]),
            "commission_per_side_pct": float(config["fee_pct"]) * 100,
            "initial_equity_usd": args.initial_equity,
            "trade_risk_pct": args.trade_risk_pct * 100,
            "portfolio_risk_cap_pct": args.portfolio_risk_pct * 100,
            "minimum_units": args.min_units,
            "unit_increment": args.unit_increment,
        }
        breach_rows = events[events["event"] == "CAP_BREACH_DETECTED"] if "event" in events else events
        reduction_rows = events[
            events["event"].isin(["FORCED_REDUCTION", "FORCED_CLOSE"])
        ] if "event" in events else events
        breach_durations = events[
            events["event"].isin([
                "CAP_BREACH_STATUS", "BREACH_RESTORED",
                "BREACH_RESOLVED_BEFORE_REDUCTION", "BREACH_UNRESOLVED_AT_DATA_END",
            ])
        ] if "event" in events else events
        summaries.append({
            **assumptions,
            **metrics,
            "cap_breach_count": len(breach_rows),
            "forced_reduction_count": len(reduction_rows),
            "forced_reduction_costs_usd": float(
                reduction_rows[["spread_cost_usd", "slippage_cost_usd", "commission_usd"]]
                .sum(axis=1).sum()
            ) if len(reduction_rows) else 0.0,
            "max_breach_duration_minutes": float(breach_durations["duration_minutes"].max())
            if len(breach_durations) and "duration_minutes" in breach_durations else 0.0,
        })
        trades.to_csv(os.path.join(scenario_dir, f"trades_{label}.csv"), index=False)
        events.to_csv(os.path.join(scenario_dir, f"events_{label}.csv"), index=False)
        equity.to_csv(os.path.join(scenario_dir, f"equity_{label}.csv"), index=False)

    summary = pd.DataFrame(summaries)
    summary_path = os.path.join(scenario_dir, "sensitivity_summary.csv")
    summary.to_csv(summary_path, index=False)

    print(summary[[
        "spread_pips", "accepted", "rejected", "closed", "expectancy_net_usd",
        "expectancy_net_r",
        "profit_factor", "ending_equity_usd", "max_drawdown_usd", "max_drawdown_pct",
    ]].to_string(index=False, float_format=lambda value: f"{value:.4f}"))
    print(f"\nScenario: {args.scenario_name}")
    print(f"Signal date range: {args.signal_start_date or 'all'} to {args.signal_end_date or 'all'}")
    print(f"Price data through: {args.data_end_date or 'file end'}")
    print(f"Detailed trades, events, and equity curves written under: {scenario_dir}")
    print(f"Sensitivity summary: {summary_path}")
    print(
        "\nAssumptions: midpoint OHLC; full spread charged half at each fill; "
        f"slippage={config['slippage_price']} per fill; "
        f"commission={config['fee_pct']:.4%} per side; "
        "GBP/USD exposure counted gross without directional netting."
    )
    print(
        "Risk-cap reductions are scheduled at the next observed 4-hour bar open; "
        "this is a bar-based approximation, not intrabar execution."
    )


if __name__ == "__main__":
    main()
