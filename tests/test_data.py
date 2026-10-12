from pathlib import Path

import pandas as pd
import pytest

from quant.data import (DataConfig, _infer_tradingview_timeframe, clean_ohlcv,
                        detect_gaps, load_tradingview_csv)

T0 = 1_704_067_200          # 2024-01-01 00:00:00 UTC in Unix seconds
H4 = 4 * 3600


def raw_frame(times, **overrides):
    """A small TradingView-style frame (Unix 'time', capital-V 'Volume', one extra column)."""
    n = len(times)
    df = pd.DataFrame({
        "time": times,
        "open": [1.27] * n, "high": [1.28] * n, "low": [1.26] * n, "close": [1.275] * n,
        "Volume": [100.0] * n,
        "RSI": [50.0] * n,
    })
    for col, values in overrides.items():
        df[col] = values
    return df


# ---------- loading ----------

def test_tradingview_csv_loads_with_utc_index_and_keeps_extras(tmp_path):
    path = tmp_path / "TEST_GBPUSD, 240_abc12.csv"
    raw_frame([T0, T0 + H4, T0 + 2 * H4]).to_csv(path, index=False)

    df = load_tradingview_csv(path)

    assert str(df.index.tz) == "UTC"
    assert df.index[0] == pd.Timestamp("2024-01-01 00:00", tz="UTC")
    assert list(df.columns[:5]) == ["open", "high", "low", "close", "volume"]
    assert "rsi" in df.columns            # indicator columns are preserved (lower-cased)


# ---------- timeframe inference ----------

def test_timeframe_inferred_from_tradingview_filename():
    assert _infer_tradingview_timeframe(Path("BITSTAMP_GBPUSD, 240_e573d.csv")) == "4h"
    assert _infer_tradingview_timeframe(Path("OANDA_GBPUSD, 60_ab12c.csv")) == "1h"
    assert _infer_tradingview_timeframe(Path("OANDA_GBPUSD, 15_ab12c.csv")) == "15m"


@pytest.mark.xfail(reason="Regex needs '_' or '.' after the number; a name like "
                          "'..., 240.csv' (no hash) falls back to '1d'.")
def test_timeframe_inferred_when_filename_has_no_hash():
    assert _infer_tradingview_timeframe(Path("OANDA_GBPUSD, 240.csv")) == "4h"


# ---------- cleaning ----------

def test_duplicate_timestamps_are_dropped_and_counted():
    raw = raw_frame([T0, T0 + H4, T0 + H4, T0 + 2 * H4])
    raw["time"] = pd.to_datetime(raw["time"], unit="s", utc=True)
    df, report = clean_ohlcv(raw, timeframe="4h")
    assert len(df) == 3
    assert report.n_duplicates_dropped == 1


def test_row_with_high_below_low_is_dropped_and_counted():
    raw = raw_frame([T0, T0 + H4, T0 + 2 * H4], high=[1.28, 1.20, 1.28])   # row 2: high < low
    raw["time"] = pd.to_datetime(raw["time"], unit="s", utc=True)
    df, report = clean_ohlcv(raw, timeframe="4h")
    assert len(df) == 2
    assert report.n_invalid_ohlc_dropped == 1


def test_missing_bar_is_reported_not_invented():
    raw = raw_frame([T0, T0 + H4, T0 + 3 * H4])       # the T0 + 2*H4 bar is missing
    raw["time"] = pd.to_datetime(raw["time"], unit="s", utc=True)
    df, report = clean_ohlcv(raw, timeframe="4h")
    assert len(df) == 3                                # no filler row was created
    assert report.n_gaps == 1
    assert report.gaps[0].missing_bars_estimate == 1


def test_detect_gaps_on_a_regular_index_finds_nothing():
    idx = pd.date_range("2024-01-01", periods=10, freq="4h", tz="UTC")
    assert detect_gaps(idx, "4h", DataConfig()) == []