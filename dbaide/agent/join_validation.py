"""Sample evidence + soft confidence for joins (LLM proposes; code never hard-blocks on types)."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, Callable

from dbaide.adapters.base import quote_identifier
from dbaide.agent.schema_context import normalize_db_table
from dbaide.models import ColumnInfo

if TYPE_CHECKING:
    from dbaide.agent.orchestrator import AskOrchestrator

logger = logging.getLogger("dbaide.join_validation")

DisclosedSchema = tuple[str, str, list[ColumnInfo]]
ProgressFn = Callable[[dict[str, Any]], None]

from dbaide.joins.catalog import USER_JOIN_CONFIDENCE

DEFAULT_SAMPLE_SIZE = 150
# Soft UI thresholds — not hard business rules.
CONFIDENCE_RECOMMENDED = 0.55
CONFIDENCE_VALIDATED = 0.35
CONFIDENCE_DROP_SEMANTIC = 0.18


def _safe_confidence(value: object) -> float:
    """Coerce a confidence value to float, returning 0.0 on failure.

    LLM output sometimes produces non-numeric strings like ``"high"``
    instead of a number.  An unguarded ``float()`` would crash the whole
    join-validation or SQL-generation step.
    """
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def normalize_type_name(data_type: str) -> str:
    """Strip length/precision for catalog comparison only (not column-name rules)."""
    text = str(data_type or "").strip().lower()
    text = re.sub(r"\([^)]*\)", "", text).strip()
    return text.split()[0] if text else ""


def type_alignment_score(left_type: str, right_type: str) -> float:
    """How well join-key types align (0–1). Mismatch lowers confidence; never forbids."""
    if not left_type or not right_type:
        return 0.5
    left = normalize_type_name(left_type)
    right = normalize_type_name(right_type)
    if left == right:
        return 1.0
    if left in {right, "unknown", ""} or right in {"unknown", ""}:
        return 0.5
    # Loosely related catalog families — scoring hint only.
    numeric = {"int", "integer", "bigint", "smallint", "tinyint", "serial", "float", "double", "real", "decimal", "numeric", "number"}
    texty = {"char", "varchar", "text", "string", "clob", "uuid"}
    left_base = left.split("(")[0]
    right_base = right.split("(")[0]
    if left_base in numeric and right_base in numeric:
        return 0.85
    if left_base in texty and right_base in texty:
        return 0.85
    if (left_base in numeric and right_base in texty) or (left_base in texty and right_base in numeric):
        return 0.45
    return 0.35


def join_confidence(
    *,
    source: str,
    llm_confidence: float = 0.0,
    type_alignment: float = 0.5,
    match_rate: float = 0.0,
    sampled: int = 0,
) -> float:
    """Blend LLM judgment with lightweight sample/type evidence."""
    if source == "foreign_key":
        base = 0.88
    else:
        base = float(llm_confidence or 0.55)
    sample_weight = match_rate if sampled > 0 else 0.5
    score = 0.50 * base + 0.30 * sample_weight + 0.20 * type_alignment
    if source == "foreign_key":
        score = max(score, 0.72)
    return round(min(1.0, max(0.0, score)), 3)


def classify_join_type(*, max_right_per_left: int, max_left_per_right: int) -> str:
    mr = max(0, int(max_right_per_left or 0))
    ml = max(0, int(max_left_per_right or 0))
    if mr <= 1 and ml <= 1:
        return "one_to_one"
    if mr > 1 and ml > 1:
        return "many_to_many"
    if mr > 1:
        return "one_to_many"
    return "many_to_one"


class JoinSampleValidator:
    """Attach sample evidence and confidence to join edges (sampled reads only)."""

    def __init__(
        self,
        orchestrator: AskOrchestrator,
        *,
        sample_size: int = DEFAULT_SAMPLE_SIZE,
        dialect: str = "",
    ) -> None:
        self.orchestrator = orchestrator
        self.sample_size = max(20, min(int(sample_size), 500))
        self.dialect = dialect or orchestrator.adapter.dialect

    def validate_relations(
        self,
        relations: list[dict[str, Any]],
        disclosed: list[DisclosedSchema],
        *,
        progress: ProgressFn | None = None,
        parent: str = "",
        drop_invalid_semantic: bool = True,
    ) -> list[dict[str, Any]]:
        if not relations or not disclosed:
            return relations
        col_types = _column_type_index(disclosed)
        table_db = {table: db for db, table, _ in disclosed}
        out: list[dict[str, Any]] = []
        for rel in relations:
            scored = self.validate_one(
                rel,
                col_types=col_types,
                table_db=table_db,
                progress=progress,
                parent=parent,
            )
            conf = _safe_confidence(scored.get("confidence"))
            if drop_invalid_semantic and scored.get("source") == "semantic" and conf < CONFIDENCE_DROP_SEMANTIC:
                continue
            out.append(scored)
        out.sort(key=lambda r: _safe_confidence(r.get("confidence")), reverse=True)
        return out

    def validate_one(
        self,
        rel: dict[str, Any],
        *,
        col_types: dict[tuple[str, str], str] | None = None,
        table_db: dict[str, str] | None = None,
        progress: ProgressFn | None = None,
        parent: str = "",
    ) -> dict[str, Any]:
        item = dict(rel)
        left_table = str(item.get("table") or "")
        left_col = str(item.get("column") or "")
        right_table = str(item.get("ref_table") or "")
        right_col = str(item.get("ref_column") or "")
        if not all([left_table, left_col, right_table, right_col]):
            item["confidence"] = 0.0
            item["validated"] = False
            item["join_type"] = "unknown"
            item["validation"] = {"ok": False, "message": "incomplete relation"}
            return item

        types = col_types or {}
        left_type = types.get((left_table, left_col), "")
        right_type = types.get((right_table, right_col), "")
        alignment = type_alignment_score(left_type, right_type)
        source = str(item.get("source") or "foreign_key")
        if source == "user":
            item["confidence"] = USER_JOIN_CONFIDENCE
            item["validated"] = True
            item["join_type"] = str(item.get("join_type") or "unknown")
            item["validation"] = {
                "ok": True,
                "recommended": True,
                "confidence": USER_JOIN_CONFIDENCE,
                "message": "User-defined join (catalog)",
                "type_alignment": round(alignment, 3),
                "left_type": left_type,
                "right_type": right_type,
            }
            return item

        llm_conf = _safe_confidence(item.get("confidence"))

        # One stable node per relation: the "sample check" and "confidence" events
        # update the same tree node, and distinct relations render as siblings.
        rel_node = f"jv:{left_table}.{left_col}->{right_table}.{right_col}"
        self._emit(
            progress,
            parent,
            f"Sample check {left_table}.{left_col} → {right_table}.{right_col}",
            detail=f"type alignment {alignment:.0%}",
            node_id=rel_node,
        )

        db_map = table_db or {}
        left_database = db_map.get(left_table) or ""
        right_database = db_map.get(right_table) or ""
        database = (
            left_database
            or right_database
            or self.orchestrator.run_state.table_database
            or self.orchestrator.run_state.database
            or ""
        )
        stats = self._sample_stats(
            left_table,
            left_col,
            right_table,
            right_col,
            database=database,
            left_database=left_database,
            right_database=right_database,
        )
        join_type = classify_join_type(
            max_right_per_left=int(stats.get("max_right_per_left") or 0),
            max_left_per_right=int(stats.get("max_left_per_right") or 0),
        )
        sampled = int(stats.get("sampled") or 0)
        match_rate = float(stats.get("match_rate") or 0.0)
        confidence = join_confidence(
            source=source,
            llm_confidence=llm_conf,
            type_alignment=alignment,
            match_rate=match_rate,
            sampled=sampled,
        )

        item["join_type"] = join_type
        item["confidence"] = confidence
        item["validated"] = confidence >= CONFIDENCE_VALIDATED
        stats["type_alignment"] = round(alignment, 3)
        stats["left_type"] = left_type
        stats["right_type"] = right_type
        stats["confidence"] = confidence
        stats["recommended"] = confidence >= CONFIDENCE_RECOMMENDED
        stats["ok"] = item["validated"]

        if stats.get("error"):
            stats["message"] = f"Sample check error: {stats['error']}"
        elif sampled == 0:
            stats["message"] = f"{join_type}; no sample rows (schema/LLM confidence only)"
        elif match_rate < 0.3:
            stats["message"] = f"{join_type}; weak sample match ({match_rate:.0%}) — verify manually"
        else:
            stats["message"] = f"{join_type}; match={match_rate:.0%}; confidence={confidence:.0%}"

        item["validation"] = stats

        self._emit(
            progress,
            parent,
            f"{left_table}.{left_col} → {right_table}.{right_col} · {join_type} · {confidence:.0%}",
            detail=str(stats.get("message") or ""),
            status="completed" if item["validated"] else "info",
            node_id=rel_node,
        )
        return item

    def _sample_stats(
        self,
        left_table: str,
        left_col: str,
        right_table: str,
        right_col: str,
        *,
        database: str,
        left_database: str = "",
        right_database: str = "",
    ) -> dict[str, Any]:
        left_db, left_table = normalize_db_table(left_table, left_database or database)
        right_db, right_table = normalize_db_table(right_table, right_database or database)
        database = left_db or right_db or database
        lt = quote_identifier(_qualified_sample_table(left_db, left_table, self.dialect), self.dialect)
        lc = quote_identifier(left_col, self.dialect)
        rt = quote_identifier(_qualified_sample_table(right_db, right_table, self.dialect), self.dialect)
        rc = quote_identifier(right_col, self.dialect)
        n = self.sample_size

        match_sql = f"""
