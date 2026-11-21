"""What to bet on each game day of the coming week, under the staking plan from the round-robin replay.

Replaying 2023-25 Sundays at real prices (round_robin_backtest), the round robin's legs made money as singles
and by 2s, while by 3s and 4s swung on one or two Sundays; none of it is proven. So the plan is:

- Sunday afternoon: the round robin's legs, the strategy's Sunday budget of the bankroll, part of it as singles and
  the rest spread over every two-leg parlay, all at the book the round robin was built at. With too few legs for a
  round robin, each flagged bet the model prices at or above break-even is still bet as a single (the owner's
  choice, 2026-11-15).
- Every other flagged game (Wednesday, Thursday, Saturday, Monday, a Sunday-morning game abroad, or Sunday
  night, which is priced after the round robin is fixed): one single of the strategy's single stake at the best price.
- Bigger lines (the owner's choice, 2026-11-20): when the strategy sets a bigger-line stake, each top-ranked
  under also gets an extra single on a line moved further (the strategy's band), priced at or above break-even.
- Nothing else: no stay-away lines, underdogs, by 3s or 4s, or props, which are untested against real prices.

A game still unpriced an hour after its prices were due (`PRICE_GRACE`) is marked NO PRICES: on 2026-11-15
a deactivated Odds API key left a whole Sunday unpriced, and the plan read like an ordinary day with no bets.

The caller passes the bankroll (BANKROLL in .env, read by load_settings); without it the stakes are shown
as percentages only.
"""

from datetime import datetime, timedelta
from math import comb

import pandas as pd

from nfl_alt_lines import strategy
from nfl_alt_lines.glance import LOCAL_ZONE
from nfl_alt_lines.odds.snapshots import TS_FORMAT
from nfl_alt_lines.picks import best_price, bet_text, big_line_picks, book_note, flagged_picks, same_bet
from nfl_alt_lines.round_robin import DAY_FORMAT, LOCK_BEFORE, SLATE_FORMAT, afternoon, locked

PLAN_DAYS = 7
LOG_WINDOW = timedelta(hours=6)  # odds_logger's default: a game is priced once it is this close to kickoff
LOG_MINUTE, EMAIL_MINUTE = 5, 15  # cron: the logger runs at :05, the report and email at :15
RUN_START_MARGIN = timedelta(minutes=10)  # a scheduled task starts a minute or two late; Fargate retries more
GAME_DAYS = ("Wednesday", "Thursday", "Saturday", "Sunday", "Monday")
PRICES_MISSING = "NO PRICES"
PRICE_GRACE = timedelta(hours=1)  # a late logger run gets an hour before a game counts as unpriced


def _stake(share: float, bank: float | None) -> str:
    percent = f"{100 * share:.2f}".rstrip("0").rstrip(".") + "% of bankroll"
    return f"${bank * share:,.2f} ({percent})" if bank else percent


def _clock(when: pd.Timestamp) -> str:
    return when.strftime("%I:%M %p").lstrip("0")


def _price_email(kickoff: pd.Timestamp) -> pd.Timestamp:
    """When the email with this game's prices goes out: the first logger run inside the window, plus the report."""
    opens = kickoff - LOG_WINDOW
    run = opens.floor("h") + timedelta(minutes=LOG_MINUTE)
    if run < opens:
        run += timedelta(hours=1)
    return (run + timedelta(minutes=EMAIL_MINUTE - LOG_MINUTE)).tz_convert(LOCAL_ZONE)


def _overdue(kickoff: str | pd.Timestamp, now: datetime) -> bool:
    """True once a game's prices are more than PRICE_GRACE past the email that should have carried them."""
    return pd.Timestamp(now) >= _price_email(pd.Timestamp(kickoff)) + PRICE_GRACE


def _final_email(first_kickoff: pd.Timestamp) -> pd.Timestamp:
    """The last email before a round robin locks (round_robin.LOCK_BEFORE): what it says is what gets bet."""
    lock = first_kickoff - LOCK_BEFORE
    run = lock.floor("h") + timedelta(minutes=EMAIL_MINUTE)
    return run if run + RUN_START_MARGIN <= lock else run - timedelta(hours=1)


