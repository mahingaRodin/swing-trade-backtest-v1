# Swing-Trade Backtest

Research code for scanning and backtesting a rules-based swing/Fibonacci
strategy on historical OHLCV data. The current example is GBP/USD on 4-hour
Bitstamp candles.

> **Research only.** This project is not financial advice or a trading
> recommendation. Historical backtests do not predict future results. The
> current out-of-sample portfolio result is negative, the sample is small,
> and spread/slippage assumptions are estimates. Do not use these results as
> a reason to trade real money.

## What the project does

- Loads and validates exported OHLCV CSV data.
- Detects confirmed swing points and scans for the configured retracement,
  Fibonacci-zone, and candle-confirmation setup.
- Builds structural stops from confirmed support/resistance, then evaluates
  outcomes using stop, target, and time-stop rules.
- Simulates a shared USD portfolio with configurable position risk, an
  aggregate open-risk cap, transaction costs, spread scenarios, and forced
  reductions when the risk cap is breached.
- Writes setup, trade, event, equity-curve, and sensitivity CSV reports.

See [research/paper_trading_playbook.md](research/paper_trading_playbook.md)
for the full strategy definition, assumptions, result interpretation, and
paper-trading checklist.

## Project layout

| Path | Purpose |
|---|---|
| `quant/` | Data cleaning, swing/level/pattern logic, and portfolio simulation |
| `scripts/scan_setups.py` | Find setups and calculate stand-alone outcomes |
| `scripts/backtest_portfolio.py` | Run portfolio and spread-sensitivity scenarios |
| `config/base.yaml` | Current GBP/USD 4-hour strategy and cost settings |
| `config/TEMPLATE.yaml` | Starting point for a separate market/timeframe config |
| `tests/` | Regression tests for data, look-ahead behavior, outcomes, and portfolio logic |
| `research/paper_trading_playbook.md` | Methodology, current reference results, and limitations |

## Requirements and setup

Use Python and PowerShell on Windows. From the repository root:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

If the virtual environment already exists, skip the first command. The
repository includes a pinned `requirements.txt`; installing it may install
more packages than the backtest scripts alone require.

Run the test suite:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The current expected result is **24 passed, 2 xfailed**. The expected failures
are documented in their tests; an `xfail` is not a passing validation of the
underlying behavior.

## Prepare historical data

Export chart data from TradingView as CSV with OHLCV columns. The current
loader supports the project's TradingView/newApp-style export format,
including a `time` timestamp column and `open`, `high`, `low`, `close`, and
`Volume` columns. Keep the feed, symbol, and timeframe consistent between
files being compared. For the reference run, use `BITSTAMP:GBPUSD` on the
4-hour chart.

Save private or large data files under `t-view/` or `data/`. CSV and common
data artifacts are excluded from Git by `.gitignore`; generated research
outputs are therefore local files, not part of a commit.

Before relying on a run, check the export's first and last timestamps, candle
ordering, duplicate timestamps, and gaps. The loader reports data issues and
does not invent missing bars. When testing a new period, use a combined file
with enough earlier history to calculate indicators and confirm swings.

## Reproduce the reference analysis

The commands below use the checked-in GBP/USD configuration and a local
TradingView export. Change the data filename only if yours differs.

### 1. Scan setups

```powershell
.\.venv\Scripts\python.exe scripts\scan_setups.py `
  --config config\base.yaml `
  --file "t-view\BITSTAMP_GBPUSD, 240_e573d.csv" `
  --out research\after_time_stop.csv
```

This writes a setup/outcome log. In the current configuration the scan uses
a 0.75 ATR stop buffer, 2:1 target-to-risk, a 60-bar time-stop, 0.0002
adverse slippage per fill, and 0.02% commission per side. A time-stop exits
at the 60th bar's close; a setup without enough later bars remains open.

### 2. Run portfolio and spread scenarios

```powershell
.\.venv\Scripts\python.exe scripts\backtest_portfolio.py `
  --config config\base.yaml `
  --file "t-view\BITSTAMP_GBPUSD, 240_e573d.csv" `
  --setups research\after_time_stop.csv `
  --out-dir research\portfolio\reference `
  --scenario-name full
```

