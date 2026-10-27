"""B1 acceptance checks for context retention without a live model/API."""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from compaction import compact_messages, message_chars


def _turn(index: int, *, failed: bool = False) -> list:
    call_id = f"call-{index}"
    command = (
        "python skills/csv-quality/scripts/check_csv.py --input data/missing.csv --max-hours 8"
        if failed
        else "python skills/csv-quality/scripts/check_csv.py --input data/workload.csv --max-hours 8"
    )
    result = {
        "ok": not failed,
        "exit_code": 2 if failed else 0,
        "path": "data/missing.csv" if failed else "data/workload.csv",
        "error": {"code": "INPUT_NOT_FOUND"} if failed else None,
        "row_count": 6,
        "stdout": "RAW_TOOL_OUTPUT_SHOULD_BE_ARCHIVED\n" * 300,
    }
    answer = (
        "Lần chạy thất bại do sai đường dẫn; chưa có kết quả. Bước tiếp theo: chạy lại với data/workload.csv."
        if failed
        else "Đã phân tích CSV với ngưỡng 8 giờ. Bước tiếp theo: ghi báo cáo output/csv-quality.md và giữ nguyên file nguồn."
    )
    return [
        AIMessage(content="", tool_calls=[{"name": "bash", "args": {"command": command}, "id": call_id, "type": "tool_call"}]),
        ToolMessage(content=json.dumps(result), name="bash", tool_call_id=call_id, status="error" if failed else "success"),
        AIMessage(content=answer),
    ]


def _long_history() -> list:
    return [
        HumanMessage(content=(
            "Phân tích data/workload.csv theo ngưỡng quá tải 8 giờ; tổng đúng bằng 8 không tính quá tải. "
            "Không sửa CSV và liệt kê các dòng bị loại cùng lý do."
        )),
        *_turn(0, failed=True),
        HumanMessage(content="Giữ ngưỡng 8 giờ và chỉ dùng số liệu script trả về."),
        *_turn(1),
        HumanMessage(content="Báo cáo phải ghi rõ các dòng bị loại."),
        *_turn(2),
    ]


def test_short_session_keeps_recent_messages_and_archives_large_tool_output():
    request = HumanMessage(content="Phân tích data/workload.csv theo ngưỡng 8 giờ, không sửa file.")
    history = [request, *_turn(1)]

    result = compact_messages(history)

    assert result.messages[0].type == "ai"
    assert result.messages[0].additional_kwargs["compaction_checkpoint"] is True
    assert any(message is request for message in result.messages)
    active_tool = next(message for message in result.messages if isinstance(message, ToolMessage))
    assert "RAW_TOOL_OUTPUT_SHOULD_BE_ARCHIVED" not in active_tool.content
    assert "compacted" in active_tool.content
    assert any("RAW_TOOL_OUTPUT_SHOULD_BE_ARCHIVED" in message.content for message in result.archived_messages if isinstance(message, ToolMessage))
    assert result.after_chars < result.before_chars


def test_checkpoint_preserves_csv_goal_constraints_failure_file_and_next_step():
    history = _long_history()
    result = compact_messages(history)
    checkpoint = result.checkpoint

    assert checkpoint.goal.startswith("Phân tích data/workload.csv")
    assert any("tổng đúng bằng 8" in requirement for requirement in checkpoint.user_requirements)
    assert any("Không sửa CSV" in requirement for requirement in checkpoint.user_requirements)
    assert any("INPUT_NOT_FOUND" in issue for issue in checkpoint.unresolved_issues)
    assert any("data/missing.csv" in attempt for attempt in checkpoint.failed_attempts)
    assert "data/workload.csv" in checkpoint.relevant_files
    assert "output/csv-quality.md" in checkpoint.next_action
    assert checkpoint.archived_tool_chars > 0
    assert result.after_chars < result.before_chars
    assert [message.content for message in result.messages if isinstance(message, HumanMessage)] == [
        "Giữ ngưỡng 8 giờ và chỉ dùng số liệu script trả về.",
        "Báo cáo phải ghi rõ các dòng bị loại.",
    ]
    assert all("RAW_TOOL_OUTPUT_SHOULD_BE_ARCHIVED" not in str(message.content) for message in result.messages)
    assert any(
        "RAW_TOOL_OUTPUT_SHOULD_BE_ARCHIVED" in message.content
        for message in result.archived_messages
        if isinstance(message, ToolMessage)
    )


def test_recompaction_merges_previous_checkpoint_without_replacing_goal():
    first = compact_messages(_long_history())
    follow_up = [
        *first.messages,
        HumanMessage(content="Thêm thống kê số dòng lỗi chất lượng vào báo cáo; giữ các điều kiện trước đó."),
        AIMessage(content="Bước tiếp theo: thêm thống kê lỗi dữ liệu vào output/csv-quality.md."),
    ]

    second = compact_messages(follow_up, prior_checkpoint=first.checkpoint.__dict__)

    assert second.checkpoint.goal == first.checkpoint.goal
    assert any("tổng đúng bằng 8" in requirement for requirement in second.checkpoint.user_requirements)
    assert any("thống kê số dòng lỗi chất lượng" in requirement for requirement in second.checkpoint.user_requirements)
    assert second.checkpoint.archived_message_count >= first.checkpoint.archived_message_count
    assert second.checkpoint.archived_tool_chars >= first.checkpoint.archived_tool_chars
    assert "thống kê lỗi dữ liệu" in second.checkpoint.next_action
