from __future__ import annotations

from dbaide.models import ColumnProfile, QueryResult, TableInfo


class AnswerFormatter:
    def tables(self, tables: list[TableInfo]) -> str:
        if not tables:
            return "No visible tables found."
        lines = [f"Found {len(tables)} table(s):"]
        for table in tables:
            row_info = "" if table.estimated_rows is None else f", ~{table.estimated_rows} rows"
            comment = f" - {table.comment}" if table.comment else ""
            lines.append(f"- {table.ref} ({table.table_type}{row_info}){comment}")
        return "\n".join(lines)

    def profiles(self, profiles: list[ColumnProfile]) -> str:
        if not profiles:
            return "No column profiles generated."
        lines = [f"Column profiles ({len(profiles)} columns):"]
        for profile in profiles:
            null_rate = (profile.null_count / profile.row_count) if profile.row_count else 0
            lines.append("")
            lines.append(f"{profile.table}.{profile.column}")
            lines.append(f"  Rows: {profile.row_count:,}  |  Null: {profile.null_count:,} ({null_rate:.1%})  |  Distinct: {profile.distinct_count or 'N/A'}")
            if profile.min_value is not None or profile.max_value is not None:
                lines.append(f"  Range: {profile.min_value} .. {profile.max_value}")
            if profile.top_values:
                top = ", ".join(f"{x.get('value')}({x.get('count')})" for x in profile.top_values[:5])
                lines.append(f"  Top: {top}")
        return "\n".join(lines)

    def query_result(self, result: QueryResult, *, sql: str = "", rationale: str = "") -> str:
        """Natural-language answer — no separate result table."""
        parts: list[str] = []
        if rationale:
            parts.append(rationale.strip())
        parts.append(_summarize_rows(result))
        timing = f"共 {result.row_count:,} 条记录" if result.row_count != 1 else "共 1 条记录"
        timing += f"，耗时 {result.elapsed_ms:.0f}ms"
        if result.truncated:
            timing += f"（仅展示前 {len(result.rows)} 条）"
        parts.append(timing)
        return "\n\n".join(p for p in parts if p)


def _summarize_rows(result: QueryResult) -> str:
    if result.row_count == 0 or not result.rows:
        return "查询未返回任何数据。"
    cols = result.columns or list(result.rows[0].keys())
    preview = result.rows[:12]

    if result.row_count == 1:
        return _describe_row(preview[0], cols)

    if len(cols) == 1:
        values = [str(row.get(cols[0], "")) for row in preview]
        joined = "、".join(values[:10])
        suffix = f" 等 {result.row_count} 项" if result.row_count > len(values) else ""
        return f"{cols[0]}：{joined}{suffix}。"

    if len(cols) == 2:
        pairs = [f"{row.get(cols[0], '')}（{row.get(cols[1], '')}）" for row in preview[:10]]
        joined = "、".join(str(p) for p in pairs if p != "（）")
        suffix = f" 等 {result.row_count} 条" if result.row_count > len(pairs) else ""
        return f"查询结果：{joined}{suffix}。"

    lines = [f"查询返回 {result.row_count} 条记录，前几项如下："]
    for i, row in enumerate(preview[:8], 1):
        lines.append(f"{i}. {_describe_row(row, cols, compact=True)}")
    if result.row_count > len(preview[:8]):
        lines.append(f"… 另有 {result.row_count - min(8, len(preview))} 条未列出。")
    return "\n".join(lines)


def _describe_row(row: dict, columns: list[str], *, compact: bool = False) -> str:
    parts = []
    for col in columns:
        val = row.get(col)
        if val is None:
            continue
        text = str(val).replace("\n", " ").strip()
        if len(text) > 80:
            text = text[:77] + "…"
        if compact:
            parts.append(f"{col}={text}")
        else:
            parts.append(f"{col} 为 **{text}**")
    if compact:
        return "，".join(parts) if parts else "（空行）"
    return "，".join(parts) + "。" if parts else "（空行）"
