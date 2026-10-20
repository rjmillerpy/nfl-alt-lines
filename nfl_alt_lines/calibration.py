"""Scoring probability forecasts against results (one place for it, rule 16): Brier score, log loss,
reliability tables, a fair (recalibrated) market benchmark, and game-level bootstrap intervals for the
difference between two forecasts.

Lines on the same game's ladder are strongly correlated, so every interval resamples whole games.
"""

from collections.abc import Callable

import numpy as np
import numpy.typing as npt
import pandas as pd
from scipy.special import expit, logit
from sklearn.linear_model import LogisticRegression

EPSILON = 1e-6  # keeps log loss finite for a forecast of exactly 0 or 1
RELIABILITY_EDGES = (0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 1)  # alt lines 6-21 against are mostly 5-35%
BOOTSTRAP_DRAWS = 2000
INTERVAL = (0.05, 0.95)
UNREGULARISED = 1e6  # LogisticRegression's C: a plain fit, the market's log-odds being a single input

Floats = npt.NDArray[np.float64]
Score = Callable[[npt.ArrayLike, npt.ArrayLike], Floats]


def brier(p: npt.ArrayLike, hit: npt.ArrayLike) -> Floats:
    """Squared error per forecast; the mean is the Brier score (lower is better)."""
    return np.asarray((np.asarray(p, dtype=float) - np.asarray(hit, dtype=float)) ** 2)


def log_loss(p: npt.ArrayLike, hit: npt.ArrayLike) -> Floats:
    """Log loss per forecast (lower is better)."""
    clipped = np.clip(np.asarray(p, dtype=float), EPSILON, 1 - EPSILON)
    outcome = np.asarray(hit, dtype=float)
    return np.asarray(-(outcome * np.log(clipped) + (1 - outcome) * np.log(1 - clipped)))


def reliability(p: pd.Series, hit: pd.Series, edges: tuple[float, ...] = RELIABILITY_EDGES) -> pd.DataFrame:
    """Per forecast band: how many lines, the average forecast and how often they actually hit (both in %)."""
    band = pd.cut(p, list(edges), include_lowest=True)
    table = pd.DataFrame({"p": p, "hit": hit, "band": band}).groupby("band", observed=True)
    out = table.agg(lines=("hit", "size"), forecast_pct=("p", "mean"), hit_pct=("hit", "mean"))
    out[["forecast_pct", "hit_pct"]] = (100 * out[["forecast_pct", "hit_pct"]]).round(1)
    return out


def _log_odds(values: pd.Series) -> Floats:
    return np.asarray(logit(np.clip(values.to_numpy(dtype=float), EPSILON, 1 - EPSILON))).reshape(-1, 1)


def recalibrate_by_season(frame: pd.DataFrame, column: str) -> pd.Series:
    """`column` refitted per bet type with a logistic curve on its log-odds, using earlier seasons only.

    Removing a book's margin proportionally leaves longshots overpriced; this is the fair version of the market
    to beat. The first season has nothing earlier to learn from and is left empty.
    """
    out = pd.Series(np.nan, index=frame.index)
    for (_, season), test in frame.groupby(["bet_type", "season"]):
        train = frame[(frame["bet_type"] == test["bet_type"].iloc[0]) & (frame["season"] < season)]
        if train.empty:
            continue
        fit = LogisticRegression(C=UNREGULARISED).fit(_log_odds(train[column]), train["hit"].astype(int))
        out[test.index] = expit(fit.decision_function(_log_odds(test[column])))
    return out


def difference_interval(
    frame: pd.DataFrame, first: str, second: str, rng: np.random.Generator, score: Score = log_loss
) -> tuple[float, float, float]:
    """Mean score of `first` minus `second` with a 90% interval, resampling games. Negative: `first` is better."""
    diff = pd.Series(score(frame[first], frame["hit"]) - score(frame[second], frame["hit"]), index=frame.index)
    games = diff.groupby(frame["game_id"]).agg(["sum", "size"])
    picks = rng.integers(0, len(games), size=(BOOTSTRAP_DRAWS, len(games)))
    boot = games["sum"].to_numpy()[picks].sum(axis=1) / games["size"].to_numpy()[picks].sum(axis=1)
    low, high = np.quantile(boot, INTERVAL)
    return float(diff.mean()), float(low), float(high)