def _big_lines(rows: pd.DataFrame, prices: pd.DataFrame, bank: float | None) -> list[str]:
    """One game day's bigger lines, in kickoff order, as extra singles."""
    stakes = strategy.load().staking
    picks = big_line_picks(prices[prices["game_id"].isin(rows["game_id"])]) if len(prices) else prices
    if picks.empty:
        return []
    low, high = stakes.big_line_shifts
    lines = [
        f"  BIGGER LINES (an extra single on each top-ranked under, moved {low:g}-{high:g} points;"
        f" on top of the bets above){stakes.big_line_note}:"
    ]
    for _, pick in picks.sort_values(["kickoff", "game"]).iterrows():
        lines.append(
            f"    BIGGER LINE SINGLE {pick['game']}: {bet_text(pick)} ({pick['shop']}),"
            f" {_stake(stakes.big_line_stake, bank)}"
            f" (needs {100 * pick['break_even']:.1f}%, model {100 * pick['model']:.1f}%)"
            f"{_early_price(pick, pd.Timestamp(pick['kickoff']))}"
        )
    return lines


def _single(game: pd.Series, picks: pd.DataFrame, now: datetime, bank: float | None) -> str:
    where = f"  {game['game']} ({game['local'].strftime('%a')} {_clock(game['local'])})"
    if game["game_id"] not in picks.index:
        return f"{where}: no bet, nothing flagged"
    pick = picks.loc[game["game_id"]]
    if pd.isna(pick.get("price")):
        email = _clock(_price_email(pd.Timestamp(game["kickoff"])))
        if _overdue(game["kickoff"], now):
            return (
                f"{where}: {pick['bet_type']} flagged (ranked {100 * pick['percentile']:.0f} of 100). {PRICES_MISSING}:"
                f" they were due in the {email} email, so there is no line or book to bet yet"
            )
        return (
            f"{where}: {pick['bet_type']} flagged (ranked {100 * pick['percentile']:.0f} of 100). Prices come in the"
            f" {email} email with the best line and book; if it is still flagged, bet that as a single,"
            f" {_stake(strategy.load().staking.single_stake, bank)}"
        )
    kickoff = pd.Timestamp(game["kickoff"])
    bet = (
        f"{where}: SINGLE {bet_text(pick)} ({pick['shop']}), {_stake(strategy.load().staking.single_stake, bank)}"
        f" (needs {100 * pick['break_even']:.1f}%, model {100 * pick['model']:.1f}%)"
    )
    return bet + _early_price(pick, kickoff)


def _early_price(pick: pd.Series, kickoff: pd.Timestamp) -> str:
    """A note for a price logged before game day (notify.py's EARLY_PRICE keeps it off the bets-only list)."""
    snapshot = pd.to_datetime(str(pick.get("snapshot", "")), format=TS_FORMAT, utc=True, errors="coerce")
    if pd.notna(snapshot) and snapshot < kickoff - LOG_WINDOW:
        return f". Early price: wait for the {_clock(_price_email(kickoff))} email on game day and bet what it shows"
    return ""


def round_robin_single(prices: pd.DataFrame, leg: pd.Series) -> pd.Series:
    """A round-robin leg bet as a single: at the book paying the most for that exact bet. Only the by 2s must
    stay at the round robin's book; on a tie the single stays there too."""
    same = same_bet(prices, leg) if len(prices) else prices
    if same.empty or same["price"].max() <= leg["price"]:
        return leg
    return best_price(prices, leg)


def _afternoon_singles(
    games: pd.DataFrame, prices: pd.DataFrame, names: str, needed: int, bank: float | None
) -> list[str]:
    """Sunday afternoon without a round robin: each flagged bet the model prices at or above break-even, as a single
    at the best price; or no bets, when there is none."""
    rows = prices[prices["game_id"].isin(games["game_id"]) & (prices["model"] >= prices["break_even"])]
    picks = flagged_picks(rows)
    if picks.empty:
        return [f"  Afternoon: no bets. None of the flagged bets ({names}) is priced at or above break-even."]
    stake = _stake(strategy.load().staking.single_stake, bank)
    lines = [
        f"  Afternoon: no round robin (it needs {needed} flagged bets priced at or above break-even at one book)."
        f" Bet {'this' if len(picks) == 1 else 'each'} as a single at its best price, before kickoff:"
    ]
    for _, pick in picks.sort_values(["kickoff", "game"]).iterrows():
        lines.append(
            f"    SINGLE {pick['game']}: {bet_text(pick)} ({pick['shop']}), {stake}"
            f" (needs {100 * pick['break_even']:.1f}%, model {100 * pick['model']:.1f}%)"
            f"{_early_price(pick, pd.Timestamp(pick['kickoff']))}"
        )
    return lines


