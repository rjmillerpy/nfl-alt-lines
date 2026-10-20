"""The private weekly results email: last week's bets as the plan emailed them, graded, plus the season so far.

Sent once a week, after the Tuesday 9:15 AM report once every game of the week (Tuesday to Monday) has a final
score, or on Wednesday with whatever is still open marked as pending. It goes only to REPORT_EMAIL_TO (the list
that gets every email), never to REPORT_EMAIL_BETS_TO, and shows stakes and profit as a share of the bankroll:
no bankroll is stored, so nobody else can see what is being spent.

It assumes every bet was placed as the emails said: each game's single at the price of the last email before
kickoff (the ledger keeps that price), and the round robin as it stood when it locked.
"""

import logging
import smtplib
import sys
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from math import comb
from pathlib import Path

import numpy as np
import pandas as pd

from nfl_alt_lines import run_status, strategy
from nfl_alt_lines.common import storage
from nfl_alt_lines.common.settings import ENV_FILE, Settings, SettingsError, load_settings
from nfl_alt_lines.common.storage import Storage
from nfl_alt_lines.db import service
from nfl_alt_lines.db.schema import PREDICTIONS, ROUND_ROBINS
from nfl_alt_lines.db.service import LedgerStore, LedgerStoreError
from nfl_alt_lines.glance import LOCAL_ZONE
from nfl_alt_lines.ledger import settle
from nfl_alt_lines.nflverse import load_schedule
from nfl_alt_lines.notify import FAILED_EXIT_CODE, EmailSendError, SmtpConnect, send
from nfl_alt_lines.odds.books import SINGLES_BOOKS
from nfl_alt_lines.page import REPORTS
from nfl_alt_lines.plan import bet_text, flagged_picks, round_robin_single
from nfl_alt_lines.pricing import closing_line_value
from nfl_alt_lines.round_robin import afternoon, combo_return

SENT_MARK = f"{REPORTS}/.last_results"  # the Tuesday that ended the last week emailed
SEND_HOUR = 9  # local: the 9:15 AM report run
GIVE_UP = timedelta(days=1)  # a day after the first chance, send with unfinished games marked pending
TUESDAY = 1
SUBJECT = "NFL alt lines results"
DATE_FORMAT = "%a %b %d"
PENDING = "pending"
COLUMNS = ["kickoff", "game", "bet", "stake", "profit", "clv"]

log = logging.getLogger(__name__)


def week_end(now: datetime) -> pd.Timestamp:
    """Midnight starting the latest Tuesday at or before `now`, local: the end of the NFL week before it."""
    local = pd.Timestamp(now).tz_convert(LOCAL_ZONE)
    days_back = (local.dayofweek - TUESDAY) % 7
    return (local - timedelta(days=days_back)).normalize()


def _scores(schedule: pd.DataFrame) -> pd.DataFrame:
    return schedule.dropna(subset=["home_score", "away_score"]).set_index("game_id")[["home_score", "away_score"]]


def _profit(row: pd.Series, scores: pd.DataFrame) -> float | None:
    """Profit per unit staked, or None while the game has no final score."""
    if row["game_id"] not in scores.index:
        return None
    return settle(row, *scores.loc[row["game_id"]])


def _clv(ledger: pd.DataFrame, bet: pd.Series) -> float:
    """The bet's expected return at the no-vig closing consensus (clv.py), or NaN without a closing snapshot.

    Computed at the bet's own price: a round robin leg may have locked at a different price than the ledger's.
    """
    if "close_fair" not in ledger:
        return np.nan
    same = ledger[
        (ledger["game_id"] == bet["game_id"]) & (ledger["bet"] == bet["bet"]) & (ledger["line"] == bet["line"])
    ]["close_fair"].dropna()
    return closing_line_value(float(bet["price"]), float(same.iloc[0])) if len(same) else np.nan


