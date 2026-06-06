"""Database resource-control layer: policy + concurrency budget.

This package centralises *how much* load DBAide may place on a database. It is
deliberately dependency-free (pure stdlib) so it can wrap every adapter without
pulling in a connection-pool library.
"""

from __future__ import annotations

from dbaide.db.budget import BudgetStats, QueryBudget
from dbaide.db import budget as budget_registry
from dbaide.db import connection_pool as connection_pool_registry
from dbaide.db.policy import (
    DEFAULT_LOAD_PROFILE,
    LOAD_PROFILE_NAMES,
    LOAD_PROFILES,
    PROFILE_MODES,
    ResourcePolicy,
    resolve_policy,
)

__all__ = [
    "ResourcePolicy",
    "resolve_policy",
    "QueryBudget",
    "BudgetStats",
    "budget_registry",
    "connection_pool_registry",
    "LOAD_PROFILES",
    "LOAD_PROFILE_NAMES",
    "DEFAULT_LOAD_PROFILE",
    "PROFILE_MODES",
]
