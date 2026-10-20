"""The ledger tables (rule 51), built from the row schemas in schema.py so each column is defined once.

Every table has a surrogate `id` and an index on its key column. Tables are created if missing when the
database is opened (see service.py); schema changes after that need a migration.
"""

import types
from typing import Any, Union, get_args, get_origin

from sqlalchemy import Column, Float, Integer, MetaData, String, Table, UniqueConstraint
from sqlalchemy.types import TypeEngine

from nfl_alt_lines.db.schema import LEDGERS, PRICE_KEY, Ledger, PriceRow, RunRow

metadata = MetaData()
SQL_TYPES: dict[type, type[TypeEngine[Any]]] = {str: String, float: Float, int: Integer}


def _column(name: str, annotation: object, key: str) -> Column[object]:
    """A column for one schema field: optional fields are nullable, the key column is indexed."""
    nullable = get_origin(annotation) in (Union, types.UnionType) and type(None) in get_args(annotation)
    python_type = next(arg for arg in get_args(annotation) if arg is not type(None)) if nullable else annotation
    if not isinstance(python_type, type) or python_type not in SQL_TYPES:
        raise TypeError(name)  # a new field type needs an entry in SQL_TYPES
    return Column(name, SQL_TYPES[python_type](), nullable=nullable, index=name == key)


def table_for(ledger: Ledger) -> Table:
    fields = ledger.row.model_fields
    columns = [_column(name, field.annotation, ledger.key) for name, field in fields.items()]
    return Table(ledger.name, metadata, Column("id", Integer, primary_key=True, autoincrement=True), *columns)


TABLES = {ledger.name: table_for(ledger) for ledger in LEDGERS}
PRICES = Table(
    "odds_prices",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    *(_column(name, field.annotation, "event_id") for name, field in PriceRow.model_fields.items()),
    UniqueConstraint(*PRICE_KEY, name="one_row_per_price_update"),
)
RUNS = Table(
    "runs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    *(_column(name, field.annotation, "job") for name, field in RunRow.model_fields.items()),
)
