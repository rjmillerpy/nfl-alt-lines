"""Public tests run against the public baseline strategy, so they behave the same wherever the production strategy
is installed or not."""

from collections.abc import Iterator
from dataclasses import replace

import pytest

from nfl_alt_lines import baseline, strategy
from nfl_alt_lines.strategy import Staking


@pytest.fixture(scope="module", autouse=True)
def baseline_strategy() -> Iterator[None]:
    # Module scope, so module-scoped fixtures that price lines see it too.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(strategy, "load", lambda: baseline.STRATEGY)
        yield


# A neutral rule set for testing the plan, glance, CLV and results mechanics: deliberately not the production
# strategy's values (those are tested privately).
TEST_RULES = replace(
    baseline.STRATEGY,
    name="test rules",
    recommendable=("favorite", "underdog", "over", "under"),
    evidence=dict.fromkeys(("favorite", "underdog", "over", "under"), " - test note"),
    staking=Staking(sunday_budget=0.02, singles_share=0.5, single_stake=0.01),
    standing_rules=("Test rules: not a betting strategy.",),
    replay_note="Test rules: no replay.",
)


@pytest.fixture(scope="module")
def test_rules(baseline_strategy: None) -> Iterator[None]:
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(strategy, "load", lambda: TEST_RULES)
        yield
