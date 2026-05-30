import pytest
from dbaide.context.catalog import CatalogMatcher, _query_tokens, _expand_tokens
from dbaide.models import TableInfo, ColumnInfo


class TestQueryTokens:
    def test_english_tokens(self):
        tokens = _query_tokens("user email address")
        assert "user" in tokens
        assert "email" in tokens
        assert "address" in tokens

    def test_chinese_tokens(self):
        tokens = _query_tokens("用户邮箱地址")
        assert "用户" in tokens
        assert "邮箱" in tokens
        assert "地址" in tokens

    def test_mixed_tokens(self):
        tokens = _query_tokens("用户 email")
        assert "用户" in tokens
        assert "email" in tokens

    def test_empty_query(self):
        tokens = _query_tokens("")
        assert tokens == []

    def test_numbers_included(self):
        tokens = _query_tokens("table123")
        assert "table123" in tokens


class TestExpandTokens:
    def test_chinese_expands(self):
        expanded = _expand_tokens(["用户"])
        assert "user" in expanded
        assert "users" in expanded
        assert "account" in expanded

    def test_english_expands(self):
        expanded = _expand_tokens(["user"])
        assert "用户" in expanded

    def test_empty_tokens(self):
        expanded = _expand_tokens([])
        assert expanded == set()

    def test_no_expansion(self):
        expanded = _expand_tokens(["xyzzy123"])
        assert "xyzzy123" in expanded


class TestCatalogMatcher:
    def test_score_tables_basic(self):
        matcher = CatalogMatcher()
        tables = [
            TableInfo(name="users", comment="用户表"),
            TableInfo(name="orders", comment="订单表"),
        ]
        results = matcher.score_tables("用户", tables)
        assert len(results) == 2
        assert results[0].table.name == "users"
        assert results[0].score > results[1].score

    def test_score_tables_alias_match(self):
        matcher = CatalogMatcher()
        tables = [
            TableInfo(name="customer", comment="客户信息"),
            TableInfo(name="product", comment="商品信息"),
        ]
        results = matcher.score_tables("用户", tables)
        assert results[0].table.name == "customer"

    def test_score_tables_no_match(self):
        matcher = CatalogMatcher()
        tables = [TableInfo(name="logs", comment="日志")]
        results = matcher.score_tables("xyzzy", tables)
        assert len(results) == 1
        assert results[0].score == 0.1

    def test_score_tables_limit(self):
        matcher = CatalogMatcher()
        tables = [TableInfo(name=f"t{i}") for i in range(20)]
        results = matcher.score_tables("t", tables, limit=5)
        assert len(results) == 5

    def test_score_columns_pk_bonus(self):
        matcher = CatalogMatcher()
        columns = [
            ColumnInfo(name="id", primary_key=True),
            ColumnInfo(name="name"),
        ]
        results = matcher.score_columns("id", "users", columns)
        assert len(results) >= 1
        assert results[0].column.name == "id"

    def test_score_columns_empty(self):
        matcher = CatalogMatcher()
        results = matcher.score_columns("test", "users", [])
        assert results == []
