import pandas as pd

from quant.portfolio import run_portfolio_backtest


def market_bars(rows):
    return pd.DataFrame(
        rows,
        columns=["open", "high", "low", "close"],
        index=pd.date_range("2024-01-01", periods=len(rows), freq="4h", tz="UTC"),
    )


def setup_rows(times, *, stop=1.29, target=1.32):
    return pd.DataFrame({
        "confirmation_time": times,
        "direction": ["buy"] * len(times),
        "entry": [1.30] * len(times),
        "stop": [stop] * len(times),
        "target": [target] * len(times),
    })


def test_open_risk_cap_rejects_overlapping_full_risk_trade():
    bars = market_bars([(1.30, 1.301, 1.299, 1.30)] * 6)
    setups = setup_rows([bars.index[0], bars.index[1]])

    trades, _, equity = run_portfolio_backtest(
        bars, setups, spread_price=0, slippage_price=0, fee_pct=0,
        max_hold_bars=3, initial_equity=10_000.01,
    )

    assert trades.loc[0, "outcome"] == "TIME_STOP"
    assert trades.loc[0, "units"] % 1000 == 0
    assert trades.loc[0, "risk_budget_usd"] <= 100.001
    assert trades.loc[1, "outcome"].startswith("REJECTED")
    assert abs(trades.net_pnl_usd.sum() - (equity.equity_usd.iloc[-1] - 10_000.01)) < 1e-8


def test_higher_spread_reduces_position_size_and_all_in_risk_stays_capped():
    bars = market_bars([(1.30, 1.301, 1.299, 1.3005)] * 3)
    setups = setup_rows([bars.index[0]], stop=1.297, target=1.306)

    narrow, _, _ = run_portfolio_backtest(
        bars, setups, spread_price=0.00005, slippage_price=0.0002,
        fee_pct=0.0002, max_hold_bars=1,
    )
    wide, _, _ = run_portfolio_backtest(
        bars, setups, spread_price=0.0002, slippage_price=0.0002,
        fee_pct=0.0002, max_hold_bars=1,
    )

    assert wide.loc[0, "units"] < narrow.loc[0, "units"]
    assert wide.loc[0, "risk_budget_usd"] <= 100
    assert narrow.loc[0, "risk_budget_usd"] <= 100


def test_cap_breach_scales_down_at_next_bar_open_and_logs_costs():
    bars = market_bars([
        (1.30, 1.301, 1.299, 1.30),
        (1.30, 1.301, 1.299, 1.30),
        (1.30, 1.301, 1.2902, 1.2902),
        (1.2902, 1.300, 1.2902, 1.2903),
        (1.2903, 1.300, 1.2902, 1.295),
    ])
    setups = setup_rows([bars.index[0]])

    trades, events, equity = run_portfolio_backtest(
        bars, setups, spread_price=0, slippage_price=0, fee_pct=0,
        max_hold_bars=4, initial_equity=10_000.01,
    )

    breach = events[events.event == "CAP_BREACH_DETECTED"]
    reduction = events[events.event == "FORCED_REDUCTION"]
    assert len(breach) == 1
    assert len(reduction) == 1
    assert reduction.iloc[0].timestamp == bars.index[3]
    assert reduction.iloc[0].units_reduced == 1000
    assert reduction.iloc[0].spread_cost_usd == 0
    after_reduction = equity[
        (equity.timestamp == bars.index[3]) & (equity.phase == "bar_close")
    ].iloc[0]
    assert after_reduction.open_risk_usd <= after_reduction.risk_cap_usd
    assert trades.loc[0, "outcome"] == "TIME_STOP"
