from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dbaide.assets import AssetStore
from dbaide.rendering.table import md_escape_cell


@dataclass(slots=True)
class SchemaDiff:
    missing_tables_left: list[str]
    missing_tables_right: list[str]
    column_diffs: list[dict[str, Any]]


class DeveloperTools:
    def __init__(self, store: AssetStore | None = None) -> None:
        self.store = store or AssetStore()

    def tree(self, instance: str, *, database: str = "", show_columns: bool = True, max_columns: int = 80) -> str:
        lines = [instance]
        db_docs = self.store.database_docs(instance)
        for db_doc in db_docs:
            db_name = str(db_doc.get("name") or "")
            if database and db_name != database:
                continue
            lines.append(f"  {db_name}/")
            for table_doc in self.store.table_docs(instance, db_name):
                table = str(table_doc.get("name") or table_doc.get("table") or "")
                desc = str(table_doc.get("description") or "").split("。", 1)[0]
                lines.append(f"    {table}  # {desc}" if desc else f"    {table}")
                if not show_columns:
                    continue
                columns = self.store.column_docs(instance, db_name, table)
                for idx, col in enumerate(columns):
                    if idx >= max_columns:
                        lines.append(f"      ... {len(columns) - max_columns} more column(s)")
                        break
                    typ = col.get("data_type") or ""
                    prof = col.get("profile_status") or ""
                    flags = []
                    if col.get("primary_key"):
                        flags.append("pk")
                    if col.get("indexed"):
                        flags.append("idx")
                    flag_text = f" [{', '.join(flags)}]" if flags else ""
                    lines.append(f"      - {col.get('name')} : {typ}{flag_text} ({prof})")
        return "\n".join(lines)

    def markdown(self, instance: str, *, database: str = "") -> str:
        lines = [f"# Schema: {instance}", ""]
        inst = self.store.instance_doc(instance)
        if inst and inst.get("description"):
            lines.extend([str(inst.get("description") or ""), ""])
        for db_doc in self.store.database_docs(instance):
            db_name = str(db_doc.get("name") or "")
            if database and db_name != database:
                continue
            full_db_doc = self._database_doc(instance, db_name) or db_doc
            lines.extend([f"## Database `{db_name}`", "", str(full_db_doc.get("description") or ""), ""])
            for table_doc in self.store.table_docs(instance, db_name):
                table = str(table_doc.get("name") or table_doc.get("table") or "")
                lines.extend([f"### `{table}`", "", str(table_doc.get("description") or ""), ""])
                lines.append("| Column | Type | PK | Indexed | Profile | Summary |")
                lines.append("| --- | --- | --- | --- | --- | --- |")
                for col in self.store.column_docs(instance, db_name, table):
                    # Escape every free-text cell (name/type/comment) for the Markdown
                    # table: a stray '|' or newline in a column name or comment would
                    # otherwise split or terminate the row.
                    lines.append(
                        "| {name} | {typ} | {pk} | {indexed} | {profile} | {summary} |".format(
                            name=md_escape_cell(col.get("name") or ""),
                            typ=md_escape_cell(col.get("data_type") or ""),
                            pk="✓" if col.get("primary_key") else "",
                            indexed="✓" if col.get("indexed") else "",
                            profile=col.get("profile_status") or "",
                            summary=md_escape_cell(str(col.get("source_comment") or "")[:240]),
                        )
                    )
                lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def relations(self, instance: str, *, database: str = "") -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for db_doc in self.store.database_docs(instance):
            db_name = str(db_doc.get("name") or "")
            if database and db_name != database:
                continue
            for table_doc in self.store.table_docs(instance, db_name):
                table = str(table_doc.get("name") or table_doc.get("table") or "")
                for fk in table_doc.get("foreign_keys") or []:
                    out.append(
                        {
                            "database": db_name,
                            "table": table,
                            "column": fk.get("column"),
                            "ref_table": fk.get("ref_table"),
                            "ref_column": fk.get("ref_column"),
                            "source": "foreign_key",
                        }
                    )
        return out

    def diff(self, left_instance: str, right_instance: str, *, left_database: str = "", right_database: str = "") -> SchemaDiff:
        left = self._table_column_map(left_instance, database=left_database)
        right = self._table_column_map(right_instance, database=right_database)
        left_tables, right_tables = set(left), set(right)
        column_diffs = []
        for table in sorted(left_tables & right_tables):
            left_cols, right_cols = left[table], right[table]
            missing_right = sorted(set(left_cols) - set(right_cols))
            missing_left = sorted(set(right_cols) - set(left_cols))
            changed = []
            for col in sorted(set(left_cols) & set(right_cols)):
                if left_cols[col].get("data_type") != right_cols[col].get("data_type"):
                    changed.append({"column": col, "left_type": left_cols[col].get("data_type"), "right_type": right_cols[col].get("data_type")})
            if missing_left or missing_right or changed:
                column_diffs.append({"table": table, "missing_left": missing_left, "missing_right": missing_right, "type_changes": changed})
        return SchemaDiff(
            missing_tables_left=sorted(right_tables - left_tables),
            missing_tables_right=sorted(left_tables - right_tables),
            column_diffs=column_diffs,
        )

    def _database_doc(self, instance: str, database: str) -> dict[str, Any] | None:
        path = self.store.database_dir(instance, database) / "database.json"
        if not path.exists():
            return None
        return self.store.read_json(path)

    def _table_column_map(self, instance: str, *, database: str = "") -> dict[str, dict[str, dict[str, Any]]]:
        out: dict[str, dict[str, dict[str, Any]]] = {}
        for db_doc in self.store.database_docs(instance):
            db_name = str(db_doc.get("name") or "")
            if database and db_name != database:
                continue
            for table_doc in self.store.table_docs(instance, db_name):
                table = str(table_doc.get("name") or table_doc.get("table") or "")
                key = f"{db_name}.{table}"
                out[key] = {str(col.get("name")): col for col in self.store.column_docs(instance, db_name, table)}
        return out

