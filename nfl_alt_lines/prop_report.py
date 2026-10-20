"""Alternate yardage props in the report: grading the logged lines once their games finish, the per-book tables,
and the leans shown at a glance. Pricing them is the strategy's job (strategy.py).

The edge shown is the strategy's cautious estimate (the smaller of its `history` and `shape` columns) minus the
price's break-even.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from nfl_alt_lines.pricing import decimal_odds
from nfl_alt_lines.prop_markets import MAX_SHIFT, MIN_SHIFT, YARDS

log = logging.getLogger(__name__)

MIN_GRADED_GAMES = 40
PERCENT = 100
UPCOMING_PERCENT = ["break_even", "model", "estimate", "shape", "edge", "others_implied", "price_edge"]
EDGE_COLUMNS = [
    "game", "player", "market", "line", "market_line", "shift", "price", "break_even", "model", "record", "estimate",
    "shape", "edge",
]  # fmt: skip
PRICE_COLUMNS = ["game", "player", "market", "line", "shift", "price", "break_even", "others_implied", "price_edge"]


def _with_edge(rows: pd.DataFrame) -> pd.DataFrame:
    rows = rows.copy()
    # Rows logged before the history check existed fall back to the model alone.
    own = rows["history"].fillna(rows["model"]) if "history" in rows else rows["model"]
    rows["estimate"] = pd.concat([own, rows["shape"]], axis=1).min(axis=1, skipna=False)
    rows["edge"] = rows["estimate"] - rows["break_even"]
    return rows


def _yards(lines: pd.DataFrame, stats: pd.DataFrame) -> np.ndarray:
    """Each line's player yards in its market (NaN without a stat line), by one join.

    A lookup per line in the stats' unsorted (game, player) index scanned the whole table each time: with
    Sunday's props in the ledger, grading took a minute of a 100 s run on Fargate (2026-10-19).
    """
    keys = ["game_id", "player_id", "market"]
    by_market = [
        stats[["game_id", "player_id", yards]].rename(columns={yards: "yards"}).assign(market=market)
        for market, yards in YARDS.items()
    ]
    found = pd.concat(by_market, ignore_index=True).drop_duplicates(keys)
    return lines[keys].merge(found, how="left", on=keys)["yards"].to_numpy(dtype=float)


def graded(ledger: pd.DataFrame, stats: pd.DataFrame) -> pd.DataFrame:
    """Settled lines. A player with no stat line in a finished game is treated as not having played (void)."""
    if ledger.empty:
        return ledger
    done = ledger[ledger["game_id"].isin(stats["game_id"]) & ledger["player_id"].notna()].copy()
    done["yards"] = _yards(done, stats)
    done = _with_edge(done.dropna(subset=["yards"]))
    done["hit"] = (done["yards"] > done["line"]).astype(float)
    done["profit"] = np.where(done["hit"] == 1, done["price"].map(decimal_odds) - 1, -1.0)
    return done


def grading_summary(done: pd.DataFrame, book: str) -> str:
    done = done[done["book"] == book] if len(done) else done
    if done.empty:
        return "Props: no logged games have finished yet."
    games = done["game_id"].nunique()
    lines = [f"Props graded so far at {book}: {games} games, {done['player'].nunique()} players, {len(done)} lines."]
    if games < MIN_GRADED_GAMES:
        lines.append(f"(Fewer than {MIN_GRADED_GAMES} games: treat every number below as noise for now.)")
    done = done.assign(flag=np.where(done["edge"] > 0, "edge above 0", "edge 0 or below"))
    table = done.groupby(["flag", "market"]).agg(
        lines=("hit", "size"),
        hit_pct=("hit", "mean"),
        model_pct=("model", "mean"),
        shape_pct=("shape", "mean"),
        break_even_pct=("break_even", "mean"),
        roi_pct=("profit", "mean"),
    )
    percent = [column for column in table.columns if column.endswith("_pct")]
    table[percent] = (PERCENT * table[percent]).round(1)
    return "\n".join(
        [*lines, "", "By whether the model saw an edge (claim: 'edge above 0' returns more):", table.to_string()]
    )


def _record(rows: pd.DataFrame) -> pd.Series[str]:
    """ "hits/games": how often each player beat the line in recent games in a similar role."""
    if "recent_games" not in rows:
        return pd.Series("n/a", index=rows.index)
    games = rows["recent_games"].fillna(0).astype(int).astype(str)
    return rows["recent_hits"].fillna(0).astype(int).astype(str) + " of " + games


def _upcoming(ledger: pd.DataFrame, books: list[str], now: datetime) -> pd.DataFrame:
    if ledger.empty:
        return ledger
    ahead = ledger[(pd.to_datetime(ledger["kickoff"], utc=True) > now) & ledger["book"].isin(books)]
    return _with_edge(ahead)


def upcoming_report(ledger: pd.DataFrame, book: str, now: datetime, top: int) -> str:
    ahead = _upcoming(ledger, [book], now)
    if ahead.empty:
        return "Props: no prices logged yet for upcoming games."
    ahead = ahead.assign(price_edge=ahead["others_implied"] - ahead["break_even"])
    ahead["record"] = _record(ahead)
    ahead[UPCOMING_PERCENT] = PERCENT * ahead[UPCOMING_PERCENT]
    best = ahead.dropna(subset=["edge"]).sort_values("edge", ascending=False).head(top)
    cheaper = ahead[ahead["price_edge"] > 0].sort_values("price_edge", ascending=False).head(top)
    return "\n".join(
        [
            f"Best prop edges at {book} (line {MIN_SHIFT}-{MAX_SHIFT} yards above the market's; edge uses the lower of"
            " model and shape):",
            best[EDGE_COLUMNS].round(1).to_string(index=False) if len(best) else "none priced",
            "",
            f"Props where {book} pays more than the other books charge on average (needs 2+ other books):",
            cheaper[PRICE_COLUMNS].round(1).to_string(index=False) if len(cheaper) else "none right now",
        ]
    )


def _lean(row: pd.Series[Any], record: str) -> str:
    return (
        f"{row['player']} over {row['line']:g} {row['market']} yds at {row['price']:+.0f} on {row['book']}"
        f" (needs {PERCENT * row['break_even']:.1f}%, model {PERCENT * row['estimate']:.1f}%, cleared it in {record})"
    )


def leans(ledger: pd.DataFrame, books: list[str], now: datetime, per_game: int = 3) -> dict[str, list[str]]:
    """game -> its few best positive-edge props across the given books, one line per player and market."""
    ahead = _upcoming(ledger, books, now)
    out: dict[str, list[str]] = {}
    if ahead.empty:
        return out
    positive = ahead[ahead["edge"] > 0].sort_values("edge", ascending=False)
    for game, rows in positive.drop_duplicates(["game", "player", "market"]).groupby("game", sort=False):
        best = rows.head(per_game)
        out[str(game)] = [_lean(row, record) for record, (_, row) in zip(_record(best), best.iterrows(), strict=True)]
    return out
