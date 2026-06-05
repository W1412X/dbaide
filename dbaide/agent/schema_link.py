"""Schema Linker — a lightweight sub-agent that resolves a *minimal-necessary*
schema for a question before SQL generation.

Why this exists (validated, not speculative): injecting the full schema into the
SQL prompt hurts accuracy because the irrelevant tables/columns enlarge the search
space and scatter attention (see the thesis ablation 5.7 — incremental, minimal
schema beats full-schema injection, with the largest gap on *simple* questions).
So instead of feeding everything the agent has described, we run a short, isolated
linking loop that:

  1. discovers candidate tables,
  2. asks the model to pick the MINIMAL set of tables + the columns that matter,
  3. confirms each pick against the real catalog (existence + consistency — a
     picked column that doesn't exist is dropped, never written),
  4. maps joins among the confirmed tables,
  5. accumulates monotonically and stops when the schema covers the question.

It hands the main loop ONE compact ``ResolvedSchema`` instead of a long transcript
of exploration steps, keeping the SQL-generation context clean. Codex-style: the
model drives the choice; the harness only confirms and validates (cheap, no RA,
no embeddings, no multi-stage pipeline).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from dbaide.agent.progress_events import child_node, subagent_event
from dbaide.agent.schema_context import collect_relations
from dbaide.llm import LLMMessage, NullLLMClient
from dbaide.models import ColumnInfo

if TYPE_CHECKING:
    from dbaide.agent.orchestrator import AskOrchestrator


@dataclass(slots=True)
class ResolvedSchema:
    """The minimal-necessary schema for a question: a few tables, only the columns
    that matter, the joins between them. Compact by construction."""

    tables: list[dict[str, Any]] = field(default_factory=list)   # {database, table, columns:[ColumnInfo], reason}
    joins: list[dict[str, Any]] = field(default_factory=list)
    notes: str = ""
    sufficient: bool = True
    pending_question: str = ""
    pending_options: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.tables

    def to_disclosed(self) -> list[tuple[str, str, list[ColumnInfo]]]:
        """Shape the SQL writer consumes: (database, table, columns) with only the
        resolved columns — this is the minimal context that gets generated on."""
        return [(t["database"], t["table"], list(t["columns"])) for t in self.tables]

    def summary_line(self) -> str:
        parts = []
        for t in self.tables:
            cols = ", ".join(c.name for c in t["columns"][:8])
            parts.append(f"{t['table']}({cols})")
        suffix = f" · {len(self.joins)} join(s)" if self.joins else ""
        return "; ".join(parts) + suffix


class SchemaLinker:
    PARENT = "resolve_schema"

    def __init__(self, orchestrator: "AskOrchestrator") -> None:
        self.orch = orchestrator

    def resolve(self, question: str, *, database: str = "", max_rounds: int = 2) -> ResolvedSchema:
        orch = self.orch
        if isinstance(orch.llm, NullLLMClient):
            # No model: fall back to "all discovered tables" (the old behaviour).
            return self._deterministic(question, database)

        # Base trace node = the resolve_schema tool step; the linker's activities
        # (discovery, selection, relations) nest under it as a true call tree.
        base = orch.run_state.trace_node or self.PARENT
        self._base = base
        confirmed: dict[tuple[str, str], dict[str, Any]] = {}  # (db,table) → {columns, reason}
        joins: list[dict[str, Any]] = []
        dropped: list[str] = []
        refine = ""
        sufficient = True
        for round_index in range(max_rounds):
            # Big direction first: discover the RELEVANT tables (assets-first,
            # progressive) — tables only, no per-column LLM pass. The single _select
            # call below confirms tables + columns in one shot (the "detail" step).
            discover_node = child_node(base, f"discover {round_index + 1}")
            self.orch.progress(subagent_event(
                agent="", parent_id=base, node_id=discover_node,
                title=f"Schema discovery (round {round_index + 1})", status="running",
            ))
            discovery = orch._discover(question + refine, parent=discover_node, column_detail=False)
            candidates = self._candidate_view(discovery, database)
            self.orch.progress(subagent_event(
                agent="", parent_id=base, node_id=discover_node, status="completed",
                title=f"Schema discovery (round {round_index + 1})",
                detail=f"{len(candidates)} candidate table(s)",
            ))
            if not candidates:
                break
            decision = self._select(question, candidates, confirmed)
            ask = decision.get("ask")
            if isinstance(ask, dict) and str(ask.get("question") or "").strip():
                return ResolvedSchema(
                    tables=self._as_tables(confirmed), joins=joins,
                    notes="; ".join(dropped), sufficient=False,
                    pending_question=str(ask["question"]).strip(),
                    pending_options=[str(o) for o in (ask.get("options") or [])],
                )
            for sel in decision.get("tables") or []:
                self._confirm(sel, database, confirmed, dropped)
            targets = list(confirmed.keys())
            if len(targets) >= 2:
                rel_node = child_node(base, "relations")
                self.orch.progress(subagent_event(
                    agent="", parent_id=base, node_id=rel_node,
                    title="Map relations", status="running",
                ))
                joins = collect_relations(orch, targets, question=question, parent=rel_node)
                self.orch.progress(subagent_event(
                    agent="", parent_id=base, node_id=rel_node, status="completed",
                    title="Map relations", detail=f"{len(joins)} join(s)",
                ))
            sufficient = bool(decision.get("sufficient", True))
            if sufficient or not confirmed:
                break
            refine = f"\n(still missing: {str(decision.get('missing') or '').strip()})"

        return ResolvedSchema(
            tables=self._as_tables(confirmed), joins=joins,
            notes="; ".join(dropped), sufficient=sufficient and bool(confirmed),
        )

    # ── internals ────────────────────────────────────────────────────────────

    def _candidate_view(self, discovery, database: str) -> list[dict[str, Any]]:
        seen: dict[tuple[str, str], dict[str, Any]] = {}
        for h in discovery.hits:
            if h.kind != "table" or not h.table:
                continue
            db = h.database or database or ""
            key = (db, h.table)
            if key in seen:
                continue
            tdoc = self.orch.asset_store.table_doc(self.orch.instance, db, h.table)
            cols = [str(c.get("name")) for c in (tdoc.get("columns") if tdoc else [])][:40]
            if not cols:
                # Thin assets (built without column detail) would leave the linker
                # judging table relevance and picking columns blind — fall back to the
                # live catalog so it always sees the real columns.
                try:
                    cols = [c.name for c in self.orch.schema.describe_table(h.table, database=db)][:40]
                except Exception:  # noqa: BLE001 — best-effort enrichment, never fatal
                    cols = []
            seen[key] = {
                "database": db, "table": h.table,
                "summary": (h.summary or "")[:160], "columns": cols,
            }
        return list(seen.values())

    def _candidate_notes(self, candidates: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
        """Authoritative user notes per candidate: the table note (falling back to the
        db note) and a {column: note} map — so the note travels with each object."""
        store = getattr(self.orch, "annotations", None)
        if store is None or not candidates:
            return {}
        try:
            view = store.annotations_for_tables(
                self.orch.instance, [(c["database"], c["table"]) for c in candidates]
            )
        except Exception:  # noqa: BLE001 — notes are best-effort, never fatal
            return {}
        tnotes = view.get("tables") or {}
        dbnotes = view.get("databases") or {}
        cnotes = view.get("columns") or {}
        out: dict[tuple[str, str], dict[str, Any]] = {}
        for c in candidates:
            db, tbl = str(c["database"]).strip().lower(), str(c["table"]).strip().lower()
            entry: dict[str, Any] = {
                "table": tnotes.get((db, tbl)) or dbnotes.get(db) or dbnotes.get("") or "",
                "columns": cnotes.get((db, tbl)) or {},
            }
            if entry["table"] or entry["columns"]:
                out[(db, tbl)] = entry
        return out

    def _select(self, question: str, candidates: list[dict[str, Any]],
                confirmed: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
        from dbaide.agent.schema_context import sanitize_note
        notes = self._candidate_notes(candidates)
        lines = []
        for c in candidates:
            db_l, tbl_l = str(c["database"]).strip().lower(), str(c["table"]).strip().lower()
            entry = notes.get((db_l, tbl_l)) or {}
            col_notes = entry.get("columns") or {}
            # Annotate any column that carries a note, inline with the column it belongs to.
            cols = ", ".join(
                f"{col}(📝{sanitize_note(col_notes[col.strip().lower()])})"
                if col.strip().lower() in col_notes else col
                for col in c["columns"][:25]
            )
            summary = f" — {c['summary']}" if c["summary"] else ""
            note_tag = (f"  📝 TABLE NOTE (authoritative): {sanitize_note(entry['table'])}"
                        if entry.get("table") else "")
            lines.append(f"- {c['database']}.{c['table']} [{cols}]{summary}{note_tag}")
        already = ", ".join(f"{db}.{t}" for (db, t) in confirmed) or "(none)"
        system = (
            "You are a schema linker for Text-to-SQL. From the candidate tables (each shown with "
            "its columns), pick the MINIMAL set of tables and the specific columns needed to answer "
            "the question — fewer is better, irrelevant schema hurts SQL accuracy. Decide in ONE "
            "shot: you already have the candidates and their columns, so confirm everything you need "
            "now and set sufficient=true. Only set sufficient=false (with `missing`) if a needed "
            "table is clearly absent from the candidates. If the question is genuinely ambiguous "
            "about which table/field is meant, return an ask.\n"
            "A 📝 USER NOTE on a candidate is AUTHORITATIVE and OVERRIDES its summary: if a note "
            "says a table is deprecated/wrong or names a replacement, you MUST NOT pick that table — "
            "pick the replacement the note points to (it is among the candidates). Return JSON only."
        )
        user = (
            f"Question:\n{question}\n\nAlready confirmed: {already}\n\n"
            f"Candidate tables:\n" + "\n".join(lines) + "\n\n"
            'Return {"tables":[{"database":"","table":"","columns":["..."],"reason":"..."}],'
            ' "sufficient":true, "missing":"", "ask":null}. '
            'Set ask={"question":"...","options":["..."]} only when truly ambiguous.'
        )
        try:
            payload = self.orch.llm.complete_json(
                [LLMMessage("system", system), LLMMessage("user", user)],
                schema_hint='{"tables":[{"database","table","columns","reason"}],"sufficient":bool,"missing":str,"ask":null}',
            )
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def _confirm(self, sel: dict[str, Any], database: str,
                 confirmed: dict[tuple[str, str], dict[str, Any]], dropped: list[str]) -> None:
        table = str(sel.get("table") or "").strip()
        if not table:
            return
        db = str(sel.get("database") or database or "").strip()
        # The model often echoes the display name "db.table" into the table field with
        # an empty database — split it so describe_table hits the real catalog entry
        # (otherwise it confirms a table with zero columns and a misleading note).
        from dbaide.agent.schema_context import normalize_db_table
        db, table = normalize_db_table(table, db)
        try:
            cols = self.orch.schema.describe_table(table, database=db)  # existence (real catalog)
        except Exception:
            dropped.append(f"table {db}.{table} not found")
            return
        by_name = {c.name: c for c in cols}
        wanted = [str(x).strip() for x in (sel.get("columns") or []) if str(x).strip()]
        chosen: list[ColumnInfo] = []
        for name in wanted:
            if name in by_name:                       # existence + consistency
                chosen.append(by_name[name])
            else:
                dropped.append(f"{table}.{name} (not a column)")
        if not chosen:
            chosen = cols  # no valid pick → keep the whole table rather than nothing
        key = (db, table)
        prior = confirmed.get(key)
        if prior:  # monotonic union — never remove a confirmed column
            names = {c.name for c in prior["columns"]}
            prior["columns"].extend(c for c in chosen if c.name not in names)
        else:
            confirmed[key] = {"columns": list(chosen), "reason": str(sel.get("reason") or "")}
        self.orch.progress(subagent_event(
            agent="", parent_id=getattr(self, "_base", self.PARENT),
            node_id=child_node(getattr(self, "_base", self.PARENT), f"confirm {table}"),
            status="completed",
            title=f"confirmed {table}: {len(confirmed[key]['columns'])} col(s)",
            detail=str(sel.get("reason") or "")[:120],
        ))

    def _as_tables(self, confirmed: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {"database": db, "table": table, "columns": info["columns"], "reason": info["reason"]}
            for (db, table), info in confirmed.items()
        ]

    def _deterministic(self, question: str, database: str) -> ResolvedSchema:
        """No-LLM fallback: take the discovered tables as-is (full columns)."""
        base = self.orch.run_state.trace_node or self.PARENT
        discovery = self.orch._discover(question, parent=child_node(base, "discover"))
        confirmed: dict[tuple[str, str], dict[str, Any]] = {}
        for h in discovery.hits:
            if h.kind != "table" or not h.table:
                continue
            db = h.database or database or ""
            try:
                cols = self.orch.schema.describe_table(h.table, database=db)
            except Exception:
                continue
            confirmed[(db, h.table)] = {"columns": cols, "reason": ""}
        joins = collect_relations(self.orch, list(confirmed.keys()), question=question,
                                  parent=child_node(base, "relations")) if len(confirmed) >= 2 else []
        return ResolvedSchema(tables=self._as_tables(confirmed), joins=joins,
                              sufficient=bool(confirmed))
