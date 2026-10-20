"""The ledger store (roadmap item 12): the four ledgers in a database, SQLite by default (a file in data/) or
Postgres when DATABASE_URL says so.

Callers keep working with whole DataFrames. `save` compares the new frame with what is stored, key by key (a
game, or a round robin's game day), and in one transaction deletes and re-inserts only the keys that changed,
in bulk (rules 21, 30, 64). A run that changes nothing writes nothing, so a retried run is harmless. Reads come
back in chunks (rule 11). Failures surface as LedgerStoreError (a data error, rule 39).
"""

from collections.abc import Iterator
from functools import cache
from typing import Any

import pandas as pd
from pydantic import ValidationError
from sqlalchemy import Engine, Insert, create_engine, delete, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import SQLAlchemyError

from nfl_alt_lines.common.settings import Settings, load_settings
from nfl_alt_lines.common.storage import DATA_ROOT
from nfl_alt_lines.db.models import PRICES, RUNS, TABLES, metadata
from nfl_alt_lines.db.schema import PRICE_KEY, Ledger, PriceRow, RunRow

DEFAULT_URL = f"sqlite:///{DATA_ROOT / 'ledger.db'}"
READ_CHUNK = 5000  # rows per read
WRITE_BATCH = 1000  # rows per bulk insert, and keys per delete


class LedgerStoreError(Exception):
    def __init__(self, action: str, ledger: str) -> None:
        super().__init__(f"ledger {action} failed for {ledger}")


def _batches(items: list[Any], size: int) -> Iterator[list[Any]]:
    return (items[start : start + size] for start in range(0, len(items), size))


def records(frame: pd.DataFrame, ledger: Ledger) -> list[dict[str, Any]]:
    """The frame's rows validated against the ledger's schema, with missing values as None."""
    columns = list(ledger.row.model_fields)
    present = frame.reindex(columns=columns).astype(object)
    raw = present.where(present.notna(), None).to_dict("records")
    try:
        return [ledger.row.model_validate(row).model_dump() for row in raw]
    except ValidationError as problem:
        raise LedgerStoreError("validation", ledger.name) from problem


def _normalised(frame: pd.DataFrame, ledger: Ledger) -> pd.DataFrame:
    """The schema's columns with one representation each (numbers as floats, text as text, missing as empty), so
    a frame read back from the database and one built in memory compare equal when their values are."""
    columns = frame.reindex(columns=list(ledger.row.model_fields))
    text = ledger.text_columns
    return pd.DataFrame(
        {
            name: columns[name].where(columns[name].notna(), "").astype(str)
            if name in text
            else pd.to_numeric(columns[name], errors="coerce").astype("float64")
            for name in columns
        }
    )


def _digests(frame: pd.DataFrame, ledger: Ledger) -> dict[str, int]:
    """An order-independent fingerprint of each key's rows (vectorised: no per-row Python)."""
    if frame.empty:
        return {}
    normal = _normalised(frame, ledger)
    hashed = pd.util.hash_pandas_object(normal, index=False).to_numpy()
    sums = pd.Series(hashed, dtype="uint64").groupby(normal[ledger.key].to_numpy()).sum()
    return {str(key): int(value) for key, value in sums.items()}


def changed_keys(before: pd.DataFrame, after: pd.DataFrame, ledger: Ledger) -> set[str]:
    """Keys whose rows differ between the stored and the new frame, including keys gone from either side."""
    old, new = _digests(before, ledger), _digests(after, ledger)
    return {k for k in old.keys() | new.keys() if old.get(k) != new.get(k)}


def _numbers_as_floats(frame: pd.DataFrame, ledger: Ledger) -> pd.DataFrame:
    """Optional number columns as floats with NaN for missing, as the ledgers were in their CSV days (an empty
    column otherwise comes back as objects)."""
    optional = [name for name, field in ledger.row.model_fields.items() if not field.is_required()]
    numbers = [name for name in optional if frame[name].dtype == object and name not in ledger.text_columns]
    return frame.astype(dict.fromkeys(numbers, "float64"))


