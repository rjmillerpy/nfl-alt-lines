"""The one storage interface (roadmap item 11): every file the jobs keep, keyed by a relative path such as
`raw/odds/events/<id>/<stamp>.json`, in one of two places chosen by STORAGE_BACKEND:

- `local` (default): under the project's data/ folder, written atomically.
- `s3`: in S3_BUCKET under S3_PREFIX; S3_ENDPOINT_URL points it at an S3-compatible server such as MinIO.

Nothing else in the package imports boto3. Failures surface as StorageError (a data error, rule 39).
"""

from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from nfl_alt_lines.common.downloads import PARTIAL_SUFFIX, save_atomically
from nfl_alt_lines.common.settings import PROJECT_ROOT, Settings, load_settings

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

DATA_ROOT = PROJECT_ROOT / "data"
MISSING_CODES = {"404", "NoSuchKey", "NotFound"}
S3_TIMEOUTS = Config(connect_timeout=5, read_timeout=60, retries={"max_attempts": 3, "mode": "standard"})


class StorageError(Exception):
    def __init__(self, action: str, key: str) -> None:
        super().__init__(f"storage {action} failed for {key}")


class MissingObjectError(StorageError):
    def __init__(self, key: str) -> None:
        super().__init__("read (no such object)", key)


class Storage(Protocol):
    def read(self, key: str) -> bytes: ...
    def write(self, key: str, body: bytes) -> None: ...
    def exists(self, key: str) -> bool: ...
    def keys(self, prefix: str) -> list[str]:
        """Every key under `prefix` (a folder-like path), sorted."""
        ...

    def modified(self, key: str) -> datetime | None:
        """When the object was last written, or None if it does not exist."""
        ...


class LocalStorage:
    """Files under a root folder; writes go to a temporary file first, so readers never see half a file."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def read(self, key: str) -> bytes:
        try:
            return (self.root / key).read_bytes()
        except FileNotFoundError:
            raise MissingObjectError(key) from None
        except OSError as problem:
            raise StorageError("read", key) from problem

    def write(self, key: str, body: bytes) -> None:
        try:
            save_atomically(self.root / key, body)
        except OSError as problem:
            raise StorageError("write", key) from problem

    def exists(self, key: str) -> bool:
        return (self.root / key).is_file()

    def keys(self, prefix: str) -> list[str]:
        files = (path for path in (self.root / prefix).rglob("*") if path.is_file())
        return sorted(path.relative_to(self.root).as_posix() for path in files if path.suffix != PARTIAL_SUFFIX)

    def modified(self, key: str) -> datetime | None:
        path = self.root / key
        return datetime.fromtimestamp(path.stat().st_mtime, UTC) if path.is_file() else None


class S3Storage:
    """Objects in one bucket under a prefix, through one shared client (rule 66). Listing is paginated (rule 11)."""

    def __init__(self, client: S3Client, bucket: str, prefix: str = "") -> None:
        self.client, self.bucket, self.prefix = client, bucket, prefix.strip("/")

    def _name(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def read(self, key: str) -> bytes:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=self._name(key))["Body"].read()
        except ClientError as problem:
            if problem.response.get("Error", {}).get("Code") in MISSING_CODES:
                raise MissingObjectError(key) from None
            raise StorageError("read", key) from problem
        except BotoCoreError as problem:
            raise StorageError("read", key) from problem

    def write(self, key: str, body: bytes) -> None:
        try:
            self.client.put_object(Bucket=self.bucket, Key=self._name(key), Body=body)
        except (ClientError, BotoCoreError) as problem:
            raise StorageError("write", key) from problem

    def modified(self, key: str) -> datetime | None:
        try:
            return self.client.head_object(Bucket=self.bucket, Key=self._name(key))["LastModified"]
        except ClientError as problem:
            if problem.response.get("Error", {}).get("Code") in MISSING_CODES:
                return None
            raise StorageError("check", key) from problem
        except BotoCoreError as problem:
            raise StorageError("check", key) from problem

    def exists(self, key: str) -> bool:
        return self.modified(key) is not None

    def keys(self, prefix: str) -> list[str]:
        start = len(self._name(""))
        folder = self._name(prefix.rstrip("/") + "/") if prefix else self._name("")
        try:
            pages = self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=folder)
            return sorted(item["Key"][start:] for page in pages for item in page.get("Contents", []))
        except (ClientError, BotoCoreError) as problem:
            raise StorageError("list", prefix) from problem


def ensure_bucket(store: S3Storage) -> None:
    """Create the bucket if it is missing (local stand-ins only: the real bucket comes from Terraform)."""
    try:
        store.client.head_bucket(Bucket=store.bucket)
    except ClientError:
        try:
            store.client.create_bucket(Bucket=store.bucket)
        except (ClientError, BotoCoreError) as problem:
            raise StorageError("create", store.bucket) from problem


def open_storage(settings: Settings, local_root: Path = DATA_ROOT) -> Storage:
    """The storage the settings ask for; the S3 client is created here, once."""
    if settings.storage_backend == "local":
        return LocalStorage(local_root)
    client = boto3.client("s3", endpoint_url=settings.s3_endpoint_url, config=S3_TIMEOUTS)
    return S3Storage(client, settings.s3_bucket or "", settings.s3_prefix)


@cache
def default_storage() -> Storage:
    """The process's storage, built once from .env and the environment. Entry points may pass their own."""
    return open_storage(load_settings())
