"""Kickoff temperature and wind for games the schedule has no weather for yet.

The schedule only gets weather some time after a game is played, so upcoming and just-played outdoor
games would otherwise be treated as average. This fills them from Open-Meteo (free, no key): its
forecast for the next 16 days and its record of the last 92.

Each stadium's reply is cached for a few hours. When Open-Meteo cannot be reached, the last cached reply
is used, and a stadium with none is skipped: its games keep the schedule's weather (missing), as before.
"""

import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from nfl_alt_lines.common import http_client, storage
from nfl_alt_lines.common.storage import Storage

CACHE = "raw/weather"  # one reply per stadium in storage
CACHE_HOURS = 6
API = "https://api.open-meteo.com/v1/forecast"
PAST_DAYS, FORECAST_DAYS = 92, 16
REACH_MARGIN_DAYS = 2, 1  # stay this far inside the past and forecast windows
GAME_HOURS = 3
SCHEDULE_ZONE = "America/New_York"  # the schedule lists kickoffs in Eastern time
HOUR_CHARS = 2  # gametime is "HH:MM"
INDOOR_ROOFS = ("dome", "closed")
QUERY: Mapping[str, str | int | float] = {
    "hourly": "temperature_2m,wind_speed_10m",
    "temperature_unit": "fahrenheit",
    "wind_speed_unit": "mph",
    "past_days": PAST_DAYS,
    "forecast_days": FORECAST_DAYS,
    "timezone": "GMT",
}
# Stadiums that are open to the weather at least some of the time: stadium_id -> (latitude, longitude).
STADIUMS = {
    "ATL97": (33.7554, -84.4008), "BAL00": (39.2780, -76.6227), "BOS00": (42.0909, -71.2643),
    "BUF00": (42.7738, -78.7870), "CAR00": (35.2258, -80.8528), "CHI98": (41.8623, -87.6167),
    "CIN00": (39.0955, -84.5161), "CLE00": (41.5061, -81.6995), "DAL00": (32.7473, -97.0945),
    "DEN00": (39.7439, -105.0201), "GNB00": (44.5013, -88.0622), "HOU00": (29.6847, -95.4107),
    "IND00": (39.7601, -86.1639), "JAX00": (30.3239, -81.6373), "KAN00": (39.0489, -94.4839),
    "MIA00": (25.9580, -80.2389), "NAS00": (36.1665, -86.7713), "NYC01": (40.8135, -74.0745),
    "PHI00": (39.9008, -75.1675), "PHO00": (33.5276, -112.2626), "PIT00": (40.4468, -80.0158),
    "SEA00": (47.5952, -122.3316), "SFO01": (37.4033, -121.9694), "TAM00": (27.9759, -82.5033),
    "WAS00": (38.9076, -76.8645), "LON00": (51.5560, -0.2795), "LON02": (51.6043, -0.0664),
    "MEX00": (19.3029, -99.1505), "FRA00": (50.0686, 8.6455), "GER00": (48.2188, 11.6247),
    "MAD01": (40.4531, -3.6883), "SAO00": (-23.5453, -46.4742), "RIO00": (-22.9121, -43.2302),
}  # fmt: skip
# Neutral-site games are sometimes filed under the home team's own stadium id.
BY_NAME = {"Tottenham": "LON02", "Wembley": "LON00"}

log = logging.getLogger(__name__)


class HourlySeries(BaseModel):
    """The `hourly` block of an Open-Meteo reply: equally long series of times and readings."""

    model_config = ConfigDict(frozen=True)

    time: tuple[str, ...]
    temperature_2m: tuple[float | None, ...]
    wind_speed_10m: tuple[float | None, ...]

    @model_validator(mode="after")
    def _same_length(self) -> HourlySeries:
        if not len(self.time) == len(self.temperature_2m) == len(self.wind_speed_10m):
            raise ValueError("hourly series differ in length")  # noqa: TRY003 - pydantic wraps it
        return self


class Forecast(BaseModel):
    """The part of an Open-Meteo reply that is used; anything else in it is ignored."""

    model_config = ConfigDict(frozen=True)

    hourly: HourlySeries


def kickoffs(schedule: pd.DataFrame) -> pd.Series:
    """Kickoff time in UTC for every game that has one."""
    local = pd.to_datetime(schedule["gameday"] + " " + schedule["gametime"], errors="coerce")
    return local.dt.tz_localize(SCHEDULE_ZONE).dt.tz_convert("UTC")


