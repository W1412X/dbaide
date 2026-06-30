"""Desktop service action registry.

The UI and workers call ``DesktopService.dispatch(action, payload)``. Keeping the
action table here avoids turning the service facade itself into both a dispatcher
and a feature catalog, and gives future domain services one place to register
their public actions.
"""

from __future__ import annotations

from typing import Any, Callable


Handler = Callable[[dict[str, Any]], Any]


ACTION_METHODS: tuple[tuple[str, str], ...] = (
    ("bootstrap", "bootstrap"),
    ("build_assets", "build_assets"),
    ("project_instance", "project_instance"),
    ("refresh_instance", "refresh_instance"),
    ("enrich_table", "enrich_table"),
    ("list_databases", "list_databases"),
    ("schema_tree", "schema_tree"),
    ("search_assets", "search_assets"),
    ("read_asset", "read_asset"),
    ("save_connection", "save_connection"),
    ("delete_connection", "delete_connection"),
    ("save_model", "save_model"),
    ("delete_model", "delete_model"),
    ("set_default_model", "set_default_model"),
    ("ask", "ask"),
    ("test_connection", "test_connection"),
    ("validate_sql", "validate_sql"),
    ("execute_sql", "execute_sql"),
    ("browse_table", "browse_table"),
    ("count_table", "count_table"),
    ("table_ddl", "table_ddl"),
    ("explain_sql", "explain_sql"),
    ("list_history", "list_history"),
    ("delete_history", "delete_history"),
    ("list_sessions", "list_sessions"),
    ("load_session", "load_session"),
    ("create_session", "create_session"),
    ("rename_session", "rename_session"),
    ("delete_session", "delete_session"),
    ("asset_markdown", "asset_markdown"),
    ("test_model_profile", "test_model_profile"),
    ("list_joins", "list_joins"),
    ("add_join", "add_join"),
    ("update_join", "update_join"),
    ("delete_join", "delete_join"),
    ("list_annotations", "list_annotations"),
    ("add_annotation", "add_annotation"),
    ("delete_annotation", "delete_annotation"),
    ("list_dashboards", "list_dashboards"),
    ("get_dashboard", "get_dashboard"),
    ("create_dashboard", "create_dashboard"),
    ("rename_dashboard", "rename_dashboard"),
    ("delete_dashboard", "delete_dashboard"),
    ("save_dashboard_layout", "save_dashboard_layout"),
    ("remove_tile", "remove_tile"),
    ("list_saved_questions", "list_saved_questions"),
    ("save_question", "save_question"),
    ("pin_chart", "pin_chart"),
    ("rename_saved_question", "rename_saved_question"),
    ("delete_saved_question", "delete_saved_question"),
    ("refresh_saved_question", "refresh_saved_question"),
    ("list_dashboard_apps", "list_dashboard_apps"),
    ("get_dashboard_app", "get_dashboard_app"),
    ("delete_dashboard_app", "delete_dashboard_app"),
    ("compile_dashboard_app", "compile_dashboard_app"),
    ("build_dashboard_app", "build_dashboard_app"),
    ("run_app_chart", "run_app_chart"),
    ("resource_defaults", "resource_defaults"),
    ("save_resource_defaults", "save_resource_defaults"),
    ("optimize_sql", "optimize_sql"),
    ("recent_queries", "recent_queries"),
    ("export_connection", "export_connection"),
    ("import_connection", "import_connection"),
    ("export_all", "export_all"),
    ("backup_run", "backup_run"),
    ("backup_list", "backup_list"),
    ("backup_delete", "backup_delete"),
    ("export_table_all", "export_table_all"),
)


def build_action_handlers(service: Any) -> dict[str, Handler]:
    return {action: getattr(service, method) for action, method in ACTION_METHODS}
