from datetime import UTC, datetime

import pandas as pd
import pytest

from nfl_alt_lines.plan import _final_email, _price_email, plan

pytestmark = pytest.mark.usefixtures("test_rules")  # tests/conftest.py: neutral rules, not production's

NOW = datetime(2026, 10, 14, 14, tzinfo=UTC)  # Wednesday morning, Central
THURSDAY_NIGHT = "2026-10-16T00:15:00+00:00"  # 7:15 PM Central
SUNDAY_NOON = "2026-10-18T17:00:00+00:00"
SUNDAY_NIGHT = "2026-10-19T00:20:00+00:00"
Row = dict[str, object]


def flag(game: str, kickoff: str, bet_type: str = "under", percentile: float = 0.9, bucket: str = "top") -> Row:
    return {"game_id": game, "game": game, "kickoff": kickoff, "bet_type": bet_type, "percentile": percentile,
            "bucket": bucket}  # fmt: skip


def priced(game: str, kickoff: str, book: str, odds: int, bet_type: str = "under", bucket: str = "top") -> Row:
    row = {**flag(game, kickoff, bet_type, bucket=bucket), "book": book, "bet": bet_type.title(), "line": 38.5}
    return {**row, "price": odds, "break_even": 100 / (odds + 100), "model": 0.33}


def empty_ledger() -> pd.DataFrame:
    return pd.DataFrame(columns=["game_id", "game", "kickoff", "book", "bet_type", "percentile", "bucket"])


@pytest.mark.parametrize(
    ("kickoff", "email"),
    [
        (THURSDAY_NIGHT, "Thu 02:15 PM"),  # window opens 1:15 PM Central: the 1:05 run is too early, so 2:05
        (SUNDAY_NOON, "Sun 06:15 AM"),  # window opens 6:00 AM: the 6:05 run logs it
        ("2026-10-18T17:05:00+00:00", "Sun 06:15 AM"),  # window opens 6:05 AM: the 6:05 run is inside it
    ],
    ids=["thursday-night", "sunday-noon", "window-opens-on-the-run"],
)
def test_price_email_follows_the_logger_and_report(kickoff: str, email: str) -> None:
    assert _price_email(pd.Timestamp(kickoff)).strftime("%a %I:%M %p") == email


def test_other_days_get_one_single_at_the_best_price() -> None:
    ledger = pd.DataFrame(
        [
            priced("SEA @ DEN", THURSDAY_NIGHT, "draftkings", 180),
            priced("SEA @ DEN", THURSDAY_NIGHT, "fanduel", 200),
            priced("SEA @ DEN", THURSDAY_NIGHT, "fanduel", 300, bet_type="underdog"),  # never an underdog
        ]
    )
    early = pd.DataFrame([flag("WAS @ SF", "2026-10-20T00:15:00+00:00", "over", 0.95)])
    ledger["snapshot"] = "20261016T191500Z"  # logged on game day
    text = plan(ledger, early, pd.DataFrame(), ["draftkings", "fanduel"], NOW, bank=1000)
    assert "THURSDAY OCT 15 - 1 game" in text
    assert (
        "SEA @ DEN (Thu 7:15 PM): SINGLE Under 38.5 at +200 on fanduel (best of 2 books; next best +180 on draftkings),"
        " $10.00 (1% of bankroll)"
    ) in text
    assert "MONDAY OCT 19 - 1 game" in text
    assert (
        "WAS @ SF (Mon 7:15 PM): over flagged (ranked 95 of 100)."
        " Prices come in the 2:15 PM email with the best line and book"
    ) in text
    assert "No games: Wednesday Oct 14, Saturday Oct 17, Sunday Oct 18." in text


def test_the_same_bet_goes_to_the_book_paying_most_even_when_another_ranks_higher() -> None:
    # 2026-10-18 HOU @ JAX: Under 35.5 at +198 on DraftKings ranked above +220 on FanDuel (each book's row is
    # rated against its own main total), and the email named the lower price.
    dk = {**priced("SEA @ DEN", THURSDAY_NIGHT, "draftkings", 198), "percentile": 0.95, "model": 0.38}
    ledger = pd.DataFrame([dk, priced("SEA @ DEN", THURSDAY_NIGHT, "fanduel", 220)])
    ledger["snapshot"] = "20261016T191500Z"
    text = plan(ledger, empty_ledger(), pd.DataFrame(), ["draftkings", "fanduel"], NOW, bank=1000)
    assert "SINGLE Under 38.5 at +220 on fanduel (best of 2 books; next best +198 on draftkings)" in text