def kickoff_hour(schedule: pd.DataFrame) -> pd.Series:
    """Kickoff hour in Eastern time, as a float (NaN where the schedule has no time)."""
    return schedule["gametime"].str.slice(0, HOUR_CHARS).astype(float)


def likely_roof(schedule: pd.DataFrame) -> pd.Series:
    """Roof state, with a retractable roof not yet announced taken as that stadium's usual state."""
    usual = schedule.groupby("stadium_id")["roof"].transform(lambda s: s.mode().iloc[0] if s.notna().any() else np.nan)
    return schedule["roof"].fillna(usual)


def _site(game: pd.Series) -> str | None:
    named = next((site for name, site in BY_NAME.items() if name in str(game["stadium"])), None)
    site = named or game["stadium_id"]
    return site if site in STADIUMS else None


def _cache_key(site: str) -> str:
    return f"{CACHE}/{site}.json"


def _is_fresh(written: datetime | None, now: datetime) -> bool:
    return written is not None and now - written <= timedelta(hours=CACHE_HOURS)


def _refresh(site: str, store: Storage) -> None:
    """Fetch and cache a stadium's reply; on failure keep whatever is cached and log why."""
    latitude, longitude = STADIUMS[site]
    try:
        body = http_client.get(API, {"latitude": latitude, "longitude": longitude, **QUERY}).body
    except http_client.FetchError as error:
        log.warning("weather not fetched; using the cached reply if any", extra={"site": site, "error": str(error)})
        return
    if _parse(site, body, "weather reply not understood; using the cached reply if any") is not None:
        store.write(_cache_key(site), body)


def _parse(site: str, body: bytes, problem: str) -> HourlySeries | None:
    """The reply's hourly series, or None (logged with `problem`) when it is not the expected shape."""
    try:
        return Forecast.model_validate_json(body).hourly
    except ValidationError as error:
        log.warning(problem, extra={"site": site, "error": str(error)})
        return None


def hourly(site: str) -> pd.DataFrame | None:
    """Hourly temperature (F) and wind (mph) at a stadium, indexed by UTC hour; None if it cannot be fetched."""
    store, key = storage.default_storage(), _cache_key(site)
    if not _is_fresh(store.modified(key), datetime.now(UTC)):
        _refresh(site, store)
    if not store.exists(key):
        return None
    series = _parse(site, store.read(key), "cached weather not understood; skipping the stadium")
    if series is None:
        return None
    index = pd.to_datetime(list(series.time), utc=True)
    readings = {"temp": list(series.temperature_2m), "wind": list(series.wind_speed_10m)}
    return pd.DataFrame(readings, index=index, dtype=float)


def lookup(schedule: pd.DataFrame, games: pd.Index) -> pd.DataFrame:
    """Average temperature and wind over each listed game's three hours, where a stadium and time are known."""
    kickoff = kickoffs(schedule)
    sites = {index: _site(schedule.loc[index]) for index in games}
    timed = {index: site for index, site in sites.items() if site is not None and not pd.isna(kickoff[index])}
    tables = {site: hourly(site) for site in set(timed.values())}  # one fetch per stadium, not per game
    rows = {}
    for index, site in timed.items():
        table = tables[site]
        if table is None:
            continue
        start = kickoff[index].floor("h")
        window = table.loc[start : start + pd.Timedelta(hours=GAME_HOURS - 1)].dropna()
        if len(window):
            rows[index] = window.mean()
    return pd.DataFrame.from_dict(rows, orient="index", columns=["temp", "wind"])


def add_weather(schedule: pd.DataFrame, now: pd.Timestamp) -> pd.DataFrame:
    """Fill missing temperature and wind for recent and upcoming open-air games; adds a `forecast` flag."""
    schedule = schedule.copy()
    kickoff = kickoffs(schedule)
    past_margin, forecast_margin = REACH_MARGIN_DAYS
    earliest = now - pd.Timedelta(days=PAST_DAYS - past_margin)
    latest = now + pd.Timedelta(days=FORECAST_DAYS - forecast_margin)
    in_reach = (kickoff > earliest) & (kickoff < latest)
    open_air = ~likely_roof(schedule).isin(INDOOR_ROOFS)
    missing = schedule["wind"].isna() | schedule["temp"].isna()
    found = lookup(schedule, schedule.index[in_reach & open_air & missing])
    schedule["forecast"] = False
    if len(found):
        schedule.loc[found.index, ["temp", "wind"]] = found.round(0).to_numpy()
        schedule.loc[found.index, "forecast"] = True
    return schedule
