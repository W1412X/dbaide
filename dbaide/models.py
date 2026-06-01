from enum import Enum
from typing import Any


class TaskType(str, Enum):
    SCHEMA_EXPLORE = "schema_explore"
    DATA_QUERY = "data_query"
    DATA_PROFILE = "data_profile"
    SQL_REWRITE = "sql_rewrite"
    SQL_DIAGNOSE = "sql_diagnose"
    DB_COMPARE = "db_compare"
    EXPORT = "export"
    UNKNOWN = "unknown"


_VALID_TYPES = {"sqlite", "mysql", "mariadb", "postgres", "postgresql"}
_VALID_LOAD_PROFILES = {"production", "staging", "dev"}


class ConnectionConfig:
    """Database connection configuration."""

    def __init__(
        self,
        name: str,
        type: str,
        database: str = "",
        host: str = "",
        port: int | None = None,
        user: str = "",
        password_env: str = "",
        password: str = "",
        path: str = "",
        load_profile: str = "production",
    ) -> None:
        self.name = str(name or "").strip()
        self.type = str(type or "").strip().lower()
        if self.type and self.type not in _VALID_TYPES:
            raise ValueError(f"Invalid connection type: {self.type!r}. Supported: {', '.join(sorted(_VALID_TYPES))}")
        if self.type == "sqlite" and not path:
            raise ValueError("SQLite connections require --path")
        if self.type in {"mysql", "mariadb", "postgres", "postgresql"} and not host:
            host = "localhost"
        if port is not None and not (1 <= port <= 65535):
            raise ValueError(f"Port must be 1-65535, got {port}")
        self.database = str(database or "").strip()
        self.host = str(host or "").strip()
        self.port = port
        self.user = str(user or "").strip()
        self.password_env = str(password_env or "").strip()
        self.password = str(password or "")
        self.path = str(path or "").strip()
        profile = str(load_profile or "production").strip().lower()
        # Default to the conservative production profile: an AI assistant must not
        # hammer a live database by accident.
        self.load_profile = profile if profile in _VALID_LOAD_PROFILES else "production"


class ModelConfig:
    """LLM model configuration."""

    def __init__(
        self,
        name: str = "default",
        provider: str = "none",
        base_url: str = "",
        api_key_env: str = "",
        api_key: str = "",
        model: str = "",
        timeout_seconds: int = 60,
    ) -> None:
        self.name = name
        self.provider = str(provider or "none").strip().lower()
        self.base_url = str(base_url or "")
        self.api_key_env = str(api_key_env or "")
        self.api_key = str(api_key or "")
        self.model = str(model or "")
        # Clamp timeout to valid range
        if timeout_seconds < 1:
            self.timeout_seconds = 1
        elif timeout_seconds > 600:
            self.timeout_seconds = 600
        else:
            self.timeout_seconds = timeout_seconds


class TableInfo:
    """Table information."""

    def __init__(
        self,
        name: str,
        schema: str = "",
        comment: str = "",
        estimated_rows: int | None = None,
        table_type: str = "table",
    ) -> None:
        self.name = name
        self.schema = schema
        self.comment = comment
        self.estimated_rows = estimated_rows
        self.table_type = table_type

    @property
    def ref(self) -> str:
        return f"{self.schema}.{self.name}" if self.schema else self.name


class ColumnInfo:
    """Column information."""

    def __init__(
        self,
        name: str,
        data_type: str = "",
        nullable: bool | None = None,
        default: str | None = None,
        comment: str = "",
        primary_key: bool = False,
        indexed: bool = False,
    ) -> None:
        self.name = name
        self.data_type = data_type
        self.nullable = nullable
        self.default = default
        self.comment = comment
        self.primary_key = primary_key
        self.indexed = indexed


class ForeignKeyInfo:
    """Foreign key information."""

    def __init__(self, table: str, column: str, ref_table: str, ref_column: str) -> None:
        self.table = table
        self.column = column
        self.ref_table = ref_table
        self.ref_column = ref_column


class ColumnProfile:
    """Column profile data."""

    def __init__(
        self,
        table: str,
        column: str,
        row_count: int,
        null_count: int,
        distinct_count: int | None = None,
        min_value: Any = None,
        max_value: Any = None,
        top_values: list[dict[str, Any]] | None = None,
        sample_values: list[Any] | None = None,
        data_kind: str = "unknown",
        null_rate: float | None = None,
        distinct_ratio: float | None = None,
        numeric_stats: dict[str, Any] | None = None,
        text_stats: dict[str, Any] | None = None,
        temporal_stats: dict[str, Any] | None = None,
        distribution: dict[str, Any] | None = None,
        sample_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self.table = table
        self.column = column
        self.row_count = row_count
        self.null_count = null_count
        self.distinct_count = distinct_count
        self.min_value = min_value
        self.max_value = max_value
        self.top_values = top_values or []
        self.sample_values = sample_values or []
        self.data_kind = data_kind
        self.null_rate = null_rate
        self.distinct_ratio = distinct_ratio
        self.numeric_stats = numeric_stats or {}
        self.text_stats = text_stats or {}
        self.temporal_stats = temporal_stats or {}
        self.distribution = distribution or {}
        self.sample_rows = sample_rows or []


class QueryResult:
    """Query result data."""

    def __init__(
        self,
        columns: list[str],
        rows: list[dict[str, Any]],
        row_count: int,
        truncated: bool = False,
        sql: str = "",
        elapsed_ms: float = 0.0,
    ) -> None:
        self.columns = columns
        self.rows = rows
        self.row_count = row_count
        self.truncated = truncated
        self.sql = sql
        self.elapsed_ms = elapsed_ms


class ValidationIssue:
    """Validation issue."""

    def __init__(self, code: str, message: str, severity: str = "error") -> None:
        self.code = code
        self.message = message
        self.severity = severity


class ValidationResult:
    """Validation result."""

    def __init__(
        self,
        ok: bool,
        issues: list[ValidationIssue] | None = None,
        normalized_sql: str = "",
    ) -> None:
        self.ok = ok
        self.issues = issues or []
        self.normalized_sql = normalized_sql


class AssistantResponse:
    """Assistant response."""

    def __init__(
        self,
        answer: str,
        sql: str = "",
        result: QueryResult | None = None,
        disclosures: list[str] | None = None,
        warnings: list[str] | None = None,
        *,
        status: str = "completed",
        pending_question: str = "",
        pending_options: list[str] | None = None,
        resume_state: dict[str, Any] | None = None,
    ) -> None:
        self.answer = answer
        self.sql = sql
        self.result = result
        self.disclosures = disclosures or []
        self.warnings = warnings or []
        self.status = status
        self.pending_question = pending_question
        self.pending_options = pending_options or []
        self.resume_state = resume_state
