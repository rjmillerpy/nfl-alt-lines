from datetime import UTC, datetime

from nfl_alt_lines.odds.prices import price_rows, selection
from nfl_alt_lines.odds.schema import Event
from tests.odds.payloads import event, main_book, outcome

FETCHED = datetime(2026, 10, 18, 12, tzinfo=UTC)
UPDATED = "2026-10-18T11:58:00Z"


def test_selection_is_unique_within_a_market() -> None:
    assert selection("Over", None, 44.5) == "Over||44.5"
    assert selection("Over", "Bo Nix", 230.0) == "Over|Bo Nix|230"


def test_price_rows_flatten_every_outcome_with_the_books_update_time() -> None:
    book = main_book("williamhill_us")
    book["markets"][0]["last_update"] = UPDATED
    prop = {"key": "player_pass_yds_alternate", "outcomes": [outcome("Over", 150, 239.5, "Bo Nix")]}
    book["markets"].append(prop)
    rows = price_rows((Event.model_validate(event(bookmakers=[book])),), FETCHED)
    assert len(rows) == 5
    spread = rows[0]
    assert (spread.book, spread.market, spread.event_id) == ("caesars", "spreads", "e1")
    assert spread.captured_at == "2026-10-18T11:58:00+00:00"
    assert rows[2].captured_at == FETCHED.isoformat()  # no last_update: the fetch time stands in
    assert (rows[4].description, rows[4].selection) == ("Bo Nix", "Over|Bo Nix|239.5")
