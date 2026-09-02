# Swing / Fib-discount strategy — paper trading playbook

Locked rule set, validated on GBP/USD 4H (Bitstamp, 2020-06 → 2026-09).
This is the version to trade on demo — nothing here is validated for
real money yet. See "Honest limits" at the bottom before you trust it.

---

## What was actually tested

| | Development (2020–2023) | Out-of-sample (2024–2026) |
|---|---|---|
| Closed trades | 24 | 16 |
| Win rate | 45.8% | 43.8% |
| Expectancy (after costs) | +0.30R | **+0.23R** |

Costs assumed: 2 pips slippage + 0.02% fee per side. The out-of-sample
period was touched exactly once, after every parameter was already
locked in on the development period — that's what makes this number
worth something.

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
below/above entry, minus/plus **0.75× ATR(14)**. This buffer was the
single most important thing this whole process found — tight stops
(under ~20 pips on this market) had a 5% win rate; this wider buffer is
what makes the strategy survive.

**7. Take-profit.** Entry ± 2× the stop distance (1:2 R:R).

**8. Time-stop.** If neither SL nor TP is hit within 60 bars (10 days on
4H), the trade is not a signal you should trust anymore — reassess.

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
- [ ] Position size = (account × 1% risk) / (entry − stop)
- [ ] Log it: date, market, direction, entry, stop, target, R:R, and outcome once known

Do not skip logging the losses. A log with only wins isn't a log.

---

## Applying this to a different market or timeframe

The rules above are written in **relative terms on purpose** — ATR
multiples instead of fixed pips, percentage-of-range instead of a fixed
candle size. That's what makes them plausible as a starting point
elsewhere. It does **not** mean they're proven elsewhere.

Steps to actually validate on a new chart:

1. Export that market's OHLCV as CSV, same way as before (`⋯ → Export
   chart data` on TradingView).
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

- **16 out-of-sample trades is a small sample.** A couple of trades
  going the other way moves the win rate by 6–12 points. This is
  "didn't fall apart," not "proven."
- **Only the stop-loss buffer was rigorously tuned** with a dev/test
  split. The retracement count, doji threshold, and engulfing
  definition were each picked once and never stress-tested the same
  way — any of them could be doing more work than they should.
- **Costs used here are estimates**, not your actual broker's numbers.
  Update `slippage_price` and `fee_pct` in the config to match your
  real spread and commission before trusting the expectancy figure.
- **This has never seen a live/forward-moving market.** Paper trading
  it now is the point — not a formality before "real" testing, but the
  actual next real test.
