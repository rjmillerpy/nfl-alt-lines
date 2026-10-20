"""A stand-in for the network: answers Odds API paths from a script and records what was asked (and paid)."""

import json
from collections.abc import Mapping

from nfl_alt_lines.common.http_client import Response
from nfl_alt_lines.odds.client import API


class FakeOddsAPI:
    def __init__(self, replies: Mapping[str, object], credits: int = 500, base: str = API) -> None:
        self._replies = replies
        self._base = base
        self.credits = credits
        self.requests: list[tuple[str, dict[str, str | int | float]]] = []

    def __call__(self, url: str, params: Mapping[str, str | int | float]) -> Response:
        path = url.removeprefix(self._base)
        self.requests.append((path, dict(params)))
        markets = str(params.get("markets", ""))
        self.credits -= len(markets.split(",")) if markets else 0
        return Response(json.dumps(self._replies[path]).encode(), {"X-Requests-Remaining": str(self.credits)})

    @property
    def paths(self) -> list[str]:
        return [path for path, _ in self.requests]
