"""The one HTTP client: every download from an outside service goes through `get` (rules 9, 16, 40, 66, 67).

Each call has a timeout and a bounded number of attempts with backoff. Only failures that can clear up on
their own are retried (no connection, a timeout, 429 or a 5xx); a 4xx means the request itself is wrong,
so it fails at once. URLs are logged without their query string, which can hold an API key (rule 18).
"""

import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass

TIMEOUT_SECONDS = 30
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 2.0
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
ALLOWED_SCHEME = "https"
ERROR_BODY_CHARS = 300

log = logging.getLogger(__name__)


class FetchError(Exception):
    """A download failed: a request the server refused, or a transient failure that outlasted its retries."""


class RefusedError(FetchError):
    def __init__(self, url: str, status: int, detail: str) -> None:
        super().__init__(f"{url} refused the request: HTTP {status}: {detail}")
        self.status = status


class RetriesExhaustedError(FetchError):
    def __init__(self, url: str, attempts: int, cause: Exception | None) -> None:
        super().__init__(f"{url} failed after {attempts} attempts: {cause}")


class UnsafeURLError(ValueError):
    def __init__(self, url: str) -> None:
        super().__init__(f"only {ALLOWED_SCHEME} URLs are fetched, not {url!r}")


@dataclass(frozen=True)
class Response:
    body: bytes
    headers: Mapping[str, str]


def _url(base: str, params: Mapping[str, str | int | float] | None) -> str:
    if urllib.parse.urlsplit(base).scheme != ALLOWED_SCHEME:
        raise UnsafeURLError(base)
    return f"{base}?{urllib.parse.urlencode(params)}" if params else base


def _attempt(url: str, timeout: float) -> Response:
    # _url allows only https, so no file: or custom scheme can reach urlopen.
    with urllib.request.urlopen(url, timeout=timeout) as reply:  # noqa: S310  # nosec B310
        return Response(body=reply.read(), headers=dict(reply.headers.items()))


def get(
    base: str,
    params: Mapping[str, str | int | float] | None = None,
    *,
    timeout: float = TIMEOUT_SECONDS,
    attempts: int = MAX_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
) -> Response:
    """GET `base` with `params`, retrying transient failures up to `attempts` times in all."""
    url = _url(base, params)
    failure: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return _attempt(url, timeout)
        except urllib.error.HTTPError as error:  # before URLError, which it subclasses
            if error.code not in RETRYABLE_STATUS:
                detail = error.read().decode(errors="replace")[:ERROR_BODY_CHARS]
                raise RefusedError(base, error.code, detail) from error
            failure = error
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            failure = error
        if attempt < attempts:
            log.warning("fetch failed, retrying", extra={"url": base, "attempt": attempt, "error": str(failure)})
            sleep(BACKOFF_SECONDS * 2 ** (attempt - 1))
    raise RetriesExhaustedError(base, attempts, failure) from failure