def test_sunday_afternoon_is_the_round_robin_and_sunday_night_a_single() -> None:
    games = ["A", "B", "C"]
    legs = pd.DataFrame([{**priced(g, SUNDAY_NOON, "fanduel", 250), "slate": "2026-10-18"} for g in games])
    london = flag("HOU @ JAX", "2026-10-18T13:30:00+00:00")  # 8:30 AM Central
    early = pd.DataFrame([flag(g, SUNDAY_NOON) for g in games] + [flag("DAL @ GB", SUNDAY_NIGHT), london])
    text = plan(empty_ledger(), early, legs, ["fanduel"], NOW, bank=1000)
    assert "SUNDAY OCT 18 - 5 games" in text
    assert text.index("HOU @ JAX") < text.index("ROUND ROBIN at") < text.index("DAL @ GB")
    assert (
        "ROUND ROBIN at fanduel (the book with the strongest legs: the by 2s go there; each single goes to the book"
        in text
    )
    # The test rules' 2% of $1,000: half over three singles, half over three two-leg parlays.
    assert text.count("SINGLE ") == 3
    assert "SINGLE A: Under 38.5 at +250 on fanduel, $3.33" in text
    assert "(3 parlays), $3.33 (0.33% of bankroll) each" in text
    assert "DAL @ GB (Sun 7:20 PM): under flagged" in text


def test_round_robin_singles_go_to_the_book_paying_most() -> None:
    """2026-10-18: the round robin was at BetMGM and every single went there too, though DraftKings paid +263
    against +249 on TEN @ IND Under 36.5. Only the by 2s must stay at the round robin's book."""
    legs = pd.DataFrame([{**priced(g, SUNDAY_NOON, "fanduel", 250), "slate": "2026-10-18"} for g in "ABC"])
    ledger = pd.DataFrame(
        [
            priced("A", SUNDAY_NOON, "draftkings", 270),  # beats fanduel: the single goes here
            priced("B", SUNDAY_NOON, "draftkings", 250),  # a tie: the single stays at the round robin's book
            {**priced("C", SUNDAY_NOON, "draftkings", 400), "line": 41.5},  # a different line is a different bet
        ]
    )
    early = pd.DataFrame([flag(g, SUNDAY_NOON) for g in "ABC"])
    text = plan(ledger, early, legs, ["draftkings", "fanduel"], NOW, bank=1000)
    assert "SINGLE A: Under 38.5 at +270 on draftkings (beats fanduel's +250), $3.33" in text
    assert "SINGLE B: Under 38.5 at +250 on fanduel, $3.33" in text
    assert "SINGLE C: Under 38.5 at +250 on fanduel, $3.33" in text
    assert "BY 2s: fanduel's round-robin 'by 2s' option on these 3 legs" in text


@pytest.mark.parametrize(
    ("kickoff", "email"),
    [
        (SUNDAY_NOON, "10:15 AM"),  # locks 10:25: the 10:15 run is the last
        ("2026-10-18T20:05:00+00:00", "1:15 PM"),  # 3:05 PM: locks 1:30
        ("2026-10-18T18:50:00+00:00", "11:15 AM"),  # 1:50 PM: locks 12:15, too close for the 12:15 run
    ],
    ids=["noon", "three-oh-five", "lock-before-the-quarter-hour"],
)
def test_final_email_is_the_last_run_before_the_lock(kickoff: str, email: str) -> None:
    assert _final_email(pd.Timestamp(kickoff).tz_convert("America/Chicago")).strftime("%-I:%M %p") == email


