from collections.abc import Iterator
from dataclasses import dataclass, field
from importlib.metadata import entry_points

import pytest

from nfl_alt_lines import strategy
from nfl_alt_lines.strategy import NoStrategyError, load


@pytest.fixture(autouse=True)
def fresh_load() -> Iterator[None]:
    load.cache_clear()
    yield
    load.cache_clear()


@dataclass
class FakePoint:
    """Stands in for an installed entry point: records that it was loaded."""

    name: str
    loaded: list[str] = field(default_factory=list)

    def load(self) -> str:
        self.loaded.append(self.name)
        return self.name


def test_production_is_used_when_installed_else_the_baseline() -> None:
    installed = {point.name for point in entry_points(group=strategy.STRATEGY_GROUP)}
    assert load().name == ("production" if "production" in installed else "baseline")


@pytest.mark.parametrize(
    ("installed", "chosen", "expected"),
    [(("baseline", "production"), None, "production"), (("baseline",), None, "baseline"),
     (("baseline", "production"), "baseline", "baseline")],
    ids=["prefers-production", "falls-back-to-baseline", "chosen-by-name"],
)  # fmt: skip
def test_the_strategy_is_picked_by_preference_or_by_name(
    monkeypatch: pytest.MonkeyPatch, installed: tuple[str, ...], chosen: str | None, expected: str
) -> None:
    picked: list[str] = []
    found = [FakePoint(name, picked) for name in installed]
    monkeypatch.setattr(strategy, "entry_points", lambda group: found)
    if chosen:
        monkeypatch.setenv(strategy.STRATEGY_VARIABLE, chosen)
    else:
        monkeypatch.delenv(strategy.STRATEGY_VARIABLE, raising=False)
    load()
    assert picked == [expected]


def test_a_missing_strategy_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(strategy, "entry_points", lambda group: [])
    monkeypatch.delenv(strategy.STRATEGY_VARIABLE, raising=False)
    with pytest.raises(NoStrategyError, match="named production or baseline"):
        load()
