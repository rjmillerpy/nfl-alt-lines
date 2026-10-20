"""Open-Meteo is replaced by a fake `http_client.get`; the reply cache lives in tmp_path."""

import json
import logging
import os
import time
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from nfl_alt_lines import weather
from nfl_alt_lines.common import http_client, storage
from nfl_alt_lines.common.storage import LocalStorage
from nfl_alt_lines.weather import HourlySeries, add_weather, kickoff_hour, likely_roof

NOW = pd.Timestamp("2026-10-13", tz="UTC")
HOURS = pd.date_range("2026-10-01", "2026-10-25", freq="h", tz="UTC")
STALE_SECONDS = (weather.CACHE_HOURS + 1) * 3600


def forecast(temp: float = 40.0, wind: float = 20.0) -> bytes:
    times = [hour.strftime("%Y-%m-%dT%H:%M") for hour in HOURS]
    hourly = {"time": times, "temperature_2m": [temp] * len(times), "wind_speed_10m": [wind] * len(times)}
    return json.dumps({"latitude": 1.0, "hourly": hourly}).encode()


class OpenMeteo:
    """Answers with `body`, or raises `failure`, and records the coordinates asked for."""

    def __init__(self, body: bytes = b"", failure: Exception | None = None) -> None:
        self.body = body or forecast()
        self.failure = failure
        self.asked: list[tuple[object, object]] = []

    def get(self, url: str, params: dict[str, object]) -> http_client.Response:
        self.asked.append((params["latitude"], params["longitude"]))
        if self.failure is not None:
            raise self.failure
        return http_client.Response(body=self.body, headers={})


