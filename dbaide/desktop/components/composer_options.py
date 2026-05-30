from __future__ import annotations

POLICIES = [
    ("Safe", "safe_auto"),
    ("SQL only", "sql_only"),
    ("Inspect", "inspect_only"),
    ("Expert", "expert"),
]

POLICY_LABELS = {value: label for label, value in POLICIES}

POLICY_TOOLTIPS: dict[str, str] = {
    "safe_auto": "Validate SQL, run EXPLAIN when supported, and auto-execute read-only queries with row limits.",
    "sql_only": "Generate and validate SQL only; queries are never executed.",
    "inspect_only": "Schema discovery and profiling only; no SQL generation or execution.",
    "expert": "Same guards as Safe, but allows higher row limits and broader read patterns.",
}
