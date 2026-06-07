from dbaide.rendering.sql_dialect import dialect_functions, dialect_keywords, normalize_dialect


def test_normalize_dialect_aliases():
    assert normalize_dialect("postgresql") == "postgres"
    assert normalize_dialect("mariadb") == "mysql"
    assert normalize_dialect("sqlite") == "sqlite"
    assert normalize_dialect("") == "generic"


def test_postgres_keywords_include_returning():
    assert "RETURNING" in dialect_keywords("postgres")


def test_mysql_functions_include_ifnull():
    assert "IFNULL" in dialect_functions("mysql")
