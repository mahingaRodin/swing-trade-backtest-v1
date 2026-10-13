# Swing / Fib-discount strategy — research and paper-trading playbook

GBP/USD 4H research on Bitstamp data (2020-06 → 2026-09). The corrected
portfolio rerun below is a historical simulation under explicit cost and
position-sizing assumptions, not evidence of a reliable trading edge. Do
not use it with real money.

---

## Corrected portfolio rerun

Results from the current 51-setup signal log, using the 1-pip spread case
from `scripts/backtest_portfolio.py`:

| Period | Setups | Accepted / closed | Positive trades | Net expectancy | Profit factor | Return on $10,000 | Max drawdown |
|---|---:|---:|---:|---:|---:|---:|---:|
| Full history (2020–2026) | 51 | 48 / 48 | 41.7% | +0.015R | 0.958 | -1.10% | 8.23% |
| Development (through 2023) | 29 | 26 / 26 | 46.2% | +0.159R | 1.188 | +2.38% | 4.37% |
| Out-of-sample (2024 onward) | 22 | 22 / 22 | 36.4% | -0.158R | 0.730 | -3.63% | 6.42% |

The original playbook figures (24 development trades at +0.30R and 16
out-of-sample trades at +0.23R) did not reproduce from the corrected
signal log and portfolio procedure. Treat those earlier figures as stale;
the exact old filtering/evaluation procedure has not been recovered.
No strategy parameters were retuned for this rerun.

Full-history spread sensitivity, with the same 2-pip slippage per fill and
0.02% commission per side:

| Assumed full spread | Accepted / rejected | Net expectancy | Profit factor | Max drawdown |
|---:|---:|---:|---:|---:|
| 0.5 pip | 48 / 3 | +0.019R | 0.955 | 8.28% |
| 1.0 pip | 48 / 3 | +0.015R | 0.958 | 8.23% |
| 2.0 pips | 47 / 4 | -0.035R | 0.915 | 8.81% |

Expectancy is mean net P&L divided by each accepted position's cost-inclusive
initial stop-risk budget. Returns and drawdowns are from the dynamically
sized $10,000 USD account. The split runs start with separate $10,000
accounts; development price data is truncated at 2023-12-31 so its trades
cannot use later prices.

---

## The rules, exactly as coded

**1. Direction filter.** Only trade in the direction of the most recent
confirmed swing leg (bullish leg → buy setups only; bearish leg → sell
setups only). A swing is "confirmed" once 5 bars have closed on both
sides of it (`swing_n = 5`).

**2. Zone.** Price must be in the discount zone (below the 50% Fib
level, measured from the confirmed leg) for a buy, or premium zone
(above 50%) for a sell.

