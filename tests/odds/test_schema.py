import pytest
from pydantic import ValidationError

from nfl_alt_lines.odds.schema import Event, EventSnapshot, HistoricalEvent, Outcome, SlateSnapshot
from tests.odds.payloads import event, main_book, outcome, snapshot


def test_event_snapshot_parses_and_ignores_extra_fields() -> None:
    parsed = EventSnapshot.model_validate_json(snapshot("2026-10-18T12:00:00Z", event(bookmakers=[main_book()])))
    game = parsed.response
    assert (game.id, game.home_team, game.away_team) == ("e1", "Atlanta Falcons", "Chicago Bears")
    assert game.commence_time.isoformat() == "2026-10-18T17:00:00+00:00"
    assert game.bookmakers[0].markets[0].outcomes[0] == Outcome(name="Atlanta Falcons", price=-110, point=-3.5)


def test_slate_snapshot_holds_many_events() -> None:
    parsed = SlateSnapshot.model_validate_json(snapshot("2026-10-18T12:00:00Z", [event("a"), event("b")]))
    assert [game.id for game in parsed.response] == ["a", "b"]


def test_prop_outcomes_keep_the_player() -> None:
    assert Outcome.model_validate(outcome("Over", 150, 64.5, "Player One")).description == "Player One"


@pytest.mark.parametrize(
    ("field", "value"),
    [("commence_time", "not a time"), ("id", None), ("bookmakers", [{"markets": []}])],
    ids=["bad-kickoff", "missing-id", "book-without-key"],
)
def test_malformed_events_are_rejected(field: str, value: object) -> None:
    payload = event()
    payload[field] = value
    with pytest.raises(ValidationError):
        Event.model_validate(payload)


def test_outcome_without_a_point_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Outcome.model_validate({"name": "Over", "price": -110})


def test_models_are_immutable() -> None:
    game = Event.model_validate(event())
    with pytest.raises(ValidationError):
        game.id = "changed"


def test_historical_event_wraps_one_game() -> None:
    found = HistoricalEvent.model_validate({"timestamp": "2025-01-01T00:00:00Z", "data": event()})
    assert found.data.id == "e1"
