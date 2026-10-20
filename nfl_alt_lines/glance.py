"""This week's text: per upcoming game what to stay away from and what is worth a look, and per book the best edges.

Both read the ledger (game-day prices) and, before prices are logged, the early ranks from the schedule lines.
"""

from datetime import datetime
from typing import cast

import pandas as pd

from nfl_alt_lines import strategy
from nfl_alt_lines.pricing import decimal_odds
from nfl_alt_lines.weather import INDOOR_ROOFS, likely_roof

LOCAL_ZONE = "America/Chicago"
SUNDAY = 6  # pandas day of week
WINDY_MPH = 12  # where the backtest's wind effect on totals showed up
TOTAL_BETS = ("over", "under")
KICKOFF_FORMAT = "%a %b %d, %I:%M %p"
EDGE_COLUMNS = ["game", "bet", "line", "shift", "price", "break_even", "base", "model", "edge", "ev", "bucket"]
MARKET_COLUMNS = ["game", "bet", "line", "price", "break_even", "market_fair", "market_edge", "other_books", "bucket"]
GLANCE_HEADER = "AT A GLANCE (ranker percentile: 20 or lower = stay away, above 80 = worth a look)"


def _upcoming(ledger: pd.DataFrame, now: datetime) -> pd.DataFrame:
    return ledger[pd.to_datetime(ledger["kickoff"], utc=True) > now]


def _best_edges(ahead: pd.DataFrame, top: int) -> pd.DataFrame:
    """The `top` lines whose model probability most exceeds the price's break-even, skip bucket removed."""
    playable = ahead[ahead["bucket"] != "skip"].copy()
    playable["edge"] = 100 * (playable["model"] - playable["break_even"])
    playable["ev"] = 100 * (playable["model"] * (playable["price"].map(decimal_odds) - 1) - (1 - playable["model"]))
    for column in ("break_even", "base", "model"):
        playable[column] = 100 * playable[column]
    return playable.sort_values("edge", ascending=False).head(top)


def _market_edges(ahead: pd.DataFrame, top: int) -> str:
    """The `top` lines this book pays more on than the other books' margin-free price."""
    mispriced = ahead.dropna(subset=["market_fair"]).copy()
    mispriced["market_edge"] = 100 * (mispriced["market_fair"] - mispriced["break_even"])
    for column in ("break_even", "market_fair"):
        mispriced[column] = 100 * mispriced[column]
    mispriced = mispriced[mispriced["market_edge"] > 0].sort_values("market_edge", ascending=False).head(top)
    if not len(mispriced):
        return "none right now"
    return mispriced[MARKET_COLUMNS].round(1).to_string(index=False)


def upcoming_report(ledger: pd.DataFrame, book: str, now: datetime, top: int) -> str:
    ahead = _upcoming(ledger, now)
    ahead = ahead[ahead["book"] == book]
    if ahead.empty:
        return "No prices logged yet for upcoming games (they are captured in the six hours before each kickoff)."
    ratings = ahead.drop_duplicates(["game", "bet_type"]).pivot(index="game", columns="bet_type", values="percentile")
    # A book can post alternate spreads before alternate totals, so a bet type may be missing for now.
    ratings = (100 * ratings.reindex(columns=list(strategy.load().bet_types))).round(0).astype("Int64")
    return "\n".join(
        [
            "Ranker percentile by game and bet type (20 or lower = skip, above 80 = top):",
            ratings.to_string(),
            "",
            f"Best edges at {book} by the ranker, skip-bucket bet types removed:",
            _best_edges(ahead, top)[EDGE_COLUMNS].round(1).to_string(index=False),
            "",
            f"Lines where {book} pays more than the other books' fair price (needs 2+ other books):",
            _market_edges(ahead, top),
        ]
    )


def low_scoring(ledger: pd.DataFrame, early: pd.DataFrame, now: datetime) -> set[str]:
    """Upcoming games the ranker expects to score little: under worth a look, or over stay away.

    Every prop the model can price is a player over, which needs the opposite game, so these games get no props.
    The game-day rating is used once prices are logged, the early one before that.
    """
    priced = _upcoming(ledger, now)
    rows = pd.concat([priced, early[~early["game_id"].isin(priced["game_id"])]])
    is_under_top = (rows["bet_type"] == "under") & (rows["bucket"] == "top")
    is_over_skip = (rows["bet_type"] == "over") & (rows["bucket"] == "skip")
    return set(rows.loc[is_under_top | is_over_skip, "game"])


