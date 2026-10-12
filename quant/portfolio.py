"""Portfolio-level GBP/USD trade sizing and bar-based execution."""

from __future__ import annotations

import math

import pandas as pd


def _floor_increment(value: float, increment: float) -> float:
    return math.floor(value / increment + 1e-12) * increment


def _exit_fill(level: float, direction: str, half_spread: float, slippage: float) -> float:
    if direction == "buy":
        return level - half_spread - slippage
    return level + half_spread + slippage


def _entry_fill(level: float, direction: str, half_spread: float, slippage: float) -> float:
    if direction == "buy":
        return level + half_spread + slippage
    return level - half_spread - slippage


def run_portfolio_backtest(
    bars: pd.DataFrame,
    setups: pd.DataFrame,
    *,
    initial_equity: float = 10_000.0,
    trade_risk_pct: float = 0.01,
    portfolio_risk_pct: float = 0.01,
    spread_price: float = 0.0001,
    slippage_price: float = 0.0002,
    fee_pct: float = 0.0002,
    max_hold_bars: int = 60,
    min_units: int = 1_000,
    unit_increment: int = 1_000,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Simulate GBP/USD trades with costs, size limits, and a shared risk cap.

    Spreads are full bid/ask widths against midpoint OHLC data. Position risk
    includes the stop distance, both spread/slippage fills, and round-trip
    commission. Open stop risk is kept gross (long and short positions are not
    netted) and any breach is reduced at the next observed bar open.
    """
    if initial_equity <= 0 or trade_risk_pct <= 0 or portfolio_risk_pct <= 0:
        raise ValueError("Equity and risk percentages must be positive.")
    if spread_price < 0 or slippage_price < 0 or fee_pct < 0:
        raise ValueError("Spread, slippage, and commission cannot be negative.")
    if max_hold_bars < 1 or min_units < 1 or unit_increment < 1:
        raise ValueError("Holding bars and position-size constraints must be positive.")

    required = {"confirmation_time", "direction", "entry", "stop", "target"}
    missing = required.difference(setups.columns)
    if missing:
        raise ValueError(f"Setups are missing required columns: {', '.join(sorted(missing))}")
    if not isinstance(bars.index, pd.DatetimeIndex) or bars.empty:
        raise ValueError("Price bars must have a non-empty DatetimeIndex.")

    bar_lookup = {time: idx for idx, time in enumerate(bars.index)}
    candidates: dict[int, list[int]] = {}
    trade_rows: list[dict] = []
    setup_values: list[dict] = []
    for setup_id, row in setups.reset_index(drop=True).iterrows():
        confirmation_time = pd.Timestamp(row["confirmation_time"])
        if confirmation_time.tzinfo is None:
            confirmation_time = confirmation_time.tz_localize("UTC")
        else:
            confirmation_time = confirmation_time.tz_convert("UTC")
        if confirmation_time not in bar_lookup:
            raise ValueError(f"Setup timestamp {confirmation_time} is not present in price bars.")
        entry_idx = bar_lookup[confirmation_time] + 1
        if entry_idx >= len(bars):
            raise ValueError(f"Setup at {confirmation_time} has no following entry bar.")
        direction = str(row["direction"]).lower()
        if direction not in {"buy", "sell"}:
            raise ValueError(f"Invalid setup direction {direction!r} at {confirmation_time}.")
        stop, target = float(row["stop"]), float(row["target"])
        if not math.isfinite(stop) or not math.isfinite(target):
            raise ValueError(f"Non-finite stop or target at {confirmation_time}.")
        if direction == "buy" and not stop < float(row["entry"]) < target:
            raise ValueError(f"Invalid buy entry/stop/target at {confirmation_time}.")
        if direction == "sell" and not target < float(row["entry"]) < stop:
            raise ValueError(f"Invalid sell entry/stop/target at {confirmation_time}.")

        candidates.setdefault(entry_idx, []).append(setup_id)
        setup_values.append({
            "setup_id": setup_id,
            "confirmation_time": confirmation_time,
            "entry_idx": entry_idx,
            "direction": direction,
            "signal_entry": float(row["entry"]),
            "stop": stop,
            "target": target,
        })
        trade_rows.append({
            "setup_id": setup_id,
            "confirmation_time": confirmation_time,
            "direction": direction,
            "signal_entry": float(row["entry"]),
            "stop": stop,
            "target": target,
            "outcome": "PENDING",
            "entry_time": pd.NaT,
            "exit_time": pd.NaT,
            "entry_fill": float("nan"),
            "units": 0.0,
            "risk_budget_usd": 0.0,
            "net_pnl_usd": 0.0,
            "net_r": float("nan"),
            "bars_held": 0,
        })

    half_spread = spread_price / 2
    cash = float(initial_equity)
    positions: list[dict] = []
    events: list[dict] = []
    equity_rows: list[dict] = []
    pending_breach: dict | None = None
    max_concurrent_positions = 0
    first_bar_time = pd.Timestamp(bars.index[0])
    if first_bar_time.tzinfo is None:
        first_bar_time = first_bar_time.tz_localize("UTC")
    else:
        first_bar_time = first_bar_time.tz_convert("UTC")
    equity_rows.append({
        "timestamp": first_bar_time,
        "phase": "initial",
        "equity_usd": cash,
        "cash_usd": cash,
        "open_risk_usd": 0.0,
        "risk_cap_usd": cash * portfolio_risk_pct,
        "risk_cap_excess_usd": 0.0,
        "open_positions": 0,
    })

    def open_risk() -> float:
        return sum(position["units"] * position["risk_per_unit"] for position in positions)

    def mark_equity(mid_price: float) -> float:
        equity = cash
        for position in positions:
            liquidation_fill = _exit_fill(
                mid_price, position["direction"], half_spread, slippage_price
            )
            sign = 1 if position["direction"] == "buy" else -1
            equity += position["units"] * (
                sign * (liquidation_fill - position["entry_fill"])
                - fee_pct * liquidation_fill
            )
        return equity

    def record_event(event_type: str, timestamp: pd.Timestamp, **values: object) -> None:
        events.append({"event": event_type, "timestamp": timestamp, **values})

    def close_units(
        position: dict,
        units: float,
        fill_level: float,
        timestamp: pd.Timestamp,
        reason: str,
        *,
        forced: bool = False,
    ) -> None:
        nonlocal cash
        units = min(units, position["units"])
        if units <= 0:
            return
        if 0 < position["units"] - units < min_units:
            units = position["units"]
        exit_fill = _exit_fill(fill_level, position["direction"], half_spread, slippage_price)
        sign = 1 if position["direction"] == "buy" else -1
        exit_commission = units * exit_fill * fee_pct
        gross_pnl = units * sign * (exit_fill - position["entry_fill"])
        realized = gross_pnl - exit_commission
        cash += realized
        row = trade_rows[position["setup_id"]]
        row["net_pnl_usd"] += realized
        row["spread_cost_usd"] += units * half_spread
        row["slippage_cost_usd"] += units * slippage_price
        row["commission_usd"] += exit_commission
        position["net_pnl_usd"] += realized
        position["units"] -= units

        if forced:
            record_event(
                "FORCED_REDUCTION" if position["units"] > 0 else "FORCED_CLOSE",
                timestamp,
                setup_id=position["setup_id"],
                units_reduced=units,
                units_remaining=position["units"],
                exit_fill=exit_fill,
                spread_cost_usd=units * half_spread,
                slippage_cost_usd=units * slippage_price,
                commission_usd=exit_commission,
                realized_pnl_usd=realized,
                breach_detected_at=pending_breach["timestamp"] if pending_breach else pd.NaT,
            )

        if position["units"] < min_units:
            position["units"] = 0.0
        if position["units"] == 0:
            row["outcome"] = reason
            row["exit_time"] = timestamp
            row["bars_held"] = int(position["bars_held"])
            row["net_r"] = (
                row["net_pnl_usd"] / row["risk_budget_usd"]
                if row["risk_budget_usd"] > 0 else float("nan")
            )
            position["closed"] = True

    def close_at_open_gaps(idx: int, timestamp: pd.Timestamp) -> None:
        bar = bars.iloc[idx]
        open_mid = float(bar["open"])
        for position in list(positions):
            direction = position["direction"]
            executable_open = open_mid - half_spread if direction == "buy" else open_mid + half_spread
            stop_hit = executable_open <= position["stop"] if direction == "buy" else executable_open >= position["stop"]
            target_hit = executable_open >= position["target"] if direction == "buy" else executable_open <= position["target"]
            if stop_hit:
                close_units(position, position["units"], open_mid, timestamp, "LOSS")
            elif target_hit:
                close_units(position, position["units"], position["target"], timestamp, "WIN")
        positions[:] = [position for position in positions if not position["closed"]]

    def enforce_pending_breach(idx: int, timestamp: pd.Timestamp) -> None:
        nonlocal pending_breach
        if pending_breach is None:
            return
        mid = float(bars.iloc[idx]["open"])
        equity = mark_equity(mid)
        cap = max(0.0, equity) * portfolio_risk_pct
        risk = open_risk()
        if risk <= cap + 1e-8:
            record_event(
                "BREACH_RESOLVED_BEFORE_REDUCTION",
                timestamp,
                detected_at=pending_breach["timestamp"],
                duration_minutes=(timestamp - pending_breach["timestamp"]).total_seconds() / 60,
                excess_risk_at_detection_usd=pending_breach["excess_usd"],
                resolution="positions exited before scheduled reduction",
            )
            pending_breach = None
            return
        excess_at_open = risk - cap

        for _ in range(len(positions) + 2):
            equity = mark_equity(mid)
            cap = max(0.0, equity) * portfolio_risk_pct
            risk = open_risk()
            if risk <= cap + 1e-8 or not positions:
                break
            scale = cap / risk if risk > 0 else 0.0
            before = risk
            for position in list(positions):
                new_units = _floor_increment(position["units"] * scale, unit_increment)
                if new_units < min_units:
                    new_units = 0.0
                units_to_close = position["units"] - new_units
                close_units(position, units_to_close, mid, timestamp,
                            "FORCED_CLOSE" if new_units == 0 else "FORCED_REDUCTION",
                            forced=True)
            positions[:] = [position for position in positions if not position["closed"]]
            if open_risk() >= before - 1e-8 and open_risk() > cap + 1e-8 and positions:
                position = max(positions, key=lambda item: item["units"] * item["risk_per_unit"])
                close_units(position, position["units"], mid, timestamp, "FORCED_CLOSE", forced=True)
                positions[:] = [position for position in positions if not position["closed"]]

        equity = mark_equity(mid)
        cap = max(0.0, equity) * portfolio_risk_pct
        if open_risk() > cap + 1e-8 and positions:
            for position in list(positions):
                close_units(position, position["units"], mid, timestamp, "FORCED_CLOSE", forced=True)
            positions[:] = [position for position in positions if not position["closed"]]

        record_event(
            "BREACH_RESTORED",
            timestamp,
            detected_at=pending_breach["timestamp"],
            duration_minutes=(timestamp - pending_breach["timestamp"]).total_seconds() / 60,
            excess_risk_at_detection_usd=pending_breach["excess_usd"],
            excess_risk_at_open_usd=excess_at_open,
            open_risk_usd=open_risk(),
            cap_usd=max(0.0, mark_equity(mid)) * portfolio_risk_pct,
        )
        pending_breach = None

    def intrabar_exit(idx: int, timestamp: pd.Timestamp) -> None:
        bar = bars.iloc[idx]
        high, low = float(bar["high"]), float(bar["low"])
        for position in list(positions):
            direction = position["direction"]
            if direction == "buy":
                stop_hit = low - half_spread <= position["stop"]
                target_hit = high - half_spread >= position["target"]
            else:
                stop_hit = high + half_spread >= position["stop"]
                target_hit = low + half_spread <= position["target"]
            if stop_hit:
                close_units(position, position["units"], position["stop"], timestamp, "LOSS")
            elif target_hit:
                close_units(position, position["units"], position["target"], timestamp, "WIN")
        positions[:] = [position for position in positions if not position["closed"]]

    for idx, (timestamp, bar) in enumerate(bars.iterrows()):
        timestamp = pd.Timestamp(timestamp)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("UTC")
        else:
            timestamp = timestamp.tz_convert("UTC")

        close_at_open_gaps(idx, timestamp)
        enforce_pending_breach(idx, timestamp)

        for setup_id in candidates.get(idx, []):
            candidate = setup_values[setup_id]
            equity = mark_equity(float(bar["open"]))
            available_risk = equity * portfolio_risk_pct - open_risk()
            intended_risk = equity * trade_risk_pct
            risk_budget = min(intended_risk, available_risk)
            row = trade_rows[setup_id]
            if equity <= 0:
                row["outcome"] = "REJECTED_NONPOSITIVE_EQUITY"
                continue
            if risk_budget <= 0:
                row["outcome"] = "REJECTED_RISK_BUDGET"
                continue

            entry_mid = float(bar["open"])
            entry_fill = _entry_fill(entry_mid, candidate["direction"], half_spread, slippage_price)
            stop_fill = _exit_fill(candidate["stop"], candidate["direction"], half_spread, slippage_price)
            risk_per_unit = abs(entry_fill - stop_fill) + fee_pct * (entry_fill + stop_fill)
            if risk_per_unit <= 0 or not math.isfinite(risk_per_unit):
                row["outcome"] = "REJECTED_INVALID_RISK"
                continue
            liquidation_fill = _exit_fill(
                entry_mid, candidate["direction"], half_spread, slippage_price
            )
            round_trip_cost_per_unit = (
                abs(entry_fill - liquidation_fill)
                + fee_pct * (entry_fill + liquidation_fill)
            )
            units = _floor_increment(risk_budget / risk_per_unit, unit_increment)
            while units >= min_units and (
                units * risk_per_unit > risk_budget
                or open_risk() + units * risk_per_unit
                > portfolio_risk_pct * (equity - units * round_trip_cost_per_unit)
            ):
                units -= unit_increment
            if units < min_units:
                row["outcome"] = "REJECTED_MINIMUM_SIZE"
                row["available_risk_usd"] = available_risk
                continue

            risk_used = units * risk_per_unit
            entry_commission = units * entry_fill * fee_pct
            cash -= entry_commission
            row.update({
                "outcome": "OPEN",
                "entry_time": timestamp,
                "entry_fill": entry_fill,
                "units": units,
                "risk_budget_usd": risk_used,
                "net_pnl_usd": -entry_commission,
                "entry_commission_usd": entry_commission,
                "commission_usd": entry_commission,
                "spread_cost_usd": units * half_spread,
                "slippage_cost_usd": units * slippage_price,
            })
            positions.append({
                **candidate,
                "units": units,
                "entry_fill": entry_fill,
                "entry_fee": entry_commission,
                "risk_per_unit": risk_per_unit,
                "net_pnl_usd": -entry_commission,
                "bars_held": 0,
                "closed": False,
            })
            max_concurrent_positions = max(max_concurrent_positions, len(positions))

        for position in positions:
            position["bars_held"] = idx - position["entry_idx"] + 1
        intrabar_exit(idx, timestamp)

        for position in list(positions):
            if position["bars_held"] >= max_hold_bars:
                close_units(position, position["units"], float(bar["close"]), timestamp, "TIME_STOP")
        positions[:] = [position for position in positions if not position["closed"]]

        equity = mark_equity(float(bar["close"]))
        risk = open_risk()
        cap = max(0.0, equity) * portfolio_risk_pct
        equity_rows.append({
            "timestamp": timestamp,
            "phase": "bar_close",
            "equity_usd": equity,
            "cash_usd": cash,
            "open_risk_usd": risk,
            "risk_cap_usd": cap,
            "risk_cap_excess_usd": max(0.0, risk - cap),
            "open_positions": len(positions),
            "max_concurrent_positions": max_concurrent_positions,
        })
        if pending_breach is not None and risk > cap + 1e-8:
            record_event(
                "CAP_BREACH_STATUS",
                timestamp,
                detected_at=pending_breach["timestamp"],
                duration_minutes=(timestamp - pending_breach["timestamp"]).total_seconds() / 60,
                open_risk_usd=risk,
                risk_cap_usd=cap,
                excess_risk_usd=risk - cap,
            )
        if risk > cap + 1e-8 and pending_breach is None:
            pending_breach = {
                "timestamp": timestamp,
                "excess_usd": risk - cap,
                "risk_usd": risk,
                "cap_usd": cap,
            }
            record_event(
                "CAP_BREACH_DETECTED",
                timestamp,
                open_risk_usd=risk,
                risk_cap_usd=cap,
                excess_risk_usd=risk - cap,
                scheduled_for=bars.index[idx + 1] if idx + 1 < len(bars) else pd.NaT,
            )

    if pending_breach is not None:
        record_event(
            "BREACH_UNRESOLVED_AT_DATA_END",
            pd.Timestamp(bars.index[-1]),
            detected_at=pending_breach["timestamp"],
            duration_minutes=(pd.Timestamp(bars.index[-1]) - pending_breach["timestamp"]).total_seconds() / 60,
            excess_risk_at_detection_usd=pending_breach["excess_usd"],
        )

    last_mid = float(bars.iloc[-1]["close"])
    for position in positions:
        row = trade_rows[position["setup_id"]]
        mark_fill = _exit_fill(last_mid, position["direction"], half_spread, slippage_price)
        sign = 1 if position["direction"] == "buy" else -1
        unrealized = position["units"] * (
            sign * (mark_fill - position["entry_fill"]) - fee_pct * mark_fill
        )
        row.update({
            "outcome": "OPEN",
            "exit_time": pd.NaT,
            "bars_held": position["bars_held"],
            "units_remaining": position["units"],
            "unrealized_pnl_usd": unrealized,
            "net_r": float("nan"),
        })

    equity_curve = pd.DataFrame(equity_rows)
    if not equity_curve.empty:
        equity_curve["peak_equity_usd"] = equity_curve["equity_usd"].cummax().clip(lower=0)
        equity_curve["drawdown_usd"] = equity_curve["peak_equity_usd"] - equity_curve["equity_usd"]
        equity_curve["drawdown_pct"] = (
            equity_curve["drawdown_usd"] / equity_curve["peak_equity_usd"].replace(0, float("nan"))
        )
    event_frame = pd.DataFrame(events)
    if event_frame.empty:
        event_frame = pd.DataFrame(columns=["event", "timestamp"])
    return pd.DataFrame(trade_rows), event_frame, equity_curve


def portfolio_metrics(trades: pd.DataFrame, equity_curve: pd.DataFrame, initial_equity: float) -> dict:
    closed = trades[trades["outcome"].isin(["WIN", "LOSS", "TIME_STOP", "FORCED_CLOSE"])]
    positives = closed[closed["net_pnl_usd"] > 0]
    negatives = closed[closed["net_pnl_usd"] < 0]
    ending_equity = float(equity_curve["equity_usd"].iloc[-1]) if not equity_curve.empty else initial_equity
    max_drawdown_usd = float(equity_curve["drawdown_usd"].max()) if not equity_curve.empty else 0.0
    max_drawdown_pct = float(equity_curve["drawdown_pct"].max()) if not equity_curve.empty else 0.0
    risk_adjusted = closed["net_r"].dropna()
    gross_profit = positives["net_pnl_usd"].sum()
    gross_loss = abs(negatives["net_pnl_usd"].sum())
    return {
        "setups": len(trades),
        "accepted": int(trades["outcome"].isin(["OPEN", "WIN", "LOSS", "TIME_STOP", "FORCED_CLOSE"]).sum()),
        "rejected": int(trades["outcome"].str.startswith("REJECTED").sum()),
        "closed": len(closed),
        "open_at_data_end": int(trades["outcome"].eq("OPEN").sum()),
        "positive_net_trades": len(positives),
        "win_rate": len(positives) / len(closed) if len(closed) else float("nan"),
        "expectancy_net_usd": float(closed["net_pnl_usd"].mean()) if len(closed) else float("nan"),
        "expectancy_net_r": float(risk_adjusted.mean()) if len(risk_adjusted) else float("nan"),
        "profit_factor": gross_profit / gross_loss if gross_loss else float("inf"),
        "ending_equity_usd": ending_equity,
        "net_return_pct": (ending_equity / initial_equity - 1) * 100,
        "max_drawdown_usd": max_drawdown_usd,
        "max_drawdown_pct": max_drawdown_pct * 100,
        "max_concurrent_positions": (
            int(equity_curve["max_concurrent_positions"].max())
            if not equity_curve.empty else 0
        ),
        "gross_profit_usd": float(gross_profit),
        "gross_loss_usd": float(gross_loss),
    }
