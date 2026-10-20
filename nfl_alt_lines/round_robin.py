"""Paper round robins: each game day's legs at one book, the pay-out structures, the lock, and how they did.

Which priced lines can be legs, and how many, is the strategy's choice (strategy.py). One leg per game, so legs
are close to independent and the bet is allowed as a plain round robin. Legs are chosen at each book and the
round robin goes to the book with the strongest set. It is rebuilt every run until it locks (LOCK_BEFORE its
first leg), then kept as it is and graded once every leg's game has finished.

A second, separate paper round robin is built from prop leans only, so the prop model's picks get the same
forward test without being mixed into the game-line one.
"""

from collections.abc import Callable
from datetime import datetime, timedelta
from itertools import combinations, product
from math import prod
from typing import Any

import numpy as np
import pandas as pd

from nfl_alt_lines import strategy
from nfl_alt_lines.db import service
from nfl_alt_lines.db.schema import ROUND_ROBINS, Ledger
from nfl_alt_lines.glance import LOCAL_ZONE, SUNDAY
from nfl_alt_lines.ledger import settle
from nfl_alt_lines.pricing import decimal_odds
from nfl_alt_lines.prop_markets import YARDS

# Local hours of the Sunday games that can be legs. An overseas morning game would freeze the pick before the
# afternoon games are priced, and the night game is only priced after the first afternoon kickoff has frozen it.
EARLIEST_HOUR, LATEST_HOUR = 11, 16
PROP_COLUMNS = ["game_id", "game", "kickoff", "market", "player", "player_id", "line", "shift", "price", "break_even"]
PROP_COLUMNS += ["model", "role"]
VOID, LOST = 1.0, 0.0  # leg multipliers: a void leg returns the stake, a lost one nothing
SIZES = (1, 2)  # singles and by 2s: by 3s and 4s lost in the replay
LEG_COLUMNS = ["game_id", "game", "kickoff", "bet_type", "side", "bet", "line", "shift", "price", "break_even"]
LEG_COLUMNS += ["model", "percentile", "role"]
FLAGGED_ROLE = "worth a look"
SLATE_FORMAT = "%Y-%m-%d"  # a game day's slate key
# A saved round robin is final this long before its first leg: after the 10:15 AM run for noon games, so what
# that email says is what gets bet. On 2026-10-18 a later run (fresh nflverse data) changed two of the legs.
LOCK_BEFORE = timedelta(minutes=95)
DAY_FORMAT = "%A %b %d"
KICKOFF_FORMAT = "%a %I:%M %p"


def structure_name(size: int) -> str:
    return "singles" if size == 1 else f"by {size}s"


def afternoon(local: pd.Series) -> pd.Series:
    """Which local kickoffs are Sunday-afternoon games, the only ones a round robin uses."""
    is_sunday: pd.Series = local.dt.dayofweek == SUNDAY
    in_afternoon: pd.Series = local.dt.hour.between(EARLIEST_HOUR, LATEST_HOUR)
    return is_sunday & in_afternoon


def combo_return(multipliers: list[float], size: int) -> float:
    """Total paid back per unit staked on every `size`-leg parlay (leg multiplier: odds on a win, 1 push, 0 loss)."""
    parlays = list(combinations(multipliers, size))
    return sum(prod(parlay) for parlay in parlays) / len(parlays)


def _sizes(legs: int) -> list[int]:
    """The structures (parlay sizes) that `legs` legs allow."""
    return [size for size in SIZES if size <= legs]


def _structure(size: int, odds: list[float], outcomes: list[tuple[int, ...]], weights: list[float]) -> dict[str, Any]:
    """One structure's row: paid back per unit staked under every outcome, weighted by its chance."""
    hit_odds = [[d * hit for d, hit in zip(odds, outcome, strict=True)] for outcome in outcomes]
    paid = [combo_return(multipliers, size) for multipliers in hit_odds]
    return {
        "structure": structure_name(size),
        "bets": len(list(combinations(odds, size))),
        "expected_return_pct": 100 * (np.dot(weights, paid) - 1),
        "chance_of_profit_pct": 100 * sum(w for w, r in zip(weights, paid, strict=True) if r > 1),
        "chance_of_nothing_pct": 100 * sum(w for w, r in zip(weights, paid, strict=True) if r == 0),
        "all_hit_pays_x_stake": max(paid),
    }


def structures(probabilities: list[float], odds: list[float]) -> pd.DataFrame:
    """Expected result of the legs as singles and by 2s, treating legs from different games as independent."""
    outcomes = list(product([0, 1], repeat=len(odds)))
    weights = [prod(p if hit else 1 - p for p, hit in zip(probabilities, o, strict=True)) for o in outcomes]
    return pd.DataFrame([_structure(size, odds, outcomes, weights) for size in _sizes(len(odds))])


