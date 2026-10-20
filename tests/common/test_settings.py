from pathlib import Path

import pytest

from nfl_alt_lines.common.settings import Settings, SettingsError, load_settings


def write_env(tmp_path: Path, text: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(text)
    return path


def test_reads_values_and_skips_comments_and_blank_lines(tmp_path: Path) -> None:
    env = write_env(tmp_path, "# a comment\n\nODDS_API_KEY=abc\nSMTP_USER=me@example.com\nnot a setting\n")
    settings = load_settings(env, environ={})
    assert settings.odds_api_key is not None
    assert settings.odds_api_key.get_secret_value() == "abc"
    assert settings.smtp_user == "me@example.com"


def test_environment_overrides_the_file(tmp_path: Path) -> None:
    env = write_env(tmp_path, "BANKROLL=100\n")
    assert load_settings(env, environ={"BANKROLL": "250", "UNRELATED": "x"}).bankroll == 250


def test_missing_file_gives_defaults(tmp_path: Path) -> None:
    settings = load_settings(tmp_path / "absent", environ={})
    assert settings.odds_api_key is None
    assert settings.bankroll is None
    assert settings.recipients == ()


def test_empty_values_count_as_unset(tmp_path: Path) -> None:
    env = write_env(tmp_path, "REPORT_EMAIL_TO=\nREPORT_SUBJECT_PREFIX=\nBANKROLL=\n")
    settings = load_settings(env, environ={})
    assert settings.report_email_to == ()
    assert settings.report_subject_prefix == ""


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        ("SMTP_USER=me@example.com\n", ("me@example.com",)),
        ("SMTP_USER=me@example.com\nREPORT_EMAIL_TO=a@x.com, b@y.com\n", ("a@x.com", "b@y.com")),
        ("REPORT_EMAIL_TO=a@x.com,,\n", ("a@x.com",)),
        ("", ()),
    ],
    ids=["defaults-to-sender", "explicit-list", "ignores-empty-entries", "nobody"],
)
def test_recipients(tmp_path: Path, lines: str, expected: tuple[str, ...]) -> None:
    assert load_settings(write_env(tmp_path, lines), environ={}).recipients == expected


@pytest.mark.parametrize(
    "line",
    [
        "BANKROLL=-5",
        "BANKROLL=0",
        "BANKROLL=lots",
        "REPORT_EMAIL_TO=not-an-address",
        "REPORT_EMAIL_BETS_TO=a@x.com, nope",
    ],
    ids=["negative-bankroll", "zero-bankroll", "text-bankroll", "bad-address", "bad-bets-address"],
)
def test_invalid_values_are_rejected(tmp_path: Path, line: str) -> None:
    with pytest.raises(SettingsError):
        load_settings(write_env(tmp_path, line + "\n"), environ={})


def test_error_names_the_field_but_never_the_secret_value(tmp_path: Path) -> None:
    env = write_env(tmp_path, "SMTP_APP_PASSWORD=hunter2\nBANKROLL=not-a-number-hunter2\n")
    with pytest.raises(SettingsError) as raised:
        load_settings(env, environ={})
    assert "bankroll" in str(raised.value)
    assert "hunter2" not in str(raised.value)
    assert raised.value.__cause__ is None


def test_secrets_are_masked_when_printed(tmp_path: Path) -> None:
    settings = load_settings(write_env(tmp_path, "ODDS_API_KEY=abc123\nSMTP_APP_PASSWORD=pw\n"), environ={})
    assert "abc123" not in repr(settings)
    assert "pw'" not in repr(settings)


def test_settings_built_in_code_accept_a_tuple_of_addresses() -> None:
    assert Settings(report_email_to=("a@x.com",)).recipients == ("a@x.com",)


def test_storage_defaults_to_local(tmp_path: Path) -> None:
    settings = load_settings(write_env(tmp_path, ""), environ={})
    assert (settings.storage_backend, settings.s3_bucket, settings.s3_prefix) == ("local", None, "")


def test_s3_storage_needs_a_bucket(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="STORAGE_BACKEND/S3_BUCKET"):
        load_settings(write_env(tmp_path, "STORAGE_BACKEND=s3\n"), environ={})
    settings = load_settings(write_env(tmp_path, "STORAGE_BACKEND=s3\nS3_BUCKET=b\n"), environ={})
    assert settings.s3_bucket == "b"


def test_bets_only_recipients_are_split_like_the_full_list(tmp_path: Path) -> None:
    env = write_env(tmp_path, "SMTP_USER=me@example.com\nREPORT_EMAIL_BETS_TO=a@x.com, b@y.com,\n")
    settings = load_settings(env, environ={})
    assert settings.report_email_bets_to == ("a@x.com", "b@y.com")
    assert settings.recipients == ("me@example.com",)  # the bets-only list never gets every email
