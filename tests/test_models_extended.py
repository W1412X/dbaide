import pytest

from dbaide.models import ConnectionConfig, ModelConfig, TaskType, ColumnProfile, QueryResult


class TestConnectionConfig:
    def test_defaults(self):
        conn = ConnectionConfig(name="test", type="mysql")
        assert conn.name == "test"
        assert conn.type == "mysql"
        assert conn.host == "localhost"
        assert conn.port is None

    def test_name_stripped(self):
        conn = ConnectionConfig(name="  test  ", type="mysql")
        assert conn.name == "test"

    def test_type_normalized(self):
        conn = ConnectionConfig(name="test", type="MYSQL")
        assert conn.type == "mysql"

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError):
            ConnectionConfig(name="test", type="invalid")

    def test_port_range_valid(self):
        conn = ConnectionConfig(name="test", type="mysql", port=3306)
        assert conn.port == 3306

    def test_port_range_invalid(self):
        with pytest.raises(ValueError):
            ConnectionConfig(name="test", type="mysql", port=0)
        with pytest.raises(ValueError):
            ConnectionConfig(name="test", type="mysql", port=70000)

    def test_sqlite_requires_path(self):
        with pytest.raises(ValueError, match="path"):
            ConnectionConfig(name="test", type="sqlite")

    def test_sqlite_with_path(self):
        conn = ConnectionConfig(name="test", type="sqlite", path="/tmp/test.db")
        assert conn.path == "/tmp/test.db"

    def test_mysql_defaults_host(self):
        conn = ConnectionConfig(name="test", type="mysql")
        assert conn.host == "localhost"

    def test_postgres_defaults_host(self):
        conn = ConnectionConfig(name="test", type="postgres")
        assert conn.host == "localhost"


class TestModelConfig:
    def test_defaults(self):
        model = ModelConfig(name="default")
        assert model.name == "default"
        assert model.provider == "none"
        assert model.timeout_seconds == 60

    def test_timeout_clamped_low(self):
        model = ModelConfig(name="test", timeout_seconds=0)
        assert model.timeout_seconds == 1

    def test_timeout_clamped_high(self):
        model = ModelConfig(name="test", timeout_seconds=9999)
        assert model.timeout_seconds == 600

    def test_provider_normalized(self):
        model = ModelConfig(name="test", provider="OPENAI")
        assert model.provider == "openai"

    def test_provider_empty(self):
        model = ModelConfig(name="test", provider="")
        assert model.provider == "none"


class TestTaskType:
    def test_values(self):
        assert TaskType.DATA_QUERY.value == "data_query"
        assert TaskType.SCHEMA_EXPLORE.value == "schema_explore"
        assert TaskType.DATA_PROFILE.value == "data_profile"
        assert TaskType.SQL_DIAGNOSE.value == "sql_diagnose"
        assert TaskType.UNKNOWN.value == "unknown"


class TestColumnProfile:
    def test_defaults(self):
        profile = ColumnProfile(table="t", column="c", row_count=100, null_count=10)
        assert profile.table == "t"
        assert profile.column == "c"
        assert profile.row_count == 100
        assert profile.null_count == 10
        assert profile.top_values == []
        assert profile.sample_values == []


class TestQueryResult:
    def test_defaults(self):
        result = QueryResult(columns=[], rows=[], sql="SELECT 1", row_count=0, elapsed_ms=1.0)
        assert result.rows == []
        assert result.sql == "SELECT 1"
        assert result.row_count == 0
        assert result.truncated is False

    def test_columns_from_rows(self):
        result = QueryResult(
            columns=["a", "b"],
            rows=[{"a": 1, "b": 2}, {"a": 3, "b": 4}],
            sql="SELECT a, b",
            row_count=2,
            elapsed_ms=1.0,
        )
        assert set(result.columns) == {"a", "b"}