class LedgerStore:
    """The ledgers in one database, through one engine and its connection pool (rule 66)."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def load(self, ledger: Ledger) -> pd.DataFrame:
        """Every stored row, in insertion order, without the surrogate id."""
        table = TABLES[ledger.name]
        query = select(*(table.c[name] for name in ledger.row.model_fields)).order_by(table.c.id)
        try:
            with self.engine.connect() as connection:
                chunks = list(pd.read_sql(query, connection, chunksize=READ_CHUNK))
        except (SQLAlchemyError, pd.errors.DatabaseError) as problem:  # pandas wraps some driver errors
            raise LedgerStoreError("read", ledger.name) from problem
        frame = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame(columns=list(ledger.row.model_fields))
        return _numbers_as_floats(frame, ledger) if len(frame) else frame

    def save(self, ledger: Ledger, frame: pd.DataFrame) -> int:
        """Make the stored ledger equal `frame`, rewriting only the keys that changed; returns how many."""
        changed = changed_keys(self.load(ledger), frame, ledger)
        table = TABLES[ledger.name]
        # Only the rows being written are validated: stored rows were validated when they were written.
        rows = records(frame[frame[ledger.key].astype(str).isin(changed)], ledger) if changed else []
        try:
            with self.engine.begin() as connection:
                for keys in _batches(sorted(changed), WRITE_BATCH):
                    connection.execute(delete(table).where(table.c[ledger.key].in_(keys)))
                for batch in _batches(rows, WRITE_BATCH):
                    connection.execute(table.insert(), batch)
        except SQLAlchemyError as problem:
            raise LedgerStoreError("write", ledger.name) from problem
        return len(changed)


def _insert_ignoring_duplicates(dialect: str) -> Insert:
    """An insert that skips rows whose unique key is already stored (SQLite and Postgres)."""
    insert = postgresql.insert if dialect == "postgresql" else sqlite.insert
    return insert(PRICES).on_conflict_do_nothing(index_elements=list(PRICE_KEY))


def add_prices(store: LedgerStore, rows: list[PriceRow]) -> int:
    """Store logged prices, skipping any already stored; returns how many rows were new.

    Counted from RETURNING, not `rowcount`: psycopg reports -1 for a batched insert, which made the run records
    say the logger wrote -7 rows (2026-10-18).
    """
    statement = _insert_ignoring_duplicates(store.engine.dialect.name).returning(PRICES.c.id)
    added = 0
    try:
        with store.engine.begin() as connection:
            for batch in _batches([row.model_dump() for row in rows], WRITE_BATCH):
                added += len(connection.execute(statement, batch).all())
    except SQLAlchemyError as problem:
        raise LedgerStoreError("write", PRICES.name) from problem
    return added


def all_prices(store: LedgerStore) -> list[PriceRow]:
    """Every logged price, oldest first (for copying a database, not for the jobs)."""
    query = select(*(PRICES.c[name] for name in PriceRow.model_fields)).order_by(PRICES.c.id)
    try:
        with store.engine.connect() as connection:
            return [PriceRow.model_validate(row._asdict()) for row in connection.execute(query)]
    except SQLAlchemyError as problem:
        raise LedgerStoreError("read", PRICES.name) from problem


def record_run(store: LedgerStore, row: RunRow) -> None:
    try:
        with store.engine.begin() as connection:
            connection.execute(RUNS.insert(), [row.model_dump()])
    except SQLAlchemyError as problem:
        raise LedgerStoreError("write", RUNS.name) from problem


def recent_runs(store: LedgerStore, job: str, limit: int) -> list[RunRow]:
    """The job's latest runs, newest first."""
    query = select(*(RUNS.c[name] for name in RunRow.model_fields)).where(RUNS.c.job == job)
    query = query.order_by(RUNS.c.id.desc()).limit(limit)
    try:
        with store.engine.connect() as connection:
            return [RunRow.model_validate(row._asdict()) for row in connection.execute(query)]
    except SQLAlchemyError as problem:
        raise LedgerStoreError("read", RUNS.name) from problem


def open_ledgers(url: str) -> LedgerStore:
    """The store at `url`, creating any missing tables."""
    engine = create_engine(url, pool_pre_ping=True)
    try:
        metadata.create_all(engine)
    except SQLAlchemyError as problem:
        raise LedgerStoreError("open", url.split("://", 1)[0]) from problem  # the scheme only: URLs hold passwords
    return LedgerStore(engine)


def database_url(settings: Settings) -> str:
    return settings.database_url.get_secret_value() if settings.database_url else DEFAULT_URL


@cache
def default_ledgers() -> LedgerStore:
    """The process's ledger store, built once from .env and the environment. Entry points may pass their own."""
    return open_ledgers(database_url(load_settings()))
