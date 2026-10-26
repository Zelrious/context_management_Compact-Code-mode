"""Cấu hình project: hằng số hiển thị, giới hạn thực thi và settings đọc từ .env."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

import paths

PROJECT_NAME = "challenge-b-context-management"
APP_TITLE = "Challenge B: Context management"
CAPABILITY_TEXT = "B1 Compact structured checkpoint; B2 Code Mode đọc và tổng hợp log trong runtime."

# ---------------------------------------------------------------- chế độ mặc định
# Giá trị hợp lệ: "native" (Compact thường) hoặc "code" (Code Mode).
DEFAULT_MODE = os.getenv("DEFAULT_MODE", "native")

# ---------------------------------------------------------------- ngưỡng compact (B1)
# Ngưỡng ký tự context ước tính để kích hoạt compact tự động.
COMPACT_TRIGGER_CHARS = int(os.getenv("COMPACT_TRIGGER_CHARS", "24000"))
# Ngưỡng số message thay thế (nếu > 0 thì ưu tiên, 0 = tắt).
COMPACT_TRIGGER_MESSAGES = int(os.getenv("COMPACT_TRIGGER_MESSAGES", "0"))
# Bật/tắt compact tự động mặc định.
AUTO_COMPACT_ENABLED = os.getenv("AUTO_COMPACT_ENABLED", "true").strip().lower() in ("true", "1", "yes")

# ---------------------------------------------------------------- giới hạn Code Mode (B2)
# Giới hạn kích thước code gửi vào runtime (ký tự).
CODE_MODE_MAX_CODE_CHARS = int(os.getenv("CODE_MODE_MAX_CODE_CHARS", "8000"))
# Giới hạn kích thước đầu ra của runtime (ký tự).
CODE_MODE_MAX_OUTPUT_CHARS = int(os.getenv("CODE_MODE_MAX_OUTPUT_CHARS", "6000"))

# ---------------------------------------------------------------- giới hạn thực thi agent
# Giới hạn số lần gọi model trong một lượt chat (ModelCallLimitMiddleware).
MODEL_CALL_LIMIT = 8
# Giới hạn số tool calls trong một lượt chat (ToolCallLimitMiddleware).
TOOL_CALL_LIMIT = 20
# Giới hạn số bước của LangGraph (recursion limit), không phải số tool calls.
RECURSION_LIMIT = 50


@dataclass(frozen=True)
class Settings:
    api_key: str
    model_name: str
    base_url: str | None
    max_model_output_tokens: int = 4096


def load_settings() -> tuple[Settings | None, list[str]]:
    """Đọc .env của project này. Trả (settings, []) hoặc (None, danh sách biến còn thiếu)."""
    # Each standalone project reads only its own local .env.
    load_dotenv(paths.ENV_PATH, override=False)
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model_name = os.getenv("MODEL_NAME", "").strip()
    base_url = os.getenv("OPENAI_BASE_URL", "").strip() or None
    missing = [name for name, value in (("OPENAI_API_KEY", api_key), ("MODEL_NAME", model_name)) if not value]
    if missing:
        return None, missing
    max_output_tokens = int(os.getenv("MAX_MODEL_OUTPUT_TOKENS", "4096"))
    return Settings(
        api_key=api_key,
        model_name=model_name,
        base_url=base_url,
        max_model_output_tokens=max_output_tokens,
    ), []
