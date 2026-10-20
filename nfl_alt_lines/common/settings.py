"""The one place settings are read (rules 5, 16, 17): `.env` in the project root, overridden by environment
variables of the same name (how a container passes them). Values are validated here, at the boundary.

Secrets are `SecretStr`, so printing or logging the settings shows `**********` instead of the value (rule 18).
"""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator, model_validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"
COMMENT = "#"
LIST_SEPARATOR = ","
WHOLE_MODEL = "STORAGE_BACKEND/S3_BUCKET"  # the only check that spans fields


class SettingsError(Exception):
    def __init__(self, problem: ValidationError) -> None:
        # Field names and reasons only: pydantic's message can echo the offending value, which may be a secret.
        fields = ", ".join(".".join(map(str, error["loc"])) or WHOLE_MODEL for error in problem.errors())
        super().__init__(f"invalid settings in .env or the environment: {fields}")


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    odds_api_key: SecretStr | None = None
    odds_api_key_free: SecretStr | None = None
    smtp_user: str | None = None
    smtp_app_password: SecretStr | None = None
    report_email_to: tuple[str, ...] = ()  # every email
    report_email_bets_to: tuple[str, ...] = ()  # only the emails with a bet to place now
    report_subject_prefix: str = ""
    bankroll: float | None = Field(default=None, gt=0)
    storage_backend: Literal["local", "s3"] = "local"
    s3_bucket: str | None = None
    s3_prefix: str = ""
    s3_endpoint_url: str | None = None  # an S3-compatible server such as MinIO; unset for AWS
    database_url: SecretStr | None = None  # SQLAlchemy URL (it can hold a password); unset: SQLite in data/

    @model_validator(mode="after")
    def _s3_needs_a_bucket(self) -> Settings:
        if self.storage_backend == "s3" and not self.s3_bucket:
            raise ValueError("STORAGE_BACKEND=s3 needs S3_BUCKET")  # noqa: TRY003 - pydantic
        return self

    @field_validator("report_email_to", "report_email_bets_to", mode="before")
    @classmethod
    def _split_addresses(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(address.strip() for address in value.split(LIST_SEPARATOR) if address.strip())
        return value

    @field_validator("report_email_to", "report_email_bets_to")
    @classmethod
    def _addresses_look_like_email(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not all("@" in address for address in value):
            raise ValueError("must be comma-separated email addresses")  # noqa: TRY003 - pydantic
        return value

    @property
    def recipients(self) -> tuple[str, ...]:
        """Who gets the report: REPORT_EMAIL_TO, or the sending address when that is not set."""
        if self.report_email_to:
            return self.report_email_to
        return (self.smtp_user,) if self.smtp_user else ()


def _read_env_file(path: Path) -> dict[str, str]:
    """NAME=value lines; blank lines, comments and lines without `=` are skipped."""
    if not path.exists():
        return {}
    pairs = {}
    for line in path.read_text().splitlines():
        name, separator, value = line.partition("=")
        if separator and not name.strip().startswith(COMMENT):
            pairs[name.strip()] = value.strip()
    return pairs


def load_settings(path: Path = ENV_FILE, environ: Mapping[str, str] = os.environ) -> Settings:
    names = {name.upper() for name in Settings.model_fields}
    values = {**_read_env_file(path), **{name: value for name, value in environ.items() if name in names}}
    non_empty = {name.lower(): value for name, value in values.items() if name in names and value != ""}
    try:
        return Settings.model_validate(non_empty)
    except ValidationError as problem:
        raise SettingsError(problem) from None  # not `from problem`: its text can hold a secret value
