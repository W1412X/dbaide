"""Result export formatters: JSON and INSERT (CSV/Markdown already exist)."""
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
