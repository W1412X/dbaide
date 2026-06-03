from dbaide.rendering.sql_templates import generate, select_star, insert_template, update_template

COLS = [
    {"name": "id", "data_type": "INTEGER", "primary_key": True},
    {"name": "city", "data_type": "TEXT"},
]


def test_select_star_runnable():
    sql = select_star("orders", COLS, "generic")
    assert sql.startswith("SELECT * FROM \"orders\"")
    assert "LIMIT 100;" in sql


def test_select_columns_lists_quoted_cols():
    sql = generate("select_columns", "orders", COLS, "generic")
    assert '"id"' in sql and '"city"' in sql and "FROM \"orders\"" in sql


def test_mysql_backtick_quoting():
    sql = generate("select_star", "orders", COLS, "mysql")
    assert "`orders`" in sql


def test_count():
    assert generate("count", "orders", COLS, "generic") == 'SELECT COUNT(*) FROM "orders";'


def test_insert_uses_named_placeholders():
    sql = insert_template("orders", COLS, "generic")
    assert '("id", "city")' in sql and "(:id, :city)" in sql


def test_update_uses_pk_in_where():
    sql = update_template("orders", COLS, "generic")
    assert '"id" = :id' in sql
    assert sql.rstrip().endswith('WHERE "id" = :id;')


def test_unknown_kind_falls_back_to_select_star():
    assert generate("bogus", "t", COLS, "generic").startswith("SELECT * FROM")


def test_no_columns_select_star():
    assert generate("select_columns", "t", [], "generic").startswith("SELECT * FROM")