@pytest.fixture
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The weather cache folder inside a temporary storage."""
    monkeypatch.setattr(storage, "default_storage", lambda: LocalStorage(tmp_path))
    folder = tmp_path / weather.CACHE
    folder.mkdir(parents=True)
    return folder


def serve(monkeypatch: pytest.MonkeyPatch, network: OpenMeteo) -> OpenMeteo:
    monkeypatch.setattr(http_client, "get", network.get)
    return network


def schedule() -> pd.DataFrame:
    rows = [
        ("g1", "2026-10-04", "13:00", "DEN00", "Mile High", "outdoors", 60.0, 5.0),
        ("g2", "2026-10-18", "13:00", "DEN00", "Mile High", None, None, None),
        ("g3", "2026-10-18", "13:00", "IND00", "Lucas Oil", None, None, None),
        ("g4", "2026-10-11", "13:00", "IND00", "Lucas Oil", "closed", None, None),
    ]
    columns = ["game_id", "gameday", "gametime", "stadium_id", "stadium", "roof", "temp", "wind"]
    return pd.DataFrame(rows, columns=columns)


def one_game(gameday: str, stadium_id: str = "DEN00", stadium: str = "Mile High") -> pd.DataFrame:
    row = ("g", gameday, "13:00", stadium_id, stadium, "outdoors", None, None)
    return pd.DataFrame([row], columns=schedule().columns)


def test_unannounced_roof_takes_the_stadiums_usual_state() -> None:
    assert list(likely_roof(schedule())) == ["outdoors", "outdoors", "closed", "closed"]


def test_kickoff_hour_reads_the_eastern_hour() -> None:
    frame = pd.DataFrame({"gametime": ["13:00", "20:15", None]})
    assert kickoff_hour(frame).tolist()[:2] == [13.0, 20.0]
    assert pd.isna(kickoff_hour(frame).iloc[2])


def test_only_open_air_games_without_weather_are_filled(cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    network = serve(monkeypatch, OpenMeteo())
    filled = add_weather(schedule(), NOW).set_index("game_id")
    assert filled.loc[["g2"], ["temp", "wind"]].to_numpy().tolist() == [[40.0, 20.0]]
    assert filled.loc["g1", "wind"] == 5.0
    assert pd.isna(filled.loc["g3", "wind"])
    assert filled["forecast"].tolist() == [False, True, False, False]
    assert network.asked == [weather.STADIUMS["DEN00"]]


@pytest.mark.parametrize(
    ("gameday", "fetched"),
    [("2026-07-01", False), ("2026-07-20", True), ("2026-10-26", True), ("2026-11-05", False)],
    ids=["beyond-the-past-record", "inside-the-past-record", "inside-the-forecast", "beyond-the-forecast"],
)
def test_only_games_within_open_meteos_reach_are_looked_up(
    cache: Path, monkeypatch: pytest.MonkeyPatch, gameday: str, fetched: bool
) -> None:
    network = serve(monkeypatch, OpenMeteo())
    add_weather(one_game(gameday), NOW)
    assert bool(network.asked) is fetched


def test_a_stadium_is_fetched_once_for_all_its_games(cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    network = serve(monkeypatch, OpenMeteo())
    games = pd.concat([one_game("2026-10-18"), one_game("2026-10-19"), one_game("2026-10-20")], ignore_index=True)
    filled = add_weather(games, NOW)
    assert network.asked == [weather.STADIUMS["DEN00"]]
    assert filled["forecast"].all()


def test_a_neutral_site_is_found_by_stadium_name(cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    network = serve(monkeypatch, OpenMeteo())
    add_weather(one_game("2026-10-18", stadium_id="JAX00", stadium="Wembley Stadium"), NOW)
    assert network.asked == [weather.STADIUMS["LON00"]]


@pytest.mark.parametrize(
    ("age_seconds", "fetches"),
    [(0, 0), (STALE_SECONDS, 1)],
    ids=["fresh-cache-is-used", "stale-cache-is-refreshed"],
)
def test_replies_are_cached_for_a_few_hours(
    cache: Path, monkeypatch: pytest.MonkeyPatch, age_seconds: int, fetches: int
) -> None:
    cached = cache / "DEN00.json"
    cached.write_bytes(forecast(temp=30.0))
    then = time.time() - age_seconds
    os.utime(cached, (then, then))
    network = serve(monkeypatch, OpenMeteo(body=forecast(temp=55.0)))
    filled = add_weather(one_game("2026-10-18"), NOW)
    assert len(network.asked) == fetches
    assert filled.loc[0, "temp"] == (55.0 if fetches else 30.0)


def test_a_failed_fetch_falls_back_to_the_stale_cache_and_is_logged(
    cache: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    cached = cache / "DEN00.json"
    cached.write_bytes(forecast(temp=30.0))
    os.utime(cached, (0, 0))
    serve(monkeypatch, OpenMeteo(failure=http_client.RetriesExhaustedError(weather.API, 3, TimeoutError())))
    with caplog.at_level(logging.WARNING, logger=weather.__name__):
        filled = add_weather(one_game("2026-10-18"), NOW)
    assert filled.loc[0, "temp"] == 30.0
    assert [getattr(record, "site", None) for record in caplog.records] == ["DEN00"]


def test_a_failed_fetch_without_a_cache_leaves_the_game_unfilled(cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, OpenMeteo(failure=http_client.RefusedError(weather.API, 400, "bad request")))
    filled = add_weather(one_game("2026-10-18"), NOW)
    assert pd.isna(filled.loc[0, "temp"])
    assert not filled.loc[0, "forecast"]


def test_a_malformed_reply_is_not_cached(
    cache: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    serve(monkeypatch, OpenMeteo(body=b'{"error": true, "reason": "bad latitude"}'))
    with caplog.at_level(logging.WARNING, logger=weather.__name__):
        filled = add_weather(one_game("2026-10-18"), NOW)
    assert not (cache / "DEN00.json").exists()
    assert pd.isna(filled.loc[0, "temp"])
    assert "not understood" in caplog.text


def test_a_corrupt_cached_reply_is_skipped(cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (cache / "DEN00.json").write_text("{not json")
    network = serve(monkeypatch, OpenMeteo())
    filled = add_weather(one_game("2026-10-18"), NOW)
    assert network.asked == []
    assert pd.isna(filled.loc[0, "temp"])


def test_missing_hourly_readings_are_left_out_of_the_average(cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reply = json.loads(forecast(temp=40.0))
    kickoff = list(HOURS).index(pd.Timestamp("2026-10-18 17:00", tz="UTC"))  # 13:00 Eastern
    reply["hourly"]["temperature_2m"][kickoff] = None
    reply["hourly"]["temperature_2m"][kickoff + 1] = 46.0
    serve(monkeypatch, OpenMeteo(body=json.dumps(reply).encode()))
    filled = add_weather(one_game("2026-10-18"), NOW)
    assert filled.loc[0, "temp"] == 43.0


@pytest.mark.parametrize(
    "hourly",
    [
        {"time": ["2026-10-18T00:00"], "temperature_2m": [1.0, 2.0], "wind_speed_10m": [1.0]},
        {"time": ["2026-10-18T00:00"], "temperature_2m": ["warm"], "wind_speed_10m": [1.0]},
        {"time": ["2026-10-18T00:00"], "wind_speed_10m": [1.0]},
    ],
    ids=["series-differ-in-length", "reading-not-a-number", "series-missing"],
)
def test_reply_shape_is_checked(hourly: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        HourlySeries.model_validate(hourly)