def test_the_whole_plan_text_is_unchanged() -> None:
    """The plan is emailed hourly: pin one week's text exactly, so a refactor cannot change it unnoticed."""
    legs = pd.DataFrame([{**priced(g, SUNDAY_NOON, "fanduel", 250), "slate": "2026-10-18"} for g in "ABC"])
    early = pd.DataFrame([flag(g, SUNDAY_NOON) for g in "ABC"] + [flag("DAL @ GB", SUNDAY_NIGHT, bucket="middle")])
    sunday = datetime(2026, 10, 18, 17, 30, tzinfo=UTC)  # the round robin's legs have kicked off
    text = plan(empty_ledger(), early, legs, ["fanduel"], datetime(2026, 10, 18, 12, tzinfo=UTC), bank=2000)
    assert text == (
        "WHAT TO BET THIS WEEK (Sunday afternoon: singles at the best price and by 2s at one book; any other flagged"
        " game: one single)\n"
        "SUNDAY OCT 18 - 4 games\n"
        "  ROUND ROBIN at fanduel (the book with the strongest legs: the by 2s go there; each single goes to the book"
        " paying the most for it), 3 legs, place everything before 12:00 PM (legs can still change until the 10:15 AM"
        " email, then are final):\n"
        "    SINGLE A: Under 38.5 at +250 on fanduel, $6.67 (0.33% of bankroll)\n"
        "    SINGLE B: Under 38.5 at +250 on fanduel, $6.67 (0.33% of bankroll)\n"
        "    SINGLE C: Under 38.5 at +250 on fanduel, $6.67 (0.33% of bankroll)\n"
        "    BY 2s: fanduel's round-robin 'by 2s' option on these 3 legs (3 parlays), $6.67 (0.33% of bankroll) each\n"
        "    Total $40.00 (2% of bankroll). No by 3s or by 4s.\n"
        "  DAL @ GB (Sun 7:20 PM): no bet, nothing flagged\n"
        "No games: Monday Oct 19, Wednesday Oct 21, Thursday Oct 22, Saturday Oct 24.\n"
        "Stakes: Sunday afternoon $40.00 (2% of bankroll) in all, 50% as singles and 50% by 2s; every other"
        " flagged game $20.00 (1% of bankroll) as a single.\n"
        "Test rules: not a betting strategy."
    )
    assert "(legs can still change" not in plan(empty_ledger(), early, legs, ["fanduel"], sunday)


def test_sunday_without_enough_flags_says_so() -> None:
    early = pd.DataFrame([flag("A", SUNDAY_NOON), flag("B", SUNDAY_NOON, "over", 0.5, "middle")])
    text = plan(empty_ledger(), early, pd.DataFrame(), ["fanduel"], NOW)
    assert "Afternoon round robin: not built yet. Flagged so far: A (under)." in text
    assert "Add BANKROLL=<amount> to .env" in text
    ledger = pd.DataFrame(
        [priced("A", SUNDAY_NOON, "fanduel", 250), priced("B", SUNDAY_NOON, "fanduel", 250, "over", "middle")]
    )
    text = plan(ledger, early.iloc[:0], pd.DataFrame(), ["fanduel"], NOW)
    assert "Afternoon: no bets. Only 1 flagged games (A (under)); the round robin needs 3." in text


def test_sunday_with_nothing_flagged_names_none() -> None:
    early = pd.DataFrame([flag("A", SUNDAY_NOON, "over", 0.5, "middle")])
    text = plan(empty_ledger(), early, pd.DataFrame(), ["fanduel"], NOW)
    assert "Afternoon round robin: not built yet. Flagged so far: none." in text


def test_a_price_logged_days_early_says_to_wait_for_the_game_day_one() -> None:
    ledger = pd.DataFrame([{**priced("SEA @ DEN", THURSDAY_NIGHT, "fanduel", 200), "snapshot": "20261013T111518Z"}])
    text = plan(ledger, empty_ledger(), pd.DataFrame(), ["fanduel"], NOW)
    assert "SINGLE Under 38.5 at +200 on fanduel" in text
    assert "Early price: wait for the 2:15 PM email on game day" in text


@pytest.mark.parametrize(
    ("bet_type", "shown"),
    [("over", "Over 38.5 at +200"), ("favorite", "Favorite +38.5 at +200")],
    ids=["total-without-sign", "spread-with-sign"],
)
def test_totals_show_the_line_and_spreads_its_sign(bet_type: str, shown: str) -> None:
    ledger = pd.DataFrame([priced("SEA @ DEN", THURSDAY_NIGHT, "fanduel", 200, bet_type)])
    assert f"SINGLE {shown} on fanduel" in plan(ledger, empty_ledger(), pd.DataFrame(), ["fanduel"], NOW)


@pytest.mark.parametrize(
    ("bank", "stakes"),
    [
        (None, "Stakes: Sunday afternoon 2% of bankroll in all"),
        (1000.0, "Stakes: Sunday afternoon $20.00 (2% of bankroll) in all"),
        (123456.0, "Stakes: Sunday afternoon $2,469.12 (2% of bankroll) in all"),
    ],
    ids=["no-bankroll", "bankroll", "bankroll-with-thousands"],
)
def test_stakes_in_dollars_only_with_a_bankroll(bank: float | None, stakes: str) -> None:
    text = plan(empty_ledger(), empty_ledger(), pd.DataFrame(), ["fanduel"], NOW, bank)
    assert stakes in text
    assert ("Add BANKROLL=<amount> to .env" in text) is (bank is None)


