"""Schema linker for DBAide - links user questions to database schema elements."""
from __future__ import annotations

import logging
from typing import Any

from dbaide.context.catalog import CatalogMatcher, ScoredColumn, ScoredTable
from dbaide.core.result import QueryPlan
from dbaide.models import ColumnInfo, TableInfo

logger = logging.getLogger("dbaide.schema_linker")


class SchemaLinkResult:
    """Result of schema linking."""

    __slots__ = (
        "candidate_tables", "candidate_columns",
        "selected_tables", "selected_columns",
        "join_paths", "measure_columns", "dimension_columns",
        "time_columns", "filter_columns",
        "missing_information", "confidence", "evidence",
    )

    def __init__(self) -> None:
        self.candidate_tables: list[ScoredTable] = []
        self.candidate_columns: list[ScoredColumn] = []
        self.selected_tables: list[str] = []
        self.selected_columns: list[str] = []
        self.join_paths: list[dict[str, Any]] = []
        self.measure_columns: list[str] = []
        self.dimension_columns: list[str] = []
        self.time_columns: list[str] = []
        self.filter_columns: list[str] = []
        self.missing_information: list[str] = []
        self.confidence: float = 0.0
        self.evidence: list[str] = []


class SchemaLinker:
    """Links user questions to database schema elements.

    Inspired by Claude Code's context gathering:
    - Progressive disclosure: only fetch what's needed
    - Evidence-based: track why each element was selected
    - Confidence scoring: flag low-confidence matches
    """

    def __init__(self, catalog_matcher: CatalogMatcher | None = None) -> None:
        self.catalog = catalog_matcher or CatalogMatcher()

    def link(
        self,
        question: str,
        tables: list[TableInfo],
        table_columns: dict[str, list[ColumnInfo]],
        foreign_keys: list[dict[str, Any]],
        *,
        limit: int = 6,
    ) -> SchemaLinkResult:
        """Link a question to schema elements."""
        result = SchemaLinkResult()

        # Step 1: Score tables
        result.candidate_tables = self.catalog.score_tables(question, tables, limit=limit)
        if not result.candidate_tables:
            result.missing_information.append("No matching tables found")
            result.confidence = 0.0
            return result

        # Step 2: Select top tables
        for scored in result.candidate_tables[:3]:
            result.selected_tables.append(scored.table.name)
            result.evidence.append(f"Table {scored.table.name}: score={scored.score:.2f}, reasons={scored.reasons}")

        # Step 3: Score columns for each selected table
        for table_name in result.selected_tables:
            columns = table_columns.get(table_name, [])
            scored_cols = self.catalog.score_columns(question, table_name, columns, limit=12)
            result.candidate_columns.extend(scored_cols)

            for scored in scored_cols[:6]:
                result.selected_columns.append(f"{table_name}.{scored.column.name}")
                result.evidence.append(f"Column {table_name}.{scored.column.name}: score={scored.score:.2f}")

                # Classify column
                role = self._infer_column_role(scored.column)
                if role == "measure":
                    result.measure_columns.append(f"{table_name}.{scored.column.name}")
                elif role == "dimension":
                    result.dimension_columns.append(f"{table_name}.{scored.column.name}")
                elif role == "time":
                    result.time_columns.append(f"{table_name}.{scored.column.name}")
                elif role == "filter":
                    result.filter_columns.append(f"{table_name}.{scored.column.name}")

        # Step 4: Find join paths
        result.join_paths = self._find_join_paths(result.selected_tables, foreign_keys, table_columns)

        # Step 5: Calculate confidence
        result.confidence = self._calculate_confidence(result)

        # Step 6: Identify missing information
        if not result.measure_columns and not result.dimension_columns:
            result.missing_information.append("No clear measure or dimension columns identified")
        if len(result.selected_tables) > 1 and not result.join_paths:
            result.missing_information.append("Multiple tables selected but no join path found")

        return result

    def build_query_plan(self, question: str, link_result: SchemaLinkResult) -> QueryPlan:
        """Build a QueryPlan from schema linking results."""
        plan = QueryPlan(
            intent_summary=question,
            target_entities=link_result.selected_tables,
            selected_columns=link_result.selected_columns,
            joins=link_result.join_paths,
            assumptions=[],
            confidence=link_result.confidence,
            missing_information=link_result.missing_information,
        )

        # Add assumptions based on evidence
        if link_result.time_columns:
            plan.assumptions.append(f"Time column candidates: {', '.join(link_result.time_columns)}")
        if link_result.measure_columns:
            plan.assumptions.append(f"Measure column candidates: {', '.join(link_result.measure_columns)}")
        if link_result.join_paths:
            for jp in link_result.join_paths:
                source = jp.get("source", "unknown")
                plan.assumptions.append(f"Join: {jp.get('from_table')}.{jp.get('from_column')} = {jp.get('to_table')}.{jp.get('to_column')} (source: {source})")

        return plan

    def _infer_column_role(self, column: ColumnInfo) -> str:
        """Infer column role for schema linking."""
        name = column.name.lower()
        typ = (column.data_type or "").lower()

        if any(k in name for k in ["amount", "price", "total", "fee", "cost", "money", "balance", "revenue"]):
            return "measure"
        if any(k in name for k in ["count", "num", "qty", "quantity", "sum"]):
            return "measure"
        if any(k in typ for k in ["int", "real", "numeric", "decimal", "float", "double"]):
            if not any(k in name for k in ["id", "status", "type", "flag"]):
                return "measure"
        if any(k in name for k in ["created", "updated", "date", "time", "day", "month", "year"]):
            return "time"
        if any(k in typ for k in ["date", "time", "timestamp"]):
            return "time"
        if any(k in name for k in ["status", "state", "type", "category", "kind", "level", "flag"]):
            return "dimension"
        if column.primary_key or name == "id" or name.endswith("_id"):
            return "key"
        return "attribute"

    def _find_join_paths(
        self,
        tables: list[str],
        foreign_keys: list[dict[str, Any]],
        table_columns: dict[str, list[ColumnInfo]],
    ) -> list[dict[str, Any]]:
        """Find join paths between selected tables."""
        joins = []
        table_set = set(tables)

        # Use explicit foreign keys
        for fk in foreign_keys:
            from_table = fk.get("table", "")
            to_table = fk.get("ref_table", "")
            if from_table in table_set and to_table in table_set:
                joins.append({
                    "from_table": from_table,
                    "from_column": fk.get("column", ""),
                    "to_table": to_table,
                    "to_column": fk.get("ref_column", ""),
                    "source": "foreign_key",
                    "confidence": 0.95,
                })

        # Use name heuristics for remaining pairs
        existing = {(j["from_table"], j["to_table"]) for j in joins}
        for t1 in tables:
            for t2 in tables:
                if t1 >= t2:
                    continue
                if (t1, t2) in existing or (t2, t1) in existing:
                    continue
                # Look for *_id columns that match other table names
                cols1 = {c.name.lower() for c in table_columns.get(t1, [])}
                cols2 = {c.name.lower() for c in table_columns.get(t2, [])}
                if f"{t2}_id" in cols1 or f"{t2.rstrip('s')}_id" in cols1:
                    join_col = f"{t2}_id" if f"{t2}_id" in cols1 else f"{t2.rstrip('s')}_id"
                    joins.append({
                        "from_table": t1,
                        "from_column": join_col,
                        "to_table": t2,
                        "to_column": "id",
                        "source": "name_heuristic",
                        "confidence": 0.7,
                    })
                elif f"{t1}_id" in cols2 or f"{t1.rstrip('s')}_id" in cols2:
                    join_col = f"{t1}_id" if f"{t1}_id" in cols2 else f"{t1.rstrip('s')}_id"
                    joins.append({
                        "from_table": t2,
                        "from_column": join_col,
                        "to_table": t1,
                        "to_column": "id",
                        "source": "name_heuristic",
                        "confidence": 0.7,
                    })

        return joins

    def _calculate_confidence(self, result: SchemaLinkResult) -> float:
        """Calculate overall confidence score."""
        if not result.selected_tables:
            return 0.0

        scores = [s.score for s in result.candidate_tables[:3]]
        avg_score = sum(scores) / len(scores) if scores else 0.0

        # Normalize to 0-1 range
        confidence = min(1.0, avg_score / 5.0)

        # Penalty for missing join paths
        if len(result.selected_tables) > 1 and not result.join_paths:
            confidence *= 0.6

        # Penalty for missing measures
        if not result.measure_columns:
            confidence *= 0.8

        return round(confidence, 2)
