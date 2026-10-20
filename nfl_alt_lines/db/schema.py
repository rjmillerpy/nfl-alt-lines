"""Row shapes of the four ledgers (rules 46, 50): one pydantic model per table, validated on every write.

models.py builds each table from these, so a column is defined once. Rows are grouped by a key (a game or a
round robin's game day) that the service rewrites as a unit.
"""

from dataclasses import dataclass
from typing import get_args

from pydantic import BaseModel, ConfigDict


class _Row(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PredictionRow(_Row):
    """One alternate line at one book, as ranked and priced (ledger.py)."""

    game_id: str
    game: str
    kickoff: str
    snapshot: str
    book: str
    bet_type: str
    side: str
    bet: str
    line: float
    shift: float
    price: int
    break_even: float
    market_fair: float | None = None
    other_books: int
    base: float
    model: float
    percentile: float
    bucket: str
    season: int
    week: int
    recorded: str
    model_version: str
    close_fair: float | None = None
    close_price: float | None = None
    close_books: int | None = None
    clv: float | None = None


class PropRow(_Row):
    """One alternate player-yardage line at one book (prop_report.py)."""

    game_id: str
    game: str
    kickoff: str
    snapshot: str
    book: str
    market: str
    player: str
    player_id: str
    line: float
    market_line: float
    shift: float
    price: int
    break_even: float
    projection: float | None = None
    model: float | None = None  # empty for a player the prop model does not know
    shape: float | None = None
    others_implied: float | None = None
    other_books: int
    season: int
    week: int
    recorded: str
    recent_games: float | None = None
    recent_hits: float | None = None
    history: float | None = None


class RoundRobinLeg(_Row):
    """One leg of a game day's paper round robin of alternate lines (round_robin.py)."""

    slate: str
    book: str
    game_id: str
    game: str
    kickoff: str
    bet_type: str
    side: str
    bet: str
    line: float
    shift: float
    price: int
    break_even: float
    model: float
    percentile: float
    role: str


class PropRoundRobinLeg(_Row):
    """One leg of a game day's paper round robin of prop leans (round_robin.py)."""

    slate: str
    book: str
    game_id: str
    game: str
    kickoff: str
    market: str
    player: str
    player_id: str
    line: float
    shift: float
    price: int
    break_even: float
    model: float
    role: str


def _types(annotation: object) -> tuple[object, ...]:
    return get_args(annotation) or (annotation,)


class PriceRow(_Row):
    """One outcome's price as logged (roadmap item 13). `captured_at` is the book's last update of the market,
    so logging unchanged prices again gives the same row, which the unique key turns away."""

    captured_at: str
    event_id: str
    book: str
    market: str
    selection: str  # name|player|point: unique within a game's market
    name: str
    description: str | None = None
    point: float
    price: float
    fetched_at: str


PRICE_KEY = ("captured_at", "event_id", "book", "market", "selection")


class RunRow(_Row):
    """One run of a job (roadmap item 22): when, how it ended, and what it did. A count is None when the job
    does not do that thing (the logger ranks no plays), 0 when it could have and did not."""

    job: str
    run_id: str
    started_at: str  # ISO 8601, UTC
    ended_at: str
    seconds: float
    outcome: str  # "ok" or "failed"
    error: str | None = None  # the exception's type only: its text can hold addresses (rule 18)
    games_priced: int | None = None
    rows_written: int | None = None
    plays_ranked: int | None = None
    emails_sent: int | None = None


@dataclass(frozen=True)
class Ledger:
    """A ledger table: its name, its row shape, and the column whose rows are rewritten together."""

    name: str
    row: type[_Row]
    key: str

    @property
    def text_columns(self) -> frozenset[str]:
        return frozenset(name for name, field in self.row.model_fields.items() if str in _types(field.annotation))


PREDICTIONS = Ledger("predictions", PredictionRow, "game_id")
PROPS = Ledger("props", PropRow, "game_id")
ROUND_ROBINS = Ledger("round_robins", RoundRobinLeg, "slate")
PROP_ROUND_ROBINS = Ledger("round_robins_props", PropRoundRobinLeg, "slate")
LEDGERS = (PREDICTIONS, PROPS, ROUND_ROBINS, PROP_ROUND_ROBINS)
