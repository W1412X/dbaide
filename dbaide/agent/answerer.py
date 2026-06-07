from __future__ import annotations

from dbaide.i18n import get_language, normalize
from dbaide.models import ColumnProfile, QueryResult, TableInfo


def _zh(language: str | None = None) -> bool:
    """Whether deterministic summaries should render in Chinese.

    Final answer paths pass the user's question language. UI-owned helper calls can
    omit it and keep using the interface language.
    """
    if language:
        return normalize(language) == "zh"
    try:
        return get_language() == "zh"
    except Exception:  # noqa: BLE001
        return False


class AnswerFormatter:
    def tables(self, tables: list[TableInfo], *, language: str | None = None) -> str:
        zh = _zh(language)
        if not tables:
            return "未发现可见的表。" if zh else "No visible tables found."
        head = f"找到 {len(tables)} 张表：" if zh else f"Found {len(tables)} table(s):"
        lines = [head]
        for table in tables:
            row_info = "" if table.estimated_rows is None else f", ~{table.estimated_rows} rows"
            comment = f" - {table.comment}" if table.comment else ""
            lines.append(f"- {table.ref} ({table.table_type}{row_info}){comment}")
        return "\n".join(lines)

    def profiles(self, profiles: list[ColumnProfile], *, language: str | None = None) -> str:
        zh = _zh(language)
        if not profiles:
            return "未生成列画像。" if zh else "No column profiles generated."
        head = f"列画像（{len(profiles)} 列）：" if zh else f"Column profiles ({len(profiles)} columns):"
        rows_l, null_l, distinct_l = ("行数", "空值", "去重") if zh else ("Rows", "Null", "Distinct")
        range_l, top_l = ("范围", "高频值") if zh else ("Range", "Top")
        lines = [head]
        for profile in profiles:
            null_rate = (profile.null_count / profile.row_count) if profile.row_count else 0
            lines.append("")
            lines.append(f"{profile.table}.{profile.column}")
            lines.append(
                f"  {rows_l}: {profile.row_count:,}  |  {null_l}: {profile.null_count:,} "
                f"({null_rate:.1%})  |  {distinct_l}: {profile.distinct_count or 'N/A'}"
            )
            if profile.min_value is not None or profile.max_value is not None:
                lines.append(f"  {range_l}: {profile.min_value} .. {profile.max_value}")
            if profile.top_values:
                top = ", ".join(f"{x.get('value')}({x.get('count')})" for x in profile.top_values[:5])
                lines.append(f"  {top_l}: {top}")
        return "\n".join(lines)

    def query_result(
        self,
        result: QueryResult,
        *,
        sql: str = "",
        rationale: str = "",
        interpretation: dict | None = None,
        language: str | None = None,
    ) -> str:
        """Natural-language answer — no separate result table."""
        zh = _zh(language)
        parts: list[str] = []
        if rationale:
            parts.append(rationale.strip())
        parts.append(_summarize_rows(result, zh))
        if interpretation:
            summary = str(interpretation.get("summary") or "").strip()
            if summary:
                parts.append(summary)
            actions = interpretation.get("next_actions") or []
            if actions:
                label = "建议：" if zh else "Suggestions: "
                sep = "；" if zh else "; "
                parts.append(label + sep.join(str(a) for a in actions[:3]))
        parts.append(_timing_line(result, zh))
        return "\n\n".join(p for p in parts if p)


def _timing_line(result: QueryResult, zh: bool) -> str:
    """The footer: row count · elapsed, noting truncation if the result was capped."""
    if zh:
        timing = f"共 {result.row_count:,} 条记录" if result.row_count != 1 else "共 1 条记录"
        timing += f"，耗时 {result.elapsed_ms:.0f}ms"
        if result.truncated:
            timing += f"（仅展示前 {len(result.rows)} 条）"
        return timing
    timing = f"{result.row_count:,} rows" if result.row_count != 1 else "1 row"
    timing += f" · {result.elapsed_ms:.0f}ms"
    if result.truncated:
        timing += f" (showing first {len(result.rows)})"
    return timing


def _summarize_rows(result: QueryResult, zh: bool) -> str:
    if result.row_count == 0 or not result.rows:
        return "查询未返回任何数据。" if zh else "The query returned no data."
    cols = result.columns or list(result.rows[0].keys())
    preview = result.rows[:12]

    if result.row_count == 1:
        return _describe_row(preview[0], cols, zh)

    if len(cols) == 1:
        values = [str(row.get(cols[0], "")) for row in preview]
        if zh:
            joined = "、".join(values[:10])
            suffix = f" 等 {result.row_count} 项" if result.row_count > len(values) else ""
            return f"{cols[0]}：{joined}{suffix}。"
        joined = ", ".join(values[:10])
        suffix = f", and {result.row_count} total" if result.row_count > len(values) else ""
        return f"{cols[0]}: {joined}{suffix}."

    if len(cols) == 2:
        if zh:
            pairs = [f"{row.get(cols[0], '')}（{row.get(cols[1], '')}）" for row in preview[:10]]
            joined = "、".join(str(p) for p in pairs if p != "（）")
            suffix = f" 等 {result.row_count} 条" if result.row_count > len(pairs) else ""
            return f"查询结果：{joined}{suffix}。"
        pairs = [f"{row.get(cols[0], '')} ({row.get(cols[1], '')})" for row in preview[:10]]
        joined = ", ".join(str(p) for p in pairs if p != " ()")
        suffix = f", and {result.row_count} total" if result.row_count > len(pairs) else ""
        return f"Results: {joined}{suffix}."

    shown = min(8, len(preview))
    if zh:
        lines = [f"查询返回 {result.row_count} 条记录，前几项如下："]
        for i, row in enumerate(preview[:8], 1):
            lines.append(f"{i}. {_describe_row(row, cols, zh, compact=True)}")
        if result.row_count > shown:
            lines.append(f"… 另有 {result.row_count - shown} 条未列出。")
        return "\n".join(lines)
    lines = [f"The query returned {result.row_count} rows. First few:"]
    for i, row in enumerate(preview[:8], 1):
        lines.append(f"{i}. {_describe_row(row, cols, zh, compact=True)}")
    if result.row_count > shown:
        lines.append(f"… and {result.row_count - shown} more not listed.")
    return "\n".join(lines)


def _describe_row(row: dict, columns: list[str], zh: bool, *, compact: bool = False) -> str:
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
        elif zh:
            parts.append(f"{col} 为 **{text}**")
        else:
            parts.append(f"{col} is **{text}**")
    sep = "，" if zh else ", "
    empty = "（空行）" if zh else "(empty row)"
    if compact:
        return sep.join(parts) if parts else empty
    if not parts:
        return empty
    return sep.join(parts) + ("。" if zh else ".")
