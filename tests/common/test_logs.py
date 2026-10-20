import io
import json
import logging
import warnings
from collections.abc import Iterator

import pytest

from nfl_alt_lines.common.logs import RUN_ID_LENGTH, configure_logging, mask, timed

PASSWORD = "hunter2"  # noqa: S105 - a fake password, to prove it never reaches a log line
SECRET_URL = f"postgresql+psycopg://nfl_alt_lines.ref:{PASSWORD}@pooler.example.com:5432/postgres"


@pytest.fixture(autouse=True)
def restore_root_logger() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers, root.level = handlers, level
    logging.captureWarnings(False)


def lines(stream: io.StringIO) -> list[dict[str, object]]:
    """Each line must be one JSON object."""
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_lines_are_json_with_job_run_id_and_context() -> None:
    stream = io.StringIO()
    run_id = configure_logging("report", stream)
    logging.getLogger("nfl_alt_lines.demo").warning("fetch failed", extra={"attempt": 2})
    (line,) = lines(stream)
    assert len(run_id) == RUN_ID_LENGTH
    assert line == line | {"event": "fetch failed", "level": "warning", "logger": "nfl_alt_lines.demo"}
    assert line["job"] == "report" and line["run_id"] == run_id and line["attempt"] == 2
    assert str(line["timestamp"]).endswith("Z")  # UTC, as CloudWatch shows it


def test_lines_without_context_carry_only_the_standard_fields() -> None:
    stream = io.StringIO()
    configure_logging("odds-logger", stream)
    logging.getLogger("x").info("prices due")
    assert set(lines(stream)[0]) == {"event", "job", "level", "logger", "run_id", "timestamp"}


def test_a_traceback_stays_on_the_same_line() -> None:
    stream = io.StringIO()
    configure_logging("report", stream)
    try:
        raise ValueError("bad row")  # noqa: TRY003, TRY301 - the traceback under test
    except ValueError:
        logging.getLogger("x").exception("report FAILED; the previous report is kept")
    (line,) = lines(stream)
    assert "report FAILED" in str(line["event"])  # the phrase the CloudWatch alarm's metric filter matches
    assert "ValueError: bad row" in str(line["exception"])


def test_library_warnings_become_log_lines() -> None:
    stream = io.StringIO()
    configure_logging("report", stream)
    warnings.warn("All-NaN slice encountered", RuntimeWarning, stacklevel=1)
    assert "All-NaN slice" in str(lines(stream)[0]["event"])


@pytest.mark.parametrize(
    ("text", "masked"),
    [
        ("sent to friend@example.com", "sent to [email]"),
        (SECRET_URL, "postgresql+psycopg://nfl_alt_lines.ref:[password]@pooler.example.com:5432/postgres"),
        ("https://example.com/odds?apiKey=x", "https://example.com/odds?apiKey=x"),
        ("credits_remaining 2480", "credits_remaining 2480"),
    ],
    ids=["email", "url-password", "url-without-password", "plain"],
)
def test_mask(text: str, masked: str) -> None:
    assert mask(text) == masked


def test_no_line_holds_an_address_or_a_password_wherever_it_hides() -> None:
    """Security: context, nested context and exception text are all masked before a line is written."""
    stream = io.StringIO()
    configure_logging("report", stream)
    log = logging.getLogger("x")
    log.info("sent", extra={"to": "a@example.com", "nested": {"url": SECRET_URL}})
    try:
        raise ConnectionError(f"could not reach {SECRET_URL} for b@example.com")  # noqa: TRY003, TRY301 - under test
    except ConnectionError:
        log.exception("database down")
    text = stream.getvalue()
    assert PASSWORD not in text and "@example.com" not in text
    assert len(lines(stream)) == 2  # masking kept the JSON valid


def test_each_run_gets_a_new_id_and_replaces_the_handler() -> None:
    first, second = configure_logging("a", io.StringIO()), configure_logging("a", io.StringIO())
    assert first != second
    assert len(logging.getLogger().handlers) == 1


def test_level_filters_quieter_lines() -> None:
    stream = io.StringIO()
    configure_logging("a", stream, level=logging.WARNING)
    logging.getLogger("x").info("hidden")
    assert stream.getvalue() == ""


def test_timed_logs_each_stage_even_when_it_fails() -> None:
    stream = io.StringIO()
    configure_logging("report", stream)
    with timed("features"):
        pass
    with pytest.raises(ValueError, match="bad"), timed("ledger"):
        raise ValueError("bad")  # noqa: TRY003 - under test
    first, second = lines(stream)
    assert (first["event"], first["stage"], second["stage"]) == ("stage finished", "features", "ledger")
    assert isinstance(first["seconds"], float) and first["seconds"] >= 0
