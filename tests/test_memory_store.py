"""MemoryStore: distils worked examples from effective questions, de-dupes,
retrieves by relevance (with session + popularity boosts), and renders a context
block. Token overlap works for English and Chinese."""

from dbaide.history.memory_store import MemoryStore


def test_add_requires_question_and_sql(tmp_path):
    m = MemoryStore(base_dir=tmp_path)
    assert m.add("c", question="", sql="SELECT 1") is None
    assert m.add("c", question="q", sql="") is None
    assert m.add("c", question="how many users", sql="SELECT COUNT(*) FROM users") is not None
    assert len(m.all("c")) == 1


def test_dedupe_bumps_uses(tmp_path):
    m = MemoryStore(base_dir=tmp_path)
    m.add("c", question="how many users", sql="SELECT COUNT(*) FROM users")
    m.add("c", question="how many users", sql="SELECT COUNT(*) FROM users")
    items = m.all("c")
    assert len(items) == 1 and items[0]["uses"] == 2


def test_relevant_ranks_by_overlap(tmp_path):
    m = MemoryStore(base_dir=tmp_path)
    m.add("c", question="how many employees in the company", sql="SELECT COUNT(*) FROM sys_user")
    m.add("c", question="total revenue last quarter", sql="SELECT SUM(amount) FROM orders")
    hits = m.relevant("c", "number of employees", limit=5)
    assert hits and hits[0]["sql"] == "SELECT COUNT(*) FROM sys_user"


def test_relevant_chinese_overlap(tmp_path):
    m = MemoryStore(base_dir=tmp_path)
    m.add("c", question="公司有多少职工", sql="SELECT COUNT(*) FROM platform.sys_user WHERE del_flag='0'", database="analysis")
    m.add("c", question="上季度总收入", sql="SELECT SUM(amount) FROM orders")
    hits = m.relevant("c", "公司职工人数", limit=5)
    assert hits and "sys_user" in hits[0]["sql"]


def test_session_items_boosted(tmp_path):
    m = MemoryStore(base_dir=tmp_path)
    m.add("c", question="active users count", sql="SELECT COUNT(*) FROM users WHERE active=1", session_id="other")
    m.add("c", question="active users count today", sql="SELECT COUNT(*) FROM users WHERE active=1 AND d=CURRENT_DATE", session_id="S")
    hits = m.relevant("c", "active users count", session_id="S", limit=2)
    # the same-session item should rank first despite the other being a closer string
    assert hits[0]["session_id"] == "S"


def test_unrelated_question_returns_nothing(tmp_path):
    m = MemoryStore(base_dir=tmp_path)
    m.add("c", question="how many employees", sql="SELECT COUNT(*) FROM sys_user")
    assert m.relevant("c", "weather forecast tomorrow") == []


def test_render_block(tmp_path):
    m = MemoryStore(base_dir=tmp_path)
    m.add("c", question="how many employees", sql="SELECT COUNT(*) FROM sys_user", database="analysis")
    block = m.render(m.relevant("c", "employees"))
    assert "how many employees" in block and "SELECT COUNT(*) FROM sys_user" in block and "db: analysis" in block
    assert MemoryStore.render([]) == ""


def test_clear_and_delete(tmp_path):
    m = MemoryStore(base_dir=tmp_path)
    it = m.add("c", question="q one", sql="SELECT 1")
    assert m.delete("c", it["id"]) is True
    m.add("c", question="q two", sql="SELECT 2")
    assert m.clear("c") == 1 and m.all("c") == []


def test_per_connection_isolation(tmp_path):
    m = MemoryStore(base_dir=tmp_path)
    m.add("a", question="qa", sql="SELECT 1")
    m.add("b", question="qb", sql="SELECT 2")
    assert len(m.all("a")) == 1 and len(m.all("b")) == 1