def _without_round_robin(
    games: pd.DataFrame, picks: pd.DataFrame, prices: pd.DataFrame, now: datetime, bank: float | None
) -> list[str]:
    """Sunday afternoon with no round robin: singles once every game is priced, else what it is waiting for."""
    first, needed = games["local"].min(), strategy.load().min_legs
    flagged = games[games["game_id"].isin(picks.index)]
    kinds = flagged["game_id"].map(picks["bet_type"])
    names = ", ".join(f"{game} ({kind})" for game, kind in zip(flagged["game"], kinds, strict=True)) or "none"
    if games["game_id"].isin(prices["game_id"]).all():
        return _afternoon_singles(games, prices, names, needed, bank)
    unpriced = games[~games["game_id"].isin(prices["game_id"])]
    if any(_overdue(kickoff, now) for kickoff in unpriced["kickoff"]):
        return [
            f"  Afternoon round robin: {PRICES_MISSING} for {', '.join(unpriced['game'])}; they were due in the"
            f" {_clock(_price_email(pd.Timestamp(unpriced['kickoff'].min())))} email. Flagged so far: {names}."
        ]
    return [
        f"  Afternoon round robin: not built yet. Flagged so far: {names}.",
        (
            f"    If {needed} or more are still flagged when prices come in, the bets and the one book to place"
            f" them at appear here by the 10:15 AM email; place them before the {_clock(first)} kickoff."
            " With fewer, each flagged bet priced at or above break-even is a single."
        ),
    ]


def _round_robin(
    games: pd.DataFrame,
    picks: pd.DataFrame,
    legs: pd.DataFrame,
    prices: pd.DataFrame,
    now: datetime,
    bank: float | None,
) -> list[str]:
    """Sunday afternoon: the round robin's bets with stakes, or what it is waiting for."""
    if legs.empty:
        return _without_round_robin(games, picks, prices, now, bank)
    count, pairs = len(legs), comb(len(legs), 2)
    kickoffs = pd.to_datetime(legs["kickoff"], utc=True).dt.tz_convert(LOCAL_ZONE)
    book = legs["book"].iloc[0]
    final = _clock(_final_email(kickoffs.min()))
    settled = (
        "" if locked(legs["kickoff"], now) else f" (legs can still change until the {final} email, then are final)"
    )
    lines = [
        f"  ROUND ROBIN at {book} (the book with the strongest legs: the by 2s go there; each single goes to the book"
        f" paying the most for it), {count} legs, place everything before {_clock(kickoffs.min())}{settled}:"
    ]
    stakes = strategy.load().staking
    single = stakes.sunday_budget * stakes.singles_share / count
    for _, leg in legs.iterrows():
        bet = round_robin_single(prices, leg)
        beats = f" (beats {book}'s {leg['price']:+.0f}{book_note(prices, bet)})" if bet["book"] != book else ""
        lines.append(f"    SINGLE {leg['game']}: {bet_text(bet)}{beats}, {_stake(single, bank)}")
    pair = stakes.sunday_budget * (1 - stakes.singles_share) / pairs
    lines += [
        f"    BY 2s: {book}'s round-robin 'by 2s' option on these {count} legs ({pairs} parlays),"
        f" {_stake(pair, bank)} each",
        f"    Total {_stake(stakes.sunday_budget, bank)}. No by 3s or by 4s.",
    ]
    return lines


