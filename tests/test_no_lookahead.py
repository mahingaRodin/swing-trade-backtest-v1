import numpy as np
import pandas as pd

from quant.levels import find_levels
from quant.swings import detect_swings
from scripts.scan_setups import levels_known_at

N = 5          # swing_n
TOL = 0.15     # sl_tolerance_atr


def make_prices(n_bars=400, seed=7):
    """Reproducible random-walk OHLC. Look-ahead tests only need *some* realistic wiggles."""
    rng = np.random.default_rng(seed)
    close = 1.30 + np.cumsum(rng.normal(0, 0.002, n_bars))
    spread = np.abs(rng.normal(0.001, 0.0003, n_bars))
    return pd.DataFrame(
        {"open": close - 0.0002, "high": close + spread, "low": close - spread,
         "close": close, "volume": 1.0},
        index=pd.date_range("2024-01-01", periods=n_bars, freq="4h", tz="UTC"),
    )


def test_swing_flags_do_not_change_when_future_bars_are_removed():
    # If a flag at bar i really only needs n bars after it, then chopping the file
    # at K must not change any flag at bars that already had their n confirming bars.
    df = make_prices()
    full = detect_swings(df, N)
    cols = ["swing_high", "swing_low"]
    for k in range(60, len(df), 7):
        truncated = detect_swings(df.iloc[:k], N)
        assert (full.iloc[:k - N][cols].values == truncated.iloc[:k - N][cols].values).all()


def test_levels_at_bar_c_use_only_swings_confirmed_by_c():
    # The decisive test: levels built the way the scanner builds them must equal levels
    # built from a file that literally ends at bar c (so the future cannot leak in).
    df = make_prices()
    full = detect_swings(df, N)
    for c in range(80, len(df) - 1, 3):
        expected = find_levels(detect_swings(df.iloc[:c + 1], N), tolerance_atr_mult=TOL)
        got = levels_known_at(full, c, N, TOL)
        assert got == expected, f"look-ahead at bar {c}"