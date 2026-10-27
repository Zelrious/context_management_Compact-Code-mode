"""Tests cho phần UI và tích hợp (Luân): tool schemas, compact, nạp session, checkpoint, Code Mode."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent import (
    CODE_MODE_TOOLS,
    TOOLS,
    capabilities,
    selected_tools,
    system_prompt,
    compact_messages,
    should_compact,
    message_chars,
)
from config import (
    AUTO_COMPACT_ENABLED,
    CODE_MODE_MAX_CODE_CHARS,
    CODE_MODE_MAX_OUTPUT_CHARS,
    COMPACT_TRIGGER_CHARS,
    COMPACT_TRIGGER_MESSAGES,
    DEFAULT_MODE,
)
from observer import tool_schema
from prompts import CODE_MODE_PROMPT, COMPACTION_PROMPT, CAPABILITY_PROMPT, build_system_prompt


# ================================================================ 1. Tool schemas theo chế độ


class TestToolSchemasByMode:
    """Kiểm tra tool schemas đúng theo từng chế độ."""

    def test_native_mode_tools(self):
        """Native mode phải có read_file, write_file, bash, search_memory, remember_convention."""
        tools = selected_tools("native")
        names = [t.name for t in tools]
        assert names == ["read_file", "write_file", "bash", "search_memory", "remember_convention"]

    def test_code_mode_tools(self):
        """Code Mode phải có run_code, search_memory, remember_convention — không có read_file, write_file, bash."""
        tools = selected_tools("code")
        names = [t.name for t in tools]
        assert names == ["run_code", "search_memory", "remember_convention"]
        assert "read_file" not in names
        assert "write_file" not in names
        assert "bash" not in names

    def test_native_tools_not_expose_run_code(self):
        """Native mode không lộ run_code."""
        names = [t.name for t in selected_tools("native")]
        assert "run_code" not in names

    def test_code_mode_not_expose_file_tools(self):
        """Code Mode không lộ read_file, write_file, bash."""
        names = [t.name for t in selected_tools("code")]
        for forbidden in ("read_file", "write_file", "bash"):
            assert forbidden not in names

    def test_tool_schemas_have_name_description_parameters(self):
        """Mỗi tool schema phải có name, description, parameters."""
        for mode in ("native", "code"):
            for t in selected_tools(mode):
                schema = tool_schema(t)
                assert "name" in schema
                assert "description" in schema
                assert "parameters" in schema

    def test_capabilities_returns_correct_tool_names(self, lab_dirs):
        """capabilities() trả danh sách tool đúng theo chế độ."""
        native_caps = capabilities("native")
        code_caps = capabilities("code")
        assert native_caps["tools"] == ["read_file", "write_file", "bash", "search_memory", "remember_convention"]
        assert code_caps["tools"] == ["run_code", "search_memory", "remember_convention"]


# ================================================================ 2. System prompt theo chế độ


class TestSystemPromptByMode:
    """Kiểm tra system prompt có hướng dẫn riêng cho từng chế độ."""

    def test_native_prompt_has_capability_block(self, lab_dirs):
        """Native mode phải có CAPABILITY_PROMPT (workspace, bash, read_file)."""
        prompt = system_prompt("native")
        assert "read_file" in prompt
        assert "bash chạy lệnh" in prompt

    def test_code_prompt_has_code_mode_block(self, lab_dirs):
        """Code Mode phải có hướng dẫn Code Mode."""
        prompt = system_prompt("code")
        assert "run_code" in prompt
        assert "Code Mode" in prompt
        assert "tools.list_files" in prompt or "Python subset" in prompt

    def test_code_prompt_not_expose_native_capability(self, lab_dirs):
        """Code Mode prompt không có hướng dẫn bash/read_file của native."""
        prompt = system_prompt("code")
        assert "bash chạy lệnh" not in prompt

    def test_both_modes_have_compaction_prompt(self, lab_dirs):
        """Cả hai chế độ đều có COMPACTION_PROMPT."""
        for mode in ("native", "code"):
            prompt = system_prompt(mode)
            assert "Compacted context" in prompt or "checkpoint" in prompt.lower()

    def test_code_mode_prompt_includes_limits(self):
        """Code Mode prompt phải hiển thị giới hạn code và output."""
        prompt = build_system_prompt("", "", mode="code")
        assert str(CODE_MODE_MAX_CODE_CHARS) in prompt or f"{CODE_MODE_MAX_CODE_CHARS:,}" in prompt
        assert str(CODE_MODE_MAX_OUTPUT_CHARS) in prompt or f"{CODE_MODE_MAX_OUTPUT_CHARS:,}" in prompt


# ================================================================ 3. Compact kích hoạt trước lượt kế tiếp


class TestCompactBeforeNextTurn:
    """Kiểm tra compact tự động xảy ra trước lượt gọi model kế tiếp."""

    def test_should_compact_when_over_threshold(self):
        """should_compact trả True khi context vượt ngưỡng."""
        msgs = [HumanMessage(content="x" * 30000)]
        needed, reason = should_compact(msgs, 24000)
        assert needed is True
        assert "vượt ngưỡng" in reason

    def test_should_not_compact_when_under_threshold(self):
        """should_compact trả False khi context chưa vượt ngưỡng."""
        msgs = [HumanMessage(content="hello")]
        needed, reason = should_compact(msgs, 24000)
        assert needed is False

    def test_compact_messages_returns_checkpoint(self):
        """compact_messages trả về checkpoint có cấu trúc đúng."""
        msgs = [
            HumanMessage(content="Tìm lỗi race condition trong auth service."),
            AIMessage(content="", tool_calls=[{"name": "bash", "args": {"command": "grep -r race src/"}, "id": "c1", "type": "tool_call"}]),
            ToolMessage(content=json.dumps({"ok": True, "exit_code": 0, "stdout": "result " * 500}), name="bash", tool_call_id="c1"),
            AIMessage(content="Đã tìm thấy pattern nghi ngờ."),
            HumanMessage(content="Kiểm tra thêm file auth.ts."),
            AIMessage(content="Tiếp tục kiểm tra."),
        ]
        result = compact_messages(msgs)
        assert hasattr(result, "checkpoint")
        assert hasattr(result, "messages")
        assert hasattr(result, "archived_messages")
        assert result.before_chars > 0
        assert result.after_chars > 0
        cp = result.checkpoint
        assert hasattr(cp, "goal")
        assert hasattr(cp, "user_requirements")
        assert hasattr(cp, "next_action")

    def test_message_chars_counts_correctly(self):
        """message_chars ước lượng đúng ký tự."""
        msgs = [HumanMessage(content="hello"), AIMessage(content="world")]
        chars = message_chars(msgs)
        assert chars >= 10  # "hello" + "world" = 10

    def test_compact_reduces_context_size(self):
        """Compact giảm kích thước context so với trước."""
        msgs = [HumanMessage(content="Yêu cầu ban đầu")]
        # Thêm nhiều lượt để tạo context dài
        for i in range(6):
            msgs.append(AIMessage(content="", tool_calls=[{"name": "bash", "args": {"command": f"cmd {i}"}, "id": f"c{i}", "type": "tool_call"}]))
            msgs.append(ToolMessage(content=json.dumps({"ok": True, "stdout": f"output {'x' * 500}"}), name="bash", tool_call_id=f"c{i}"))
            msgs.append(AIMessage(content=f"Kết quả lần {i}: vẫn chưa tìm ra."))
            if i % 2 == 0:
                msgs.append(HumanMessage(content=f"Thử cách khác {i}."))
        result = compact_messages(msgs)
        assert result.after_chars < result.before_chars


# ================================================================ 4. Nạp session dài mẫu


class TestLoadLongSession:
    """Kiểm tra hàm load_long_session_demo tạo đúng history."""

    def test_load_creates_history(self):
        """load_long_session_demo tạo model_history có nhiều message."""
        from app import load_long_session_demo
        import streamlit as st

        # Mock session state
        class FakeSessionState(dict):
            def __getattr__(self, key):
                return self[key]
            def __setattr__(self, key, value):
                self[key] = value
            def setdefault(self, key, default=None):
                if key not in self:
                    self[key] = default
                return self[key]

        ss = FakeSessionState()
        ss["model_history"] = []
        ss["compaction_archive"] = []
        ss["compaction_checkpoint"] = None
        ss["compaction_stats"] = None
        ss["observer"] = type("MockObserver", (), {"sync_messages": lambda self, msgs: None})()

        with patch.object(st, "session_state", ss):
            load_long_session_demo()

        assert len(ss["model_history"]) > 10
        # Có cả HumanMessage, AIMessage, ToolMessage
        types = {type(m).__name__ for m in ss["model_history"]}
        assert "HumanMessage" in types
        assert "AIMessage" in types
        assert "ToolMessage" in types

    def test_long_session_has_errors_and_success(self):
        """Session mẫu có cả tool result lỗi và thành công."""
        from app import load_long_session_demo
        import streamlit as st

        class FakeSessionState(dict):
            def __getattr__(self, key):
                return self[key]
            def __setattr__(self, key, value):
                self[key] = value
            def setdefault(self, key, default=None):
                if key not in self:
                    self[key] = default
                return self[key]

        ss = FakeSessionState()
        ss["model_history"] = []
        ss["compaction_archive"] = []
        ss["compaction_checkpoint"] = None
        ss["compaction_stats"] = None
        ss["observer"] = type("MockObserver", (), {"sync_messages": lambda self, msgs: None})()

        with patch.object(st, "session_state", ss):
            load_long_session_demo()

        tool_msgs = [m for m in ss["model_history"] if isinstance(m, ToolMessage)]
        statuses = {m.status for m in tool_msgs}
        assert "error" in statuses
        assert "success" in statuses


# ================================================================ 5. Config mặc định


class TestConfig:
    """Kiểm tra config đọc từ biến môi trường với giá trị mặc định hợp lý."""

    def test_default_mode_is_native(self):
        """Chế độ mặc định phải là native."""
        assert DEFAULT_MODE == "native"

    def test_compact_trigger_chars_is_positive(self):
        """Ngưỡng compact phải là số dương."""
        assert COMPACT_TRIGGER_CHARS > 0

    def test_compact_trigger_messages_default_zero(self):
        """Ngưỡng số message mặc định là 0 (tắt)."""
        assert COMPACT_TRIGGER_MESSAGES == 0

    def test_auto_compact_enabled_default_true(self):
        """Auto compact mặc định bật."""
        assert AUTO_COMPACT_ENABLED is True

    def test_code_mode_limits_positive(self):
        """Giới hạn Code Mode phải là số dương."""
        assert CODE_MODE_MAX_CODE_CHARS > 0
        assert CODE_MODE_MAX_OUTPUT_CHARS > 0


# ================================================================ 6. Compact thủ công qua hàm


class TestManualCompact:
    """Kiểm tra hàm compact_current_session hoạt động đúng."""

    def test_compact_empty_session_returns_none(self):
        """Compact session rỗng trả None."""
        from app import compact_current_session
        import streamlit as st

        class FakeSessionState(dict):
            def __getattr__(self, key):
                return self.get(key)
            def __setattr__(self, key, value):
                self[key] = value
            def setdefault(self, key, default=None):
                if key not in self:
                    self[key] = default
                return self[key]

        ss = FakeSessionState()
        ss["model_history"] = []
        ss["compaction_archive"] = []
        ss["compaction_checkpoint"] = None
        ss["compaction_count"] = 0
        ss["compaction_stats"] = None
        ss["compact_trigger_chars"] = COMPACT_TRIGGER_CHARS
        ss["compact_trigger_messages"] = 0
        ss["observer"] = type("MockObserver", (), {"sync_messages": lambda self, msgs: None})()

        with patch.object(st, "session_state", ss):
            result = compact_current_session(force=True)

        assert result is None

    def test_compact_nonempty_session_returns_stats(self):
        """Compact session có dữ liệu trả stats."""
        from app import compact_current_session
        import streamlit as st

        class FakeSessionState(dict):
            def __getattr__(self, key):
                return self.get(key)
            def __setattr__(self, key, value):
                self[key] = value
            def setdefault(self, key, default=None):
                if key not in self:
                    self[key] = default
                return self[key]

        ss = FakeSessionState()
        # Tạo session có dữ liệu
        history = [HumanMessage(content="Tìm lỗi.")]
        for i in range(4):
            history.append(AIMessage(content="", tool_calls=[{"name": "bash", "args": {"command": f"grep error {i}"}, "id": f"t{i}", "type": "tool_call"}]))
            history.append(ToolMessage(content=json.dumps({"ok": True, "stdout": "x" * 200}), name="bash", tool_call_id=f"t{i}"))
            history.append(AIMessage(content=f"Kết quả {i}."))
            history.append(HumanMessage(content=f"Tiếp {i}."))

        ss["model_history"] = history
        ss["compaction_archive"] = []
        ss["compaction_checkpoint"] = None
        ss["compaction_count"] = 0
        ss["compaction_stats"] = None
        ss["compact_trigger_chars"] = COMPACT_TRIGGER_CHARS
        ss["compact_trigger_messages"] = 0
        ss["observer"] = type("MockObserver", (), {"sync_messages": lambda self, msgs: None})()

        with patch.object(st, "session_state", ss):
            result = compact_current_session(force=True)

        assert result is not None
        assert "before_chars" in result
        assert "after_chars" in result
        assert "archived_messages" in result
        assert result["before_chars"] >= result["after_chars"]


# ================================================================ 7. Code Mode execute_code


class TestCodeModeExecution:
    """Kiểm tra execute_code hoạt động (dùng code thực, không mock API)."""

    def test_execute_simple_code(self, lab_dirs):
        """execute_code chạy được chương trình đơn giản."""
        from agent import execute_code
        result = execute_code('result = {"total": 42}')
        assert result["ok"] is True
        assert result["result"]["total"] == 42

    def test_execute_code_with_error(self, lab_dirs):
        """execute_code trả error khi code có lỗi."""
        from agent import execute_code
        result = execute_code('result = undefined_var')
        assert result["ok"] is False
        assert "error" in result

    def test_execute_code_no_result_var(self, lab_dirs):
        """execute_code yêu cầu biến result."""
        from agent import execute_code
        result = execute_code('x = 42')
        assert result["ok"] is False

    def test_execute_code_reads_logs(self, lab_dirs):
        """execute_code đọc được log files trong workspace."""
        from agent import execute_code
        import paths
        code = '''files = tools.list_files("**/*.log")
result = {"count": len(files)}'''
        result = execute_code(code, workspace=paths.WORKSPACE_DIR)
        assert result["ok"] is True
        assert result["result"]["count"] >= 0


# ================================================================ 8. Hiển thị checkpoint


class TestCheckpointDisplay:
    """Kiểm tra checkpoint có đủ các trường cần thiết để UI hiển thị."""

    def test_checkpoint_has_all_fields(self):
        """Checkpoint từ compact_messages có đủ các trường cho UI."""
        msgs = [
            HumanMessage(content="Phân tích log lỗi trong auth service."),
            AIMessage(content="", tool_calls=[{"name": "bash", "args": {"command": "cat logs/auth.log"}, "id": "c1", "type": "tool_call"}]),
            ToolMessage(content=json.dumps({"ok": True, "stdout": "ERROR: token expired\n" * 100}), name="bash", tool_call_id="c1"),
            AIMessage(content="Lỗi token expired xảy ra 100 lần."),
            HumanMessage(content="Tìm nguyên nhân gốc."),
            AIMessage(content="Tiếp tục điều tra."),
        ]
        result = compact_messages(msgs)
        cp_dict = result.checkpoint.__dict__

        # Tất cả các trường cần cho UI
        required_fields = [
            "goal", "user_requirements", "current_state",
            "unresolved_issues", "findings", "failed_attempts",
            "relevant_files", "next_action",
            "archived_message_count", "archived_tool_chars", "created_at",
        ]
        for field in required_fields:
            assert field in cp_dict, f"Thiếu trường {field} trong checkpoint"

    def test_checkpoint_goal_matches_first_user_message(self):
        """goal trong checkpoint lấy từ message đầu tiên của user."""
        msgs = [
            HumanMessage(content="Tìm nguyên nhân lỗi timeout."),
            AIMessage(content="Đang kiểm tra."),
            HumanMessage(content="Kiểm tra thêm."),
            AIMessage(content="Tiếp tục."),
        ]
        result = compact_messages(msgs)
        assert "timeout" in result.checkpoint.goal.lower()

    def test_checkpoint_render_produces_text(self):
        """checkpoint.render() tạo ra text có cấu trúc."""
        msgs = [
            HumanMessage(content="Debug lỗi."),
            AIMessage(content="Đã kiểm tra."),
            HumanMessage(content="Thêm thông tin."),
            AIMessage(content="OK."),
        ]
        result = compact_messages(msgs)
        rendered = result.checkpoint.render()
        assert "CHECKPOINT COMPACTION" in rendered
        assert "Mục tiêu" in rendered
