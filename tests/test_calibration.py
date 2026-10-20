import numpy as np
import pandas as pd
import pytest

from nfl_alt_lines.calibration import brier, difference_interval, log_loss, recalibrate_by_season, reliability


def test_scores_on_known_inputs() -> None:
    assert brier([0.2, 0.7], [0, 1]).tolist() == pytest.approx([0.04, 0.09])
    assert log_loss([0.5], [1])[0] == pytest.approx(np.log(2))
    assert log_loss([0.2], [0])[0] == pytest.approx(-np.log(0.8))
    assert np.isfinite(log_loss([0.0, 1.0], [1, 0])).all()


def test_reliability_bands() -> None:
    table = reliability(pd.Series([0.08, 0.09, 0.22, 0.24]), pd.Series([0.0, 1.0, 0.0, 0.0]))
    assert table["lines"].tolist() == [2, 2]
    assert table["forecast_pct"].tolist() == [8.5, 23.0]
    assert table["hit_pct"].tolist() == [50.0, 0.0]


def test_recalibration_uses_earlier_seasons_only() -> None:
    # A market that says 30% on lines that hit 15% of the time: the refit learns that from 2023 for 2024.
    rng = np.random.default_rng(1)
    n = 4000
    frame = pd.DataFrame({"bet_type": "under", "season": np.repeat([2023, 2024], n // 2),
                          "consensus": rng.uniform(0.25, 0.35, n),
                          "hit": (rng.random(n) < 0.15).astype(float)})  # fmt: skip
    refit = recalibrate_by_season(frame, "consensus")
    assert refit[frame["season"] == 2023].isna().all()
    assert refit[frame["season"] == 2024].mean() == pytest.approx(0.15, abs=0.02)


def test_difference_interval_signs_the_better_forecast() -> None:
    frame = pd.DataFrame({"game_id": np.repeat(np.arange(50), 4), "hit": np.tile([1.0, 0, 0, 0], 50),
                          "good": 0.25, "bad": 0.6})  # fmt: skip
    diff, low, high = difference_interval(frame, "good", "bad", np.random.default_rng(0))
    assert diff < 0
    assert low <= diff <= high
    assert high < 0
