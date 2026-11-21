"""Phone alerts through Pushover (the owner's choice, 2026-11-21), next to the emails.

Loud alerts use Pushover's emergency priority, which Android plays through the alarm channel: it sounds on a
phone set to silent while calls and other notifications stay quiet. They repeat every minute for two minutes or
until acknowledged. Normal alerts follow the phone's own sound settings. Keys: PUSHOVER_USER and PUSHOVER_TOKEN;
without both, nothing is sent. A failed alert is logged and never fails the job: the email already went.
"""

import logging
from collections.abc import Callable, Mapping

from nfl_alt_lines.common import http_client
from nfl_alt_lines.common.settings import Settings

API = "https://api.pushover.net/1/messages.json"
TITLE_CHARS, MESSAGE_CHARS = 250, 1024
EMERGENCY, NORMAL = 2, 0
RETRY_SECONDS, EXPIRE_SECONDS = 60, 120

Post = Callable[[str, Mapping[str, str | int]], http_client.Response]

log = logging.getLogger(__name__)


def _post(url: str, form: Mapping[str, str | int]) -> http_client.Response:
    return http_client.post_once(url, form)


def configured(settings: Settings) -> bool:
    return settings.pushover_user is not None and settings.pushover_token is not None


def alert(settings: Settings, title: str, message: str, loud: bool, post: Post = _post) -> bool:
    """Send one alert; True if Pushover accepted it."""
    if settings.pushover_user is None or settings.pushover_token is None:
        return False
    form: dict[str, str | int] = {
        "token": settings.pushover_token.get_secret_value(),
        "user": settings.pushover_user.get_secret_value(),
        "title": title[:TITLE_CHARS],
        "message": message[:MESSAGE_CHARS],
        "priority": EMERGENCY if loud else NORMAL,
    }
    if loud:
        form |= {"retry": RETRY_SECONDS, "expire": EXPIRE_SECONDS}
    try:
        post(API, form)
    except http_client.FetchError as problem:
        log.warning("phone alert not sent", extra={"reason": type(problem).__name__})
        return False
    log.info("phone alert sent", extra={"loud": loud})
    return True