SELECT
  COUNT(DISTINCT l.v) AS sampled,
  COUNT(DISTINCT CASE WHEN r.{rc} IS NOT NULL THEN l.v END) AS matched
FROM (
  SELECT {lc} AS v FROM {lt} WHERE {lc} IS NOT NULL LIMIT {n}
) l
LEFT JOIN {rt} r ON l.v = r.{rc}
""".strip()

        # Right rows per sampled left key — pre-aggregated join, no correlated subquery.
        max_right_sql = f"""
SELECT MAX(cnt) AS max_cnt FROM (
  SELECT r.{rc} AS k, COUNT(*) AS cnt
  FROM (SELECT DISTINCT {lc} AS v FROM {lt} WHERE {lc} IS NOT NULL LIMIT {n}) ls
  JOIN {rt} r ON r.{rc} = ls.v
  GROUP BY r.{rc}
) g
""".strip()

        # Left rows per matched right key. Join to DISTINCT right keys so a duplicated
        # right key cannot inflate the count into the (left×right) join product.
        max_left_sql = f"""
SELECT MAX(cnt) AS max_cnt FROM (
  SELECT l.v AS k, COUNT(*) AS cnt
  FROM (SELECT {lc} AS v FROM {lt} WHERE {lc} IS NOT NULL LIMIT {n}) l
  JOIN (SELECT DISTINCT {rc} AS rk FROM {rt}) r ON l.v = r.rk
  GROUP BY l.v
) g
""".strip()

        try:
            match_row = self._scalar_row(match_sql, database=database)
            sampled = int(match_row.get("sampled") or 0)
            matched = int(match_row.get("matched") or 0)
            match_rate = (matched / sampled) if sampled else 0.0
            max_right = int(self._scalar_row(max_right_sql, database=database).get("max_cnt") or 0) if matched else 0
            max_left = int(self._scalar_row(max_left_sql, database=database).get("max_cnt") or 0) if matched else 0
            return {
                "sampled": sampled,
                "matched": matched,
                "match_rate": round(match_rate, 4),
                "max_right_per_left": max_right,
                "max_left_per_right": max_left,
                "sample_size_limit": n,
                "method": "sample_join",
            }
        except Exception as exc:
            logger.warning("join_sample_validation_failed: %s", exc)
            return {
                "sampled": 0,
                "matched": 0,
                "match_rate": 0.0,
                "max_right_per_left": 0,
                "max_left_per_right": 0,
                "sample_size_limit": n,
                "method": "sample_join",
                "error": str(exc),
            }

    def _scalar_row(self, sql: str, *, database: str) -> dict[str, Any]:
        result = self.orchestrator.query.execute_sql(sql, database=database, limit=10)
        if not result.rows:
            return {}
        row = result.rows[0]
        if isinstance(row, dict):
            return row
        return dict(zip(result.columns, row))

    def _emit(
        self,
        progress: ProgressFn | None,
        parent: str,
        title: str,
        *,
        detail: str = "",
        status: str = "info",
        node_id: str = "",
    ) -> None:
        if not progress:
            return
        from dbaide.agent.progress_events import subagent_event

        progress(subagent_event(
            agent="join_validate", title=title, parent_id=parent, detail=detail,
            status=status, node_id=node_id,
        ))


def validate_join_relations(
    orchestrator: AskOrchestrator,
    relations: list[dict[str, Any]],
    disclosed: list[DisclosedSchema],
    *,
    sample_size: int | None = None,
    progress: ProgressFn | None = None,
    parent: str = "",
    drop_invalid_semantic: bool = True,
) -> list[dict[str, Any]]:
    if sample_size is None:
        policy = getattr(orchestrator.adapter, "policy", None)
        sample_size = policy.join_sample_size if policy else DEFAULT_SAMPLE_SIZE
    validator = JoinSampleValidator(orchestrator, sample_size=sample_size)
    return validator.validate_relations(
        relations,
        disclosed,
        progress=progress or orchestrator.progress,
        parent=parent,
        drop_invalid_semantic=drop_invalid_semantic,
    )


def _column_type_index(disclosed: list[DisclosedSchema]) -> dict[tuple[str, str], str]:
    return {(table, col.name): col.data_type for _, table, columns in disclosed for col in columns}


def _qualified_sample_table(database: str, table: str, dialect: str) -> str:
    if dialect == "mysql" and database and "." not in table:
        return f"{database}.{table}"
    return table
