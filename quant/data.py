"""
OHLCV loading and cleaning for swing-trading research.

Responsibilities:
  - Load one (symbol, timeframe) file from CSV or Parquet
  - Enforce a consistent schema and timezone-aware DatetimeIndex
  - Drop duplicate timestamps (logged)
  - Detect missing bars / irregular gaps (logged; never fabricated)
  - Optionally drop rows that violate basic OHLC integrity

Look-ahead note: this module only cleans historical bars. It does not
compute features or signals. Downstream code must still respect bar-close
timing when using the cleaned frame.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

OHLCV_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")

# Maps timeframe labels used in filenames / configs to pandas offsets.
# All magic intervals live here (or in DataConfig), never inline at call sites.
TIMEFRAME_TO_OFFSET: dict[str, str] = {
    "1m": "1min",
    "3m": "3min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "1h": "1h",
    "2h": "2h",
    "4h": "4h",
    "6h": "6h",
    "12h": "12h",
    "1d": "1D",
    "1w": "1W",
}


@dataclass
class DataConfig:
    """
    Tunables for OHLCV load/clean. Pass an instance (or load from YAML later);
    do not hardcode these values at call sites.

    Inputs / fields:
        timestamp_col: Column name to promote to the index if present.
        required_columns: Lowercased OHLCV column names that must exist.
        duplicate_keep: Which row to keep when timestamps collide.
        gap_factor: A bar-to-bar delta larger than
            ``gap_factor * expected_bar_duration`` is reported as a gap.
            1.0 flags any late bar; 1.5 tolerates small exchange jitter.
        raise_on_gaps: If True, raise after detecting one or more gaps.
        raise_on_duplicates: If True, raise when duplicates are found
            (before dropping). Useful for strict raw-data audits.
        validate_ohlc: If True, check high/low vs open/close consistency
            and non-positive prices/volume.
        drop_invalid_ohlc: If True, drop failing OHLC rows; else raise.
        expect_24_7: If True (crypto), weekend/holiday holes count as gaps.
            If False (equity/FX cash sessions), gaps spanning Saturdays /
            Sundays are ignored for daily-and-coarser bars only.
        timezone: Target timezone for the index. Naive timestamps are
            localized; aware timestamps are converted.
    """

    timestamp_col: str = "timestamp"
    required_columns: tuple[str, ...] = OHLCV_COLUMNS
    duplicate_keep: Literal["first", "last"] = "last"
    gap_factor: float = 1.5
    raise_on_gaps: bool = False
    raise_on_duplicates: bool = False
    validate_ohlc: bool = True
    drop_invalid_ohlc: bool = True
    expect_24_7: bool = True
    timezone: str = "UTC"


@dataclass
class GapRecord:
    """One missing-bar stretch between two consecutive present bars."""

    left_timestamp: pd.Timestamp
    right_timestamp: pd.Timestamp
    expected_delta: pd.Timedelta
    actual_delta: pd.Timedelta
    missing_bars_estimate: int


@dataclass
class CleaningReport:
    """
    Audit trail for a single load/clean pass.

    Outputs:
        source: Path that was loaded (if any).
        n_rows_loaded: Row count immediately after read, before cleaning.
        n_rows_final: Row count after all drops.
        n_duplicates_dropped: Timestamps removed as duplicates.
        n_invalid_ohlc_dropped: Rows removed for OHLC integrity failures.
        gaps: Detected irregular intervals (never auto-filled).
        messages: Human-readable log lines mirroring logger output.
        dropped_duplicate_timestamps: Timestamps that had extras removed.
        dropped_invalid_ohlc_timestamps: Timestamps dropped for bad OHLC.
    """

    source: str | None = None
    n_rows_loaded: int = 0
    n_rows_final: int = 0
    n_duplicates_dropped: int = 0
    n_invalid_ohlc_dropped: int = 0
    gaps: list[GapRecord] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    dropped_duplicate_timestamps: list[pd.Timestamp] = field(default_factory=list)
    dropped_invalid_ohlc_timestamps: list[pd.Timestamp] = field(default_factory=list)

    @property
    def n_gaps(self) -> int:
        return len(self.gaps)

    def log_summary(self, level: int = logging.INFO) -> None:
        """Emit the accumulated messages plus a one-line summary."""
        for msg in self.messages:
            logger.log(level, msg)
        logger.log(
            level,
            "Cleaning summary | source=%s loaded=%d final=%d "
            "duplicates_dropped=%d invalid_ohlc_dropped=%d gaps=%d",
            self.source,
            self.n_rows_loaded,
            self.n_rows_final,
            self.n_duplicates_dropped,
            self.n_invalid_ohlc_dropped,
            self.n_gaps,
        )


class DataValidationError(ValueError):
    """Raised when OHLCV data fails a configured hard check."""


def timeframe_to_timedelta(timeframe: str) -> pd.Timedelta:
    """
    Convert a timeframe label (e.g. ``"1h"``, ``"1d"``) to a Timedelta.

    Inputs:
        timeframe: Key present in ``TIMEFRAME_TO_OFFSET``.
    Outputs:
        pandas Timedelta equal to one bar of that timeframe.
    Assumes:
        ``timeframe`` is a supported label; otherwise raises KeyError-wrapped
        ValueError. Does not model exchange sessions or DST — callers that
        need session-aware calendars must layer that on separately.
    """
    key = timeframe.strip().lower()
    if key not in TIMEFRAME_TO_OFFSET:
        supported = ", ".join(sorted(TIMEFRAME_TO_OFFSET))
        raise ValueError(f"Unsupported timeframe {timeframe!r}. Supported: {supported}")
    return pd.to_timedelta(TIMEFRAME_TO_OFFSET[key])


def resolve_ohlcv_path(
    data_dir: str | Path,
    symbol: str,
    timeframe: str,
    *,
    prefer: Literal["parquet", "csv", "any"] = "any",
) -> Path:
    """
    Resolve ``data_dir / f"{symbol}_{timeframe}.{ext}"`` for parquet or csv.

    Inputs:
        data_dir: Directory containing one file per (symbol, timeframe).
        symbol: Instrument id as used in the filename (e.g. ``BTCUSDT``).
        timeframe: Bar size label (e.g. ``1h``).
        prefer: Which extension to try first, or ``any`` (parquet then csv).
    Outputs:
        Path to an existing file.
    Assumes:
        Filenames follow ``{SYMBOL}_{TIMEFRAME}.parquet`` or ``.csv``
        (case-sensitive symbol as provided). Raises FileNotFoundError if
        neither exists.
    """
    data_dir = Path(data_dir)
    stem = f"{symbol}_{timeframe}"
    candidates: list[Path]
    if prefer == "parquet":
        candidates = [data_dir / f"{stem}.parquet"]
    elif prefer == "csv":
        candidates = [data_dir / f"{stem}.csv"]
    else:
        candidates = [data_dir / f"{stem}.parquet", data_dir / f"{stem}.csv"]

    for path in candidates:
        if path.exists():
            return path
    raise FileNotFoundError(
        f"No OHLCV file for symbol={symbol!r} timeframe={timeframe!r} in {data_dir}. "
        f"Tried: {', '.join(str(p) for p in candidates)}"
    )


def _read_file(path: Path) -> pd.DataFrame:
    """
    Read a CSV or Parquet file into a DataFrame.

    Inputs:
        path: Existing file with suffix ``.csv`` or ``.parquet``.
    Outputs:
        Raw DataFrame as stored on disk (no cleaning yet).
    Assumes:
        CSV is readable with bare pandas. Parquet requires an optional
        engine (``pyarrow`` or ``fastparquet``); if neither is installed,
        raises ImportError with an install hint — we do not silently invent
        a parser. Unsupported suffixes raise ValueError.
    """
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix == ".parquet":
        try:
            return pd.read_parquet(path)
        except ImportError as exc:
            raise ImportError(
                "Reading parquet requires 'pyarrow' (preferred) or 'fastparquet'. "
                "CSV works with pandas alone. Say if you want pyarrow added to "
                "requirements.txt — pandas cannot parse parquet by itself."
            ) from exc
    raise ValueError(f"Unsupported file type {suffix!r} for {path}")


def _normalize_columns(df: pd.DataFrame, config: DataConfig) -> pd.DataFrame:
    """Lowercase column names and strip whitespace."""
    out = df.copy()
    out.columns = [str(c).strip().lower() for c in out.columns]
    # Common aliases → canonical timestamp name
    aliases = {
        "date": config.timestamp_col,
        "datetime": config.timestamp_col,
        "time": config.timestamp_col,
        "ts": config.timestamp_col,
    }
    out = out.rename(columns={k: v for k, v in aliases.items() if k in out.columns})
    return out


def _ensure_datetime_index(df: pd.DataFrame, config: DataConfig) -> pd.DataFrame:
    """
    Promote timestamp column (or existing DatetimeIndex) to a tz-aware index.

    Inputs:
        df: Frame with either a DatetimeIndex or a timestamp column.
        config: Supplies ``timestamp_col`` and ``timezone``.
    Outputs:
        Frame indexed by sorted, timezone-aware timestamps; timestamp column
        removed if it was promoted.
    Assumes:
        Timestamps are parseable by ``pd.to_datetime``. Naive values are
        localized to ``config.timezone``; aware values are converted to it.
    """
    out = df.copy()
    ts_col = config.timestamp_col.lower()

    if isinstance(out.index, pd.DatetimeIndex) and ts_col not in out.columns:
        idx = out.index
    elif ts_col in out.columns:
        idx = pd.to_datetime(out[ts_col], utc=False)
        out = out.drop(columns=[ts_col])
    else:
        raise DataValidationError(
            f"Expected a {ts_col!r} column or a DatetimeIndex; "
            f"got columns={list(out.columns)}"
        )

    idx = pd.DatetimeIndex(idx)
    if idx.tz is None:
        idx = idx.tz_localize(config.timezone)
    else:
        idx = idx.tz_convert(config.timezone)

    out.index = idx
    out.index.name = ts_col
    return out.sort_index()


def _require_ohlcv_columns(df: pd.DataFrame, config: DataConfig) -> None:
    missing = [c for c in config.required_columns if c not in df.columns]
    if missing:
        raise DataValidationError(
            f"Missing required OHLCV columns {missing}. Present: {list(df.columns)}"
        )


def _drop_duplicates(
    df: pd.DataFrame,
    config: DataConfig,
    report: CleaningReport,
) -> pd.DataFrame:
    """
    Drop duplicate index timestamps, logging each removed stamp.

    Inputs:
        df: Time-indexed OHLCV.
        config: ``duplicate_keep`` and ``raise_on_duplicates``.
        report: Mutated in place with counts and messages.
    Outputs:
        Frame with unique index.
    Assumes:
        Index is already sorted. Keep policy is first or last occurrence.
    """
    dup_mask = df.index.duplicated(keep=False)
    if not dup_mask.any():
        return df

    dup_timestamps = df.index[df.index.duplicated(keep=config.duplicate_keep)]
    n_drop = int(len(dup_timestamps))
    msg = (
        f"Dropping {n_drop} duplicate row(s) "
        f"(keep={config.duplicate_keep!r}); "
        f"timestamps={list(dup_timestamps)}"
    )
    report.messages.append(msg)
    report.n_duplicates_dropped = n_drop
    report.dropped_duplicate_timestamps = list(dup_timestamps)

    if config.raise_on_duplicates:
        raise DataValidationError(msg)

    return df[~df.index.duplicated(keep=config.duplicate_keep)].copy()


def _ohlc_invalid_mask(df: pd.DataFrame) -> pd.Series:
    """True where a row violates basic OHLC / positivity rules."""
    o = df["open"]
    h = df["high"]
    low = df["low"]
    c = df["close"]
    v = df["volume"]
    return (
        o.isna()
        | h.isna()
        | low.isna()
        | c.isna()
        | v.isna()
        | (h < low)
        | (h < o)
        | (h < c)
        | (low > o)
        | (low > c)
        | (o <= 0)
        | (h <= 0)
        | (low <= 0)
        | (c <= 0)
        | (v < 0)
    )


def _validate_ohlc(
    df: pd.DataFrame,
    config: DataConfig,
    report: CleaningReport,
) -> pd.DataFrame:
    """
    Check OHLC integrity; drop or raise per config.

    Inputs:
        df: Frame with open/high/low/close/volume columns.
        config: ``validate_ohlc`` / ``drop_invalid_ohlc``.
        report: Mutated with drop counts and messages.
    Outputs:
        Cleaned frame (possibly unchanged).
    Assumes:
        Columns named in ``OHLCV_COLUMNS`` exist and are numeric-compatible.
    """
    if not config.validate_ohlc:
        return df

    bad = _ohlc_invalid_mask(df)
    if not bad.any():
        return df

    bad_ts = list(df.index[bad])
    n_bad = int(bad.sum())
    msg = f"Invalid OHLC on {n_bad} row(s); timestamps={bad_ts}"
    report.messages.append(msg)
    report.n_invalid_ohlc_dropped = n_bad
    report.dropped_invalid_ohlc_timestamps = bad_ts

    if not config.drop_invalid_ohlc:
        raise DataValidationError(msg)

    return df.loc[~bad].copy()


def _is_weekend_spanning(left: pd.Timestamp, right: pd.Timestamp) -> bool:
    """True if the open interval (left, right) touches Saturday or Sunday."""
    # Walk calendar days strictly between the two stamps.
    start = (left + pd.Timedelta(days=1)).normalize()
    end = right.normalize()
    day = start
    while day < end:
        if day.dayofweek >= 5:
            return True
        day += pd.Timedelta(days=1)
    # Also: Fri close → Mon open for daily bars (no calendar day strictly between
    # still spans the weekend via weekday jump).
    if left.dayofweek == 4 and right.dayofweek == 0 and (right - left) <= pd.Timedelta(days=4):
        return True
    return False


def detect_gaps(
    index: pd.DatetimeIndex,
    timeframe: str,
    config: DataConfig | None = None,
) -> list[GapRecord]:
    """
    Find irregular gaps between consecutive bars.

    Inputs:
        index: Sorted, unique, timezone-aware DatetimeIndex of bar opens
            (or bar labels — consistency matters more than convention).
        timeframe: Expected bar size label (``1h``, ``1d``, …).
        config: Gap sensitivity (``gap_factor``) and session mode
            (``expect_24_7``). Defaults to ``DataConfig()``.
    Outputs:
        List of ``GapRecord`` for each pair whose delta exceeds
        ``gap_factor * expected_bar_duration``. ``missing_bars_estimate``
        is ``round(actual/expected) - 1`` (floored at 1 when flagged).
    Assumes:
        ``index`` is sorted ascending with no duplicates. Does **not**
        insert filler bars. When ``expect_24_7`` is False and timeframe is
        daily or coarser, weekend-spanning holes are skipped (cash sessions).
        Intraday equity auction/holiday calendars are NOT modeled — those
        will still flag as gaps unless you pre-filter the series.
    """
    config = config or DataConfig()
    if len(index) < 2:
        return []

    expected = timeframe_to_timedelta(timeframe)
    threshold = expected * config.gap_factor
    coarse = timeframe.strip().lower() in {"1d", "1w"}
    gaps: list[GapRecord] = []

    for i in range(1, len(index)):
        left_ts = index[i - 1]
        right_ts = index[i]
        delta = right_ts - left_ts
        if delta <= threshold:
            continue
        if not config.expect_24_7 and coarse and _is_weekend_spanning(left_ts, right_ts):
            continue
        missing = max(int(np.round(delta / expected)) - 1, 1)
        gaps.append(
            GapRecord(
                left_timestamp=left_ts,
                right_timestamp=right_ts,
                expected_delta=expected,
                actual_delta=delta,
                missing_bars_estimate=missing,
            )
        )
    return gaps


def clean_ohlcv(
    df: pd.DataFrame,
    timeframe: str,
    config: DataConfig | None = None,
) -> tuple[pd.DataFrame, CleaningReport]:
    """
    Normalize, de-duplicate, validate, and gap-check an OHLCV frame.

    Inputs:
        df: Raw OHLCV with a timestamp column or DatetimeIndex.
        timeframe: Expected bar size for gap detection.
        config: Cleaning tunables; defaults to ``DataConfig()``.
    Outputs:
        (cleaned_df, report) where ``cleaned_df`` has a unique tz-aware
        DatetimeIndex, columns ``open/high/low/close/volume`` (plus any
        extra columns preserved), float dtypes for OHLCV, and **no
        synthetic bars** for gaps. ``report`` lists everything dropped
        and every gap found.
    Assumes:
        Caller passes one symbol's bars only. Gap detection uses a fixed
        timedelta — not an exchange calendar — unless ``expect_24_7=False``
        for daily+ weekend skipping.
    """
    config = config or DataConfig()
    report = CleaningReport(n_rows_loaded=len(df))

    out = _normalize_columns(df, config)
    out = _ensure_datetime_index(out, config)
    _require_ohlcv_columns(out, config)

    for col in config.required_columns:
        out[col] = pd.to_numeric(out[col], errors="coerce").astype("float64")

    out = _drop_duplicates(out, config, report)
    out = _validate_ohlc(out, config, report)

    gaps = detect_gaps(out.index, timeframe, config)
    report.gaps = gaps
    if gaps:
        preview = "; ".join(
            f"{g.left_timestamp} → {g.right_timestamp} "
            f"(~{g.missing_bars_estimate} missing)"
            for g in gaps[:10]
        )
        more = "" if len(gaps) <= 10 else f" … and {len(gaps) - 10} more"
        msg = f"Detected {len(gaps)} gap(s): {preview}{more}"
        report.messages.append(msg)
        if config.raise_on_gaps:
            raise DataValidationError(msg)

    report.n_rows_final = len(out)
    # Keep a stable column order: OHLCV first, then extras.
    extras = [c for c in out.columns if c not in config.required_columns]
    out = out.loc[:, list(config.required_columns) + extras]
    return out, report


def load_ohlcv(
    path: str | Path | None = None,
    *,
    symbol: str | None = None,
    timeframe: str | None = None,
    data_dir: str | Path | None = None,
    config: DataConfig | None = None,
) -> tuple[pd.DataFrame, CleaningReport]:
    """
    Load one (symbol, timeframe) OHLCV file and clean it.

    Inputs:
        path: Explicit file path (``.csv`` or ``.parquet``). If omitted,
            ``symbol``, ``timeframe``, and ``data_dir`` are required and
            resolved via ``resolve_ohlcv_path``.
        symbol / timeframe / data_dir: Alternative to ``path``.
        config: Cleaning tunables; defaults to ``DataConfig()``.
    Outputs:
        (cleaned_df, report) — see ``clean_ohlcv``. ``report.source`` is set
        to the resolved path string; ``report.log_summary()`` prints drops.
    Assumes:
        File contains a single instrument. ``timeframe`` must be supplied
        (explicitly or via the filename resolver) so gap checks know the
        expected bar duration. Does not download data.
    """
    config = config or DataConfig()

    if path is None:
        if symbol is None or timeframe is None or data_dir is None:
            raise ValueError(
                "Provide either path=... or all of symbol, timeframe, and data_dir."
            )
        path = resolve_ohlcv_path(data_dir, symbol, timeframe)
    else:
        path = Path(path)
        if timeframe is None:
            # Infer from ``SYMBOL_TIMEFRAME.ext`` when possible.
            stem = path.stem
            if "_" in stem:
                timeframe = stem.rsplit("_", 1)[-1]
            else:
                raise ValueError(
                    "timeframe is required when it cannot be inferred from the filename "
                    f"(expected '{{symbol}}_{{timeframe}}', got stem={stem!r})."
                )

    raw = _read_file(path)
    cleaned, report = clean_ohlcv(raw, timeframe=timeframe, config=config)
    report.source = str(path)
    report.log_summary()
    return cleaned, report
