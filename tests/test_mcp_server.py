"""Tests for the MCP server dual-mode tool exposure."""

from __future__ import annotations

import json

import pytest

import dbaide.mcp_server as mcp


class TestToolDefinitions:
    """Verify all tool definitions are valid MCP inputSchema."""

    def test_ask_tool_schema(self):
        schema = mcp.ASK_TOOL["inputSchema"]
        assert schema["type"] == "object"
        assert "question" in schema["properties"]
        assert "question" in schema["required"]

    def test_atomic_tools_have_valid_schema(self):
        for tool in mcp._ATOMIC_TOOLS:
            assert "name" in tool
            assert "description" in tool
            schema = tool["inputSchema"]
            assert schema["type"] == "object"
            assert "properties" in schema
            for prop_name, prop in schema["properties"].items():
                assert "type" in prop or "$ref" in prop, (
                    f"{tool['name']}.{prop_name} missing type"
                )

    def test_all_atomic_tools_have_handlers(self):
        for tool in mcp._ATOMIC_TOOLS:
            assert tool["name"] in mcp._TOOL_HANDLERS, (
                f"No handler for {tool['name']}"
            )

    def test_ask_has_handler(self):
        assert "ask" in mcp._TOOL_HANDLERS

    def test_all_tools_have_readonly_annotation(self):
        all_tools = [mcp.ASK_TOOL] + mcp._ATOMIC_TOOLS
        for tool in all_tools:
            assert "annotations" in tool, f"{tool['name']} missing annotations"
            assert tool["annotations"].get("readOnlyHint") is True, (
                f"{tool['name']} missing readOnlyHint"
            )


class TestModeFiltering:
    """Verify tools/list respects the active mode."""

    def test_full_mode(self):
        mcp._active_mode = "full"
        tools = mcp.handle_tools_list({})["tools"]
        names = {t["name"] for t in tools}
        assert "ask" in names
        assert "execute_sql" in names
        assert "list_connections" in names

    def test_ask_mode(self):
        mcp._active_mode = "ask"
        tools = mcp.handle_tools_list({})["tools"]
        names = {t["name"] for t in tools}
        assert names == {"ask"}

    def test_tools_mode(self):
        mcp._active_mode = "tools"
        tools = mcp.handle_tools_list({})["tools"]
        names = {t["name"] for t in tools}
        assert "ask" not in names
        assert "execute_sql" in names
        assert "list_databases" in names

    def test_mode_enforcement_ask(self):
        mcp._active_mode = "ask"
        result = mcp.handle_tools_call({"name": "execute_sql", "arguments": {"sql": "SELECT 1"}})
        assert result.get("isError") is True
        assert "not available in 'ask' mode" in result["content"][0]["text"]

    def test_mode_enforcement_tools(self):
        mcp._active_mode = "tools"
        result = mcp.handle_tools_call({"name": "ask", "arguments": {"question": "test"}})
        assert result.get("isError") is True
        assert "not available in 'tools' mode" in result["content"][0]["text"]

    def test_unknown_tool_returns_error(self):
        mcp._active_mode = "full"
        result = mcp.handle_tools_call({"name": "nonexistent", "arguments": {}})
        assert result.get("isError") is True
        assert "Unknown tool" in result["content"][0]["text"]


class TestInitialize:
    def test_initialize_response(self):
        result = mcp.handle_initialize({})
        assert result["protocolVersion"] == mcp.PROTOCOL_VERSION
        assert result["serverInfo"]["name"] == "dbaide"
        assert "tools" in result["capabilities"]

    def test_initialize_has_instructions(self):
        result = mcp.handle_initialize({})
        assert "instructions" in result
        assert "read-only" in result["instructions"]

    def test_ping(self):
        result = mcp.handle_ping({})
        assert result == {}


class TestSerialize:
    def test_primitives(self):
        assert mcp._serialize(42) == 42
        assert mcp._serialize("hello") == "hello"
        assert mcp._serialize(True) is True
        assert mcp._serialize(None) is None

    def test_dict(self):
        assert mcp._serialize({"a": 1}) == {"a": 1}

    def test_list(self):
        assert mcp._serialize([1, 2]) == [1, 2]

    def test_object_with_dict(self):
        class Obj:
            def __init__(self):
                self.x = 1
                self._private = 2
        result = mcp._serialize(Obj())
        assert result == {"x": 1}

    def test_fallback_to_str(self):
        from decimal import Decimal
        assert mcp._serialize(Decimal("1.5")) == "1.5"


class TestHandlerValidation:
    """Verify handlers reject missing required args."""

    def test_execute_sql_requires_sql(self):
        mcp._active_mode = "full"
        result = mcp.handle_execute_sql({})
        assert result.get("isError") is True
        assert "sql is required" in result["content"][0]["text"]

    def test_describe_table_requires_table(self):
        result = mcp.handle_describe_table({})
        assert result.get("isError") is True
        assert "table is required" in result["content"][0]["text"]

    def test_column_stats_requires_table(self):
        result = mcp.handle_column_stats({})
        assert result.get("isError") is True
        assert "table is required" in result["content"][0]["text"]

    def test_sample_rows_requires_table(self):
        result = mcp.handle_sample_rows({})
        assert result.get("isError") is True
        assert "table is required" in result["content"][0]["text"]

    def test_validate_sql_requires_sql(self):
        result = mcp.handle_validate_sql({})
        assert result.get("isError") is True

    def test_explain_sql_requires_sql(self):
        result = mcp.handle_explain_sql({})
        assert result.get("isError") is True
