"""Golden test cases for DBAide evaluation."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class GoldenCase:
    """A single golden test case.

    Each case has:
    - question: natural language question
    - expected_sql: expected SQL (or pattern)
    - expected_columns: expected output columns
    - expected_tables: expected tables referenced
    - tags: for filtering (e.g., "count", "join", "time_range")
    """

    __slots__ = (
        "id", "question", "expected_sql", "expected_sql_pattern",
        "expected_columns", "expected_tables", "expected_row_count_min",
        "tags", "difficulty", "notes",
    )

    def __init__(
        self,
        *,
        id: str = "",
        question: str = "",
        expected_sql: str = "",
        expected_sql_pattern: str = "",
        expected_columns: list[str] | None = None,
        expected_tables: list[str] | None = None,
        expected_row_count_min: int | None = None,
        tags: list[str] | None = None,
        difficulty: str = "medium",
        notes: str = "",
    ) -> None:
        self.id = id
        self.question = question
        self.expected_sql = expected_sql
        self.expected_sql_pattern = expected_sql_pattern
        self.expected_columns = expected_columns or []
        self.expected_tables = expected_tables or []
        self.expected_row_count_min = expected_row_count_min
        self.tags = tags or []
        self.difficulty = difficulty
        self.notes = notes

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "expected_sql": self.expected_sql,
            "expected_sql_pattern": self.expected_sql_pattern,
            "expected_columns": self.expected_columns,
            "expected_tables": self.expected_tables,
            "expected_row_count_min": self.expected_row_count_min,
            "tags": self.tags,
            "difficulty": self.difficulty,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GoldenCase:
        return cls(
            id=data.get("id", ""),
            question=data.get("question", ""),
            expected_sql=data.get("expected_sql", ""),
            expected_sql_pattern=data.get("expected_sql_pattern", ""),
            expected_columns=data.get("expected_columns", []),
            expected_tables=data.get("expected_tables", []),
            expected_row_count_min=data.get("expected_row_count_min"),
            tags=data.get("tags", []),
            difficulty=data.get("difficulty", "medium"),
            notes=data.get("notes", ""),
        )


class GoldenSuite:
    """A collection of golden test cases."""

    def __init__(self, name: str = "", cases: list[GoldenCase] | None = None) -> None:
        self.name = name
        self.cases = cases or []

    def add(self, case: GoldenCase) -> None:
        self.cases.append(case)

    def filter_by_tag(self, tag: str) -> list[GoldenCase]:
        return [c for c in self.cases if tag in c.tags]

    def filter_by_difficulty(self, difficulty: str) -> list[GoldenCase]:
        return [c for c in self.cases if c.difficulty == difficulty]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "cases": [c.to_dict() for c in self.cases],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GoldenSuite:
        return cls(
            name=data.get("name", ""),
            cases=[GoldenCase.from_dict(c) for c in data.get("cases", [])],
        )


def load_golden_suite(path: Path) -> GoldenSuite:
    """Load a golden test suite from a JSON file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return GoldenSuite.from_dict(data)


def save_golden_suite(suite: GoldenSuite, path: Path) -> None:
    """Save a golden test suite to a JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(suite.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Default golden suite for basic e-commerce schema
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_GOLDEN = GoldenSuite(
    name="default_ecommerce",
    cases=[
        GoldenCase(
            id="count_all_orders",
            question="How many orders are there?",
            expected_tables=["orders"],
            expected_columns=["row_count"],
            tags=["count", "basic"],
            difficulty="easy",
        ),
        GoldenCase(
            id="count_recent_orders",
            question="How many orders in the last 7 days?",
            expected_tables=["orders"],
            expected_columns=["day", "row_count"],
            tags=["count", "time_range"],
            difficulty="easy",
        ),
        GoldenCase(
            id="daily_order_count",
            question="Daily order count for the past week",
            expected_tables=["orders"],
            expected_columns=["day", "row_count"],
            tags=["count", "group_by", "time_range"],
            difficulty="medium",
        ),
        GoldenCase(
            id="total_revenue",
            question="Total revenue",
            expected_tables=["orders"],
            expected_columns=["total_amount"],
            tags=["sum", "aggregation"],
            difficulty="easy",
        ),
        GoldenCase(
            id="order_status_distribution",
            question="Order status distribution",
            expected_tables=["orders"],
            expected_columns=["status", "count"],
            tags=["group_by", "distribution"],
            difficulty="easy",
        ),
        GoldenCase(
            id="top_customers",
            question="Top 10 customers by order count",
            expected_tables=["orders", "users"],
            expected_columns=["user_id", "order_count"],
            tags=["join", "group_by", "top_n"],
            difficulty="medium",
        ),
        GoldenCase(
            id="user_email_lookup",
            question="Find user by email",
            expected_tables=["users"],
            expected_sql_pattern="SELECT.*FROM.*users.*WHERE.*email",
            tags=["lookup", "filter"],
            difficulty="easy",
        ),
        GoldenCase(
            id="schema_explore",
            question="What tables are available?",
            expected_tables=[],
            tags=["schema", "explore"],
            difficulty="easy",
        ),
    ],
)