def singles(ledger: pd.DataFrame, scores: pd.DataFrame) -> pd.DataFrame:
    """Each game's single as the plan picked it at its last pre-kickoff price, Sunday afternoon excluded."""
    rows = ledger[(ledger["recorded"] == "before kickoff") & ledger["book"].isin(SINGLES_BOOKS)]
    local = pd.to_datetime(rows["kickoff"], utc=True).dt.tz_convert(LOCAL_ZONE)
    rows = rows[~afternoon(local)]
    if rows.empty:
        return pd.DataFrame(columns=COLUMNS)
    picks = flagged_picks(rows)
    return pd.DataFrame(
        {
            "kickoff": picks["kickoff"],
            "game": picks["game"],
            "bet": ["SINGLE " + bet_text(pick) for _, pick in picks.iterrows()],
            "stake": strategy.load().staking.single_stake,
            "profit": [_profit(pick, scores) for _, pick in picks.iterrows()],
            "clv": [_clv(ledger, pick) for _, pick in picks.iterrows()],
        }
    )


def _robin_rows(legs: pd.DataFrame, prices: pd.DataFrame, scores: pd.DataFrame) -> list[dict[str, object]]:
    """One round robin: its singles (each at the best price, as emailed) and its by 2s as one line."""
    count, book = len(legs), legs["book"].iloc[0]
    stakes = strategy.load().staking
    single = stakes.sunday_budget * stakes.singles_share / count
    rows: list[dict[str, object]] = []
    for _, leg in legs.iterrows():
        bet = round_robin_single(prices, leg)
        rows.append({"kickoff": leg["kickoff"], "game": leg["game"], "bet": "ROUND ROBIN SINGLE " + bet_text(bet),
                     "stake": single, "profit": _profit(bet, scores), "clv": _clv(prices, bet)})  # fmt: skip
    results = [_profit(leg, scores) for _, leg in legs.iterrows()]
    parlays = stakes.sunday_budget * (1 - stakes.singles_share)
    paid = None if None in results else combo_return([1 + float(r) for r in results if r is not None], 2)
    rows.append({"kickoff": legs["kickoff"].min(), "game": f"{count} legs",
                 "bet": f"BY 2s at {book}: {comb(count, 2)} parlays", "stake": parlays,
                 "profit": None if paid is None else paid - 1, "clv": np.nan})  # fmt: skip
    return rows


