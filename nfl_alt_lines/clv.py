"""Closing line value: each ledger line's price against the market's margin-free price at the close.

Once a game kicks off, its rows get close_fair (the margin-free probability averaged over every book still
posting that exact line in the closing snapshot: the no-vig consensus), close_price (the same book's closing
price), close_books and clv (the bet's expected return at close_fair). CLV settles long before profit does.
"""

from datetime import datetime

import numpy as np
import pandas as pd

from nfl_alt_lines import strategy
from nfl_alt_lines.alt_lines import ALT_SPREADS, ALT_TOTALS, FairPrices, LineKey, fair_probabilities
from nfl_alt_lines.odds.schema import Event
from nfl_alt_lines.odds.snapshots import markets
from nfl_alt_lines.pricing import closing_line_value

CLOSE_COLUMNS = ["close_fair", "close_price", "close_books", "clv"]
TOTAL_SIDES = ("over", "under")
TOP_BUCKET = "top"


def _line_key(row: pd.Series) -> LineKey:
    market = ALT_TOTALS if row["side"] in TOTAL_SIDES else ALT_SPREADS
    return market, str(row["bet"]), float(row["line"])


def _closing_price(event: Event, book: str, key: LineKey) -> float:
    market, name, point = key
    offered = markets(event).get(book, {}).get(market, ())
    return next((float(o.price) for o in offered if (o.name, o.point) == (name, point)), np.nan)


def closing_values(row: pd.Series, event: Event, fair: FairPrices) -> list[float] | None:
    """[close_fair, close_price, close_books, clv] for one ledger row, or None if the line was gone at the close."""
    key = _line_key(row)
    found = [book_fair[key] for book_fair in fair.values() if key in book_fair]
    if not found:
        return None
    consensus = float(np.mean(found))
    price = _closing_price(event, str(row["book"]), key)
    return [consensus, price, float(len(found)), closing_line_value(float(row["price"]), consensus)]


def add_closing(ledger: pd.DataFrame, closing: dict[str, Event], now: datetime) -> pd.DataFrame:
    """Fill the closing columns of rows whose game has kicked off, has a closing snapshot and has none yet."""
    ledger = ledger.copy()
    for column in CLOSE_COLUMNS:
        if column not in ledger:
            ledger[column] = np.nan
    if ledger.empty:
        return ledger
    started = pd.to_datetime(ledger["kickoff"], utc=True) <= now
    todo = ledger.index[started & ledger["clv"].isna() & ledger["game_id"].isin(closing.keys())]
    fair = {game_id: fair_probabilities(closing[game_id]) for game_id in ledger.loc[todo, "game_id"].unique()}
    for i in todo:
        game_id = str(ledger.at[i, "game_id"])
        values = closing_values(ledger.loc[i], closing[game_id], fair[game_id])
        if values is not None:
            ledger.loc[i, CLOSE_COLUMNS] = values
    return ledger


def clv_headline(done: pd.DataFrame) -> str:
    """Average CLV over every graded line with a closing price, and over the top-bucket plays."""
    with_close = done.dropna(subset=["clv"])
    if with_close.empty:
        return "CLV: no closing prices yet (the logger takes them at the last hourly run before kickoff)."
    recommendable = strategy.load().recommendable
    plays = with_close[with_close["bet_type"].isin(recommendable) & (with_close["bucket"] == TOP_BUCKET)]
    parts = [f"all lines {100 * with_close['clv'].mean():+.1f}% (n={len(with_close)})"]
    if len(plays):
        parts.append(f"top-bucket plays {100 * plays['clv'].mean():+.1f}% (n={len(plays)})")
    return "CLV, expected return at the no-vig closing consensus: " + ", ".join(parts) + "."