By default this runs 0.5-, 1-, and 2-pip full-spread scenarios. The portfolio
defaults are a $10,000 starting account, 1% target risk per trade, a 1%
aggregate open-risk cap, and position sizes rounded down to 1,000 GBP units.
Output files are written beneath
`research\portfolio\reference\full\`:

- `sensitivity_summary.csv` — one row per spread scenario.
- `trades_*.csv` — accepted/rejected trades, fills, outcomes, and P&L.
- `events_*.csv` — risk-cap breaches, reductions, and related execution events.
- `equity_*.csv` — close-to-close marked account equity.

Use a new `--out-dir` for each run to avoid confusing results from different
data or assumptions.

### 3. Compare development and out-of-sample periods

The date filters below mirror the existing historical split. Development
price data is truncated at its end date to avoid using later prices in that
run. The out-of-sample period should not be used to choose or tune parameters
if it is being treated as a holdout.

```powershell
.\.venv\Scripts\python.exe scripts\backtest_portfolio.py `
  --config config\base.yaml `
  --file "t-view\BITSTAMP_GBPUSD, 240_e573d.csv" `
  --setups research\after_time_stop.csv `
  --out-dir research\portfolio\reference `
  --scenario-name dev_2020_2023 `
  --signal-start-date 2020-06-01 `
  --signal-end-date 2023-12-31 `
  --data-end-date 2023-12-31

.\.venv\Scripts\python.exe scripts\backtest_portfolio.py `
  --config config\base.yaml `
  --file "t-view\BITSTAMP_GBPUSD, 240_e573d.csv" `
  --setups research\after_time_stop.csv `
  --out-dir research\portfolio\reference `
  --scenario-name oos_2024_2026 `
  --signal-start-date 2024-01-01 `
  --signal-end-date 2026-09-02 `
  --data-end-date 2026-09-02
```

Each scenario starts with a separate account. Date filters restrict which
setups are simulated; the data-end filter limits the bars available to
simulate their outcomes.

## Test on new, unseen data

To make a meaningful forward-period backtest:

1. Keep the strategy parameters unchanged. Export fresh candles for the same
   symbol, feed, and timeframe.
2. Append the new candles to a copy of the existing history. Keep sufficient
   older bars for swing and indicator context; verify timestamps and remove
   duplicates deliberately.
3. Run `scan_setups.py` on that combined history and save to a new setup-log
   filename.
4. Run `backtest_portfolio.py` with `--signal-start-date` at the start of the
   new period and `--signal-end-date` at the evaluation cutoff. Set
   `--data-end-date` to the last available bar so trades cannot use prices
   beyond the cutoff.
5. For a test focused on completed trades, set the signal end date far enough
   before the last data date for trades to finish—up to 60 four-hour bars
   (about 10 days) under the current time-stop rule. Otherwise recent trades
   may remain open.
6. Save outputs to a new directory. Review trade counts, costs, expectancy,
   profit factor, account return, drawdown, rejections, and risk events.

A small number of trades is inconclusive. Do not repeatedly adjust parameters
against the same holdout and continue calling it unseen data. If rules are
changed, record the new version and reserve a later period for its evaluation.

## Important modeling assumptions and limitations

- OHLC is treated as midpoint pricing. Historical bid/ask quotes are not
  included; 0.5/1/2-pip spreads are illustrative scenarios.
- Slippage is fixed in raw price units and applied adversely on each fill;
  commission is charged on each side. These values should be replaced with
  broker-specific assumptions where reliable data is available.
- The portfolio simulator is tailored to GBP/USD unit sizing and this
  example's assumptions. Do not apply its portfolio results to another asset
  without checking contract sizing, pip value, costs, and risk conversion.
- A bar that reaches both stop and target is treated as a stop first. Risk
  cap breaches observed at a candle close are handled at the next observed
  bar open, not intrabar.
- Drawdown is calculated from 4-hour close-to-close marked equity and may
  understate intrabar drawdown.
- The corrected reference out-of-sample sample has only 22 trades and
  negative expectancy in the model. These results are uncertain and are not
  evidence of a profitable strategy.

For full assumptions and the current reference tables, see the
[research playbook](research/paper_trading_playbook.md).
