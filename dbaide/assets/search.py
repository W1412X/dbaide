from __future__ import annotations

from typing import Any

from dbaide.assets.store import AssetStore
from dbaide.context.catalog import ALIASES


class AssetSearchHit:
    """Search result hit."""

    __slots__ = ("kind", "path", "score", "title", "summary", "metadata")

    def __init__(self, kind: str, path: str, score: float, title: str, summary: str, metadata: dict[str, Any]) -> None:
        self.kind = kind
        self.path = path
        self.score = score
        self.title = title
        self.summary = summary
        self.metadata = metadata


class AssetSearch:
    def __init__(self, store: AssetStore | None = None) -> None:
        self.store = store or AssetStore()

    def search(self, query: str, *, instances: list[str], limit: int = 12) -> list[AssetSearchHit]:
        tokens = expand_terms(query)
        hits: list[AssetSearchHit] = []
        for instance in instances:
            inst = self.store.instance_doc(instance)
            if inst:
                self._add_hit(hits, query, tokens, "instance", instance, inst)
            for db in self.store.database_docs(instance):
                db_name = str(db.get("name") or "")
                db_path = self.store.database_dir(instance, db_name) / "database.json"
                db_doc = self.store._read_optional(db_path) or db
                self._add_hit(hits, query, tokens, "database", f"{instance}.{db_name}", db_doc)
                for table_doc in self.store.table_docs(instance, db_name):
                    table = str(table_doc.get("name") or table_doc.get("table") or "")
                    self._add_hit(hits, query, tokens, "table", f"{instance}.{db_name}.{table}", table_doc)
                    for col_doc in self.store.column_docs(instance, db_name, table):
                        col = str(col_doc.get("name") or col_doc.get("column") or "")
                        self._add_hit(hits, query, tokens, "column", f"{instance}.{db_name}.{table}.{col}", col_doc)
        hits.sort(key=lambda hit: (-hit.score, hit.path))
        return hits[:limit]

    def _add_hit(self, hits: list[AssetSearchHit], query: str, tokens: set[str], kind: str, path: str, doc: dict[str, Any]) -> None:
        text = document_text(doc)
        score = score_text(text, tokens)
        name = str(doc.get("name") or doc.get("column") or doc.get("table") or doc.get("database") or path)
        name_lower = name.lower()
        direct_name_hit = False
        for token in tokens:
            if token and token in name_lower:
                direct_name_hit = True
                score += 8 if kind == "column" else 4
        for part in path.lower().split("."):
            if part and part in tokens:
                score += 3
        if kind == "column" and direct_name_hit:
            score += 4
        if kind == "table" and any(k in query.lower() for k in ["字段", "列", "在哪", "where", "which column"]):
            score *= 0.65
        if score <= 0:
            return
        hits.append(
            AssetSearchHit(
                kind=kind,
                path=path,
                score=score,
                title=name,
                summary=str(doc.get("semantic_summary") or doc.get("description") or doc.get("source_comment") or "")[:500],
                metadata={
                    "role": doc.get("likely_role"),
                    "tags": doc.get("semantic_tags") or [],
                    "profile_status": doc.get("profile_status"),
                    "data_type": doc.get("data_type"),
                },
            )
        )


def expand_terms(query: str) -> set[str]:
    lowered = query.lower()
    terms = {part for part in split_terms(lowered) if part}
    for zh, aliases in ALIASES.items():
        if zh in query:
            terms.add(zh)
            terms.update(a.lower() for a in aliases)
        for alias in aliases:
            if alias.lower() in lowered:
                terms.add(zh)
                terms.update(a.lower() for a in aliases)
    return terms


def split_terms(text: str) -> list[str]:
    out: list[str] = []
    buf = []
    for ch in text:
        if ch.isalnum() or ch == "_":
            buf.append(ch)
        else:
            if buf:
                out.append("".join(buf))
                buf = []
    if buf:
        out.append("".join(buf))
    return out


def document_text(doc: dict[str, Any]) -> str:
    values = [
        doc.get("name"),
        doc.get("column"),
        doc.get("table"),
        doc.get("database"),
        doc.get("source_comment"),
        doc.get("semantic_summary"),
        doc.get("description"),
        doc.get("likely_role"),
        " ".join(doc.get("semantic_tags") or []),
        " ".join(doc.get("usage_hints") or []),
    ]
    if doc.get("columns"):
        values.append(" ".join(str(col.get("name", "")) + " " + str(col.get("semantic_summary", "")) for col in doc.get("columns") or []))
    return " ".join(str(v or "").lower() for v in values)


def score_text(text: str, tokens: set[str]) -> float:
    score = 0.0
    for token in tokens:
        if not token or len(token) < 2:
            continue
        count = text.count(token.lower())
        if count:
            score += min(5, count) * (2.0 if len(token) > 2 else 0.5)
    return score
