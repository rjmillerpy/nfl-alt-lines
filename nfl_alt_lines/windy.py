"""Windy unders (experimental; the owner's choice, 2026-11-21): the main-line under in open-air games forecast to be
windy, at the books that do not limit winning bettors (Circa, Novig, Kalshi; odds/books.py NO_LIMIT_BOOKS).

Kept apart from the model's plan: the main report lists the games forecast windy this week (no prices: the
forecast and the line both move), and the email about an hour before kickoff (kickoff_email.py) prices them from
the odds logger's latest venue snapshot. The wind threshold, stake and evidence note come from the strategy
(Staking.windy_*); a stake of 0 turns all of it off.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd

from nfl_alt_lines import strategy
from nfl_alt_lines.common.storage import Storage
from nfl_alt_lines.odds.books import NO_LIMIT_BOOKS, TEAM_CODES, book_name, net_price
from nfl_alt_lines.odds.schema import Event, SlateSnapshot
from nfl_alt_lines.odds.snapshots import VENUES
from nfl_alt_lines.pricing import break_even
from nfl_alt_lines.weather import INDOOR_ROOFS, kickoffs, likely_roof

WATCH_DAYS = 7
VENUE_MAX_AGE = timedelta(hours=2)  # an older venue snapshot is not shown as a price
HEADING = "WINDY UNDERS"
LINE_START = "WINDY UNDER"


@dataclass(frozen=True)
class Quote:
    book: str
    point: float
    price: float  # American odds after the book's fee


def enabled() -> bool:
    return strategy.load().staking.windy_stake > 0


def windy_games(schedule: pd.DataFrame, start: datetime, end: datetime) -> pd.DataFrame:
    """Open-air games kicking off in [start, end) with forecast wind at or above the strategy's threshold.

    Returns `game` ("AWAY @ HOME", the plan's spelling), `kickoff` (UTC) and `wind` (mph), soonest first."""
    kickoff = kickoffs(schedule)
    threshold = strategy.load().staking.windy_wind_mph
    keep = (
        (kickoff >= pd.Timestamp(start))
        & (kickoff < pd.Timestamp(end))
        & ~likely_roof(schedule).isin(INDOOR_ROOFS)
        & (schedule["wind"] >= threshold)
    )
    rows = schedule[keep]
    games = pd.DataFrame(
        {"game": rows["away_team"] + " @ " + rows["home_team"], "kickoff": kickoff[keep], "wind": rows["wind"]}
    )
    return games.sort_values("kickoff").reset_index(drop=True)


def latest_venues(store: Storage, now: datetime) -> SlateSnapshot | None:
    """The odds logger's newest venue snapshot, if it is recent enough to quote."""
    keys = sorted(store.keys(VENUES))
    if not keys:
        return None
    snapshot = SlateSnapshot.model_validate_json(store.read(keys[-1]))
    return snapshot if now - snapshot.fetched_at <= VENUE_MAX_AGE else None


def _game(event: Event) -> str | None:
    away, home = TEAM_CODES.get(event.away_team), TEAM_CODES.get(event.home_team)
    return f"{away} @ {home}" if away and home else None


def best_under(snapshot: SlateSnapshot, game: str) -> Quote | None:
    """The best under for `game` at a no-limit book: the highest total, then the best price after fees."""
    quotes = [
        Quote(book_name(book.key), outcome.point, net_price(book_name(book.key), outcome.price))
        for event in snapshot.response
        if _game(event) == game
        for book in event.bookmakers
        if book_name(book.key) in NO_LIMIT_BOOKS
        for market in book.markets
        if market.key == "totals"
        for outcome in market.outcomes
        if outcome.name == "Under"
    ]
    return max(quotes, key=lambda quote: (quote.point, -break_even(quote.price)), default=None)


def _stake(bank: float | None) -> str:
    share = strategy.load().staking.windy_stake
    percent = f"{100 * share:.2f}".rstrip("0").rstrip(".") + "% of bankroll"
    return f"${bank * share:,.2f} ({percent})" if bank else percent


def heading() -> str:
    note = strategy.load().staking.windy_note
    return f"{HEADING} (experimental but promising; only at Circa, Novig or Kalshi, which do not limit winners){note}:"


def watch_section(schedule: pd.DataFrame, now: datetime, bank: float | None) -> list[str]:
    """The main report's list of this week's games forecast windy; prices come in the email before kickoff."""
    if not enabled():
        return []
    games = windy_games(schedule, now, now + timedelta(days=WATCH_DAYS))
    note = "priced in the email about an hour before kickoff"
    lines = [heading(), *(f"    {row.game}: forecast wind {row.wind:.0f} mph; {note}" for row in games.itertuples())]
    if games.empty:
        lines.append("    none forecast this week")
    lines.append(f"    Stake when it comes: {_stake(bank)} on the main-line under.")
    return lines


def priced_lines(games: pd.DataFrame, snapshot: SlateSnapshot | None, bank: float | None) -> list[str]:
    """One line per windy game for the email before kickoff, at the best no-limit price, or a note without one."""
    lines = []
    for game, wind_mph in zip(games["game"], games["wind"], strict=True):
        quote = best_under(snapshot, str(game)) if snapshot else None
        wind = f"forecast wind {wind_mph:.0f} mph"
        if quote is None:
            lines.append(f"    {LINE_START} {game}: no Circa, Novig or Kalshi price yet ({wind}); check the apps")
            continue
        lines.append(
            f"    {LINE_START} {game}: under {quote.point:g} at {quote.price:+.0f} on {quote.book} ({wind}), "
            f"{_stake(bank)}"
        )
    return lines
