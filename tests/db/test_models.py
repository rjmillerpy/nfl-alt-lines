import pytest
from sqlalchemy import Float, Integer, String

from nfl_alt_lines.db.models import TABLES, _column
from nfl_alt_lines.db.schema import LEDGERS


def test_each_ledger_has_a_table_with_an_id_and_every_schema_column() -> None:
    for ledger in LEDGERS:
        table = TABLES[ledger.name]
        assert [column.name for column in table.columns] == ["id", *ledger.row.model_fields]
        assert table.c[ledger.key].index


def test_columns_follow_the_field_types() -> None:
    predictions = TABLES["predictions"]
    assert isinstance(predictions.c.price.type, Integer) and not predictions.c.price.nullable
    assert isinstance(predictions.c.clv.type, Float) and predictions.c.clv.nullable
    assert isinstance(predictions.c.book.type, String)


def test_an_unknown_field_type_is_refused() -> None:
    with pytest.raises(TypeError):
        _column("when", bytes, "key")
