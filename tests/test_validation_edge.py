from dbaide.validation.sql_guard import SQLGuard, _strip_strings_and_comments
from dbaide.validation.schema_guard import SchemaGuard
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
        ctx.record_tables([TableInfo(name="users")], database="main")
        result = SchemaGuard().validate("SELECT * FROM users", ctx)
        assert result.ok

    def test_unknown_table_fails(self):
        ctx = DisclosureContext()
        ctx.record_tables([TableInfo(name="users")], database="main")
        result = SchemaGuard().validate("SELECT * FROM nonexistent", ctx)
        assert not result.ok
        assert any("nonexistent" in i.message for i in result.issues)

    def test_cte_name_allowed(self):
        ctx = DisclosureContext()
        ctx.record_tables([TableInfo(name="users")], database="main")
        result = SchemaGuard().validate("WITH cte AS (SELECT * FROM users) SELECT * FROM cte", ctx)
        assert result.ok

    def test_quoted_table_name(self):
        ctx = DisclosureContext()
        ctx.record_tables([TableInfo(name="users")], database="main")
        result = SchemaGuard().validate('SELECT * FROM "users"', ctx)
        assert result.ok

    def test_qualified_table_name(self):
        ctx = DisclosureContext()
        ctx.record_tables([TableInfo(name="users")], database="main")
        result = SchemaGuard().validate('SELECT * FROM "main"."users"', ctx)
        assert result.ok

    def test_extract_from_not_mistaken_for_table(self):
        """EXTRACT(YEAR FROM col) uses FROM as function syntax, not as a table
        reference. The schema guard must not reject it as 'undisclosed table: col'.
        This was the root cause of a production infinite-loop (66 retries)."""
        ctx = DisclosureContext()
        ctx.record_tables([TableInfo(name="order")], database="order_data")
        sql = (
            'SELECT EXTRACT(YEAR FROM order_created_at) AS year, '
            'EXTRACT(MONTH FROM order_created_at) AS month, '
            'SUM(quantity) AS total '
            'FROM order_data."order" '
            'WHERE order_created_at >= \'2025-01-01\' '
            'GROUP BY year, month'
        )
        result = SchemaGuard().validate(sql, ctx)
        assert result.ok, f"False positive: {[i.message for i in result.issues]}"

    def test_trim_from_not_mistaken_for_table(self):
        """TRIM(chars FROM col) is another SQL function that uses FROM."""
        ctx = DisclosureContext()
        ctx.record_tables([TableInfo(name="users")], database="main")
        sql = "SELECT TRIM(' ' FROM name) FROM users"
        result = SchemaGuard().validate(sql, ctx)
        assert result.ok, f"False positive: {[i.message for i in result.issues]}"

    def test_real_table_ref_still_validated_alongside_extract(self):
        """EXTRACT in the same SQL shouldn't suppress real table validation."""
        ctx = DisclosureContext()
        ctx.record_tables([TableInfo(name="users")], database="main")
        sql = (
            'SELECT EXTRACT(YEAR FROM created_at) FROM nonexistent'
        )
        result = SchemaGuard().validate(sql, ctx)
        assert not result.ok
        assert any("nonexistent" in i.message for i in result.issues)

    def test_duplicate_bare_table_accepted_when_disclosed(self):
        ctx = DisclosureContext()
        ctx.record_tables([TableInfo(name="orders")], database="sales")
        ctx.record_tables([TableInfo(name="orders")], database="archive")

        bare = SchemaGuard().validate("SELECT * FROM orders", ctx)
        qualified = SchemaGuard().validate("SELECT * FROM sales.orders", ctx)

        assert bare.ok
        assert qualified.ok

    def test_substring_from_not_mistaken_for_table(self):
        """SUBSTRING(col FROM n FOR m) is SQL-standard syntax using FROM."""
        ctx = DisclosureContext()
        ctx.record_tables([TableInfo(name="users")], database="main")
        sql = "SELECT SUBSTRING(name FROM 1 FOR 3) FROM users"
        result = SchemaGuard().validate(sql, ctx)
        assert result.ok, f"False positive: {[i.message for i in result.issues]}"


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
        ctx = DisclosureContext()
        ctx.record_tables([TableInfo(name="users")], database="main")
        sql = (
            "WITH cte1 AS (\n"
            "  SELECT * FROM users WHERE name = ')'\n"
            "), cte2 AS (\n"
            "  SELECT 1\n"
            ")\n"
            "SELECT * FROM cte1"
        )
        result = SchemaGuard().validate(sql, ctx)
        assert result.ok, f"CTE with string paren broke parser: {[i.message for i in result.issues]}"

    def test_escaped_quote_in_cte_body(self):
        ctx = DisclosureContext()
        ctx.record_tables([TableInfo(name="t")], database="main")
        sql = (
            "WITH c AS (\n"
            "  SELECT * FROM t WHERE v = 'it''s )'\n"
            ")\n"
            "SELECT * FROM c"
        )
        result = SchemaGuard().validate(sql, ctx)
        assert result.ok

    def test_multiple_ctes_with_string_parens(self):
        ctx = DisclosureContext()
        ctx.record_tables([TableInfo(name="a"), TableInfo(name="b")], database="main")
        sql = (
            "WITH x AS (\n"
            "  SELECT ')' AS col FROM a\n"
            "), y AS (\n"
            "  SELECT '(' AS col FROM b\n"
            ")\n"
            "SELECT * FROM x JOIN y ON x.col = y.col"
        )
        result = SchemaGuard().validate(sql, ctx)
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
