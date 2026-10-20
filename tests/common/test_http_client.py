"""The network is the only thing replaced: urlopen is swapped for a fake that plays back scripted replies."""

import io
import urllib.error
import urllib.request
from collections.abc import Iterator
from email.message import Message

import pytest

from nfl_alt_lines.common import http_client
from nfl_alt_lines.common.http_client import RefusedError, RetriesExhaustedError, UnsafeURLError, get

URL = "https://api.example.com/v1/things"


class FakeReply:
    def __init__(self, body: bytes, headers: dict[str, str]) -> None:
        self._body = body
        self.headers = Message()
        for name, value in headers.items():
            self.headers[name] = value

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> FakeReply:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def http_error(code: int, body: bytes = b"{}") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(URL, code, "error", Message(), io.BytesIO(body))


class Network:
    """Plays back one scripted reply or failure per call and records each requested URL and timeout."""

    def __init__(self, script: list[FakeReply | Exception]) -> None:
        self._script: Iterator[FakeReply | Exception] = iter(script)
        self.calls: list[tuple[str, float]] = []

    def urlopen(self, url: str, timeout: float) -> FakeReply:
        self.calls.append((url, timeout))
        step = next(self._script)
        if isinstance(step, Exception):
            raise step
        return step


@pytest.fixture
def sleeps() -> list[float]:
    return []


def install(monkeypatch: pytest.MonkeyPatch, script: list[FakeReply | Exception]) -> Network:
    network = Network(script)
    monkeypatch.setattr(urllib.request, "urlopen", network.urlopen)
    return network


def test_success_returns_body_and_headers(monkeypatch: pytest.MonkeyPatch, sleeps: list[float]) -> None:
    network = install(monkeypatch, [FakeReply(b'{"ok": 1}', {"X-Requests-Remaining": "42"})])
    response = get(URL, {"a": "1", "b": 2}, sleep=sleeps.append)
    assert response.body == b'{"ok": 1}'
    assert response.headers["X-Requests-Remaining"] == "42"
    assert network.calls == [(f"{URL}?a=1&b=2", http_client.TIMEOUT_SECONDS)]
    assert sleeps == []


def test_no_params_requests_the_bare_url(monkeypatch: pytest.MonkeyPatch) -> None:
    network = install(monkeypatch, [FakeReply(b"", {})])
    get(URL, timeout=5)
    assert network.calls == [(URL, 5)]


@pytest.mark.parametrize(
    "failure",
    [urllib.error.URLError("no route"), TimeoutError("slow"), ConnectionResetError("reset"), http_error(503)],
    ids=["no-connection", "timeout", "connection-reset", "server-503"],
)
def test_transient_failures_are_retried_with_backoff(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float], failure: Exception
) -> None:
    network = install(monkeypatch, [failure, FakeReply(b"ok", {})])
    assert get(URL, sleep=sleeps.append).body == b"ok"
    assert len(network.calls) == 2
    assert sleeps == [http_client.BACKOFF_SECONDS]


def test_gives_up_after_the_last_attempt(monkeypatch: pytest.MonkeyPatch, sleeps: list[float]) -> None:
    network = install(monkeypatch, [http_error(429), http_error(500), http_error(502)])
    with pytest.raises(RetriesExhaustedError) as raised:
        get(URL, attempts=3, sleep=sleeps.append)
    assert len(network.calls) == 3
    assert sleeps == [http_client.BACKOFF_SECONDS, http_client.BACKOFF_SECONDS * 2]
    assert isinstance(raised.value.__cause__, urllib.error.HTTPError)


@pytest.mark.parametrize("code", [400, 401, 404, 422], ids=["bad-request", "unauthorised", "not-found", "invalid"])
def test_client_errors_fail_at_once(monkeypatch: pytest.MonkeyPatch, sleeps: list[float], code: int) -> None:
    network = install(monkeypatch, [http_error(code, b'{"message": "bad key"}')])
    with pytest.raises(RefusedError) as raised:
        get(URL, sleep=sleeps.append)
    assert raised.value.status == code
    assert "bad key" in str(raised.value)
    assert len(network.calls) == 1
    assert sleeps == []


def test_errors_never_include_the_query_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """The query can carry an API key, so messages name only the base URL."""
    install(monkeypatch, [http_error(401)])
    with pytest.raises(RefusedError) as raised:
        get(URL, {"apiKey": "secret-key"})
    assert "secret-key" not in str(raised.value)


@pytest.mark.parametrize(
    "url",
    ["http://api.example.com", "file:///etc/passwd", "ftp://host/file"],
    ids=["plain-http", "local-file", "ftp"],
)
def test_only_https_is_fetched(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    network = install(monkeypatch, [])
    with pytest.raises(UnsafeURLError):
        get(url)
    assert network.calls == []


def test_retry_is_logged_without_the_query(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    install(monkeypatch, [TimeoutError("slow"), FakeReply(b"", {})])
    get(URL, {"apiKey": "secret-key"}, sleep=lambda seconds: None)
    assert "fetch failed, retrying" in caplog.text
    assert "secret-key" not in caplog.text
    assert all(getattr(record, "url", URL) == URL for record in caplog.records)
