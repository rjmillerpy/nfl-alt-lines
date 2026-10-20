import logging
from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pytest
from moto import mock_aws

from nfl_alt_lines import local_cloud
from nfl_alt_lines.common.storage import LocalStorage, S3Storage
from nfl_alt_lines.db.schema import PREDICTIONS
from nfl_alt_lines.db.service import open_ledgers
from nfl_alt_lines.local_cloud import copy_prefix, local_s3, main
from tests.db.test_schema import prediction


@pytest.fixture
def mocked_s3() -> Iterator[None]:
    with mock_aws():  # the local moto server is an outside boundary too
        yield


def test_copy_prefix_copies_only_what_is_missing(tmp_path: Path) -> None:
    source, target = LocalStorage(tmp_path / "a"), LocalStorage(tmp_path / "b")
    source.write("raw/odds/main/1.json", b"1")
    source.write("raw/odds/main/2.json", b"2")
    target.write("raw/odds/main/1.json", b"1")
    assert copy_prefix(source, target, "raw/odds") == 1
    assert copy_prefix(source, target, "raw/odds") == 0
    assert target.read("raw/odds/main/2.json") == b"2"


def test_local_s3_points_at_the_stand_in(mocked_s3: None) -> None:
    store = local_s3("http://localhost:4566", "bucket")
    assert isinstance(store, S3Storage)
    assert (store.bucket, store.client.meta.endpoint_url) == ("bucket", "http://localhost:4566")


def test_main_creates_the_bucket_and_seeds(mocked_s3: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = LocalStorage(tmp_path / "data")
    data.write("raw/odds/main/1.json", b"{}")
    (tmp_path / "ledger").mkdir()
    pd.DataFrame([prediction()]).to_csv(tmp_path / "ledger" / "predictions.csv", index=False)
    monkeypatch.setattr(local_cloud, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(local_cloud, "CSV_FOLDER", tmp_path / "ledger")
    monkeypatch.setattr(local_cloud, "S3_ENDPOINT", "https://s3.amazonaws.com")  # where moto intercepts
    monkeypatch.setattr(logging.getLogger(), "handlers", [])
    url = f"sqlite:///{tmp_path / 'cloud.db'}"
    main(["--seed", "--database-url", url])
    assert local_s3().read("raw/odds/main/1.json") == b"{}"
    assert len(open_ledgers(url).load(PREDICTIONS)) == 1