**3. Retracement.** Exactly 3 consecutive candles against the leg
direction (3 red for a buy, 3 green for a sell) immediately before the
confirmation candle. Any doji (body < 10% of the bar's range) breaks
the sequence — start counting over.

**4. Confirmation.** An engulfing candle: closes beyond the open of the
prior candle, and opens at or inside the prior candle's close.

**5. Entry.** The open of the candle immediately after confirmation
closes. Never enter on the confirmation candle itself.

**6. Stop-loss.** Nearest confirmed support (buy) or resistance (sell)
below/above entry, minus/plus **0.75× ATR(14)**. Earlier parameter
comparisons favored a wider buffer, but the corrected out-of-sample
portfolio result is negative; this setting is not proven robust.

**7. Take-profit.** Entry ± 2× the stop distance (1:2 R:R).

**8. Time-stop.** If neither SL nor TP is hit within 60 bars (10 days on
4H), close at the 60th bar's close. The backtest applies the configured
adverse exit slippage and round-trip fees to that close. If the data ends
before 60 bars are available, the trade is still open and is excluded from
closed-trade metrics.

**9. Support/resistance.** A level needs ≥2 confirmed swing touches
within 0.15× ATR of each other to count. A single touch is not support.

---

## Paper-trading checklist, per setup

- [ ] Confirmed swing leg identified — write down direction, start price, end price
- [ ] Price in discount/premium zone — write down the 50% level
- [ ] 3 clean retracement candles, no doji — check each one's open/close
- [ ] Confirmation candle closed (not just forming) — check the engulfing condition explicitly
- [ ] Support/resistance level identified with ≥2 touches
- [ ] Entry, stop, target calculated **before** placing the trade
- [ ] Position size respects the 1% aggregate open-risk cap, includes
      expected spread, slippage and commissions, and is rounded down to
      the broker's valid size increment
- [ ] Log it: date, market, direction, entry, stop, target, R:R, and outcome once known

Do not skip logging the losses. A log with only wins isn't a log.

## Portfolio backtest assumptions

`scripts/backtest_portfolio.py` applies the scanner's entries and structural
stops to a shared USD account. It is a scenario model, not broker execution
history or investment advice.

- Starting equity is $10,000 USD. Each accepted setup targets 1% of current
  equity, limited by the remaining 1% aggregate open-stop-risk cap. Position
  sizes are rounded down to 1,000 GBP units (0.01 standard lots); trades below
  that minimum are rejected.
- Because the ordinary trade target equals the whole portfolio cap, an
  already-open full-risk trade will normally leave no room for another
  setup. Overlapping signals are rejected unless price/equity changes or
  size rounding leaves valid risk capacity.
- Entries use the next 4-hour bar's open, not the scanner CSV's pre-cost
  outcome or pre-cost R multiple. With GBP/USD, one GBP unit of position size
  gains or loses one USD for each 1.0 change in the quote price; the 100,000
  GBP standard-lot contract and 1,000 GBP minimum are assumptions.
- GBP/USD positions are counted as gross exposure. Long and short positions
  are not netted, and all positions in this one pair share the same risk cap.
  This does not model cross-pair correlation or diversification.
- newApp OHLC values are treated as mid prices because historical
  bid/ask quotes are unavailable. Full spread widths of 0.5, 1, and 2 pips
  are illustrative assumptions, split equally across entry and exit. The
  configured 0.0002 price-unit slippage is adverse on each fill; the
  configured 0.02% commission is charged on each side. Spread, slippage, and
  commission are applied once per fill, not added again as a separate
  adjustment to the scanner's results.
- Position sizing includes the entry-to-stop price loss, expected adverse
  exit slippage, spread at both fills, and exit commission. Entry commission
  reduces account equity when the position opens. Open trade risk is the
  gross sum of those per-position stop risks.
- Stops and targets are checked against each 4-hour bar's range. If both
  trigger in one bar, the stop is assumed first. A gap through a stop is
  filled from the bar open with adverse spread and slippage. Time-stops exit
  at the 60th bar's close with the same exit costs.
- If a bar-close mark shows open stop risk above the account's 1% cap, the
  simulator logs the breach and schedules a reduction for the next available
  bar's open. It rechecks stops, targets, and the risk budget at that open,
  rounds reductions down to valid 1,000 GBP increments, and closes a position
  if that is necessary to restore compliance. New trades are blocked until
  the cap is restored. The breach can therefore persist until the next
  observed bar; this is a bar-based approximation, not immediate or
  intrabar enforcement. Breach duration, excess risk, reduction fills, and
  costs are written to the event log.
- Expectancy is reported as mean net R and mean net USD per closed position.
  Profit factor uses closed-position net USD. Maximum drawdown is calculated
  from 4-hour close-to-close marked equity, so intrabar drawdown may be larger.
  Positions still open at the data end remain marked to market and are not
  counted as closed trades.

Run the spread sensitivity analysis after generating the corrected signal
log:

```powershell
python scripts\scan_setups.py --config config\base.yaml --file "t-view\BITSTAMP_GBPUSD, 240_e573d.csv" --out research\after_time_stop.csv
python scripts\backtest_portfolio.py --config config\base.yaml --file "t-view\BITSTAMP_GBPUSD, 240_e573d.csv" --setups research\after_time_stop.csv --scenario-name full
python scripts\backtest_portfolio.py --config config\base.yaml --file "t-view\BITSTAMP_GBPUSD, 240_e573d.csv" --setups research\after_time_stop.csv --scenario-name dev_2020_2023 --signal-start-date 2020-06-01 --signal-end-date 2023-12-31 --data-end-date 2023-12-31
python scripts\backtest_portfolio.py --config config\base.yaml --file "t-view\BITSTAMP_GBPUSD, 240_e573d.csv" --setups research\after_time_stop.csv --scenario-name oos_2024_2026 --signal-start-date 2024-01-01 --signal-end-date 2026-09-02
```

Each spread scenario writes its trade ledger, event ledger, and close-to-close
equity curve under `research/portfolio/full/`, plus a sensitivity summary.
The split scenarios are saved under their own folders. Review the rejection
and forced-reduction logs before interpreting portfolio metrics. The
per-trade scanner statistics and the portfolio simulation answer different
questions and should not be compared as if they were the same backtest.

---

## Applying this to a different market or timeframe

The rules above are written in **relative terms on purpose** — ATR
multiples instead of fixed pips, percentage-of-range instead of a fixed
candle size. That's what makes them plausible as a starting point
elsewhere. It does **not** mean they're proven elsewhere.

Steps to actually validate on a new chart:

1. Export that market's OHLCV as CSV, same way as before (`⋯ → Export
   chart data` on newApp).
2. Copy `config/TEMPLATE.yaml` to `config/<symbol>_<timeframe>.yaml`.
   Change `slippage_price` to something sane for that market's price
   units (2 pips ≈ 0.0002 for GBPUSD; that number means nothing for
   gold or BTC — see the note in the template).
3. Run the scanner: `python scripts/scan_setups.py --config
   config/<your_file>.yaml --file data/<your_market>.csv`
4. **Do not just look at the combined result.** Split into a
   development period and an out-of-sample period again, tune nothing
   on the out-of-sample slice, and check whether the expectancy holds
   up the way it did here. If it falls apart, that's real information
   — it means these specific parameters were tuned to GBPUSD's
   volatility character, not to something universal.
5. Only promote a market to paper trading once it's passed its own
   out-of-sample check — inheriting GBPUSD's good result is not the
   same as earning one.

---

## Honest limits — read this before trading anything on it

- **The corrected out-of-sample sample is only 22 trades** and has
  negative expectancy in this model. It is not proof of profitability;
  a few outcomes can still move the estimate substantially.
- **Only the stop-loss buffer was rigorously tuned** with a dev/test
  split. The retracement count, doji threshold, and engulfing
  definition were each picked once and never stress-tested the same
  way — any of them could be doing more work than they should.
- **Costs used here are estimates**, not historical bid/ask or your
  broker's execution. The 0.5/1/2-pip spreads are scenarios; replace
  them with broker-specific history/specifications when available.
- **This has never seen a live/forward-moving market.** Paper trading
  it now is the point — not a formality before "real" testing, but the
  actual next real test.
