"""Which bet to place and where: each game's flagged bet, its bigger line, and the book paying the most for it.

Split from plan.py, which writes the plan's text; results.py grades the same picks.
"""

import pandas as pd

from nfl_alt_lines import strategy
from nfl_alt_lines.odds.books import EXCHANGES, OFFSHORE


def same_bet(rows: pd.DataFrame, pick: pd.Series) -> pd.DataFrame:
    """Every book's price for the pick's exact bet: same game, side and line, so the same result pays out."""
    same: pd.DataFrame = rows[
        (rows["game_id"] == pick["game_id"]) & (rows["bet_type"] == pick["bet_type"]) & (rows["line"] == pick["line"])
    ]
    return same


def best_price(rows: pd.DataFrame, pick: pd.Series) -> pd.Series:
    """The pick at the book paying the most for it. The model rates each book's row against that book's own main
    line, so the same bet can rank higher at a book paying less; the bet wins or loses the same everywhere."""
    return same_bet(rows, pick).sort_values("price", ascending=False, kind="stable").iloc[0]


def book_note(rows: pd.DataFrame, bet: pd.Series) -> str:
    """A warning for a bet at an exchange or an offshore book: an exchange's posted price may only fill a small
    stake, and Bovada is licensed nowhere, so the best licensed price for the same bet is named as well."""
    if bet["book"] in EXCHANGES:
        return "; exchange: the price may only fill a small stake"
    if bet["book"] not in OFFSHORE:
        return ""
    same = same_bet(rows, bet)
    licensed = same[~same["book"].isin(OFFSHORE)]
    if licensed.empty:
        return "; Bovada is unlicensed and no licensed book has this line"
    backup = licensed.sort_values("price", ascending=False, kind="stable").iloc[0]
    return f"; Bovada is unlicensed: if you'd rather not, {backup['price']:+.0f} on {backup['book']}"


def shop(rows: pd.DataFrame, pick: pd.Series) -> str:
    """How the pick's price compares: the books with the same line, the runner-up's price, and any book note."""
    same = same_bet(rows, pick)
    others = same[same["book"] != pick["book"]]
    note = book_note(rows, pick)
    if others.empty:
        return f"the only book with this line{note}"
    second = others.loc[others["price"].idxmax()]
    return f"best of {same['book'].nunique()} books; next best {second['price']:+.0f} on {second['book']}{note}"


def flagged_picks(rows: pd.DataFrame) -> pd.DataFrame:
    """Each game's flagged bet: an under first, else the highest-ranked type; priced rows keep the best-edge line.

    The chosen line is then bet at the book paying the most for it (`best_price`).
    """
    rows = rows[(rows["bucket"] == "top") & rows["bet_type"].isin(strategy.load().recommendable)]
    edge = rows["model"] - rows["break_even"] if "price" in rows else 0.0
    rows = rows.assign(order=rows["bet_type"] != "under", edge=edge)
    order = ["order", "percentile", "edge"]
    picks = rows.sort_values(order, ascending=[True, False, False]).drop_duplicates("game_id")
    if "price" in rows:
        picks = pd.DataFrame([best_price(rows, pick) for _, pick in picks.iterrows()], columns=picks.columns)
        picks = picks.assign(shop=[shop(rows, pick) for _, pick in picks.iterrows()])
    return picks


def big_line_picks(rows: pd.DataFrame) -> pd.DataFrame:
    """Each top-ranked under's bigger line: among its lines moved `big_line_shifts` points, the best-edge one the
    model prices at or above break-even, at the book paying the most for it, never the game's ordinary single
    (`flagged_picks`): the bigger line is an extra bet. Empty when the stake is 0."""
    stakes = strategy.load().staking
    if stakes.big_line_stake <= 0 or "price" not in rows or "shift" not in rows or rows.empty:
        return rows.iloc[0:0]
    single = flagged_picks(rows)
    taken = set(zip(single["game_id"], single["bet_type"], single["line"], strict=True))
    rows = rows[[key not in taken for key in zip(rows["game_id"], rows["bet_type"], rows["line"], strict=True)]]
    low, high = stakes.big_line_shifts
    rows = rows[
        (rows["bucket"] == "top")
        & (rows["bet_type"] == "under")
        & rows["shift"].between(low, high)
        & (rows["model"] >= rows["break_even"])
    ]
    if rows.empty:
        return rows
    rows = rows.assign(edge=rows["model"] - rows["break_even"])
    picks = rows.sort_values("edge", ascending=False, kind="stable").drop_duplicates("game_id")
    picks = pd.DataFrame([best_price(rows, pick) for _, pick in picks.iterrows()], columns=picks.columns)
    return picks.assign(shop=[shop(rows, pick) for _, pick in picks.iterrows()])


def bet_text(row: pd.Series) -> str:
    line = f"{row['line']:g}" if row["bet_type"] in ("over", "under") else f"{row['line']:+g}"
    return f"{row['bet']} {line} at {row['price']:+.0f} on {row['book']}"