def _strength(legs: pd.DataFrame) -> tuple[int, float]:
    """How good a book's legs are: flagged legs first, then the best structure's expected return by the model."""
    if legs.empty:
        return (0, -np.inf)
    table = structures(list(legs["model"]), [decimal_odds(price) for price in legs["price"]])
    return (int((legs["role"] == FLAGGED_ROLE).sum()), float(table["expected_return_pct"].max()))


def locked(kickoffs: pd.Series, now: datetime) -> bool:
    """Whether a round robin is final: its first leg kicks off within LOCK_BEFORE."""
    first: pd.Timestamp = pd.to_datetime(kickoffs, utc=True).min()
    return bool(first - LOCK_BEFORE <= now)


def update(
    ledger: pd.DataFrame,
    books: list[str],
    now: datetime,
    table: Ledger = ROUND_ROBINS,
    choose: Callable[[pd.DataFrame], pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """Rebuild each game day's round robin until it locks (LOCK_BEFORE its first leg); keep locked ones as they
    are.

    A round robin has to sit at one book, so the legs are chosen at each of `books` and the book with the most
    flagged legs wins, then the one whose best structure the model expects to return most.
    """
    rules = strategy.load()
    choose = choose or rules.choose_legs
    store = service.default_ledgers()
    saved = store.load(table)
    started = saved.groupby("slate")["kickoff"].transform(locked, now=now)
    saved = saved[started.astype(bool)]
    frozen = set(saved["slate"])
    built = [saved]
    if len(ledger):
        local = pd.to_datetime(ledger["kickoff"], utc=True).dt.tz_convert(LOCAL_ZONE)
        ahead = ledger.assign(slate=local.dt.strftime(SLATE_FORMAT))
        ahead = ahead[(local > now) & afternoon(local) & ledger["book"].isin(books)]
        for slate, priced in ahead.groupby("slate"):
            if slate in frozen:
                continue
            options = [choose(priced[priced["book"] == book]).assign(book=book) for book in books]
            options = [legs for legs in options if len(legs) >= rules.min_legs]
            if options:
                built.append(max(options, key=_strength).assign(slate=slate))
    saved = pd.concat(built, ignore_index=True)
    store.save(table, saved)
    return saved


def _local_kickoff(legs: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(legs["kickoff"], utc=True).dt.tz_convert(LOCAL_ZONE).dt.strftime(KICKOFF_FORMAT)


def _percent(table: pd.DataFrame, columns: tuple[str, ...]) -> pd.DataFrame:
    scaled = table.assign(**{column: (100 * table[column]).round(1) for column in columns})
    return scaled.rename(columns={"break_even": "needs_pct", "model": "model_pct"})


def _leg_table(legs: pd.DataFrame) -> str:
    table = legs.assign(kickoff=_local_kickoff(legs))
    spread = ~table["bet_type"].isin(["over", "under"])
    table["bet"] = (
        table["bet"] + " " + np.where(spread, table["line"].map("{:+g}".format), table["line"].map("{:g}".format))
    )
    table = _percent(table, ("break_even", "model", "percentile"))
    columns = ["game", "kickoff", "bet", "shift", "price", "needs_pct", "model_pct", "percentile", "role"]
    return table[columns].to_string(index=False)


def _prop_table(legs: pd.DataFrame) -> str:
    table = legs.assign(kickoff=_local_kickoff(legs))
    table["bet"] = table["player"] + " over " + table["line"].map("{:g}".format) + " " + table["market"] + " yds"
    table = _percent(table, ("break_even", "model"))
    return table[["game", "kickoff", "bet", "shift", "price", "needs_pct", "model_pct"]].to_string(index=False)


def game_multipliers(schedule: pd.DataFrame) -> Callable[[pd.DataFrame], list[float] | None]:
    """Leg results from final scores: odds on a win, 1 on a push, 0 on a loss; None until every game is final."""
    scores = schedule.dropna(subset=["home_score", "away_score"]).set_index("game_id")[["home_score", "away_score"]]

    def multipliers(legs: pd.DataFrame) -> list[float] | None:
        if not legs["game_id"].isin(scores.index).all():
            return None
        return [1 + settle(leg, *scores.loc[leg["game_id"]]) for _, leg in legs.iterrows()]

    return multipliers


def prop_multipliers(stats: pd.DataFrame) -> Callable[[pd.DataFrame], list[float] | None]:
    """Leg results from player stats; a player with no stat line is treated as not having played (leg void)."""
    played = stats.set_index(["game_id", "player_id"])
    finished = set(stats["game_id"])

    def result(leg: pd.Series) -> float:
        yards = played[YARDS[leg["market"]]].get((leg["game_id"], leg["player_id"]), np.nan)
        if pd.isna(yards):
            return VOID
        return decimal_odds(leg["price"]) if yards > leg["line"] else LOST

    def multipliers(legs: pd.DataFrame) -> list[float] | None:
        if not legs["game_id"].isin(finished).all():
            return None
        return [result(leg) for _, leg in legs.iterrows()]

    return multipliers


def _graded_rows(slate: str, book: str, results: list[float]) -> list[dict[str, Any]]:
    """Units staked and paid back by each structure of one finished round robin."""
    legs_hit = sum(m > 1 for m in results)
    rows = []
    for size in _sizes(len(results)):
        bets = len(list(combinations(results, size)))
        rows.append(
            {
                "slate": slate,
                "book": book,
                "structure": structure_name(size),
                "legs": len(results),
                "legs_hit": legs_hit,
                "staked": bets,
                "paid": bets * combo_return(results, size),
            }
        )
    return rows


def graded(saved: pd.DataFrame, multipliers: Callable[[pd.DataFrame], list[float] | None]) -> pd.DataFrame:
    """One row per finished round robin and structure: units staked and paid back."""
    out = []
    for (slate, book), legs in saved.groupby(["slate", "book"]):
        results = multipliers(legs)
        if results is not None:
            out += _graded_rows(str(slate), str(book), results)
    return pd.DataFrame(out)


def _live(slate: str, legs: pd.DataFrame, now: datetime, props: bool) -> list[str]:
    """An ungraded round robin: its legs, the structures' expected results and the verdict."""
    first = pd.to_datetime(legs["kickoff"], utc=True).min()
    state = "final, first leg has kicked off" if first <= now else "may still change until the first leg kicks off"
    legs_table = _prop_table(legs) if props else _leg_table(legs)
    where = legs["book"].iloc[0]
    lines = ["", f"{pd.Timestamp(slate).strftime(DAY_FORMAT)} at {where} ({state}):", legs_table, ""]
    table = structures(list(legs["model"]), [decimal_odds(price) for price in legs["price"]])
    lines.append(table.round(1).to_string(index=False))
    best = table.iloc[table["expected_return_pct"].argmax()]
    verdict = "above" if best["expected_return_pct"] > 0 else "below"
    lines.append(
        f"Highest expected return: {best['structure']} at {best['expected_return_pct']:+.1f}% of the stake, which"
        f" is {verdict} break-even by the model's own numbers."
    )
    if props:
        lines.append("The prop model has not been tested against real prices, so that figure is probably too high.")
    else:
        lines.append(strategy.load().replay_note)
    return lines


def _finished(results: pd.DataFrame) -> list[str]:
    """Totals over every graded round robin by structure, and how the latest one went."""
    totals = results.groupby("structure").agg(
        round_robins=("slate", "size"), staked=("staked", "sum"), paid=("paid", "sum")
    )
    totals["return_pct"] = (100 * (totals["paid"] / totals["staked"] - 1)).round(1)
    latest = results[results["slate"] == results["slate"].max()].iloc[0]
    return [
        "",
        "Finished ones, one unit per bet (a handful of these is noise):",
        totals.round(1).to_string(),
        f"Latest ({latest['slate']}): {latest['legs_hit']} of {latest['legs']} legs hit.",
    ]


def report(
    saved: pd.DataFrame,
    multipliers: Callable[[pd.DataFrame], list[float] | None],
    books: list[str],
    now: datetime,
    props: bool = False,
) -> str:
    if props:
        title = (
            f"PAPER PROPS-ONLY ROUND ROBIN at one book, the best of {', '.join(books)}"
            " (one leg per game; do not bet it)"
        )
    else:
        title = (
            f"SUNDAY ROUND ROBIN DETAILS: every leg at one book, the best of {', '.join(books)}"
            " (one leg per game; stakes in the plan)"
        )
    lines = [title]
    live = [(slate, legs) for slate, legs in saved.groupby("slate") if multipliers(legs) is None]
    if not live and props:
        lines.append(f"None yet: it needs positive-edge props in {strategy.load().min_legs} games on the same day.")
    elif not live:
        lines.append(
            f"None yet: it needs {strategy.load().min_legs} priced Sunday games with a 'worth a look' bet."
            " Only Sunday's afternoon games are used; it is complete in the run at about 10:15 AM Central."
        )
    for slate, legs in live:
        lines += _live(str(slate), legs, now, props)
    results = graded(saved, multipliers)
    if len(results):
        lines += _finished(results)
    return "\n".join(lines)
