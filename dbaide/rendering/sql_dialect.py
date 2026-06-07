"""Dialect-specific SQL vocabulary for editors and tooling."""

from __future__ import annotations

from dbaide.rendering.sanitize import _SQL_KEYWORDS

_DIALECT_ALIASES = {
    "mysql": "mysql",
    "mariadb": "mysql",
    "postgres": "postgres",
    "postgresql": "postgres",
    "sqlite": "sqlite",
    "generic": "generic",
}

_DIALECT_KEYWORDS: dict[str, set[str]] = {
    "mysql": {
        "replace", "ignore", "unsigned", "zerofill", "mediumint", "tinyint", "bigint",
        "datetime", "longtext", "mediumtext", "enum", "show", "describe", "use",
        "dual", "regexp", "rlike", "interval", "partition", "auto_increment",
    },
    "postgres": {
        "returning", "ilike", "serial", "bigserial", "boolean", "bytea", "jsonb",
        "plpgsql", "variadic", "window", "over", "filter", "within", "materialized",
        "conflict", "nothing", "do", "limit", "offset", "fetch", "only",
    },
    "sqlite": {
        "autoincrement", "pragma", "without", "rowid", "strict", "attach", "detach",
        "vacuum", "reindex", "if", "not", "exists",
    },
}

_DIALECT_FUNCTIONS: dict[str, list[str]] = {
    "mysql": [
        "IFNULL", "NULLIF", "CONCAT", "CONCAT_WS", "DATE_FORMAT", "STR_TO_DATE",
        "NOW", "CURDATE", "CURTIME", "UNIX_TIMESTAMP", "FROM_UNIXTIME", "GROUP_CONCAT",
        "JSON_EXTRACT", "JSON_OBJECT", "JSON_ARRAY",
    ],
    "postgres": [
        "COALESCE", "NULLIF", "TO_CHAR", "TO_DATE", "TO_TIMESTAMP", "NOW", "CURRENT_DATE",
        "CURRENT_TIMESTAMP", "DATE_TRUNC", "EXTRACT", "STRING_AGG", "ARRAY_AGG",
        "JSONB_BUILD_OBJECT", "JSONB_AGG", "GENERATE_SERIES",
    ],
    "sqlite": [
        "IFNULL", "NULLIF", "IIF", "PRINTF", "GROUP_CONCAT", "JSON_EXTRACT", "JSON_OBJECT",
        "DATETIME", "DATE", "TIME", "JULIANDAY", "STRFTIME", "TYPEOF", "LENGTH",
    ],
    "generic": [
        "COALESCE", "NULLIF", "COUNT", "SUM", "AVG", "MIN", "MAX", "CAST", "CONCAT",
    ],
}


def normalize_dialect(raw: str) -> str:
    key = str(raw or "generic").strip().lower()
    return _DIALECT_ALIASES.get(key, "generic")


def dialect_keywords(dialect: str) -> set[str]:
    base = {kw.upper() for kw in _SQL_KEYWORDS}
    name = normalize_dialect(dialect)
    extra = _DIALECT_KEYWORDS.get(name, set())
    return base | {kw.upper() for kw in extra}


def dialect_functions(dialect: str) -> list[str]:
    name = normalize_dialect(dialect)
    funcs = list(_DIALECT_FUNCTIONS.get(name, []))
    if name != "generic":
        funcs.extend(_DIALECT_FUNCTIONS.get("generic", []))
    return sorted({f.upper() for f in funcs})
