import logging
from datetime import date
from pathlib import Path

import pytest

from nfl_alt_lines import page
from nfl_alt_lines.common import storage
from nfl_alt_lines.common.storage import LocalStorage
from nfl_alt_lines.page import BLUE, GREEN, GREY, RED, render, write_page

REPORT = """Run at 20261013T150000Z; ledger: data/ledger/predictions.csv

WHAT TO BET THIS WEEK (Sunday afternoon: singles and by 2s at one book; any other flagged game: one single)
SUNDAY OCT 18 - 2 games
  ROUND ROBIN at fanduel, 3 legs, place everything before 12:00 PM:
    SINGLE CHI @ ATL: Under 38.5 at +250 on fanduel, 0.58% of bankroll
    BY 2s: the round-robin 'by 2s' option on these 3 legs (3 parlays), 0.25% each
  DAL @ GB (Sun 7:20 PM): no bet, nothing flagged
Test rules: not a betting strategy.

AT A GLANCE (ranker percentile: 20 or lower = stay away, above 80 = worth a look)

DAL @ GB  (Sun Oct 18, 07:20 PM)  56F, wind 19 mph (windy)  - early line, prices arrive on game day
  STAY AWAY:    favorite (17)
  WORTH A LOOK: under (100) - strongest backtest evidence
  no opinion:   over (34)

CHI @ ATL  (Sun Oct 18, 12:00 PM)
  STAY AWAY:    nothing flagged
  WORTH A LOOK: underdog (97)
  no opinion:   over <b> (34)

SUNDAY ROUND ROBIN DETAILS at draftkings (one leg per game; stakes in the plan)
None yet.

PAPER PROPS-ONLY ROUND ROBIN at draftkings
None yet.

============================== DRAFTKINGS ==============================

No logged games have finished yet.
"""


def test_page_has_a_card_per_game_and_lists_only_real_unders() -> None:
    page = render(REPORT)
    assert page.count("font-size:17px") == 2
    assert "Unders worth a look:</b> DAL @ GB<" in page
    assert "Draftkings tables" in page and "Round robin details" in page
    assert page.count("<pre") == 3  # two round-robin boxes and one book table
    assert "<b> (34)" not in page  # report text is escaped


def test_plan_card_comes_first_with_bets_highlighted() -> None:
    page = render(REPORT)
    assert page.index("WHAT TO BET THIS WEEK") < page.index("Unders worth a look") < page.index("Run at")
    assert "font-weight:bold'>SINGLE CHI @ ATL" in page
    assert "border-bottom:1px solid #d9dce1'>SUNDAY OCT 18 - 2 games" in page


def test_report_without_book_sections_renders() -> None:
    """Regression: render() zipped the book starts with one extra end, so a report with no
    "=== BOOK ===" section raised ValueError (zip strict). It now renders the other sections."""
    rendered = render("WHAT TO BET THIS WEEK\n  DAL @ GB (Sun 7:20 PM): no bet, nothing flagged")
    assert "WHAT TO BET THIS WEEK" in rendered
    assert "tables (scroll sideways)" not in rendered


@pytest.mark.parametrize(
    ("line", "shown"),
    [
        ("  STAY AWAY:    favorite (17)", f"color:{RED};font-weight:bold'>Stay away: favorite (17)"),
        ("  STAY AWAY:    nothing flagged", f"color:{GREY};font-weight:normal'>Stay away: nothing flagged"),
        ("  WORTH A LOOK: over (90)", f"color:{GREEN};font-weight:bold'>Worth a look: over (90)"),
        ("  best price:   +250 at fanduel", f"color:{GREEN};font-weight:normal'>Best price: +250 at fanduel"),
        ("  PROP LEAN:    Joe over 60.5", f"color:{BLUE};font-weight:bold'>Prop lean: Joe over 60.5"),
        ("  no opinion:   over (34)", f"color:{GREY};font-weight:normal'>No opinion: over (34)"),
        ("  injury note & more", "<div style='margin:3px 0'>injury note &amp; more</div>"),
    ],
    ids=["stay-away", "nothing-flagged", "worth-a-look", "best-price", "prop-lean", "no-opinion", "unlabelled"],
)
def test_game_card_lines_are_labelled_and_coloured(line: str, shown: str) -> None:
    assert shown in render(f"DAL @ GB  (Sun Oct 18, 07:20 PM)\n{line}")


@pytest.mark.parametrize(
    ("line", "shown"),
    [
        ("    SINGLE A: Under 38.5", f"margin:4px 0 4px 14px;color:{GREEN};font-weight:bold'>SINGLE A"),
        ("  DAL @ GB (Sun 7:20 PM): no bet, nothing flagged", f"margin:4px 0 4px 0px;color:{GREY}'>DAL"),
        ("  Afternoon: no bets. Only 1 flagged game", f"margin:4px 0 4px 0px;color:{GREY}'>Afternoon"),
        ("  A (Thu 7:15 PM): under flagged", "margin:4px 0 4px 0px;'>A (Thu"),
        ("Stakes: 2% of bankroll", f"margin:3px 0;font-size:12px;color:{GREY}'>Stakes"),
    ],
    ids=["bet", "no-bet", "no-bets", "waiting", "footer"],
)
def test_plan_lines_are_styled_by_what_they_say(line: str, shown: str) -> None:
    assert shown in render(f"WHAT TO BET THIS WEEK\n{line}")


def test_game_without_a_worth_a_look_under_has_a_plain_edge_and_no_summary() -> None:
    rendered = render("DAL @ GB  (Sun Oct 18, 07:20 PM)  56F - early line\n  WORTH A LOOK: over (90)")
    assert "border-left:5px solid #d9dce1" in rendered
    assert "Unders worth a look" not in rendered
    assert "Sun Oct 18, 07:20 PM &middot; 56F &middot; early line" in rendered


def test_each_book_gets_its_own_tables() -> None:
    report = "=== DRAFTKINGS ===\n\ntable one\n\ntable two\n=== FANDUEL ===\n\ntable three"
    rendered = render(report)
    assert rendered.index("Draftkings tables") < rendered.index("table two") < rendered.index("Fanduel tables")
    assert rendered.count("<pre") == 3


def test_write_page_turns_the_text_report_into_html(tmp_path: Path) -> None:
    store = LocalStorage(tmp_path)
    store.write(page.REPORT, REPORT.encode())
    write_page(store)
    assert store.read(page.PAGE).decode() == render(REPORT)


def test_main_writes_the_latest_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = LocalStorage(tmp_path)
    store.write(page.REPORT, REPORT.encode())
    monkeypatch.setattr(logging.getLogger(), "handlers", [])  # main() replaces them; put them back afterwards
    page.main(store)
    assert store.read(page.PAGE).decode() == render(REPORT)


def test_main_uses_the_default_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = LocalStorage(tmp_path)
    store.write(page.REPORT, REPORT.encode())
    monkeypatch.setattr(storage, "default_storage", lambda: store)
    monkeypatch.setattr(logging.getLogger(), "handlers", [])
    page.main()
    assert store.exists(page.PAGE)


def test_dated_report_key() -> None:
    assert page.dated_report(date(2026, 10, 18)) == "reports/2026-10-18.txt"
