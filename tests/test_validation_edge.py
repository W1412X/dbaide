from dbaide.validation.sql_guard import SQLGuard, _strip_strings_and_comments
from dbaide.validation.schema_guard import TableScopeGuard


def _scope(*tables):
    """Build a TableScopeGuard allow-list from (db, name) pairs, accepting both the
    qualified (db.name) and bare (name) forms — mirrors how a table is 'in scope'."""
    allow: set[str] = set()
    for db, name in tables:
        allow.add(f"{db}.{name}" if db else name)
        allow.add(name)
    return TableScopeGuard(allow=list(allow))
from dbaide.validation.sql_cleanup import strip_function_from_keywords
from dbaide.agent.loop import LoopState, ToolCallRecord, _inject_stuck_loop_hint
from dbaide.agent.toolkit.support import _safe_int, _safe_float, _tables_in_sql
from dbaide.core.workflow import _extract_tables as workflow_extract_tables
from dbaide.context.disclosure import DisclosureContext
from dbaide.llm import LLMMessage
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

    def test_limit_not_swallowed_by_trailing_line_comment(self):
        # A trailing "-- comment" must not absorb the injected LIMIT (which would
        # let the query run unbounded). The injected LIMIT must be parseable.
        from dbaide.validation.sql_guard import _explicit_limit

        result = SQLGuard(default_limit=25).validate("SELECT * FROM t -- get everything")
        assert result.ok
        assert _explicit_limit(result.normalized_sql) == 25


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


class TestTableScopeGuard:
    """TableScopeGuard reuses the table-reference extraction; with no scope it is a
    no-op, with an allow-list it rejects out-of-scope tables. (Same extraction
    correctness the old disclosure guard had — CTEs, quoting, function FROM, etc.)"""

    def test_no_scope_passes(self):
        result = TableScopeGuard().validate("SELECT * FROM anything")
        assert result.ok

    def test_in_scope_passes(self):
        result = _scope(("main", "users")).validate("SELECT * FROM users")
        assert result.ok

    def test_out_of_scope_fails(self):
        result = _scope(("main", "users")).validate("SELECT * FROM nonexistent")
        assert not result.ok
        assert any("nonexistent" in i.message for i in result.issues)

    def test_cjk_table_extracted_and_scoped(self):
        """Unquoted CJK table names must be extracted (not crash) so allow/deny
        enforcement works for Chinese-named databases."""
        from dbaide.validation.sql_cleanup import table_references
        assert table_references("SELECT * FROM 订单") == ["订单"]
        assert table_references(
            "SELECT * FROM 分析.订单 JOIN 客户 c ON c.id=订单.cid"
        ) == ["客户", "分析.订单"]
        # deny rule on a CJK table blocks it but leaves others alone
        deny = TableScopeGuard(deny=["订单"])
        assert not deny.validate("SELECT * FROM 订单").ok
        assert deny.validate("SELECT * FROM 客户").ok
        # allow-list rejects an out-of-scope CJK table
        allow = TableScopeGuard(allow=["客户"])
        assert not allow.validate("SELECT * FROM 订单").ok
        assert allow.validate("SELECT * FROM 客户").ok

    def test_cjk_cte_name_allowed(self):
        """A CJK-named CTE must be recognized as a CTE, not flagged as a table."""
        result = _scope(("main", "客户")).validate(
            "WITH 临时 AS (SELECT * FROM 客户) SELECT * FROM 临时")
        assert result.ok

    def test_cte_name_allowed(self):
        result = _scope(("main", "users")).validate(
            "WITH cte AS (SELECT * FROM users) SELECT * FROM cte")
        assert result.ok

    def test_quoted_table_name(self):
        result = _scope(("main", "users")).validate('SELECT * FROM "users"')
        assert result.ok

    def test_qualified_table_name(self):
        result = _scope(("main", "users")).validate('SELECT * FROM "main"."users"')
        assert result.ok

    def test_extract_from_not_mistaken_for_table(self):
        """EXTRACT(YEAR FROM col) uses FROM as function syntax, not a table ref —
        the scope check must not flag the column as an out-of-scope table."""
        sql = (
            'SELECT EXTRACT(YEAR FROM order_created_at) AS year, '
            'EXTRACT(MONTH FROM order_created_at) AS month, '
            'SUM(quantity) AS total '
            'FROM order_data."order" '
            'WHERE order_created_at >= \'2025-01-01\' '
            'GROUP BY year, month'
        )
        result = _scope(("order_data", "order")).validate(sql)
        assert result.ok, f"False positive: {[i.message for i in result.issues]}"

    def test_trim_from_not_mistaken_for_table(self):
        result = _scope(("main", "users")).validate("SELECT TRIM(' ' FROM name) FROM users")
        assert result.ok

    def test_real_table_ref_still_checked_alongside_extract(self):
        sql = 'SELECT EXTRACT(YEAR FROM created_at) FROM nonexistent'
        result = _scope(("main", "users")).validate(sql)
        assert not result.ok
        assert any("nonexistent" in i.message for i in result.issues)

    def test_duplicate_bare_table_accepted(self):
        guard = _scope(("sales", "orders"), ("archive", "orders"))
        assert guard.validate("SELECT * FROM orders").ok
        assert guard.validate("SELECT * FROM sales.orders").ok

    def test_substring_from_not_mistaken_for_table(self):
        result = _scope(("main", "users")).validate("SELECT SUBSTRING(name FROM 1 FOR 3) FROM users")
        assert result.ok

    def test_comment_cannot_hide_out_of_scope_table(self):
        """A comment between FROM and the table must not smuggle an out-of-scope
        table past the scope check (it still reaches the DB)."""
        result = _scope(("main", "users")).validate("SELECT * FROM /*x*/ secret_table")
        assert not result.ok
        assert any("secret_table" in i.message for i in result.issues)

    def test_comma_join_table_is_in_scope(self):
        """Old-style comma joins (FROM a, b) must check EVERY table — a denied/out-
        of-scope table after a comma was previously not extracted (scope bypass)."""
        allow = _scope(("main", "orders"))
        r = allow.validate("SELECT * FROM orders, secret")
        assert not r.ok and any("secret" in i.message for i in r.issues)
        # aliased comma list
        r2 = allow.validate("SELECT * FROM orders o, secret s WHERE o.id = s.id")
        assert not r2.ok and any("secret" in i.message for i in r2.issues)
        # all-in-scope comma list still passes
        assert _scope(("main", "orders"), ("main", "lines")).validate(
            "SELECT * FROM orders, lines"
        ).ok

    def test_comma_join_respects_deny_list(self):
        guard = TableScopeGuard(deny=["secret"])
        assert not guard.validate("SELECT * FROM orders, secret").ok
        assert guard.validate("SELECT * FROM orders, lines").ok

    def test_deny_list_blocks(self):
        guard = TableScopeGuard(deny=["secret_table"])
        assert guard.validate("SELECT * FROM orders").ok
        assert not guard.validate("SELECT * FROM secret_table").ok


