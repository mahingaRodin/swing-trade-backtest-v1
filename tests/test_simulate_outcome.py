import pandas as pd
import pytest

from scripts.scan_setups import simulate_outcome, leg_confirmed_by


def bars(*rows):
    """Build a tiny OHLC frame. Each row is (open, high, low, close)."""
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"])


# ---------- simulate_outcome: exits ----------

def test_buy_hits_target_first_is_a_2R_win():
    df = bars((100, 101.5, 99.5, 101),    # entry bar: touches neither 99 nor 102
              (101, 102.5, 100.5, 102))   # next bar: high reaches the 102 target
    outcome, r, held = simulate_outcome(df, 0, entry=100, stop=99, target=102, direction="buy")
    assert outcome == "WIN"
    assert r == pytest.approx(2.0)
    assert held == 1


def test_buy_hits_stop_is_a_minus_1R_loss():
    df = bars((100, 100.5, 98.5, 99))     # low reaches the 99 stop on the entry bar itself
    outcome, r, held = simulate_outcome(df, 0, entry=100, stop=99, target=102, direction="buy")
    assert outcome == "LOSS"
    assert r == pytest.approx(-1.0)
    assert held == 0                      # the entry bar counts: price can move after the open


def test_sell_hits_target_first_is_a_2R_win():
    df = bars((100, 100.5, 98.5, 99),
              (99, 99.5, 97.5, 98))       # low reaches the 98 target
    outcome, r, _ = simulate_outcome(df, 0, entry=100, stop=101, target=98, direction="sell")
    assert outcome == "WIN"
    assert r == pytest.approx(2.0)


def test_same_bar_touches_stop_and_target_counts_as_loss():
    # We cannot know which was hit first inside one candle; the code assumes the worst.
    df = bars((100, 102.5, 98.5, 100))
    outcome, r, _ = simulate_outcome(df, 0, entry=100, stop=99, target=102, direction="buy")
    assert outcome == "LOSS"
    assert r == pytest.approx(-1.0)


def test_no_exit_within_max_bars_is_open_with_zero_r():
    df = bars(*[(100, 100.4, 99.6, 100)] * 5)
    outcome, r, held = simulate_outcome(df, 0, entry=100, stop=99, target=102,
                                        direction="buy", max_bars=3)
    assert outcome == "OPEN"
    assert r == 0.0
    assert held == 3


def test_zero_risk_is_skipped():
    df = bars((100, 101, 99, 100))
    outcome, r, _ = simulate_outcome(df, 0, entry=100, stop=100, target=102, direction="buy")
    assert outcome == "SKIP"


# ---------- simulate_outcome: costs ----------

def test_costs_shrink_a_win_and_deepen_a_loss():
    win_df = bars((100, 102.5, 99.5, 102))
    loss_df = bars((100, 100.5, 98.5, 99))
    kwargs = dict(entry=100, stop=99, target=102, direction="buy",
                  slippage_price=0.1, fee_pct=0.001)

    _, win_r, _ = simulate_outcome(win_df, 0, **kwargs)
    _, loss_r, _ = simulate_outcome(loss_df, 0, **kwargs)

    # entry fill 100.1; fee in R = 100.1 * 0.001 * 2 / 1 = 0.2002
    assert win_r == pytest.approx((101.9 - 100.1) - 0.2002)    # 1.5998, not 2.0
    assert loss_r == pytest.approx((98.9 - 100.1) - 0.2002)    # -1.4002, not -1.0


# ---------- known limitation, written down as a test ----------

@pytest.mark.xfail(reason="Gap through the stop is filled AT the stop, not at the (worse) open.")
def test_gap_through_stop_should_fill_at_the_open():
    df = bars((98, 98.5, 97.5, 98))       # bar opens at 98, already below the 99 stop
    _, r, _ = simulate_outcome(df, 0, entry=100, stop=99, target=102, direction="buy")
    assert r == pytest.approx(-2.0)       # real fill ~98 => (98 - 100) / 1


# ---------- leg_confirmed_by: only swings known by the cutoff ----------

SWINGS = [
    {"idx": 10, "kind": "low",  "price": 1.30},
    {"idx": 20, "kind": "high", "price": 1.35},
    {"idx": 30, "kind": "low",  "price": 1.32},
]


def test_leg_needs_two_known_swings():
    assert leg_confirmed_by(SWINGS, cutoff_idx=15) is None   # only the low at 10 is known


def test_leg_ignores_swings_after_the_cutoff():
    leg = leg_confirmed_by(SWINGS, cutoff_idx=29)            # low at 30 is NOT known yet
    assert leg["direction"] == "up"
    assert (leg["start_price"], leg["end_price"]) == (1.30, 1.35)


def test_leg_flips_once_the_next_swing_is_known():
    leg = leg_confirmed_by(SWINGS, cutoff_idx=30)
    assert leg["direction"] == "down"
    assert (leg["start_price"], leg["end_price"]) == (1.35, 1.32)


def test_leg_skips_back_over_same_kind_swings():
    swings = [{"idx": 10, "kind": "low", "price": 1.30},
              {"idx": 20, "kind": "high", "price": 1.34},
              {"idx": 25, "kind": "high", "price": 1.36}]
    leg = leg_confirmed_by(swings, cutoff_idx=25)
    assert (leg["start_price"], leg["end_price"]) == (1.30, 1.36)