"""Make one tiny, non-sensitive request using the project's .env configuration."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent import build_model
from config import load_settings


settings, missing = load_settings()
if settings is None:
    print({"api_request": "not_sent", "missing_settings": missing})
    raise SystemExit(2)

try:
    response = build_model(settings).invoke("Reply with exactly OK.", max_tokens=48)
    print({
        "api_request": "success",
        "model": settings.model_name,
        "response": str(response.content).strip()[:120],
        "finish_reason": response.response_metadata.get("finish_reason"),
    })
except Exception as exc:  # redact the secret if an SDK error ever echoes it
    message = str(exc).replace(settings.api_key, "[REDACTED]")
    print({
        "api_request": "failed",
        "exception": type(exc).__name__,
        "status_code": getattr(exc, "status_code", None),
        "message": message[:500],
    })
    raise SystemExit(1)