def round_robins(robins: pd.DataFrame, ledger: pd.DataFrame, scores: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, legs in robins.groupby("slate"):
        rows += _robin_rows(legs, ledger, scores)
    return pd.DataFrame(rows, columns=COLUMNS)


def bets(ledgers: LedgerStore, schedule: pd.DataFrame) -> pd.DataFrame:
    """Every bet the plan has emailed, graded where the game is final: stake and profit as bankroll shares."""
    ledger, scores = ledgers.load(PREDICTIONS), _scores(schedule)
    out = pd.concat([singles(ledger, scores), round_robins(ledgers.load(ROUND_ROBINS), ledger, scores)])
    out["kickoff"] = pd.to_datetime(out["kickoff"], utc=True).dt.tz_convert(LOCAL_ZONE)
    out["profit_share"] = out["stake"] * out["profit"].astype(float)
    return out.sort_values(["kickoff", "bet"]).reset_index(drop=True)


def _line(bet: pd.Series) -> str:
    if pd.isna(bet["profit"]):
        outcome, profit = PENDING, ""
    else:
        outcome = "won" if bet["profit"] > 0 else "push" if bet["profit"] == 0 else "lost"
        profit = f"{100 * bet['profit_share']:+.2f}%"
    clv = "" if pd.isna(bet["clv"]) else f", CLV {100 * bet['clv']:+.1f}%"
    when = bet["kickoff"].strftime(DATE_FORMAT)
    return f"{when}  {bet['game']}: {bet['bet']}, stake {100 * bet['stake']:.2f}%  {outcome} {profit}{clv}".rstrip()


def _total(label: str, rows: pd.DataFrame) -> str:
    done = rows[rows["profit"].notna()]
    staked, profit = done["stake"].sum(), done["profit_share"].sum()
    back = f" (return {100 * profit / staked:+.0f}% on the stakes)" if staked else ""
    closed = rows["clv"].dropna()
    clv = f"; average CLV {100 * closed.mean():+.1f}% over {len(closed)} bets" if len(closed) else ""
    return (
        f"{label}: {len(done)} bets settled, staked {100 * staked:.2f}% of bankroll, profit {100 * profit:+.2f}%"
        f"{back}{clv}"
    )


def summary(graded: pd.DataFrame, end: pd.Timestamp) -> str:
    """The email body: the week's bets one per line, the week's total, and the season so far."""
    start = end - timedelta(days=7)
    week = graded[(graded["kickoff"] >= start) & (graded["kickoff"] < end)]
    to_date = graded[graded["kickoff"] < end]
    last_day = (end - timedelta(days=1)).strftime(DATE_FORMAT)
    lines = [f"Week {start.strftime(DATE_FORMAT)} to {last_day}, as a share of your bankroll:", ""]
    lines += [_line(bet) for _, bet in week.iterrows()] or ["No bets this week."]
    lines += ["", _total("This week", week), _total("Season so far", to_date), ""]
    lines.append(
        "Assumes every bet was placed as the emails said, at the last price before kickoff; the round robin as it"
        " stood when it locked. Multiply by your bankroll for dollars (it is not stored anywhere)."
        " CLV is each bet's expected return at the no-vig closing price: consistently above 0 is the earliest sign"
        " of a real edge, long before wins and losses can show one."
    )
    return "\n".join(lines)


def due(store: Storage, now: datetime, graded: pd.DataFrame) -> pd.Timestamp | None:
    """The week to send, or None: not yet Tuesday morning, already sent, no bets that week, or games still open
    (until GIVE_UP)."""
    end = week_end(now)
    first_chance = end + timedelta(hours=SEND_HOUR)
    if pd.Timestamp(now) < first_chance:
        return None
    if store.exists(SENT_MARK) and store.read(SENT_MARK).decode() == end.date().isoformat():
        return None
    week = graded[(graded["kickoff"] >= end - timedelta(days=7)) & (graded["kickoff"] < end)]
    if week.empty:  # 2026-10-19: the first run emailed an empty week (Oct 6-12, before any bets) at 5:16 AM
        return None
    if week["profit"].isna().any() and pd.Timestamp(now) < first_chance + GIVE_UP:
        return None
    return end


def message(body: str, end: pd.Timestamp, settings: Settings) -> EmailMessage:
    """Only to the every-email list: the bets-only list never sees results or stakes."""
    email = EmailMessage()
    subject = f"{SUBJECT} through {(end - timedelta(days=1)).strftime(DATE_FORMAT)}"
    prefix = settings.report_subject_prefix
    email["Subject"] = f"{prefix} {subject}" if prefix else subject
    email["From"] = settings.smtp_user
    email["To"] = ", ".join(settings.recipients)
    email.set_content(body)
    return email


def send_results(
    settings: Settings,
    now: datetime,
    store: Storage,
    ledgers: LedgerStore,
    schedule: pd.DataFrame,
    connect: SmtpConnect = smtplib.SMTP_SSL,
) -> bool:
    """Email last week's results if they are due; returns whether one was sent."""
    if not settings.smtp_user or settings.smtp_app_password is None:
        return False
    graded = bets(ledgers, schedule)
    end = due(store, now, graded)
    if end is None:
        return False
    password = settings.smtp_app_password.get_secret_value().replace(" ", "")
    send(message(summary(graded, end), end, settings), settings.smtp_user, password, connect)
    store.write(SENT_MARK, end.date().isoformat().encode())  # only after a successful send: a failure retries
    run_status.count("emails_sent")
    log.info("results emailed", extra={"week_ending": end.date().isoformat()})
    return True


def main(
    connect: SmtpConnect = smtplib.SMTP_SSL,
    env_file: Path = ENV_FILE,
    store: Storage | None = None,
    now: datetime | None = None,
) -> None:
    """Run after the hourly report and its email; does nothing most hours."""
    try:
        files = store or storage.default_storage()
        sent = send_results(
            load_settings(env_file),
            now or datetime.now(UTC),
            files,
            service.default_ledgers(),
            load_schedule(store=files),
            connect,
        )
    except (SettingsError, LedgerStoreError, EmailSendError) as problem:
        # No traceback (TRY400): the chained low-level error's text can hold addresses.
        log.error("results email not sent; the next run tries again", extra={"reason": str(problem)})  # noqa: TRY400
        sys.exit(FAILED_EXIT_CODE)
    if not sent:
        log.debug("results email not due")


def preview(now: datetime | None = None, store: Storage | None = None) -> str:
    """The summary of the week in progress (Tuesday to now), sent to nobody: `nfl-alt-lines results`."""
    files = store or storage.default_storage()
    graded = bets(service.default_ledgers(), load_schedule(store=files))
    return summary(graded, week_end(now or datetime.now(UTC)) + timedelta(days=7))
