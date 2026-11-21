from collections.abc import Mapping

import pytest
from pydantic import SecretStr

from nfl_alt_lines import push
from nfl_alt_lines.common.http_client import RefusedError, Response
from nfl_alt_lines.common.settings import Settings

KEYS = Settings(pushover_user=SecretStr("u"), pushover_token=SecretStr("t"))


class FakePushover:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.forms: list[Mapping[str, str | int]] = []

    def __call__(self, url: str, form: Mapping[str, str | int]) -> Response:
        assert url == push.API
        if self.failure:
            raise self.failure
        self.forms.append(form)
        return Response(b'{"status":1}', {})


def test_nothing_without_keys() -> None:
    server = FakePushover()
    assert not push.configured(Settings()) and not push.alert(Settings(), "t", "m", loud=True, post=server)
    assert server.forms == []


def test_a_loud_alert_is_emergency_priority_repeated_for_two_minutes() -> None:
    server = FakePushover()
    assert push.alert(KEYS, "Kickoff", "x" * 2000, loud=True, post=server)
    form = server.forms[0]
    assert form["priority"] == push.EMERGENCY and form["retry"] == 60 and form["expire"] == 120
    assert form["token"] == "t" and form["user"] == "u"  # noqa: S105 - fake keys and len(str(form["message"])) == push.MESSAGE_CHARS


def test_a_normal_alert_follows_the_phones_sound_settings() -> None:
    server = FakePushover()
    assert push.alert(KEYS, "Bets", "m", loud=False, post=server)
    assert server.forms[0]["priority"] == push.NORMAL and "retry" not in server.forms[0]


def test_a_failed_alert_is_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    assert not push.alert(KEYS, "t", "m", loud=False, post=FakePushover(RefusedError(push.API, 400, "bad token")))
    assert "phone alert not sent" in caplog.text
