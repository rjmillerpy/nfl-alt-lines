import pytest
from pydantic import SecretStr

from nfl_alt_lines.common.http_client import Response
from nfl_alt_lines.odds.client import API, MissingCreditsHeaderError, OddsClient
from tests.odds.network import FakeOddsAPI


def test_sends_the_key_and_returns_body_and_credits() -> None:
    network = FakeOddsAPI({"/events": []}, credits=321)
    body, remaining = OddsClient(SecretStr("k"), fetch=network).get("/events", markets="spreads")
    assert body == b"[]"
    assert remaining == 320
    assert network.requests == [("/events", {"apiKey": "k", "markets": "spreads"})]


@pytest.mark.parametrize("header", ["x-requests-remaining", "X-Requests-Remaining"], ids=["lower", "title"])
def test_credits_header_is_read_in_any_case(header: str) -> None:
    client = OddsClient(SecretStr("k"), fetch=lambda url, params: Response(b"[]", {header: "12.0"}))
    assert client.get("/events")[1] == 12


def test_missing_credits_header_is_an_error() -> None:
    client = OddsClient(SecretStr("k"), fetch=lambda url, params: Response(b"[]", {}))
    with pytest.raises(MissingCreditsHeaderError):
        client.get("/events")


def test_base_url_is_configurable() -> None:
    network = FakeOddsAPI({"/x": []}, base="https://other")
    OddsClient(SecretStr("k"), base="https://other", fetch=network).get("/x")
    assert network.paths == ["/x"]
    assert API.startswith("https://")
