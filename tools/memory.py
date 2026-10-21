"""Agent tools for explicit persistent memory operations."""

import json

from langchain_core.tools import tool

import memory_store


@tool
def search_memory(query: str) -> str:
    """Tìm quy ước project đang hiện hành trong persistent memory theo chủ đề hoặc từ khóa."""
    return json.dumps({"ok": True, "matches": memory_store.search(query)}, ensure_ascii=False)


@tool
def remember_convention(topic: str, content: str, source: str = "user confirmed", replaces_topics: list[str] | None = None) -> str:
    """Lưu hoặc cập nhật quy ước project khi người dùng nói rõ muốn ghi nhớ/thay đổi quy ước. Dùng lại topic hiện có; nếu gộp một topic trùng nghĩa thì truyền vào replaces_topics. Phiên bản cũ được đánh dấu superseded, không bị xóa."""
    return json.dumps(memory_store.remember(topic, content, source, replaces_topics), ensure_ascii=False)
