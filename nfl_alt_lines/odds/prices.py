"""Flattening a logged response into one price row per outcome, for the odds_prices table (db/service.py)."""

from datetime import datetime

from nfl_alt_lines.db.schema import PriceRow
from nfl_alt_lines.odds.books import book_name
from nfl_alt_lines.odds.schema import Bookmaker, Event


def selection(name: str, description: str | None, point: float) -> str:
    """What the bettor picks, unique within one game's market: name, player (for props) and point."""
    return f"{name}|{description or ''}|{point:g}"


def _book_rows(event_id: str, book: Bookmaker, fetched_at: datetime) -> list[PriceRow]:
    return [
        PriceRow(
            captured_at=(market.last_update or fetched_at).isoformat(),
            event_id=event_id,
            book=book_name(book.key),
            market=market.key,
            selection=selection(outcome.name, outcome.description, outcome.point),
            name=outcome.name,
            description=outcome.description,
            point=outcome.point,
            price=outcome.price,
            fetched_at=fetched_at.isoformat(),
        )
        for market in book.markets
        for outcome in market.outcomes
    ]


def price_rows(events: tuple[Event, ...], fetched_at: datetime) -> list[PriceRow]:
    """Every outcome at every book, stamped with the book's last update (or the fetch time if it gave none)."""
    return [row for event in events for book in event.bookmakers for row in _book_rows(event.id, book, fetched_at)]