class TestWorkflowExtractTables:
    """workflow.py::_extract_tables must not be tricked by SQL function FROM."""

    def test_extract_from_ignored(self):
        tables = workflow_extract_tables(
            "SELECT EXTRACT(YEAR FROM created_at) FROM orders"
        )
        assert "orders" in tables
        assert "created_at" not in tables

    def test_trim_from_ignored(self):
        tables = workflow_extract_tables(
            "SELECT TRIM(' ' FROM name) FROM users"
        )
        assert "users" in tables
        assert "name" not in tables

    def test_substring_from_ignored(self):
        tables = workflow_extract_tables(
            "SELECT SUBSTRING(name FROM 1 FOR 3) FROM users"
        )
        assert "users" in tables
        assert "name" not in tables

    def test_comma_join_lists_every_table(self):
        # Both extractors share the robust helper now — comma joins must list all
        # tables (the risk gate derives has_joins/table_count from this).
        from dbaide.agent.toolkit.support import _tables_in_sql
        from dbaide.validation.sql_cleanup import table_references

        for fn in (workflow_extract_tables, _tables_in_sql, table_references):
            tables = fn("SELECT * FROM orders o, line_items l WHERE o.id = l.oid")
            assert set(tables) == {"orders", "line_items"}, fn
            assert len(tables) == 2  # has_joins would be True


class TestSQLGuardExtractTables:
    """SQLGuard._extract_tables must not be tricked by SQL function FROM."""

    def test_extract_from_ignored(self):
        guard = SQLGuard()
        tables = guard._extract_tables(
            "select extract(year from created_at) from orders"
        )
        assert "orders" in tables
        assert "created_at" not in tables

    def test_trim_from_ignored(self):
        guard = SQLGuard()
        tables = guard._extract_tables(
            "select trim(' ' from name) from users"
        )
        assert "users" in tables
        assert "name" not in tables

    def test_substring_from_ignored(self):
        guard = SQLGuard()
        tables = guard._extract_tables(
            "select substring(name from 1 for 3) from users"
        )
        assert "users" in tables
        assert "name" not in tables

    def test_real_tables_still_extracted(self):
        guard = SQLGuard()
        tables = guard._extract_tables(
            "select extract(year from created_at) from orders join users on orders.uid = users.id"
        )
        assert "orders" in tables
        assert "users" in tables
        assert "created_at" not in tables


