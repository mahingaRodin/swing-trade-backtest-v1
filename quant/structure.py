import pandas as pd


def detect_swing_highs(
    df: pd.DataFrame,
    lookback: int = 2
) -> pd.Series:
    """
    Detect local swing highs.

    A candle is considered a swing high when its high
    is greater than the highs of `lookback` candles
    on both sides.
    """

    highs = df["high"]
    swing_high = pd.Series(False, index=df.index)

    for i in range(lookback, len(df) - lookback):

        current_high = highs.iloc[i]

        left_highs = highs.iloc[i - lookback:i]
        right_highs = highs.iloc[i + 1:i + lookback + 1]

        if (
            current_high > left_highs.max()
            and current_high > right_highs.max()
        ):
            swing_high.iloc[i] = True

    return swing_high


def detect_swing_lows(
    df: pd.DataFrame,
    lookback: int = 2
) -> pd.Series:
    """
    Detect local swing lows.

    A candle is considered a swing low when its low
    is lower than the lows of `lookback` candles
    on both sides.
    """

    lows = df["low"]
    swing_low = pd.Series(False, index=df.index)

    for i in range(lookback, len(df) - lookback):

        current_low = lows.iloc[i]

        left_lows = lows.iloc[i - lookback:i]
        right_lows = lows.iloc[i + 1:i + lookback + 1]

        if (
            current_low < left_lows.min()
            and current_low < right_lows.min()
        ):
            swing_low.iloc[i] = True

    return swing_low