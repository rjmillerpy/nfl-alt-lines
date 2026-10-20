import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql

from nfl_alt_lines.common.settings import Settings
from nfl_alt_lines.db import service
from nfl_alt_lines.db.models import PRICES, RUNS, TABLES
from nfl_alt_lines.db.schema import PREDICTIONS, ROUND_ROBINS, PriceRow, RunRow
from nfl_alt_lines.db.service import (
    DEFAULT_URL,
    LedgerStore,
    LedgerStoreError,
    _insert_ignoring_duplicates,
    add_prices,
    all_prices,
    changed_keys,
    database_url,
    default_ledgers,
    open_ledgers,
    recent_runs,
    record_run,
    records,
)
from tests.db.test_schema import prediction


def frame(*rows: dict[str, object]) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


@pytest.fixture
def store(tmp_path: Path) -> LedgerStore:
    return open_ledgers(f"sqlite:///{tmp_path / 'ledger.db'}")


def rows_in(store: LedgerStore) -> int:
    with store.engine.connect() as connection:
        return int(connection.execute(select(func.count()).select_from(TABLES["predictions"])).scalar_one())


def test_save_then_load_round_trips(store: LedgerStore) -> None:
    saved = frame(prediction(), prediction(line=38.5, clv=0.05), prediction(game_id="g2", market_fair=np.nan))
    assert store.save(PREDICTIONS, saved) == 2
    loaded = store.load(PREDICTIONS)
    assert len(loaded) == 3
    assert loaded["price"].dtype == np.int64
    assert loaded["clv"].isna().tolist() == [True, False, True]
    assert loaded["market_fair"].isna().iloc[2]


def test_an_unchanged_save_writes_nothing(store: LedgerStore) -> None:
    # A retried or double-fired run is harmless (roadmap item 13).
    saved = frame(prediction(), prediction(game_id="g2"))
    store.save(PREDICTIONS, saved)
    assert store.save(PREDICTIONS, store.load(PREDICTIONS)) == 0
    assert store.save(PREDICTIONS, saved) == 0
    assert rows_in(store) == 2


def test_only_changed_games_are_rewritten(store: LedgerStore) -> None:
    store.save(PREDICTIONS, frame(prediction(), prediction(game_id="g2"), prediction(game_id="g3")))
    after = frame(prediction(clv=0.1), prediction(game_id="g2"), prediction(game_id="g4"))  # g1 changed, g3 gone
    assert store.save(PREDICTIONS, after) == 3
    loaded = store.load(PREDICTIONS)
    assert sorted(loaded["game_id"]) == ["2026_07_CHI_ATL", "g2", "g4"]
    assert loaded.loc[loaded["game_id"] == "2026_07_CHI_ATL", "clv"].iloc[0] == 0.1


def test_saves_in_batches(store: LedgerStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "WRITE_BATCH", 2)
    monkeypatch.setattr(service, "READ_CHUNK", 2)
    many = frame(*(prediction(game_id=f"g{i}") for i in range(5)))
    assert store.save(PREDICTIONS, many) == 5
    assert len(store.load(PREDICTIONS)) == 5


def test_an_empty_ledger_loads_with_its_columns(store: LedgerStore) -> None:
    empty = store.load(ROUND_ROBINS)
    assert empty.empty
    assert list(empty.columns) == list(ROUND_ROBINS.row.model_fields)
    assert store.save(ROUND_ROBINS, empty) == 0


def test_invalid_rows_are_refused_and_nothing_is_written(store: LedgerStore) -> None:
    with pytest.raises(LedgerStoreError, match="validation failed for predictions"):
        store.save(PREDICTIONS, frame(prediction(price="lots")))
    assert rows_in(store) == 0


def test_records_fill_missing_optional_columns() -> None:
    found = records(frame(prediction()), PREDICTIONS)
    assert found[0]["close_fair"] is None


def test_changed_keys_ignore_row_order_and_representation() -> None:
    first, second = prediction(), prediction(line=38.5)
    assert changed_keys(frame(first, second), frame(second, first), PREDICTIONS) == set()
    # As read back from a database: a whole-number float and None for missing.
    as_stored = frame({**first, "price": 250.0, "clv": None}, second)
    assert changed_keys(as_stored, frame(first, second), PREDICTIONS) == set()
    assert changed_keys(frame(first), frame(second), PREDICTIONS) == {"2026_07_CHI_ATL"}
    assert changed_keys(frame(first), frame(), PREDICTIONS) == {"2026_07_CHI_ATL"}


def test_database_errors_are_ledger_errors(store: LedgerStore) -> None:
    store.engine.dispose()
    broken = LedgerStore(open_ledgers("sqlite://").engine)
    with broken.engine.begin() as connection:
        TABLES["predictions"].drop(connection)
    with pytest.raises(LedgerStoreError, match="read failed"):
        broken.load(PREDICTIONS)
    with pytest.raises(LedgerStoreError, match="read failed"):
        broken.save(PREDICTIONS, frame(prediction()))


