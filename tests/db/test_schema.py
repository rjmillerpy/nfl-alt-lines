import pytest
from pydantic import ValidationError

from nfl_alt_lines.db.schema import LEDGERS, PREDICTIONS, PROPS, PredictionRow


def prediction(**changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "game_id": "2026_07_CHI_ATL", "game": "CHI @ ATL", "kickoff": "2026-10-18T17:00:00+00:00",
        "snapshot": "20261018T120000Z", "book": "draftkings", "bet_type": "under", "side": "under", "bet": "Under",
        "line": 37.5, "shift": 7.0, "price": 250, "break_even": 0.2857, "other_books": 3, "base": 0.2, "model": 0.22,
        "percentile": 0.9, "bucket": "top", "season": 2026, "week": 7, "recorded": "before kickoff",
        "model_version": "test-version",
    }  # fmt: skip
    return {**row, **changes}


def test_a_prediction_row_validates_with_optional_columns_empty() -> None:
    row = PredictionRow.model_validate(prediction())
    assert (row.market_fair, row.clv, row.close_books) == (None, None, None)


@pytest.mark.parametrize(
    "changes", [{"price": "not a price"}, {"surprise": 1}, {"game_id": None}], ids=["bad-type", "extra", "missing"]
)
def test_a_bad_prediction_row_is_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PredictionRow.model_validate(prediction(**changes))


def test_every_ledger_has_its_key_among_its_columns() -> None:
    assert [ledger.name for ledger in LEDGERS] == ["predictions", "props", "round_robins", "round_robins_props"]
    assert all(ledger.key in ledger.row.model_fields for ledger in LEDGERS)
    assert (PREDICTIONS.key, PROPS.key) == ("game_id", "game_id")
