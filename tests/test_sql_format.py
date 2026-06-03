from dbaide.rendering.sql_format import format_sql, split_statements, statement_at


def test_format_basic_clauses():
    out = format_sql("select id, name from users where age>30 order by name desc limit 10")
    lines = out.splitlines()
    assert lines[0] == "SELECT id,"
    assert "FROM users" in out
    assert "WHERE age>30" in out
    assert "ORDER BY name DESC" in out
    assert out.strip().endswith("LIMIT 10")


def test_format_preserves_semicolon_in_string():
    out = format_sql("select * from t where x='a;b'")
    assert "'a;b'" in out  # literal untouched


def test_format_empty():
    assert format_sql("   ") == ""


def test_split_ignores_string_and_comment_semicolons():
    sql = "select 1; select 2 from t where x=';'"
    spans = split_statements(sql)
    assert len(spans) == 2
    assert spans[1][2].endswith("';'")


def test_statement_at_cursor():
    sql = "select 1;\nselect 2 from t"
    assert statement_at(sql, 0) == "select 1"
    assert statement_at(sql, len(sql)).startswith("select 2")


def test_statement_at_single_returns_all():
    sql = "select a, b from t"
    assert statement_at(sql, 3) == sql
