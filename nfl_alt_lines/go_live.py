"""Copy this machine's state into the cloud before the scheduled tasks take over from cron (infra/README.md):
the files under data/ (odds snapshots, nflverse and weather downloads, reports and the email mark) into the
S3 bucket, and the ledgers and logged prices from data/ledger.db into the cloud database.

Safe to run more than once: files already in the bucket are skipped, ledgers rewrite only what changed, and
logged prices are unique. Run it with the laptop's cron jobs switched off, so nothing changes mid-copy.

    AWS_PROFILE=<admin profile> uv run python -m nfl_alt_lines.go_live --bucket <bucket_name output>

The database URL holds a password, so it is asked for (not echoed) instead of taken as an argument, where it
would land in the shell history and the process list.
"""

import argparse
import getpass
import logging
from collections.abc import Callable

from nfl_alt_lines.common.logs import configure_logging
from nfl_alt_lines.common.settings import Settings
from nfl_alt_lines.common.storage import DATA_ROOT, LocalStorage, Storage, open_storage
from nfl_alt_lines.db.schema import LEDGERS
from nfl_alt_lines.db.service import DEFAULT_URL, LedgerStore, add_prices, all_prices, open_ledgers
from nfl_alt_lines.local_cloud import copy_prefix

PREFIXES = ("raw", "reports")  # everything the jobs keep in storage
URL_PROMPT = "Cloud DATABASE_URL (postgresql+psycopg://...; not shown): "
POSTGRES_SCHEMES = ("postgresql://", "postgresql+psycopg://")

log = logging.getLogger(__name__)


class NotPostgresError(Exception):
    def __init__(self) -> None:
        super().__init__("the cloud database URL must start with postgresql+psycopg:// (or postgresql://)")


def copy_files(source: Storage, target: Storage) -> dict[str, int]:
    """Objects copied per prefix; ones the target already has are skipped."""
    return {prefix: copy_prefix(source, target, prefix) for prefix in PREFIXES}


def copy_ledgers(source: LedgerStore, target: LedgerStore) -> dict[str, int]:
    """Make each target ledger equal the source's; returns the keys rewritten per ledger, then new prices."""
    counts = {ledger.name: target.save(ledger, source.load(ledger)) for ledger in LEDGERS}
    counts["odds_prices"] = add_prices(target, all_prices(source))
    return counts


def cloud_database(url: str) -> LedgerStore:
    """The target store; only Postgres, so a typo cannot quietly create another SQLite file."""
    if not url.startswith(POSTGRES_SCHEMES):
        raise NotPostgresError
    return open_ledgers(url)


def main(argv: list[str] | None = None, ask: Callable[[str], str] = getpass.getpass) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bucket", required=True, help="the S3 bucket (terraform output bucket_name)")
    args = parser.parse_args(argv)
    configure_logging("go-live")
    target_db = cloud_database(ask(URL_PROMPT).strip())
    files = copy_files(LocalStorage(DATA_ROOT), open_storage(Settings(storage_backend="s3", s3_bucket=args.bucket)))
    log.info("files copied", extra=files)
    ledgers = copy_ledgers(open_ledgers(DEFAULT_URL), target_db)
    log.info("ledgers copied", extra=ledgers)


if __name__ == "__main__":
    main()
