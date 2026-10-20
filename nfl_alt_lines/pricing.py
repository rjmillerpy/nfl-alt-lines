"""Odds arithmetic."""


def decimal_odds(american: float) -> float:
    return 1 + (american / 100 if american > 0 else 100 / -american)


def american_odds(decimal: float) -> float:
    return (decimal - 1) * 100 if decimal >= 2 else -100 / (decimal - 1)  # noqa: PLR2004 - even money


def break_even(american: float) -> float:
    """Win probability at which a bet at this price returns exactly zero."""
    return 1 / decimal_odds(american)


def expected_value(win_prob: float, push_prob: float, american: float) -> float:
    """Expected profit per 1 unit staked; pushes return the stake."""
    lose_prob = 1 - win_prob - push_prob
    return win_prob * (decimal_odds(american) - 1) - lose_prob


def closing_line_value(american: float, closing_fair: float) -> float:
    """Expected profit per unit of a bet at this price if the margin-free closing probability is the true one.

    Positive means the bet beat the closing market.
    """
    return expected_value(closing_fair, 0, american)
