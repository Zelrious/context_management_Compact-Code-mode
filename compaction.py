"""Selective, structured compaction for long agent conversations.

Raw messages remain in the local session archive; the model sees a checkpoint,
all user requirements, and the most recent complete turns.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

DEFAULT_KEEP_TURNS = 2
DEFAULT_TRIGGER_CHARS = 24_000
RAW_TOOL_CHARS_TRIGGER = 12_000
MAX_CHECKPOINT_FIELD_CHARS = 900
MAX_FAILURES = 8
MAX_FINDINGS = 6


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(content)


def message_chars(messages: list[BaseMessage]) -> int:
    total = 0
    for message in messages:
        total += len(_content_text(message.content))
        if isinstance(message, AIMessage):
            total += len(json.dumps(message.tool_calls, ensure_ascii=False, default=str))
    return total


def raw_tool_chars(messages: list[BaseMessage]) -> int:
    return sum(len(_content_text(m.content)) for m in messages if isinstance(m, ToolMessage))


def should_compact(messages: list[BaseMessage], threshold_chars: int = DEFAULT_TRIGGER_CHARS) -> tuple[bool, str]:
    chars = message_chars(messages)
    tool_chars = raw_tool_chars(messages)
    if chars >= threshold_chars:
        return True, f"active context đạt {chars:,} ký tự, vượt ngưỡng {threshold_chars:,}"
    if tool_chars >= RAW_TOOL_CHARS_TRIGGER:
        return True, f"tool outputs tích lũy {tool_chars:,} ký tự, vượt ngưỡng {RAW_TOOL_CHARS_TRIGGER:,}"
    return False, "chưa cần compact"


def _user_messages(messages: list[BaseMessage]) -> list[str]:
    return [
        _content_text(m.content).strip()[:MAX_CHECKPOINT_FIELD_CHARS]
        for m in messages
        if isinstance(m, HumanMessage) and _content_text(m.content).strip()
    ]


def _tool_summary(message: ToolMessage) -> tuple[str | None, str | None, str | None]:
    """Return compact file/status/error details, never a large raw content field."""
    text = _content_text(message.content)
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        payload = None
    if isinstance(payload, dict):
        path = str(payload.get("path", "")) or None
        status = str(payload.get("status", "")) or None
        error = payload.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            error = f"error code {str(code)[:80]}" if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", code) else "tool error"
        elif error:
            error = "tool error"
        if not error and payload.get("ok") is False:
            code = payload.get("code")
            error = f"error code {str(code)[:80]}" if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", code) else "tool returned ok=false"
        if not error and payload.get("exit_code") not in (None, 0):
            error = f"exit_code={payload.get('exit_code')}"
        # Preserve numeric counters only; arbitrary summary/error text may contain raw logs.
        fields = []
        for key in ("count", "error_count", "warning_count", "files_read"):
            value = payload.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                fields.append(f"{key}={value}")
        if isinstance(payload.get("truncated"), bool):
            fields.append(f"truncated={payload['truncated']}")
        finding = ", ".join(fields) or None
        return path, str(error)[:MAX_CHECKPOINT_FIELD_CHARS] if error else None, finding
    if message.status == "error":
        return None, "tool returned a non-JSON error; raw output omitted", None
    return None, None, None


@dataclass
class ContextCheckpoint:
    version: int
    goal: str
    user_requirements: list[str]
    current_state: str
    unresolved_issues: list[str]
    findings: list[str]
    failed_attempts: list[str]
    relevant_files: list[str]
    next_action: str
    archived_message_count: int
    archived_tool_chars: int
    created_at: str

    def render(self) -> str:
        def lines(items: list[str]) -> str:
            return "\n".join(f"- {item}" for item in items) if items else "- Chưa có dữ liệu ghi nhận."

        return (
            "CHECKPOINT COMPACTION — dữ liệu cũ đã được lưu ở archive của session.\n"
            f"Mục tiêu: {self.goal or 'Tiếp tục nhiệm vụ gần nhất.'}\n"
            f"Yêu cầu/ràng buộc đã nêu:\n{lines(self.user_requirements)}\n"
            f"Trạng thái gần nhất: {self.current_state or 'Chưa có kết luận của agent.'}\n"
            f"Vấn đề chưa giải quyết:\n{lines(self.unresolved_issues)}\n"
            f"Phát hiện đã xác nhận:\n{lines(self.findings)}\n"
            f"Lần thử lỗi/không thành công:\n{lines(self.failed_attempts)}\n"
            f"Tệp liên quan:\n{lines(self.relevant_files)}\n"
            f"Bước tiếp theo: {self.next_action}\n"
            "Tiếp tục từ checkpoint này; không coi log đã lược bỏ là bằng chứng mới."
        )


@dataclass
class CompactResult:
    messages: list[BaseMessage]
    checkpoint: ContextCheckpoint
    archived_messages: list[BaseMessage]
    before_chars: int
    after_chars: int


def _last_complete_turn_start(messages: list[BaseMessage], keep_turns: int) -> int:
    starts = [i for i, message in enumerate(messages) if isinstance(message, HumanMessage)]
    if len(starts) <= keep_turns:
        return len(messages)
    return starts[-keep_turns]


def compact_messages(messages: list[BaseMessage], keep_turns: int = DEFAULT_KEEP_TURNS) -> CompactResult:
    if not messages:
        raise ValueError("Không có message để compact.")
    before = message_chars(messages)
    recent_start = _last_complete_turn_start(messages, keep_turns)
    archived = list(messages[:recent_start])
    users = _user_messages(messages)
    failures: list[str] = []
    findings: list[str] = []
    files: set[str] = set()
    unresolved: list[str] = []
    latest_assistant = ""
    attempted_actions: dict[str, str] = {}

    for message in messages:
        if isinstance(message, AIMessage):
            if not message.tool_calls and message.text.strip():
                safe_lines = [
                    line for line in message.text.splitlines()
                    if len(line) <= 240 and not re.match(r"^\s*\d{4}-\d\d-\d\d[T ]\S*\s+(INFO|WARN|ERROR|DEBUG|TRACE)\b", line, re.I)
                ]
                latest_assistant = "\n".join(safe_lines).strip()[:MAX_CHECKPOINT_FIELD_CHARS]
            for call in message.tool_calls:
                args = call.get("args", {}) if isinstance(call, dict) else {}
                call_id = call.get("id") if isinstance(call, dict) else None
                call_name = call.get("name", "tool") if isinstance(call, dict) else "tool"
                if isinstance(args, dict):
                    action = next((args[key] for key in ("command", "path", "pattern") if isinstance(args.get(key), str)), "")
                    if call_id and action:
                        attempted_actions[call_id] = f"{call_name}: {action[:180]}"
                if isinstance(args, dict):
                    for key, value in args.items():
                        if key in {"path", "file", "files", "pattern", "glob"}:
                            for match in re.findall(r"[\w./*-]+\.(?:log|txt|md|py|json|csv)", str(value)):
                                files.add(match[:180])
        elif isinstance(message, ToolMessage):
            path, error, finding = _tool_summary(message)
            if path:
                files.add(path)
            tool_name = message.name or "tool"
            if error:
                action = attempted_actions.get(message.tool_call_id or "")
                attempt = f"{action or tool_name}: {error}"
                failures.append(attempt[:MAX_CHECKPOINT_FIELD_CHARS])
                unresolved.append(attempt[:MAX_CHECKPOINT_FIELD_CHARS])
            if finding:
                findings.append(f"{tool_name}: {finding}"[:MAX_CHECKPOINT_FIELD_CHARS])

    goal = users[0] if users else ""
    requirements = users[-8:]
    # No LLM-generated inference: if the assistant did not state a concrete next step,
    # direct the next turn to continue from the exact latest user request and evidence.
    next_action = latest_assistant or (
        f"Tiếp tục theo yêu cầu gần nhất của người dùng: {users[-1]}" if users else "Chờ yêu cầu tiếp theo của người dùng."
    )
    if latest_assistant and re.search(r"\b(error|failed|lỗi|chưa|tiếp theo|next)\b", latest_assistant, re.I):
        unresolved.append(latest_assistant[:MAX_CHECKPOINT_FIELD_CHARS])

    checkpoint = ContextCheckpoint(
        version=1,
        goal=goal,
        user_requirements=requirements,
        current_state=latest_assistant,
        unresolved_issues=list(dict.fromkeys(unresolved))[-MAX_FAILURES:],
        findings=list(dict.fromkeys(findings))[-MAX_FINDINGS:],
        failed_attempts=list(dict.fromkeys(failures))[-MAX_FAILURES:],
        relevant_files=sorted(files)[:20],
        next_action=next_action,
        archived_message_count=len(archived),
        archived_tool_chars=raw_tool_chars(archived),
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    compacted = [HumanMessage(content=checkpoint.render()), *messages[recent_start:]]
    return CompactResult(
        messages=compacted,
        checkpoint=checkpoint,
        archived_messages=archived,
        before_chars=before,
        after_chars=message_chars(compacted),
    )