def test_a_database_that_cannot_open_is_a_ledger_error(tmp_path: Path) -> None:
    with pytest.raises(LedgerStoreError, match="open failed for sqlite"):
        open_ledgers(f"sqlite:///{tmp_path / 'missing' / 'ledger.db'}")


def test_database_url_defaults_to_sqlite_in_data() -> None:
    assert database_url(Settings()) == DEFAULT_URL
    assert (
        database_url(Settings.model_validate({"database_url": "postgresql+psycopg://db-host/ledgers"}))
        == "postgresql+psycopg://db-host/ledgers"
    )


def test_default_ledgers_is_built_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        service, "load_settings", lambda: Settings.model_validate({"database_url": f"sqlite:///{tmp_path}/x.db"})
    )
    default_ledgers.cache_clear()
    try:
        assert default_ledgers() is default_ledgers()
    finally:
        default_ledgers.cache_clear()


def price(captured_at: str = "2026-10-18T11:58:00+00:00", **changes: object) -> PriceRow:
    row: dict[str, object] = {
        "captured_at": captured_at, "event_id": "e1", "book": "draftkings", "market": "alternate_totals",
        "selection": "Under||44.5", "name": "Under", "point": 44.5, "price": 240.0,
        "fetched_at": "2026-10-18T12:00:00+00:00",
    }  # fmt: skip
    return PriceRow.model_validate({**row, **changes})


def prices_in(store: LedgerStore) -> int:
    with store.engine.connect() as connection:
        return int(connection.execute(select(func.count()).select_from(PRICES)).scalar_one())


def test_the_same_price_update_is_stored_once(store: LedgerStore) -> None:
    assert add_prices(store, [price(), price(selection="Over||44.5", name="Over")]) == 2
    # Logged again, even at a later fetch: the book's update time is the same, so nothing new.
    assert add_prices(store, [price(fetched_at="2026-10-18T13:00:00+00:00")]) == 0
    assert add_prices(store, [price("2026-10-18T12:30:00+00:00", price=230.0)]) == 1  # the book moved
    assert prices_in(store) == 3


def test_price_write_errors_are_ledger_errors(store: LedgerStore) -> None:
    with store.engine.begin() as connection:
        PRICES.drop(connection)
    with pytest.raises(LedgerStoreError, match="write failed for odds_prices"):
        add_prices(store, [price()])


def test_postgres_gets_its_own_insert() -> None:
    dialect = postgresql.psycopg.dialect()  # type: ignore[no-untyped-call]  # SQLAlchemy leaves it unannotated
    assert "ON CONFLICT" in str(_insert_ignoring_duplicates("postgresql").compile(dialect=dialect))


SEASON_ROWS = 80_000  # about a season of the predictions ledger: ~270 games x ~300 lines
SEASON_SECONDS = 5.0  # generous: about 0.7 s on the laptop, so only a real slowdown fails it


def test_performance_a_season_rewrites_only_what_changed(store: LedgerStore) -> None:
    season = frame(*(prediction(game_id=f"g{i // 300}", line=37.5 + i % 300) for i in range(SEASON_ROWS)))
    store.save(PREDICTIONS, season)
    started = time.perf_counter()
    assert store.save(PREDICTIONS, season) == 0
    changed = season.assign(clv=np.where(season["game_id"] == "g5", 0.1, np.nan))
    assert store.save(PREDICTIONS, changed) == 1
    assert time.perf_counter() - started < SEASON_SECONDS
    assert len(store.load(PREDICTIONS)) == SEASON_ROWS


def test_all_prices_reads_back_what_was_added(ledgers: LedgerStore) -> None:
    rows = [price(), price("2026-10-18T12:30:00+00:00", price=230.0)]
    add_prices(ledgers, rows)
    assert all_prices(ledgers) == rows


def test_all_prices_of_an_empty_database(ledgers: LedgerStore) -> None:
    assert all_prices(ledgers) == []


def run_row(job: str, outcome: str) -> RunRow:
    started, ended = "2026-10-18T09:05:00+00:00", "2026-10-18T09:05:12+00:00"
    return RunRow(job=job, run_id="abcd1234", outcome=outcome, started_at=started, ended_at=ended, seconds=12.0)


def test_runs_come_back_newest_first_per_job(ledgers: LedgerStore) -> None:
    for job, outcome in [("report", "ok"), ("odds-logger", "ok"), ("report", "failed"), ("report", "ok")]:
        record_run(ledgers, run_row(job, outcome))
    assert [row.outcome for row in recent_runs(ledgers, "report", 2)] == ["ok", "failed"]
    assert recent_runs(ledgers, "odds-logger", 5) == [run_row("odds-logger", "ok")]


def test_runs_table_failures_are_store_errors() -> None:
    broken = open_ledgers("sqlite://")
    with broken.engine.begin() as connection:
        RUNS.drop(connection)
    with pytest.raises(LedgerStoreError, match="write failed for runs"):
        record_run(broken, run_row("report", "ok"))
    with pytest.raises(LedgerStoreError, match="read failed for runs"):
        recent_runs(broken, "report", 1)
