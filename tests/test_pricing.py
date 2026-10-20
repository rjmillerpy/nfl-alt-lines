import pytest

from nfl_alt_lines.pricing import break_even, closing_line_value, decimal_odds, expected_value


@pytest.mark.parametrize(
    ("american", "decimal"),
    [(250, 3.5), (100, 2.0), (-120, 1.8333), (-400, 1.25)],
    ids=["plus-250", "even", "minus-120", "minus-400"],
)
def test_decimal_odds(american: float, decimal: float) -> None:
    assert decimal_odds(american) == pytest.approx(decimal, abs=1e-4)


@pytest.mark.parametrize(
    ("american", "probability"),
    [(250, 0.2857), (100, 0.5), (-400, 0.8)],
    ids=["plus-250", "even", "minus-400"],
)
def test_break_even(american: float, probability: float) -> None:
    assert break_even(american) == pytest.approx(probability, abs=1e-4)


def test_expected_value_is_zero_at_break_even() -> None:
    assert expected_value(break_even(180), 0, 180) == pytest.approx(0)
    assert expected_value(0.25, 0.5, 100) == pytest.approx(0)


@pytest.mark.parametrize(
    ("american", "closing_fair", "value"),
    [(250, 1 / 3.5, 0.0), (250, 0.30, 0.05), (-150, 0.5, -1 / 6)],
    ids=["no-value", "beat-the-close", "favourite-behind-the-close"],
)
def test_closing_line_value(american: float, closing_fair: float, value: float) -> None:
    assert closing_line_value(american, closing_fair) == pytest.approx(value)
