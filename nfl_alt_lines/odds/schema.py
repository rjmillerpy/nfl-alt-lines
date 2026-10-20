"""Shapes of The Odds API's responses, validated when a response or a saved snapshot is read (rules 5, 46, 59).

Only the fields this project uses are declared; others in the payload are ignored. Every market requested
(spreads, totals and their alternates, player yardage props) has a `point` on each outcome.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Outcome(_Frozen):
    name: str  # a team for spreads, "Over"/"Under" for totals and props
    price: float  # American odds
    point: float
    description: str | None = None  # the player, for props


class Market(_Frozen):
    key: str
    last_update: datetime | None = None  # when the book last changed this market
    outcomes: tuple[Outcome, ...] = ()


class Bookmaker(_Frozen):
    key: str
    markets: tuple[Market, ...] = ()


class Event(_Frozen):
    id: str
    commence_time: datetime
    home_team: str
    away_team: str
    bookmakers: tuple[Bookmaker, ...] = ()


class EventSnapshot(_Frozen):
    """One game's prices as saved by the logger or the history download."""

    fetched_at: datetime
    response: Event


class SlateSnapshot(_Frozen):
    """Main lines for every upcoming game, as saved by the logger."""

    fetched_at: datetime
    response: tuple[Event, ...]


class HistoricalEvents(_Frozen):
    """The history endpoint's list of games at a past moment."""

    data: tuple[Event, ...]


class HistoricalEvent(_Frozen):
    """The history endpoint's snapshot of one game."""

    timestamp: datetime
    data: Event
