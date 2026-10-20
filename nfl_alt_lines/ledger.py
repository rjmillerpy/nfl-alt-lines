"""The append-only prediction ledger: rows refreshed until kickoff and frozen after, then graded on the final score.

The grading summary is the forward test of the backtests' two claims: bet types in the ranker's bottom fifth
hit less often than the base rate, and unders in its top fifth hit more often.
"""

from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from nfl_alt_lines import run_status, strategy
from nfl_alt_lines.alt_lines import price_rows
from nfl_alt_lines.calibration import brier, log_loss, reliability
from nfl_alt_lines.clv import add_closing, clv_headline
from nfl_alt_lines.db import service
from nfl_alt_lines.db.schema import PREDICTIONS
from nfl_alt_lines.db.service import LedgerStore
from nfl_alt_lines.odds.schema import Event
from nfl_alt_lines.odds.snapshots import LoggedGame
from nfl_alt_lines.pricing import decimal_odds

MIN_GRADED_GAMES = 150  # below this the graded numbers are mostly noise
TOTAL_SIDES = ("over", "under")
BUCKET_ORDER = pd.CategoricalDtype(["skip", "middle", "top"], ordered=True)
CALIBRATION_FORECASTS = {"model": "model", "base": "base rate", "market_fair": "consensus",
                         "close_fair": "closing consensus"}  # fmt: skip
PERCENT_COLUMNS = ["hit_pct", "model_pct", "base_pct", "break_even_pct", "roi_pct", "clv_pct"]


def settle(row: pd.Series, home_score: float, away_score: float) -> float:
    """Profit per unit staked: the payout on a win, -1 on a loss, 0 on a push."""
    if row["side"] in TOTAL_SIDES:
        beat = home_score + away_score - row["line"]
        beat = beat if row["side"] == "over" else -beat
    else:
        margin = home_score - away_score if row["side"] == "home" else away_score - home_score
        beat = margin + row["line"]
    if beat == 0:
        return 0.0
    return float(decimal_odds(row["price"]) - 1) if beat > 0 else -1.0


