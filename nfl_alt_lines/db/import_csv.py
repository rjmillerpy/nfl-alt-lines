"""One-off: copy the CSV ledgers from before the database (data/ledger/*.csv) into the ledger store.

Safe to run again: the store rewrites only games that differ, so a second import writes nothing. Rows from
before model versioning (2026-10-15) were ranked with the current settings and get the strategy's version. The CSV
files are left in place as a backup.

Run: uv run python -m nfl_alt_lines.db.import_csv
"""

import argparse
import logging
from pathlib import Path

import pandas as pd

from nfl_alt_lines import strategy
from nfl_alt_lines.common.logs import configure_logging
from nfl_alt_lines.common.storage import DATA_ROOT
from nfl_alt_lines.db import service
from nfl_alt_lines.db.schema import LEDGERS, PREDICTIONS, Ledger
from nfl_alt_lines.db.service import LedgerStore

CSV_FOLDER = DATA_ROOT / "ledger"

log = logging.getLogger(__name__)


def read_csv_ledger(folder: Path, ledger: Ledger) -> pd.DataFrame | None:
    """The ledger's CSV file, or None when there is none."""
    path = folder / f"{ledger.name}.csv"
    if not path.exists():
        return None
    frame = pd.read_csv(path)
    if ledger is PREDICTIONS and "model_version" not in frame:
        frame["model_version"] = strategy.load().version
    return frame


def import_ledgers(folder: Path, store: LedgerStore) -> dict[str, int]:
    """Copy every CSV ledger found in `folder`; returns rows read per ledger."""
    counts = {}
    for ledger in LEDGERS:
        frame = read_csv_ledger(folder, ledger)
        if frame is not None:
            written = store.save(ledger, frame)
            counts[ledger.name] = len(frame)
            log.info("ledger imported", extra={"ledger": ledger.name, "rows": len(frame), "keys_written": written})
    return counts


def main(argv: list[str] | None = None, store: LedgerStore | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, default=CSV_FOLDER, help="where the CSV ledgers are")
    args = parser.parse_args(argv)
    configure_logging("import-csv")
    import_ledgers(args.folder, store or service.default_ledgers())


if __name__ == "__main__":
    main()