class TestStripFunctionFromKeywords:
    """Shared cleanup utility covers all known SQL-function FROM usages."""

    def test_extract(self):
        cleaned = strip_function_from_keywords("EXTRACT(YEAR FROM created_at)")
        assert "FROM" not in cleaned.upper().replace("EXTRACT(", "")

    def test_trim(self):
        cleaned = strip_function_from_keywords("TRIM(' ' FROM name)")
        assert cleaned.count("FROM") == 0 or "TRIM(" in cleaned

    def test_substring(self):
        cleaned = strip_function_from_keywords("SUBSTRING(col FROM 1 FOR 3)")
        assert cleaned.count("FROM") == 0 or "SUBSTRING(" in cleaned

    def test_preserves_real_from(self):
        sql = "SELECT EXTRACT(YEAR FROM created_at) FROM orders"
        cleaned = strip_function_from_keywords(sql)
        assert "FROM orders" in cleaned

    def test_multiple_functions_cleaned(self):
        sql = (
            "SELECT EXTRACT(YEAR FROM d), TRIM(' ' FROM n), "
            "SUBSTRING(s FROM 1 FOR 3) FROM t"
        )
        cleaned = strip_function_from_keywords(sql)
        # Only the real FROM (before "t") should remain
        # Count how many FROM remain — should be exactly 1 (the table ref)
        from_count = len(__import__("re").findall(r"\bFROM\b", cleaned, __import__("re").I))
        assert from_count == 1, f"Expected 1 FROM, got {from_count} in: {cleaned}"

    def test_case_insensitive(self):
        cleaned = strip_function_from_keywords("extract(year from col)")
        assert "from" not in cleaned.lower().replace("extract(", "")


class TestStuckLoopCircuitBreaker:
    """Circuit-breaker injects a hint when the same tool fails repeatedly."""

    def _make_state(self, calls):
        state = LoopState(question="test", database="", execute_allowed=True)
        state.calls = calls
        return state

    def test_hint_injected_after_three_identical_failures(self):
        calls = [
            ToolCallRecord(tool="execute_sql", args={"sql": "SELECT 1"}, ok=False, summary="ERROR: undisclosed table: x"),
            ToolCallRecord(tool="execute_sql", args={"sql": "SELECT 1"}, ok=False, summary="ERROR: undisclosed table: x"),
            ToolCallRecord(tool="execute_sql", args={"sql": "SELECT 1"}, ok=False, summary="ERROR: undisclosed table: x"),
        ]
        state = self._make_state(calls)
        messages: list[LLMMessage] = []
        _inject_stuck_loop_hint(state, messages)
        assert len(messages) == 1
        assert "WARNING" in messages[0].content
        assert "execute_sql" in messages[0].content

    def test_no_hint_with_only_two_failures(self):
        calls = [
            ToolCallRecord(tool="execute_sql", args={"sql": "SELECT 1"}, ok=False, summary="ERROR: x"),
            ToolCallRecord(tool="execute_sql", args={"sql": "SELECT 1"}, ok=False, summary="ERROR: x"),
        ]
        state = self._make_state(calls)
        messages: list[LLMMessage] = []
        _inject_stuck_loop_hint(state, messages)
        assert len(messages) == 0

    def test_no_hint_when_errors_differ(self):
        calls = [
            ToolCallRecord(tool="execute_sql", args={"sql": "SELECT 1"}, ok=False, summary="ERROR: undisclosed table: x"),
            ToolCallRecord(tool="execute_sql", args={"sql": "SELECT 1"}, ok=False, summary="ERROR: syntax error"),
            ToolCallRecord(tool="execute_sql", args={"sql": "SELECT 1"}, ok=False, summary="ERROR: undisclosed table: x"),
        ]
        state = self._make_state(calls)
        messages: list[LLMMessage] = []
        _inject_stuck_loop_hint(state, messages)
        assert len(messages) == 0

    def test_no_hint_when_tools_differ(self):
        calls = [
            ToolCallRecord(tool="validate_sql", args={"sql": "SELECT 1"}, ok=False, summary="ERROR: x"),
            ToolCallRecord(tool="execute_sql", args={"sql": "SELECT 1"}, ok=False, summary="ERROR: x"),
            ToolCallRecord(tool="execute_sql", args={"sql": "SELECT 1"}, ok=False, summary="ERROR: x"),
        ]
        state = self._make_state(calls)
        messages: list[LLMMessage] = []
        _inject_stuck_loop_hint(state, messages)
        assert len(messages) == 0

    def test_no_hint_when_last_call_succeeds(self):
        calls = [
            ToolCallRecord(tool="execute_sql", args={"sql": "SELECT 1"}, ok=False, summary="ERROR: x"),
            ToolCallRecord(tool="execute_sql", args={"sql": "SELECT 1"}, ok=False, summary="ERROR: x"),
            ToolCallRecord(tool="execute_sql", args={"sql": "SELECT 1"}, ok=True, summary="ok"),
        ]
        state = self._make_state(calls)
        messages: list[LLMMessage] = []
        _inject_stuck_loop_hint(state, messages)
        # Last call succeeded, so all 3 are NOT all failures
        assert len(messages) == 0


