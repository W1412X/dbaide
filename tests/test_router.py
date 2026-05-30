import pytest

from dbaide.agent.router import TaskRouter
from dbaide.models import TaskType
from tests.llm_mock import RoutingMockLLM


@pytest.fixture
def router() -> TaskRouter:
    routes = {
        "数据质量": "data_profile",
        "profile this table": "data_profile",
        "空值率": "data_profile",
        "explain this query": "sql_diagnose",
        "为什么这么慢": "sql_diagnose",
        "执行计划": "sql_diagnose",
        "有哪些表": "schema_explore",
        "表结构": "schema_explore",
        "schema": "schema_explore",
        "字段": "schema_explore",
        "查一下有多少用户": "data_query",
        "统计订单数量": "data_query",
        "select * from users": "data_query",
        "改写这个SQL": "sql_rewrite",
    }
    return TaskRouter(RoutingMockLLM(routes))


class TestTaskRouter:
    def test_profile_keywords(self, router: TaskRouter):
        assert router.route("数据质量") == TaskType.DATA_PROFILE
        assert router.route("profile this table") == TaskType.DATA_PROFILE
        assert router.route("空值率") == TaskType.DATA_PROFILE

    def test_diagnose_keywords(self, router: TaskRouter):
        assert router.route("explain this query") == TaskType.SQL_DIAGNOSE
        assert router.route("为什么这么慢") == TaskType.SQL_DIAGNOSE
        assert router.route("执行计划") == TaskType.SQL_DIAGNOSE

    def test_schema_explore_keywords(self, router: TaskRouter):
        assert router.route("有哪些表") == TaskType.SCHEMA_EXPLORE
        assert router.route("表结构") == TaskType.SCHEMA_EXPLORE
        assert router.route("schema") == TaskType.SCHEMA_EXPLORE
        assert router.route("字段") == TaskType.SCHEMA_EXPLORE

    def test_data_query_keywords(self, router: TaskRouter):
        assert router.route("查一下有多少用户") == TaskType.DATA_QUERY
        assert router.route("统计订单数量") == TaskType.DATA_QUERY
        assert router.route("select * from users") == TaskType.DATA_QUERY

    def test_rewrite_keywords(self, router: TaskRouter):
        assert router.route("改写这个SQL") == TaskType.SQL_REWRITE

    def test_unknown_falls_back(self, router: TaskRouter):
        result = router.route("hello world")
        assert result == TaskType.UNKNOWN
