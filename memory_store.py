"""Persistent project memory with explicit revisions and source timestamps."""

from __future__ import annotations

import json
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path

import paths

STORE_PATH = paths.PROJECT_ROOT / "memory" / "memory.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slug(topic: str) -> str:
    text = topic.strip().casefold().replace("đ", "d")
    text = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "general"


def _is_response_language_rule(topic: str, content: str = "") -> bool:
    topic_key = _slug(topic)
    if any(term in topic_key for term in ("ngon-ngu", "language", "answer-language", "response-language")):
        return True
    content_key = _slug(content)
    mentions_languages = any(term in content_key for term in ("english", "vietnamese", "tieng-anh", "tieng-viet"))
    return mentions_languages and any(term in topic_key for term in ("tra-loi", "answer", "response"))


def _canonical_key(topic: str, content: str = "") -> str:
    """Fold common names for the response-language convention into one topic."""
    return "ngon-ngu-tra-loi" if _is_response_language_rule(topic, content) else _slug(topic)


def load_store() -> dict:
    if not STORE_PATH.exists():
        return {"schema_version": 1, "entries": []}
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
        if data.get("schema_version") != 1 or not isinstance(data.get("entries"), list):
            raise ValueError("unsupported memory schema")
        # Recompute keys so Vietnamese topics compare consistently across diacritics.
        for entry in data["entries"]:
            entry["topic_key"] = _slug(entry.get("topic", ""))
        return data
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"Không đọc được kho memory: {exc}") from exc


def save_store(data: dict) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = STORE_PATH.with_suffix(".json.tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(STORE_PATH)


def current_entries() -> list[dict]:
    return sorted((e for e in load_store()["entries"] if e.get("status") == "current"), key=lambda e: e["topic"].casefold())


def history_entries() -> list[dict]:
    entries = load_store()["entries"]
    # Group versions by topic, put the active rule first, then show its history
    # newest-first. A replacement gives the old and new rows the same timestamp;
    # sorting only by updated_at could therefore display the superseded row first.
    entries.sort(key=lambda e: e["updated_at"], reverse=True)
    entries.sort(key=lambda e: e.get("status") != "current")
    entries.sort(key=lambda e: e.get("topic", "").casefold())
    return entries


def remember(
    topic: str,
    content: str,
    source: str = "user confirmed",
    replaces_topics: list[str] | None = None,
) -> dict:
    topic, content = topic.strip(), content.strip()
    if not topic or not content:
        return {"ok": False, "error": "Cần có topic và nội dung quy ước."}
    data = load_store()
    slug, now = _canonical_key(topic, content), _now()
    # Keep synonymous names such as "Language", "Ngôn ngữ", or
    # "Trả lời bằng tiếng Anh" under the existing response-language topic.
    if slug == "ngon-ngu-tra-loi":
        topic = "Ngôn ngữ trả lời"
    replaced_keys = {slug, *(_canonical_key(value, content) for value in (replaces_topics or []) if value.strip())}
    prior = [e for e in data["entries"] if _canonical_key(e.get("topic", ""), e.get("content", "")) in replaced_keys and e["status"] == "current"]
    for entry in prior:
        entry["status"] = "superseded"
        entry["updated_at"] = now
    item = {
        "id": f"{slug}-{uuid.uuid4().hex[:10]}",
        "topic": topic,
        "topic_key": slug,
        "content": content,
        "status": "current",
        "source": source.strip() or "unspecified",
        "created_at": now,
        "updated_at": now,
        "supersedes": [e["id"] for e in prior],
    }
    data["entries"].append(item)
    save_store(data)
    return {"ok": True, "entry": item, "superseded": [e["id"] for e in prior]}


def search(query: str) -> list[dict]:
    normalized = unicodedata.normalize("NFKD", query.casefold().replace("đ", "d"))
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    terms = {w for w in re.findall(r"[\w-]+", normalized) if len(w) > 1}
    entries = current_entries()
    if not terms:
        return entries
    def score(item: dict) -> int:
        haystack = unicodedata.normalize("NFKD", f"{item['topic']} {item['content']}".casefold().replace("đ", "d"))
        haystack = "".join(ch for ch in haystack if not unicodedata.combining(ch))
        return sum(1 for term in terms if term in haystack)
    return sorted((e for e in entries if score(e)), key=lambda e: (-score(e), e["topic"].casefold()))


def render_injected_memory() -> str:
    entries = current_entries()
    if not entries:
        return "Chưa có quy ước project nào được ghi nhớ."
    lines = ["Quy ước project hiện hành (persistent memory; nguồn và thời điểm được lưu trong kho):"]
    lines.extend(f"- {e['topic']}: {e['content']}" for e in entries)
    return "\n".join(lines)
