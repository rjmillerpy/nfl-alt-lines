import logging
from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pytest
from moto import mock_aws

from nfl_alt_lines import go_live
from nfl_alt_lines.common.settings import Settings
from nfl_alt_lines.common.storage import LocalStorage, S3Storage, ensure_bucket, open_storage
from nfl_alt_lines.db.schema import LEDGERS, PREDICTIONS
from nfl_alt_lines.db.service import LedgerStore, add_prices, all_prices, open_ledgers
from nfl_alt_lines.go_live import NotPostgresError, cloud_database, copy_files, copy_ledgers, main
from tests.db.test_schema import prediction
from tests.db.test_service import price

BUCKET = "nfl-alt-lines-data-test"


def database(folder: Path, name: str) -> LedgerStore:
    return open_ledgers(f"sqlite:///{folder / name}")


def test_copy_files_copies_every_prefix_once(tmp_path: Path) -> None:
    laptop, cloud = LocalStorage(tmp_path / "laptop"), LocalStorage(tmp_path / "cloud")
    laptop.write("raw/odds/main/1.json", b"{}")
    laptop.write("reports/latest.txt", b"plan")
    laptop.write("reports/.last_emailed", b"mark")  # copied too, so the cloud does not email the same plan
    assert copy_files(laptop, cloud) == {"raw": 1, "reports": 2}
    assert copy_files(laptop, cloud) == {"raw": 0, "reports": 0}
    assert cloud.read("reports/.last_emailed") == b"mark"


def test_copy_ledgers_makes_the_cloud_equal_the_laptop(tmp_path: Path) -> None:
    laptop, cloud = database(tmp_path, "laptop.db"), database(tmp_path, "cloud.db")
    laptop.save(PREDICTIONS, pd.DataFrame([prediction()]))
    add_prices(laptop, [price()])
    first = copy_ledgers(laptop, cloud)
    assert first == {ledger.name: int(ledger is PREDICTIONS) for ledger in LEDGERS} | {"odds_prices": 1}
    pd.testing.assert_frame_equal(cloud.load(PREDICTIONS), laptop.load(PREDICTIONS))
    assert all_prices(cloud) == all_prices(laptop)
    assert set(copy_ledgers(laptop, cloud).values()) == {0}  # a second run changes nothing


@pytest.mark.parametrize(
    "url",
    ["sqlite:///cloud.db", "postgres://host/db", ""],
    ids=["sqlite", "unsupported-scheme", "empty"],
)
def test_cloud_database_must_be_postgres(url: str) -> None:
    with pytest.raises(NotPostgresError):
        cloud_database(url)


def test_a_bad_url_is_not_echoed_in_the_error() -> None:
    """Security: the URL holds a password, so the error names the expected scheme and never the input."""
    with pytest.raises(NotPostgresError) as raised:
        cloud_database("mysql://db/x?password=hunter2")
    assert "hunter2" not in str(raised.value)


@pytest.fixture
def mocked_s3() -> Iterator[None]:
    with mock_aws():
        yield


def test_main_copies_files_and_ledgers(mocked_s3: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    LocalStorage(tmp_path / "data").write("raw/odds/main/1.json", b"{}")
    laptop = database(tmp_path, "laptop.db")
    laptop.save(PREDICTIONS, pd.DataFrame([prediction()]))
    cloud = database(tmp_path, "cloud.db")
    bucket = open_storage(Settings(storage_backend="s3", s3_bucket=BUCKET))
    assert isinstance(bucket, S3Storage)
    ensure_bucket(bucket)
    monkeypatch.setattr(go_live, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(go_live, "DEFAULT_URL", f"sqlite:///{tmp_path / 'laptop.db'}")
    monkeypatch.setattr(go_live, "cloud_database", lambda _url: cloud)  # the real check is tested above
    monkeypatch.setattr(logging.getLogger(), "handlers", [])
    asked: list[str] = []

    def ask(prompt: str) -> str:
        asked.append(prompt)
        return "postgresql+psycopg://h/db"

    main(["--bucket", BUCKET], ask=ask)
    assert asked == [go_live.URL_PROMPT]
    assert bucket.read("raw/odds/main/1.json") == b"{}"
    assert len(cloud.load(PREDICTIONS)) == 1
