from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from nfl_alt_lines.common import storage
from nfl_alt_lines.common.settings import Settings
from nfl_alt_lines.common.storage import (
    LocalStorage,
    MissingObjectError,
    S3Storage,
    Storage,
    StorageError,
    default_storage,
    ensure_bucket,
    open_storage,
)

BUCKET = "nfl-alt-lines-test"


@pytest.fixture
def s3() -> Iterator[S3Storage]:
    with mock_aws():  # S3 is an outside boundary: mocked, never reached (UNITTEST-RULES)
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield S3Storage(client, BUCKET, prefix="prod/")


@pytest.fixture(params=["local", "s3"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Storage:
    if request.param == "local":
        return LocalStorage(tmp_path)
    result: Storage = request.getfixturevalue("s3")
    return result


def test_write_then_read(store: Storage) -> None:
    store.write("raw/odds/events/e1/20261018T120000Z.json", b"{}")
    assert store.read("raw/odds/events/e1/20261018T120000Z.json") == b"{}"
    assert store.exists("raw/odds/events/e1/20261018T120000Z.json")


def test_missing_object(store: Storage) -> None:
    assert not store.exists("reports/latest.txt")
    assert store.modified("reports/latest.txt") is None
    with pytest.raises(MissingObjectError):
        store.read("reports/latest.txt")


def test_keys_lists_one_folder_sorted(store: Storage) -> None:
    for key in ("raw/odds/events/e2/b.json", "raw/odds/events/e1/a.json", "raw/odds/main/x.json", "raw/oddsx/y.json"):
        store.write(key, b"1")
    assert store.keys("raw/odds/events") == ["raw/odds/events/e1/a.json", "raw/odds/events/e2/b.json"]
    assert store.keys("raw/nothing") == []


def test_modified_is_recent(store: Storage) -> None:
    store.write("reports/latest.txt", b"report")
    written = store.modified("reports/latest.txt")
    assert written is not None
    assert abs(datetime.now(UTC) - written) < timedelta(minutes=1)


def test_local_listing_skips_partial_downloads(tmp_path: Path) -> None:
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / ".games.csv.abc.partial").write_bytes(b"half")
    assert LocalStorage(tmp_path).keys("raw") == []


def test_local_read_error_is_a_storage_error(tmp_path: Path) -> None:
    (tmp_path / "folder").mkdir()
    with pytest.raises(StorageError):
        LocalStorage(tmp_path).read("folder")


def test_local_write_error_is_a_storage_error(tmp_path: Path) -> None:
    (tmp_path / "blocked").write_bytes(b"a file where a folder should be")
    with pytest.raises(StorageError):
        LocalStorage(tmp_path).write("blocked/latest.txt", b"x")


def test_s3_keys_follow_the_prefix_and_page_past_1000(s3: S3Storage) -> None:
    for i in range(1001):
        s3.write(f"raw/weather/{i:04}.json", b"{}")
    keys = s3.keys("raw/weather")
    assert len(keys) == 1001
    assert keys[0] == "raw/weather/0000.json"
    assert s3.client.list_objects_v2(Bucket=BUCKET, MaxKeys=1)["Contents"][0]["Key"].startswith("prod/raw/")


def test_s3_without_a_prefix(s3: S3Storage) -> None:
    bare = S3Storage(s3.client, BUCKET)
    bare.write("a.txt", b"a")
    assert bare.read("a.txt") == b"a"
    assert bare.keys("") == ["a.txt"]


def test_s3_failures_are_storage_errors(s3: S3Storage) -> None:
    gone = S3Storage(s3.client, "no-such-bucket")
    for action in (lambda: gone.write("a", b"a"), lambda: gone.keys("a"), lambda: gone.modified("a")):
        with pytest.raises(StorageError):
            action()
    with pytest.raises(StorageError):
        gone.read("a")


def test_open_storage_picks_the_backend(tmp_path: Path) -> None:
    local = open_storage(Settings(), tmp_path)
    assert isinstance(local, LocalStorage)
    assert local.root == tmp_path
    with mock_aws():
        remote = open_storage(Settings(storage_backend="s3", s3_bucket=BUCKET, s3_prefix="p"))
    assert isinstance(remote, S3Storage)
    assert (remote.bucket, remote.prefix) == (BUCKET, "p")


def test_default_storage_is_built_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    built: list[Settings] = []
    monkeypatch.setattr(storage, "load_settings", lambda: Settings())

    def fake_open(settings: Settings) -> Storage:
        built.append(settings)
        return LocalStorage(tmp_path)

    monkeypatch.setattr(storage, "open_storage", fake_open)
    default_storage.cache_clear()
    try:
        assert default_storage() is default_storage()
        assert len(built) == 1
    finally:
        default_storage.cache_clear()


def test_ensure_bucket_creates_a_missing_bucket_once(s3: S3Storage) -> None:
    fresh = S3Storage(s3.client, "made-here")
    ensure_bucket(fresh)
    ensure_bucket(fresh)  # already there: nothing to do
    fresh.write("a", b"a")
    assert fresh.read("a") == b"a"


def test_ensure_bucket_failure_is_a_storage_error(s3: S3Storage, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(**_: object) -> None:
        raise ClientError({"Error": {"Code": "AccessDenied"}}, "CreateBucket")

    monkeypatch.setattr(s3.client, "create_bucket", refuse)
    with pytest.raises(StorageError):
        ensure_bucket(S3Storage(s3.client, "not-allowed"))