def test_empty_week_says_so() -> None:
    text = plan(empty_ledger(), empty_ledger(), pd.DataFrame(), ["fanduel"], NOW)
    assert "No games in the next week." in text
    assert "No games: Wednesday Oct 14, Thursday Oct 15, Saturday Oct 17, Sunday Oct 18, Monday Oct 19." in text


@pytest.mark.parametrize(
    "kickoff",
    ["2026-10-14T13:00:00+00:00", "2026-10-21T15:00:00+00:00"],
    ids=["already-started", "beyond-a-week"],
)
def test_only_games_in_the_next_week_are_planned(kickoff: str) -> None:
    ledger = pd.DataFrame([priced("SEA @ DEN", kickoff, "fanduel", 200)])
    assert "SEA @ DEN" not in plan(ledger, empty_ledger(), pd.DataFrame(), ["fanduel"], NOW)


def test_prices_at_other_books_are_ignored() -> None:
    ledger = pd.DataFrame([priced("SEA @ DEN", THURSDAY_NIGHT, "bovada", 300)])
    assert "SEA @ DEN" not in plan(ledger, empty_ledger(), pd.DataFrame(), ["fanduel"], NOW)


def test_a_single_at_one_book_says_no_other_book_has_the_line() -> None:
    ledger = pd.DataFrame(
        [priced("SEA @ DEN", THURSDAY_NIGHT, "fanduel", 200), priced("SEA @ DEN", THURSDAY_NIGHT, "draftkings", 400)]
    )
    ledger.loc[1, "line"] = 30.5  # another line, so not a runner-up for 38.5
    text = plan(ledger, empty_ledger(), pd.DataFrame(), ["fanduel", "draftkings"], NOW)
    assert "SINGLE Under 30.5 at +400 on draftkings (the only book with this line)" in text


def test_a_round_robin_still_waiting_says_its_book_will_be_named() -> None:
    early = pd.DataFrame([flag(g, SUNDAY_NOON) for g in "ABC"])
    text = plan(empty_ledger(), early, pd.DataFrame(), ["fanduel"], NOW)
    assert "the bets and the one book to place them at appear here by the 10:15 AM email" in text


def bovada_week(*rows: Row) -> str:
    ledger = pd.DataFrame(list(rows))
    ledger["snapshot"] = "20261016T191500Z"
    books = ["draftkings", "fanduel", "kalshi", "novig", "bovada"]
    return plan(ledger, empty_ledger(), pd.DataFrame(), books, NOW, bank=1000)


def test_a_single_at_bovada_names_the_best_licensed_price_too() -> None:
    text = bovada_week(
        priced("SEA @ DEN", THURSDAY_NIGHT, "bovada", 230),
        priced("SEA @ DEN", THURSDAY_NIGHT, "fanduel", 200),
        priced("SEA @ DEN", THURSDAY_NIGHT, "draftkings", 210),
    )
    assert (
        "SINGLE Under 38.5 at +230 on bovada (best of 3 books; next best +210 on draftkings; Bovada is unlicensed:"
        " if you'd rather not, +210 on draftkings)"
    ) in text


def test_a_line_only_bovada_has_says_so() -> None:
    text = bovada_week(priced("SEA @ DEN", THURSDAY_NIGHT, "bovada", 230))
    assert "(the only book with this line; Bovada is unlicensed and no licensed book has this line)" in text


def test_a_single_at_an_exchange_warns_about_the_stake() -> None:
    text = bovada_week(
        priced("SEA @ DEN", THURSDAY_NIGHT, "kalshi", 230), priced("SEA @ DEN", THURSDAY_NIGHT, "fanduel", 200)
    )
    assert (
        "on kalshi (best of 2 books; next best +200 on fanduel; exchange: the price may only fill a small stake)"
        in text
    )


def test_a_round_robin_single_at_bovada_names_a_licensed_backup() -> None:
    legs = pd.DataFrame([{**priced(g, SUNDAY_NOON, "fanduel", 250), "slate": "2026-10-18"} for g in "ABC"])
    ledger = pd.DataFrame([priced("A", SUNDAY_NOON, "bovada", 300), priced("A", SUNDAY_NOON, "draftkings", 260)])
    early = pd.DataFrame([flag(g, SUNDAY_NOON) for g in "ABC"])
    text = plan(ledger, early, legs, ["fanduel", "draftkings", "bovada"], NOW, bank=1000)
    assert (
        "SINGLE A: Under 38.5 at +300 on bovada (beats fanduel's +250; Bovada is unlicensed: if you'd rather not,"
        " +260 on draftkings)"
    ) in text
