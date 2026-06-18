"""Column type → kind classification (drives which profiling metrics run)."""
from dbaide.assets.profiler import kind_from_type, infer_data_kind
from dbaide.models import ColumnInfo


def _kind(data_type: str) -> str:
    return kind_from_type(ColumnInfo(name="c", data_type=data_type))


def test_postgres_format_type_text_columns_classified_as_text():
    # PostgreSQL's format_type() canonical names for CHAR/VARCHAR reduce to "character".
    assert _kind("character varying(255)") == "text"
    assert _kind("character(10)") == "text"
    assert _kind("varchar") == "text"
    assert infer_data_kind(ColumnInfo(name="c", data_type="character varying")) == "text"


def test_other_types_unaffected():
    assert _kind("integer") == "numeric"
    assert _kind("double precision") == "numeric"
    assert _kind("timestamp without time zone") == "temporal"
    assert _kind("boolean") == "boolean"
    assert _kind("json") == "text"
    assert _kind("some_weird_udt") == "unknown"
