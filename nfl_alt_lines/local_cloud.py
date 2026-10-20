"""Prepare the local stand-in for the cloud (docker-compose.cloud.yml): create the S3 bucket and, with --seed,
copy this machine's odds snapshots into it and its ledgers into the local Postgres, so a container with no
data/ folder starts from the same state as the laptop.

Run (with the stand-ins up): uv run python -m nfl_alt_lines.local_cloud [--seed]
"""

import argparse
import logging
import os

from nfl_alt_lines.common.logs import configure_logging
from nfl_alt_lines.common.settings import Settings
from nfl_alt_lines.common.storage import DATA_ROOT, LocalStorage, S3Storage, Storage, ensure_bucket, open_storage
from nfl_alt_lines.db.import_csv import CSV_FOLDER, import_ledgers
from nfl_alt_lines.db.service import open_ledgers
from nfl_alt_lines.odds.snapshots import RAW_PREFIX

S3_ENDPOINT = "http://localhost:4566"  # the moto server's port on this machine
BUCKET = "nfl-alt-lines"
DATABASE_URL = "postgresql+psycopg://nfl@localhost:55432/nfl_alt_lines"
# Not secrets: the local moto server accepts any keys, and boto3 refuses to sign requests without some.
PLACEHOLDER_KEYS = {
    "AWS_ACCESS_KEY_ID": "local",
    "AWS_SECRET_ACCESS_KEY": "local",  # nosec B105
    "AWS_DEFAULT_REGION": "us-east-1",
}

log = logging.getLogger(__name__)


def copy_prefix(source: Storage, target: Storage, prefix: str) -> int:
    """Copy every object under `prefix` that the target does not have yet; returns how many were copied."""
    have = set(target.keys(prefix))
    todo = [key for key in source.keys(prefix) if key not in have]
    for key in todo:
        target.write(key, source.read(key))
    return len(todo)


def local_s3(endpoint: str | None = None, bucket: str = BUCKET) -> S3Storage:
    """The stand-in bucket, at S3_ENDPOINT unless another endpoint is given."""
    endpoint = endpoint or S3_ENDPOINT
    for name, value in PLACEHOLDER_KEYS.items():
        os.environ.setdefault(name, value)  # the moto server takes any keys
    store = open_storage(Settings(storage_backend="s3", s3_bucket=bucket, s3_endpoint_url=endpoint))
    if not isinstance(store, S3Storage):
        raise TypeError(bucket)  # open_storage returns S3 for these settings
    return store


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", action="store_true", help="copy odds snapshots and ledgers in")
    parser.add_argument("--database-url", default=DATABASE_URL)
    args = parser.parse_args(argv)
    configure_logging("local-cloud")
    store = local_s3()
    ensure_bucket(store)
    if args.seed:
        copied = copy_prefix(LocalStorage(DATA_ROOT), store, RAW_PREFIX)
        imported = import_ledgers(CSV_FOLDER, open_ledgers(args.database_url))
        log.info("seeded", extra={"snapshots": copied, "ledgers": imported})


if __name__ == "__main__":
    main()
