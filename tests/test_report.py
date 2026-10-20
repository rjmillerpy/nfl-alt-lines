"""The whole weekly run on a tiny synthetic slate, with the public baseline strategy (tests/conftest.py): two
upcoming games logged at three books, five played seasons. Only the data sources are replaced, through `Sources`."""

from pathlib import Path

import pytest

from nfl_alt_lines import baseline, report
from nfl_alt_lines.db.service import LedgerStore
from nfl_alt_lines.notify import bets
from nfl_alt_lines.odds.snapshots import logged_games
from tests import synthetic_slate


@pytest.fixture
def slate(tmp_path: Path, ledgers: LedgerStore) -> report.Sources:
    """The synthetic slate's inputs: two logged games at three books, five played seasons, no props."""
    store = synthetic_slate.write_snapshots(tmp_path / "odds")
    schedule = synthetic_slate.schedule()
    props, stats = baseline.no_props(schedule, synthetic_slate.NOW, refresh=False)
    return report.Sources(
        schedule=lambda refresh, now: schedule.copy(),
        features=lambda everything, schedule, refresh: schedule.copy(),
        games=lambda schedule: logged_games(schedule, store),
        closing=lambda schedule: {},
        props=lambda everything, now, refresh: (props.copy(), stats.copy()),
        bankroll=lambda: 1000.0,
        clock=lambda: synthetic_slate.NOW,
    )


def test_the_whole_weekly_run_works_on_the_baseline(slate: report.Sources, capsys: pytest.CaptureFixture[str]) -> None:
    """The public repo runs end to end on the baseline: every section prints, nothing is bet."""
    report.main(["--no-refresh"], slate)
    text = capsys.readouterr().out
    for heading in ("WHAT TO BET THIS WEEK", "AT A GLANCE", "SUNDAY ROUND ROBIN DETAILS", "===== DRAFTKINGS ====="):
        assert heading in text
    assert bets(text) == ""  # what the bets-only email list would be sent: nothing
    assert "Baseline strategy: every line is priced at its historical base rate" in text