def _frozen_rows(stored: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """Ledger rows for games that have kicked off; the rest are re-priced this run."""
    if not len(stored):
        return pd.DataFrame()
    return stored[pd.to_datetime(stored["kickoff"], utc=True) <= now]


def _is_unpriceable(game: pd.DataFrame) -> bool:
    return bool(game[["spread_line", "total_line_"]].isna().any(axis=None))


def update_ledger(
    schedule: pd.DataFrame,
    frame: pd.DataFrame,
    games: list[LoggedGame],
    now: datetime,
    closing: dict[str, Event] | None = None,
    ledgers: LedgerStore | None = None,
) -> pd.DataFrame:
    """Refresh rows for games that have not kicked off; freeze the rest; add any game not yet recorded; fill the
    closing price and CLV of games that have kicked off."""
    store = ledgers or service.default_ledgers()
    ledger = _frozen_rows(store.load(PREDICTIONS), now)
    frozen = set(ledger["game_id"]) if len(ledger) else set()
    played = frame["result"].notna() & frame["spread_line"].notna() & (frame["spread_line"] != 0)
    rules = strategy.load()
    rankers = rules.rankers(frame[played & (frame["season"] >= rules.first_feature_season)])
    new_rows: list[dict[str, Any]] = []
    for logged in games:
        game = frame.loc[[logged.row]]
        if game["game_id"].iloc[0] in frozen or _is_unpriceable(game):
            continue
        season, week = int(game["season"].iloc[0]), int(schedule["week"].loc[logged.row])
        recorded = "before kickoff" if logged.kickoff > now else "after kickoff"
        rows = price_rows(logged, game, rankers.before(season, week))
        context = {"season": season, "week": week, "recorded": recorded, "model_version": rules.version}
        new_rows += [{**row, **context} for row in rows]
    run_status.count("plays_ranked", len(new_rows))
    ledger = add_closing(pd.concat([ledger, pd.DataFrame(new_rows)], ignore_index=True), closing or {}, now)
    store.save(PREDICTIONS, ledger)
    return ledger


def graded(ledger: pd.DataFrame, schedule: pd.DataFrame) -> pd.DataFrame:
    """Ledger rows for finished games, with the profit per unit and whether the bet hit (NaN on a push)."""
    scores = schedule.dropna(subset=["home_score", "away_score"]).set_index("game_id")[["home_score", "away_score"]]
    done = ledger[ledger["game_id"].isin(scores.index)].copy()
    if done.empty:
        return done
    done["profit"] = [settle(row, *scores.loc[row["game_id"]]) for _, row in done.iterrows()]
    done["hit"] = np.where(done["profit"] == 0, np.nan, done["profit"] > 0)
    return done


def _bucket_table(rows: pd.DataFrame) -> pd.DataFrame:
    grouped = rows.groupby("bucket", observed=True)
    out = grouped.agg(
        games=("game_id", "nunique"),
        lines=("hit", "size"),
        hit_pct=("hit", "mean"),
        model_pct=("model", "mean"),
        base_pct=("base", "mean"),
        break_even_pct=("break_even", "mean"),
        roi_pct=("profit", "mean"),
        clv_pct=("clv", "mean"),
    )
    out[PERCENT_COLUMNS] = (100 * out[PERCENT_COLUMNS]).round(1)
    return out


def calibration_summary(done: pd.DataFrame) -> str:
    """Model vs the no-vig consensus when the bet was logged and at the close: Brier, log loss, reliability."""
    settled = done[done["hit"].notna()]
    rows = []
    for column, name in CALIBRATION_FORECASTS.items():
        have = settled[settled[column].notna()] if column in settled else settled.iloc[:0]
        if len(have):
            scores = {
                "brier": brier(have[column], have["hit"]).mean(),
                "log_loss": log_loss(have[column], have["hit"]).mean(),
            }
            rows.append({"forecast": name, "lines": len(have), **scores})
    if not rows:
        return "Calibration: no settled lines yet."
    table = pd.DataFrame(rows).round(4).to_string(index=False)
    bands = reliability(settled["model"], settled["hit"].astype(float)).to_string()
    return (
        "Calibration (lower is better; the consensus rows are the market to beat, on the lines that have them):\n"
        f"{table}\n\nReliability of the model: forecast vs how often the lines hit\n{bands}"
    )


def _holdout_rows(done: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Rows ranked with the current model version, and how many from other versions were left out."""
    model_version = strategy.load().version
    version = done["model_version"] if "model_version" in done else pd.Series(model_version, index=done.index)
    current = version == model_version
    return done[current], int((~current).sum())


def grading_summary(done: pd.DataFrame, book: str) -> str:
    done, left_out = _holdout_rows(done[done["book"] == book])
    if "clv" not in done:
        done = done.assign(clv=np.nan)  # ledgers written before closing prices were logged
    if done.empty:
        return "No logged games have finished yet."
    games = done["game_id"].nunique()
    excluded = f"; {left_out} lines from earlier versions left out" if left_out else ""
    rules = strategy.load()
    holdout = f"clean holdout: {rules.holdout_season}, model version {rules.version}{excluded}"
    lines = [f"Graded so far at {book}: {games} games, {len(done)} alternate lines ({holdout})."]
    if games < MIN_GRADED_GAMES:
        lines.append(f"(Fewer than {MIN_GRADED_GAMES} games: treat every number below as noise for now.)")
    lines += ["", clv_headline(done), "", calibration_summary(done)]
    done = done.assign(bucket=done["bucket"].astype(BUCKET_ORDER))
    lines += ["", "By ranker bucket (claim: 'skip' hits below its base rate):", _bucket_table(done).to_string()]
    unders = done[done["bet_type"] == "under"]
    if len(unders):
        lines += ["", "Unders by bucket (claim: 'top' hits above its base rate):", _bucket_table(unders).to_string()]
    return "\n".join(lines)
