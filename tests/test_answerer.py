import pytest

from dbaide.agent.answerer import AnswerFormatter
from dbaide.models import ColumnProfile, QueryResult, TableInfo


class TestAnswerFormatter:
    def setup_method(self):
        self.formatter = AnswerFormatter()

    def test_tables_empty(self):
        result = self.formatter.tables([])
        assert "No visible tables" in result

    def test_tables_single(self):
        tables = [TableInfo(name="users", table_type="table", estimated_rows=100)]
        result = self.formatter.tables(tables)
        assert "users" in result
        assert "100" in result

    def test_tables_multiple(self):
        tables = [
            TableInfo(name="users", table_type="table"),
            TableInfo(name="orders", table_type="table"),
        ]
        result = self.formatter.tables(tables)
        assert "2 table(s)" in result
        assert "users" in result
        assert "orders" in result

    def test_tables_with_comment(self):
        tables = [TableInfo(name="users", table_type="table", comment="User accounts")]
        result = self.formatter.tables(tables)
        assert "User accounts" in result

    def test_profiles_empty(self):
        result = self.formatter.profiles([])
        assert "No column profiles" in result

    def test_profiles_single(self):
        profiles = [ColumnProfile(table="users", column="email", row_count=100, null_count=5, distinct_count=95)]
        result = self.formatter.profiles(profiles)
        assert "users.email" in result
        assert "100" in result
        assert "5" in result

    def test_profiles_with_top_values(self):
        profiles = [ColumnProfile(
            table="users", column="status", row_count=100, null_count=0,
            distinct_count=3,
            top_values=[{"value": "active", "count": 80}, {"value": "inactive", "count": 20}],
        )]
        result = self.formatter.profiles(profiles)
        assert "active" in result
        assert "80" in result

    def test_query_result_empty(self):
        result = QueryResult(columns=[], rows=[], sql="SELECT 1", row_count=0, elapsed_ms=1.0)
        formatted = self.formatter.query_result(result, sql="SELECT 1")
        assert "查询未返回任何数据" in formatted
        assert "共 0 条记录" in formatted

    def test_query_result_with_rows(self):
        result = QueryResult(
            columns=["id", "name"],
            rows=[{"id": 1, "name": "test"}],
            sql="SELECT id, name",
            row_count=1,
            elapsed_ms=5.0,
        )
        formatted = self.formatter.query_result(result, sql="SELECT id, name")
        assert "id" in formatted
        assert "name" in formatted
        assert "test" in formatted
        assert "共 1 条记录" in formatted

    def test_query_result_truncated(self):
        rows = [{"id": i} for i in range(25)]
        result = QueryResult(columns=["id"], rows=rows[:20], sql="SELECT id", row_count=25, elapsed_ms=5.0, truncated=True)
        formatted = self.formatter.query_result(result, sql="SELECT id")
        assert "仅展示前 20 条" in formatted
        assert "25" in formatted

    def test_query_result_with_rationale(self):
        result = QueryResult(columns=[], rows=[], sql="SELECT 1", row_count=0, elapsed_ms=1.0)
        formatted = self.formatter.query_result(result, sql="SELECT 1", rationale="Test rationale")
        assert "Test rationale" in formatted
        assert "查询未返回任何数据" in formatted