def _upcoming(
    ledger: pd.DataFrame, early: pd.DataFrame, books: list[str], now: datetime
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Games in the next PLAN_DAYS days in kickoff order, the priced lines at `books`, and each game's pick."""
    end = now + timedelta(days=PLAN_DAYS)

    def ahead(frame: pd.DataFrame) -> pd.DataFrame:
        kickoff = pd.to_datetime(frame["kickoff"], utc=True)
        return frame[(kickoff > now) & (kickoff <= end)]

    priced = ahead(ledger)
    priced = priced[priced["book"].isin(books)]
    early = ahead(early)
    early = early[~early["game_id"].isin(priced["game_id"])]
    games = pd.concat([priced, early])[["game_id", "game", "kickoff"]].drop_duplicates("game_id")
    games["local"] = pd.to_datetime(games["kickoff"], utc=True).dt.tz_convert(LOCAL_ZONE)
    games = games.sort_values("local")
    picks = pd.concat([flagged_picks(priced), flagged_picks(early)]).set_index("game_id")
    return games, priced, picks


def _day(
    slate: str,
    rows: pd.DataFrame,
    picks: pd.DataFrame,
    robins: pd.DataFrame,
    prices: pd.DataFrame,
    now: datetime,
    bank: float | None,
) -> list[str]:
    """One game day: its heading, then each game's bet in time order."""
    count = len(rows)
    lines = [f"{rows['local'].iloc[0].strftime(DAY_FORMAT).upper()} - {count} game{'s' * (count > 1)}"]
    in_robin = afternoon(rows["local"])
    # In time order: a Sunday-morning game abroad, then the afternoon round robin, then the night game.
    before = ~in_robin & (rows["local"] < rows.loc[in_robin, "local"].min()) if in_robin.any() else ~in_robin
    lines += [_single(game, picks, now, bank) for _, game in rows[before].iterrows()]
    if in_robin.any():
        legs = robins[robins["slate"] == slate] if len(robins) else robins
        lines += _round_robin(rows[in_robin], picks, legs, prices, now, bank)
    lines += [_single(game, picks, now, bank) for _, game in rows[~in_robin & ~before].iterrows()]
    return lines + _big_lines(rows, prices, bank)


def _quiet_days(games: pd.DataFrame, now: datetime) -> list[str]:
    """Game days (GAME_DAYS) in the next PLAN_DAYS days with no game."""
    today = pd.Timestamp(now).tz_convert(LOCAL_ZONE)
    dates = [today + timedelta(days=offset) for offset in range(PLAN_DAYS)]
    played = set(games["local"].dt.strftime(SLATE_FORMAT))
    return [
        d.strftime(DAY_FORMAT)
        for d in dates
        if d.strftime("%A") in GAME_DAYS and d.strftime(SLATE_FORMAT) not in played
    ]


def _rules(bank: float | None) -> list[str]:
    """The stakes and the strategy's standing rules under every plan."""
    rules = strategy.load()
    stakes = rules.staking
    lines = [
        (
            f"Stakes: Sunday afternoon {_stake(stakes.sunday_budget, bank)} in all, {100 * stakes.singles_share:.0f}%"
            f" as singles and {100 * (1 - stakes.singles_share):.0f}% by 2s; every other flagged game"
            f" {_stake(stakes.single_stake, bank)}"
            " as a single."
        ),
        *rules.standing_rules,
    ]
    if stakes.big_line_stake > 0:
        low, high = stakes.big_line_shifts
        lines.insert(
            1,
            f"Bigger lines: each top-ranked under also gets one line moved {low:g}-{high:g} points, priced at or above"
            f" break-even, {_stake(stakes.big_line_stake, bank)} as an extra single.",
        )
    if bank is None:
        lines.append("Add BANKROLL=<amount> to .env to see the stakes in dollars.")
    return lines


def plan(
    ledger: pd.DataFrame,
    early: pd.DataFrame,
    robins: pd.DataFrame,
    books: list[str],
    now: datetime,
    bank: float | None = None,
) -> str:
    """Day by day for the next PLAN_DAYS days: every game with what to bet, how much, where and when."""
    games, priced, picks = _upcoming(ledger, early, books, now)
    lines = [
        "WHAT TO BET THIS WEEK (Sunday afternoon: singles at the best price and by 2s at one book; any other flagged"
        " game: one single)"
    ]
    unpriced = games[~games["game_id"].isin(priced["game_id"])]
    missing = unpriced[[_overdue(kickoff, now) for kickoff in unpriced["kickoff"]]]
    if len(missing):
        lines.append(
            f"{PRICES_MISSING} for {', '.join(missing['game'])}: the odds feed has not delivered their lines, so"
            ' "no bet" for these games is not the model\'s answer. Check the odds logger.'
        )
    for slate, rows in games.groupby(games["local"].dt.strftime(SLATE_FORMAT), sort=True):
        lines += _day(str(slate), rows, picks, robins, priced, now, bank)
    empty = _quiet_days(games, now)
    if not len(games):
        lines.append("No games in the next week.")
    if empty:
        lines.append(f"No games: {', '.join(empty)}.")
    return "\n".join(lines + _rules(bank))
