"""ChatSessionStore: sessions group conversation turns, auto-title from the first
question, list most-recent-first, and round-trip through disk."""

from dbaide.history.session_store import ChatSessionStore, DEFAULT_TITLE, make_turn


def test_create_and_load(tmp_path):
    store = ChatSessionStore(base_dir=tmp_path)
    s = store.create("shop")
    assert s["session_id"] and s["title"] == DEFAULT_TITLE and s["turns"] == []
    loaded = store.load("shop", s["session_id"])
    assert loaded == s


def test_append_turn_autotitles_from_first_question(tmp_path):
    store = ChatSessionStore(base_dir=tmp_path)
    s = store.create("shop")
    store.append_turn("shop", s["session_id"], make_turn(
        question="Which cities have the most paying users?",
        answer_markdown="Tokyo leads.", selected_sql="SELECT 1", workflow_id="wf1",
    ))
    loaded = store.load("shop", s["session_id"])
    assert loaded["title"] == "Which cities have the most paying users?"
    assert len(loaded["turns"]) == 1
    assert loaded["turns"][0]["answer_markdown"] == "Tokyo leads."
    # a second turn does not re-title
    store.append_turn("shop", s["session_id"], make_turn(question="and the totals?"))
    assert store.load("shop", s["session_id"])["title"] == "Which cities have the most paying users?"


def test_long_title_truncated(tmp_path):
    store = ChatSessionStore(base_dir=tmp_path)
    s = store.create("shop")
    store.append_turn("shop", s["session_id"], make_turn(question="x" * 200))
    title = store.load("shop", s["session_id"])["title"]
    assert len(title) <= 60 and title.endswith("…")


def test_list_sessions_recent_first(tmp_path):
    store = ChatSessionStore(base_dir=tmp_path)
    a = store.create("shop")
    b = store.create("shop")
    # touch `a` last so it sorts first by updated_at
    store.append_turn("shop", b["session_id"], make_turn(question="b q"))
    store.append_turn("shop", a["session_id"], make_turn(question="a q"))
    sessions = store.list_sessions("shop")
    assert [x["session_id"] for x in sessions][:2] == [a["session_id"], b["session_id"]]
    top = sessions[0]
    assert top["turn_count"] == 1 and top["last_question"] == "a q"
    assert "trace" not in top  # summaries carry no turn bodies


def test_rename_and_delete(tmp_path):
    store = ChatSessionStore(base_dir=tmp_path)
    s = store.create("shop")
    assert store.rename("shop", s["session_id"], "Renamed") is True
    assert store.load("shop", s["session_id"])["title"] == "Renamed"
    assert store.delete("shop", s["session_id"]) is True
    assert store.load("shop", s["session_id"]) is None
    assert store.list_sessions("shop") == []


def test_per_connection_isolation(tmp_path):
    store = ChatSessionStore(base_dir=tmp_path)
    store.create("shop")
    store.create("analytics")
    assert len(store.list_sessions("shop")) == 1
    assert len(store.list_sessions("analytics")) == 1


def test_append_to_missing_session_returns_none(tmp_path):
    store = ChatSessionStore(base_dir=tmp_path)
    assert store.append_turn("shop", "nope", make_turn(question="q")) is None


def test_attachments_and_schema_scope_persisted(tmp_path):
    """Composer attachments + schema_scope round-trip through the session store."""
    store = ChatSessionStore(base_dir=tmp_path)
    s = store.create("shop")
    attachments = [
        {"kind": "database", "path": "local.shop", "name": "shop", "database": "shop"},
        {"kind": "table", "path": "local.shop.orders", "name": "orders", "database": "shop"},
    ]
    scope = {"databases": ["shop"], "tables": [{"database": "shop", "table": "orders"}]}
    store.append_turn("shop", s["session_id"], make_turn(
        question="How many orders last month?",
        attachments=attachments,
        schema_scope=scope,
    ))
    loaded = store.load("shop", s["session_id"])
    turn = loaded["turns"][0]
    assert turn["attachments"] == attachments
    assert turn["schema_scope"] == scope


def test_make_turn_defaults_empty_attachments():
    """Backward-compatible: turns without attachments get empty defaults."""
    turn = make_turn(question="plain question")
    assert turn["attachments"] == []
    assert turn["schema_scope"] == {}
