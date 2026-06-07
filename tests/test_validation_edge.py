from dbaide.validation.sql_guard import SQLGuard, _strip_strings_and_comments
from dbaide.validation.schema_guard import SchemaGuard
from dbaide.context.disclosure import DisclosureContext
from dbaide.models import TableInfo


class TestSQLGuardEdgeCases:
    def test_empty_sql(self):
        result = SQLGuard().validate("")
        assert not result.ok
        assert any(i.code == "EMPTY_SQL" for i in result.issues)

    def test_whitespace_only(self):
        result = SQLGuard().validate("   ")
        assert not result.ok

    def test_into_outfile_blocked(self):
        result = SQLGuard().validate("SELECT * FROM t INTO OUTFILE '/tmp/x'")
        assert not result.ok
        assert any("outfile" in i.message.lower() for i in result.issues)

    def test_into_dumpfile_blocked(self):
        result = SQLGuard().validate("SELECT * FROM t INTO DUMPFILE '/tmp/x'")
        assert not result.ok

    def test_load_data_infile_blocked(self):
        result = SQLGuard().validate("LOAD DATA INFILE '/tmp/x' INTO TABLE t")
        assert not result.ok

    def test_sleep_blocked(self):
        result = SQLGuard().validate("SELECT sleep(10)")
        assert not result.ok

    def test_benchmark_blocked(self):
        result = SQLGuard().validate("SELECT benchmark(1000000, SHA1('test'))")
        assert not result.ok

    def test_backslash_escape_in_string(self):
        result = SQLGuard().validate(r"SELECT * FROM t WHERE x = 'it''s ok'")
        assert result.ok

    def test_semicolon_in_string(self):
        result = SQLGuard().validate("SELECT * FROM t WHERE x = 'a;b'")
        assert result.ok

    def test_comment_injection(self):
        result = SQLGuard().validate("SELECT * FROM t -- ; DROP TABLE t")
        assert result.ok

    def test_block_comment_injection(self):
        result = SQLGuard().validate("SELECT * FROM t /* ; DROP TABLE t */")
        assert result.ok

    def test_explain_allowed(self):
        result = SQLGuard().validate("EXPLAIN SELECT * FROM t")
        assert result.ok

    def test_with_cte_allowed(self):
        result = SQLGuard().validate("WITH cte AS (SELECT 1) SELECT * FROM cte")
        assert result.ok

    def test_create_blocked(self):
        result = SQLGuard().validate("CREATE TABLE t (id INT)")
        assert not result.ok

    def test_grant_blocked(self):
        result = SQLGuard().validate("GRANT ALL ON t TO user")
        assert not result.ok

    def test_limit_preserved(self):
        result = SQLGuard().validate("SELECT * FROM t LIMIT 50")
        assert result.ok
        assert "LIMIT 50" in result.normalized_sql

    def test_limit_added(self):
        result = SQLGuard(default_limit=25).validate("SELECT * FROM t")
        assert result.ok
        assert result.normalized_sql.endswith("LIMIT 25")

    def test_no_limit_on_explain(self):
        result = SQLGuard(default_limit=25).validate("EXPLAIN SELECT * FROM t", add_limit=False)
        assert result.ok
        assert "LIMIT" not in result.normalized_sql


class TestStripStringsAndComments:
    def test_single_quotes(self):
        assert _strip_strings_and_comments("SELECT 'hello'") == "SELECT "

    def test_double_quotes(self):
        assert _strip_strings_and_comments('SELECT "hello"') == "SELECT "

    def test_backtick_quotes(self):
        assert _strip_strings_and_comments("SELECT `hello`") == "SELECT "

    def test_line_comment(self):
        result = _strip_strings_and_comments("SELECT 1 -- comment\nFROM t")
        assert "SELECT 1" in result
        assert "FROM t" in result
        assert "comment" not in result

    def test_block_comment(self):
        assert _strip_strings_and_comments("SELECT 1 /* comment */ FROM t") == "SELECT 1  FROM t"

    def test_escaped_quote(self):
        result = _strip_strings_and_comments(r"SELECT 'it''s ok'")
        assert "SELECT" in result

    def test_nested_comments(self):
        result = _strip_strings_and_comments("SELECT 1 /* /* nested */ */ FROM t")
        assert "SELECT" in result


class TestSchemaGuardEdgeCases:
    def test_empty_context_passes(self):
        ctx = DisclosureContext()
        result = SchemaGuard().validate("SELECT * FROM anything", ctx)
        assert result.ok

    def test_known_table_passes(self):
        ctx = DisclosureContext()
        ctx.record_tables([TableInfo(name="users")], instance="local", database="main")
        result = SchemaGuard().validate("SELECT * FROM users", ctx)
        assert result.ok

    def test_unknown_table_fails(self):
        ctx = DisclosureContext()
        ctx.record_tables([TableInfo(name="users")], instance="local", database="main")
        result = SchemaGuard().validate("SELECT * FROM nonexistent", ctx)
        assert not result.ok
        assert any("nonexistent" in i.message for i in result.issues)

    def test_cte_name_allowed(self):
        ctx = DisclosureContext()
        ctx.record_tables([TableInfo(name="users")], instance="local", database="main")
        result = SchemaGuard().validate("WITH cte AS (SELECT * FROM users) SELECT * FROM cte", ctx)
        assert result.ok

    def test_quoted_table_name(self):
        ctx = DisclosureContext()
        ctx.record_tables([TableInfo(name="users")], instance="local", database="main")
        result = SchemaGuard().validate('SELECT * FROM "users"', ctx)
        assert result.ok

    def test_qualified_table_name(self):
        ctx = DisclosureContext()
        ctx.record_tables([TableInfo(name="users")], instance="local", database="main")
        result = SchemaGuard().validate('SELECT * FROM "main"."users"', ctx)
        assert result.ok

    def test_duplicate_bare_table_requires_qualification(self):
        ctx = DisclosureContext()
        ctx.record_tables([TableInfo(name="orders")], instance="local", database="sales")
        ctx.record_tables([TableInfo(name="orders")], instance="local", database="archive")

        bare = SchemaGuard().validate("SELECT * FROM orders", ctx)
        qualified = SchemaGuard().validate("SELECT * FROM sales.orders", ctx)

        assert not bare.ok
        assert qualified.ok
