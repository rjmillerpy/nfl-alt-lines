import logging
from pathlib import Path

import pandas as pd
import pytest

from nfl_alt_lines import strategy
from nfl_alt_lines.db.import_csv import import_ledgers, main, read_csv_ledger
from nfl_alt_lines.db.schema import PREDICTIONS, PROPS
from nfl_alt_lines.db.service import LedgerStore
from tests.db.test_schema import prediction


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    found = tmp_path / "ledger"
    found.mkdir()
    rows = pd.DataFrame([prediction(), prediction(game_id="g2")]).drop(columns="model_version")
    rows.to_csv(found / "predictions.csv", index=False)
    return found


def test_rows_from_before_versioning_get_the_current_version(folder: Path) -> None:
    frame = read_csv_ledger(folder, PREDICTIONS)
    assert frame is not None
    assert set(frame["model_version"]) == {strategy.load().version}
    assert read_csv_ledger(folder, PROPS) is None


def test_import_copies_each_ledger_once(folder: Path, ledgers: LedgerStore) -> None:
    assert import_ledgers(folder, ledgers) == {"predictions": 2}
    assert len(ledgers.load(PREDICTIONS)) == 2
    assert import_ledgers(folder, ledgers) == {"predictions": 2}
    assert len(ledgers.load(PREDICTIONS)) == 2  # importing again changes nothing


def test_main_imports_into_the_given_store(folder: Path, ledgers: LedgerStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logging.getLogger(), "handlers", [])  # main() replaces them; put them back afterwards
    main(["--folder", str(folder)], ledgers)
    assert len(ledgers.load(PREDICTIONS)) == 2
    main(["--folder", str(folder)])  # the default store is the test's own `ledgers` too
    assert len(ledgers.load(PREDICTIONS)) == 2
