"""Result export formatters: JSON and INSERT (CSV/Markdown already exist),
plus sanitizer hardening tests."""
from dbaide.rendering.table import export_insert, export_json, export_markdown_table


def test_export_json_array_of_objects():
    import json
    rows = [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
    out = json.loads(export_json(rows, ["id", "name"]))
    assert out == rows


def test_export_json_empty():
    assert export_json([]) == "[]"


def test_export_insert_escapes_and_types():
    rows = [{"id": 1, "name": "O'Brien", "amt": 3.5, "flag": None}]
    sql = export_insert(rows, ["id", "name", "amt", "flag"], table="users")
    assert sql == 'INSERT INTO "users" ("id", "name", "amt", "flag") VALUES (1, \'O\'\'Brien\', 3.5, NULL);'


def test_export_insert_backslash_all_dialects():
    """Trailing backslash must be doubled on every dialect, not just MySQL."""
    rows = [{"path": "C:\\"}]
    for dialect in ("generic", "mysql", "mariadb", "postgres", "sqlite"):
        sql = export_insert(rows, ["path"], table="t", dialect=dialect)
        assert "C:\\\\" in sql, f"backslash not doubled for dialect={dialect}"
        assert sql.endswith(");"), f"trailing quote broken for dialect={dialect}"


def test_export_csv_null_vs_empty():
    """NULL and empty string must be distinguishable in CSV output."""
    from dbaide.rendering.table import export_csv
    rows = [{"a": None, "b": ""}, {"a": "x", "b": "y"}]
    csv_text = export_csv(rows, ["a", "b"])
    lines = csv_text.strip().splitlines()
    assert lines[1] == "NULL,"  # NULL rendered as literal, empty string as empty


def test_export_insert_empty():
    assert export_insert([]) == ""


def _structural_pipes(line: str) -> int:
    """Count unescaped '|' (the column separators), ignoring '\\|' inside cells."""
    n, i = 0, 0
    while i < len(line):
        if line[i] == "\\":
            i += 2
            continue
        if line[i] == "|":
            n += 1
        i += 1
    return n


def test_export_markdown_escapes_pipe_and_newline():
    """A cell containing '|' or a newline must not break the table: '|' is escaped and
    newlines collapse to <br>, so every data row keeps exactly one cell per column."""
    rows = [{"id": 1, "name": "a|b"}, {"id": 2, "name": "x\ny"}, {"id": 3, "name": None}]
    md = export_markdown_table(rows, ["id", "name"])
    lines = md.splitlines()
    # header + separator + 3 data rows, each with 3 structural pipes (2 columns).
    assert len(lines) == 5
    for line in lines:
        assert _structural_pipes(line) == 3
    assert "a\\|b" in md          # pipe escaped, not a column break
    assert "x<br>y" in md         # newline collapsed
    assert "| NULL |" in md       # None rendered, structure intact


def test_sanitize_unquoted_event_handlers():
    """Unquoted HTML event handlers must also be stripped."""
    from dbaide.rendering.sanitize import sanitize_markdown_html
    assert "onerror" not in sanitize_markdown_html('<img src=x onerror=alert(1)>')
    assert "onload" not in sanitize_markdown_html('<svg onload=alert(1)>')
    assert "onclick" not in sanitize_markdown_html('<a onclick="alert(1)">x</a>')
    assert "onmouseover" not in sanitize_markdown_html("<div onmouseover='alert(1)'>x</div>")
