"""The Odds API client: the key is injected, every call goes through the shared HTTP client (rules 9, 58, 66)."""

from collections.abc import Callable, Mapping

from pydantic import SecretStr

from nfl_alt_lines.common import http_client
from nfl_alt_lines.odds.books import SINGLES_BOOKS, api_key

API = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl"
HISTORY_API = "https://api.the-odds-api.com/v4/historical/sports/americanfootball_nfl"
REMAINING_HEADER = "x-requests-remaining"
# Exactly the books used, by name rather than region: The Odds API charges each group of up to 10 books as one
# region, so these 9 (sportsbooks, exchanges and Bovada, which sit in two regions) cost what one region did.
COMMON_PARAMS = {"bookmakers": ",".join(api_key(book) for book in SINGLES_BOOKS), "oddsFormat": "american"}

Fetch = Callable[[str, Mapping[str, str | int | float]], http_client.Response]


class MissingCreditsHeaderError(http_client.FetchError):
    def __init__(self, path: str) -> None:
        super().__init__(f"{path}: the response did not say how many credits are left")


class OddsClient:
    """Owns the API key and base URL; returns each response's raw JSON (saved as is) and the credits left."""

    def __init__(self, key: SecretStr, base: str = API, fetch: Fetch = http_client.get) -> None:
        self._key = key
        self._base = base
        self._fetch = fetch

    def get(self, path: str, **params: str) -> tuple[bytes, int]:
        query = {"apiKey": self._key.get_secret_value(), **params}
        response = self._fetch(f"{self._base}{path}", query)
        remaining = {name.lower(): value for name, value in response.headers.items()}.get(REMAINING_HEADER)
        if remaining is None:
            raise MissingCreditsHeaderError(path)
        return response.body, int(float(remaining))
