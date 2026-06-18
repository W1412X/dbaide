from __future__ import annotations

from typing import Any

from dbaide.assets.store import AssetStore


class AssetSearchHit:
    __slots__ = ("kind", "path", "score", "title", "summary", "metadata")

    def __init__(self, kind: str, path: str, score: float, title: str, summary: str, metadata: dict[str, Any]) -> None:
        self.kind = kind
        self.path = path
        self.score = score
        self.title = title
        self.summary = summary
        self.metadata = metadata


class AssetSearch:
    """Full-text search over offline asset documents (no alias dictionaries)."""

    def __init__(self, store: AssetStore | None = None) -> None:
        self.store = store or AssetStore()

    def search(self, query: str, *, instances: list[str], limit: int = 12, fingerprint: str = "") -> list[AssetSearchHit]:
        tokens = query_terms(query)
        hits: list[AssetSearchHit] = []
        for instance in instances:
            inst = self.store.instance_doc(instance, fingerprint=fingerprint)
            if inst:
                self._add_hit(hits, tokens, "instance", instance, inst)
            for db in self.store.database_docs(instance, fingerprint=fingerprint):
                db_name = str(db.get("name") or "")
                db_path = self.store.database_dir(instance, db_name) / "database.json"
                db_doc = self.store._read_optional(db_path) or db
                self._add_hit(hits, tokens, "database", f"{instance}.{db_name}", db_doc)
                for table_doc in self.store.table_docs(instance, db_name, fingerprint=fingerprint):
                    table = str(table_doc.get("name") or table_doc.get("table") or "")
                    self._add_hit(hits, tokens, "table", f"{instance}.{db_name}.{table}", table_doc)
                    for col_doc in self.store.column_docs(instance, db_name, table, fingerprint=fingerprint):
                        col = str(col_doc.get("name") or col_doc.get("column") or "")
                        self._add_hit(hits, tokens, "column", f"{instance}.{db_name}.{table}.{col}", col_doc)
        hits.sort(key=lambda hit: (-hit.score, hit.path))
        # Guard the slice: a non-positive limit (e.g. CLI `find --limit -5`) would
        # otherwise become hits[:-5] and silently return "all but the last 5" instead
        # of the top results. Clamp to at least 1.
        return hits[: max(1, int(limit))]

    def _add_hit(self, hits: list[AssetSearchHit], tokens: set[str], kind: str, path: str, doc: dict[str, Any]) -> None:
        text = document_text(doc)
        score = score_text(text, tokens)
        name = str(doc.get("name") or doc.get("column") or doc.get("table") or doc.get("database") or path)
        name_lower = name.lower()
        for token in tokens:
            if token and token in name_lower:
                score += 8 if kind == "column" else 4
        for part in path.lower().split("."):
            if part and part in tokens:
                score += 3
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
                    "data_type": doc.get("data_type"),
                    "primary_key": doc.get("primary_key"),
                    "indexed": doc.get("indexed"),
                    "profile_status": doc.get("profile_status"),
                },
            )
        )


def query_terms(query: str) -> set[str]:
    return {part for part in split_terms(query.lower()) if part}


def split_terms(text: str) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
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
        doc.get("data_type"),
    ]
    if doc.get("columns"):
        values.append(
            " ".join(
                str(col.get("name", "")) + " " + str(col.get("semantic_summary", "")) + " " + str(col.get("source_comment", ""))
                for col in doc.get("columns") or []
            )
        )
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