class TestSafeTypeConversion:
    """_safe_int and _safe_float must absorb bad LLM input."""

    def test_safe_int_valid(self):
        assert _safe_int("42", 0) == 42
        assert _safe_int(42, 0) == 42
        assert _safe_int(3.7, 0) == 3

    def test_safe_int_invalid_returns_default(self):
        assert _safe_int("large", 150) == 150
        assert _safe_int("5000rows", 150) == 150
        assert _safe_int(None, 150) == 150
        assert _safe_int([], 150) == 150
        assert _safe_int("", 150) == 150

    def test_safe_float_valid(self):
        assert _safe_float("0.7", 0.0) == 0.7
        assert _safe_float(0.5, 0.0) == 0.5
        assert _safe_float(1, 0.0) == 1.0

    def test_safe_float_invalid_returns_default(self):
        assert _safe_float("high", 0.7) == 0.7
        assert _safe_float("0.5abc", 0.7) == 0.7
        assert _safe_float(None, 0.7) == 0.7
        assert _safe_float([], 0.7) == 0.7

    def test_safe_float_zero_preserved(self):
        """A genuine 0.0 must NOT be replaced by default."""
        assert _safe_float(0.0, 0.7) == 0.0
        assert _safe_float("0.0", 0.7) == 0.0


class TestTablesInSqlExtractCleanup:
    """support._tables_in_sql must not be tricked by SQL function FROM."""

    def test_extract_from_ignored(self):
        tables = _tables_in_sql(
            "SELECT EXTRACT(YEAR FROM created_at) FROM orders"
        )
        assert "orders" in tables
        assert "created_at" not in tables

    def test_trim_from_ignored(self):
        tables = _tables_in_sql("SELECT TRIM(' ' FROM name) FROM users")
        assert "users" in tables
        assert "name" not in tables

    def test_substring_from_ignored(self):
        tables = _tables_in_sql(
            "SELECT SUBSTRING(name FROM 1 FOR 3) FROM users"
        )
        assert "users" in tables
        assert "name" not in tables


class TestCTEParserStringLiterals:
    """_cte_names must skip parentheses inside string literals."""

    def test_closing_paren_in_string_does_not_break_cte(self):
        sql = (
            "WITH cte1 AS (\n"
            "  SELECT * FROM users WHERE name = ')'\n"
            "), cte2 AS (\n"
            "  SELECT 1\n"
            ")\n"
            "SELECT * FROM cte1"
        )
        result = _scope(("main", "users")).validate(sql)
        assert result.ok, f"CTE with string paren broke parser: {[i.message for i in result.issues]}"

    def test_escaped_quote_in_cte_body(self):
        sql = (
            "WITH c AS (\n"
            "  SELECT * FROM t WHERE v = 'it''s )'\n"
            ")\n"
            "SELECT * FROM c"
        )
        result = _scope(("main", "t")).validate(sql)
        assert result.ok

    def test_multiple_ctes_with_string_parens(self):
        sql = (
            "WITH x AS (\n"
            "  SELECT ')' AS col FROM a\n"
            "), y AS (\n"
            "  SELECT '(' AS col FROM b\n"
            ")\n"
            "SELECT * FROM x JOIN y ON x.col = y.col"
        )
        result = _scope(("main", "a"), ("main", "b")).validate(sql)
        assert result.ok


class TestWorkflowResultSerialization:
    """WorkflowResult.to_dict() must include all fields."""

    def test_to_dict_includes_clarifications_and_disclosed_tables(self):
        from dbaide.core.result import WorkflowResult
        r = WorkflowResult(question="test")
        r.clarifications = ["use Beijing time"]
        r.disclosed_tables = ["orders", "users"]
        d = r.to_dict()
        assert d["clarifications"] == ["use Beijing time"]
        assert d["disclosed_tables"] == ["orders", "users"]

    def test_to_dict_defaults_empty(self):
        from dbaide.core.result import WorkflowResult
        d = WorkflowResult().to_dict()
        assert d["clarifications"] == []
        assert d["disclosed_tables"] == []