def weather_notes(schedule: pd.DataFrame) -> dict[str, str]:
    """game_id -> kickoff weather for open-air games that have it."""
    open_air = ~likely_roof(schedule).isin(INDOOR_ROOFS) & schedule["wind"].notna() & schedule["temp"].notna()
    games = schedule[open_air]
    return {
        game_id: f"{temp:.0f}F, wind {wind:.0f} mph" + (" (windy)" if wind >= WINDY_MPH else "")
        for game_id, temp, wind in zip(games["game_id"], games["temp"], games["wind"], strict=True)
    }


def _best_price(rows: pd.DataFrame, bet_type: str) -> str:
    """The logged line of this bet type with the largest model edge over its price."""
    options = rows[rows["bet_type"] == bet_type]
    best = options.loc[cast(int, (options["model"] - options["break_even"]).idxmax())]
    line = f"{best['line']:g}" if bet_type in TOTAL_BETS else f"{best['line']:+g}"
    return (
        f"                best price: {best['bet']} {line} at {best['price']:+.0f} on {best['book']}"
        f" (needs {100 * best['break_even']:.1f}%, model {100 * best['model']:.1f}%)"
    )


def _flags(types: pd.DataFrame) -> tuple[list[str], list[str], list[str], dict[str, str]]:
    """(stay away, worth a look, no opinion) bet types for one game, and each type's label with its percentile."""
    described = {name: f"{name} ({100 * types.loc[name, 'percentile']:.0f})" for name in types.index}
    skip = [described[name] for name in types.index if types.loc[name, "bucket"] == "skip"]
    recommendable = strategy.load().recommendable
    top = [name for name in types.index if types.loc[name, "bucket"] == "top" and name in recommendable]
    middle = [described[name] for name in types.index if described[name] not in skip and name not in top]
    return skip, top, middle, described


def _game_block(game: str, rows: pd.DataFrame, prop_leans: dict[str, list[str]], weather: dict[str, str]) -> list[str]:
    local = rows["kickoff_at"].iloc[0].tz_convert(LOCAL_ZONE)
    has_prices = rows["book"].iloc[0] != ""
    note = "" if has_prices else "  - early line, prices arrive on game day"
    outlook = weather.get(rows["game_id"].iloc[0])
    outlook = f"  {outlook}" if outlook else ""
    lines = ["", f"{game}  ({local.strftime(KICKOFF_FORMAT)}){outlook}{note}"]
    types = rows.drop_duplicates("bet_type").set_index("bet_type").reindex(list(strategy.load().bet_types))
    types = types.dropna(how="all")
    skip, top, middle, described = _flags(types)
    lines.append(f"  STAY AWAY:    {', '.join(skip) or 'nothing flagged'}")
    if not top:
        lines.append("  WORTH A LOOK: nothing flagged")
    for name in top:
        lines.append(f"  WORTH A LOOK: {described[name]}{strategy.load().evidence[name]}")
        if has_prices:
            lines.append(_best_price(rows, name))
    lines.append(f"  no opinion:   {', '.join(middle) or 'none'}")
    return lines + [f"  PROP LEAN:    {lean}" for lean in prop_leans.get(game, [])]


def at_a_glance(
    ledger: pd.DataFrame,
    early: pd.DataFrame,
    books: list[str],
    now: datetime,
    prop_leans: dict[str, list[str]],
    weather: dict[str, str],
) -> str:
    """Per upcoming game: bet types to stay away from, bet types worth a look, and the best price once logged."""
    priced = _upcoming(ledger, now)
    priced = priced[priced["book"].isin(books)]
    early = early[~early["game_id"].isin(priced["game_id"])]
    games = pd.concat([priced, early.assign(book="")], ignore_index=True)
    if games.empty:
        return "\n".join([GLANCE_HEADER, "No upcoming games have lines yet."])
    games["kickoff_at"] = pd.to_datetime(games["kickoff"], utc=True)
    lines = [GLANCE_HEADER]
    for game, rows in games.sort_values("kickoff_at").groupby("game", sort=False):
        lines += _game_block(str(game), rows, prop_leans, weather)
    footer = ["", "Early-line flags can change if the line moves; only the game-day version is graded."]
    return "\n".join([*lines, *footer, *strategy.load().glance_notes])
