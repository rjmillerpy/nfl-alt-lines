"""No test can read or write the real data/ folder or ledgers: for the whole session, the default storage is a
temporary folder and the default ledger store a temporary SQLite file. Fixtures that need their own (the prop
league, a fresh ledger) patch them again on top."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine

from nfl_alt_lines.common import storage
from nfl_alt_lines.common.storage import LocalStorage
from nfl_alt_lines.db import service
from nfl_alt_lines.db.service import LedgerStore, open_ledgers


@pytest.fixture(scope="session", autouse=True)
def isolated_storage(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    root = tmp_path_factory.mktemp("storage")
    store = LocalStorage(root)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(storage, "default_storage", lambda: store)
        yield root


@pytest.fixture(autouse=True)
def close_databases(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Close every database engine a test opens: Python 3.13+ warns about SQLite connections left to the
    garbage collector, and the warning lands on whichever test happens to be running then."""
    engines: list[Engine] = []
    real = create_engine

    def tracked(url: str, *, pool_pre_ping: bool) -> Engine:  # how service.open_ledgers calls it
        engine = real(url, pool_pre_ping=pool_pre_ping)
        engines.append(engine)
        return engine

    monkeypatch.setattr(service, "create_engine", tracked)
    yield
    for engine in engines:
        engine.dispose()


def fresh_ledgers(folder: Path) -> LedgerStore:
    return open_ledgers(f"sqlite:///{folder / 'ledger.db'}")


@pytest.fixture(scope="session", autouse=True)
def isolated_ledgers(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LedgerStore]:
    store = fresh_ledgers(tmp_path_factory.mktemp("ledgers"))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(service, "default_ledgers", lambda: store)
        yield store
    store.engine.dispose()  # close the pooled SQLite connections (Python 3.13+ warns about unclosed ones)


@pytest.fixture
def ledgers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[LedgerStore]:
    """An empty ledger store of the test's own, used by default for the test."""
    store = fresh_ledgers(tmp_path)
    monkeypatch.setattr(service, "default_ledgers", lambda: store)
    yield store
    store.engine.dispose()
