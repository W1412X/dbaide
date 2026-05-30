from __future__ import annotations

POLICIES = [
    ("Safe", "safe_auto"),
    ("SQL only", "sql_only"),
    ("Inspect", "inspect_only"),
    ("Expert", "expert"),
]

POLICY_LABELS = {value: label for label, value in POLICIES}
