"""Pricing a logged game's alternate lines: every line 6-21 points against the bettor, at every book, as ledger rows.

Each row carries the ranker's probability and percentile, the price's break-even, and the other books'
margin-free probability for the same line.
"""

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from nfl_alt_lines import strategy
from nfl_alt_lines.odds.books import TEAM_CODES
from nfl_alt_lines.odds.schema import Event, Outcome
from nfl_alt_lines.odds.snapshots import LoggedGame, markets
from nfl_alt_lines.pricing import break_even
from nfl_alt_lines.strategy import Ranker

MIN_SHIFT, MAX_SHIFT = 6, 21
ALT_SPREADS, ALT_TOTALS = "alternate_spreads", "alternate_totals"
OVER, UNDER = "Over", "Under"
MIN_OTHER_BOOKS = 2  # a market fair price needs at least this many other books

LineKey = tuple[str, str, float]  # (market, outcome name, point)
FairPrices = dict[str, dict[LineKey, float]]  # book -> line -> margin-free win probability


@dataclass(frozen=True)
class Candidate:
    """An alternate line and how far it moves the bettor from the book's main line."""

    bet_type: str
    side: str  # "home", "away", "over" or "under"
    market: str
    outcome: Outcome
    shift: float


def fair_probabilities(event: Event) -> FairPrices:
    """book -> (market, outcome name, point) -> win probability with that book's margin removed.

    The margin is removed by pairing each alternate line with its opposite side at the same book.
    """
    home, away = event.home_team, event.away_team
    other = {home: away, away: home, OVER: UNDER, UNDER: OVER}
    fair: FairPrices = {}
    for book, offered in markets(event).items():
        prices = {
            (market, outcome.name, outcome.point): break_even(outcome.price)
            for market in (ALT_SPREADS, ALT_TOTALS)
            for outcome in offered.get(market, ())
        }
        fair[book] = {}
        for (market, name, point), implied in prices.items():
            opposite = prices.get((market, other[name], -point if market == ALT_SPREADS else point))
            if opposite:
                fair[book][(market, name, point)] = implied / (implied + opposite)
    return fair


def _candidates(
    offered: dict[str, tuple[Outcome, ...]], home: str, spreads: dict[str, float], total_line: float
) -> list[Candidate]:
    """Every alternate line at one book, spreads first, with its shift off that book's main line."""
    candidates = []
    for outcome in offered.get(ALT_SPREADS, ()):
        main = spreads[outcome.name]
        if main != 0:
            side = "home" if outcome.name == home else "away"
            bet_type = "favorite" if main < 0 else "underdog"
            candidates.append(Candidate(bet_type, side, ALT_SPREADS, outcome, main - outcome.point))
    for outcome in offered.get(ALT_TOTALS, ()):
        side = "over" if outcome.name == OVER else "under"
        shift = outcome.point - total_line if side == "over" else total_line - outcome.point
        candidates.append(Candidate(side, side, ALT_TOTALS, outcome, shift))
    return candidates


def _american(price: float) -> int | float:
    """The price as the API sent it: American odds are whole numbers, kept as int so the ledger shows 186, not 186.0."""
    return int(price) if price.is_integer() else price


def _market_fair(fair: FairPrices, book: str, key: LineKey) -> tuple[float, int]:
    """The other books' average margin-free probability for this line (NaN below 2 books), and their count."""
    elsewhere = [fair[other][key] for other in fair if other != book and key in fair[other]]
    average = float(np.mean(elsewhere)) if len(elsewhere) >= MIN_OTHER_BOOKS else np.nan
    return average, len(elsewhere)


def _game_name(event: Event) -> str:
    return f"{TEAM_CODES[event.away_team]} @ {TEAM_CODES[event.home_team]}"


def _row(
    logged: LoggedGame, book: str, game: pd.DataFrame, candidate: Candidate, ranker: Ranker, fair: FairPrices
) -> dict[str, Any]:
    model, base = ranker.probability(candidate.bet_type, game, candidate.shift)
    outcome = candidate.outcome
    market_fair, other_books = _market_fair(fair, book, (candidate.market, outcome.name, outcome.point))
    return {
        "game_id": game["game_id"].iloc[0],
        "game": _game_name(logged.event),
        "kickoff": logged.kickoff.isoformat(),
        "snapshot": logged.stamp,
        "book": book,
        "bet_type": candidate.bet_type,
        "side": candidate.side,
        "bet": outcome.name,
        "line": outcome.point,
        "shift": candidate.shift,
        "price": _american(outcome.price),
        "break_even": break_even(outcome.price),
        "market_fair": market_fair,
        "other_books": other_books,
        "base": base,
        "model": model,
    }


def price_rows(logged: LoggedGame, game: pd.DataFrame, ranker: Ranker) -> list[dict[str, Any]]:
    """One row per in-range alternate line, for every book that posted both main and alternate lines."""
    event = logged.event
    rules = strategy.load()
    percentiles = {bet: ranker.percentile(bet, game) for bet in rules.bet_types}
    fair = fair_probabilities(event)
    in_range = [
        (book, candidate)
        for book, offered in markets(event).items()
        if book in logged.lines
        for candidate in _candidates(offered, event.home_team, *logged.lines[book])
        if MIN_SHIFT <= candidate.shift <= MAX_SHIFT
    ]
    return [
        {
            **_row(logged, book, game, candidate, ranker, fair),
            "percentile": percentiles[candidate.bet_type],
            "bucket": rules.bucket(percentiles[candidate.bet_type]),
        }
        for book, candidate in in_range
    ]
