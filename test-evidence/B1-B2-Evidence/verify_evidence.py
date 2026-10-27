"""Script tái lập số liệu kiểm chứng thực tế cho B1 (Compact) và B2 (Code Mode).

Chạy từ thư mục gốc project:
    .\\.venv\\Scripts\\python.exe -X utf8 test-evidence\\B1-B2-Evidence\\verify_evidence.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

import paths
from app import DEMO_CODE, compact_current_session, load_long_session_demo
from compaction import compact_messages, raw_tool_chars, should_compact
from config import CODE_MODE_MAX_OUTPUT_CHARS, COMPACT_TRIGGER_CHARS
from tests.test_evidence_scenarios import build_b1_sc01_history, build_b1_sc02_history
from tools.code_mode import execute_code


def main() -> int:
    evidence_dir = Path(__file__).resolve().parent
    b2_spec = json.loads(
        (paths.FIXTURES_DIR / "data/scenarios/b2_code_mode_scenarios.json").read_text(encoding="utf-8")
    )["scenarios"]

    # 1. B1-SC01
    sc01_hist = build_b1_sc01_history()
    sc01_trig, sc01_reason = should_compact(sc01_hist, COMPACT_TRIGGER_CHARS)
    sc01_res = compact_messages(sc01_hist)

    # 2. B1-SC02 (đa tệp)
    sc02_hist = build_b1_sc02_history(paths.WORKSPACE_DIR)
    sc02_trig, sc02_reason = should_compact(sc02_hist, COMPACT_TRIGGER_CHARS)
    sc02_res = compact_messages(sc02_hist, keep_turns=1)

    # 3. B1-SC03 (UI demo session)
    class FakeState(dict):
        def __getattr__(self, key):
            return self.get(key)

        def __setattr__(self, key, value):
            self[key] = value

    ss = FakeState(
        model_history=[],
        compaction_archive=[],
        compaction_checkpoint=None,
        compaction_count=0,
        compaction_stats=None,
        compact_trigger_chars=COMPACT_TRIGGER_CHARS,
        compact_trigger_messages=0,
        observer=type("DummyObs", (), {"sync_messages": lambda self, msgs: None})(),
    )
    with patch.object(st, "session_state", ss):
        load_long_session_demo()
        ui_stats = compact_current_session(force=True)

    # 4. B2 scenarios
    b2_sc01_res = execute_code(b2_spec[0]["code"], workspace=paths.WORKSPACE_DIR)
    b2_sc02_res = execute_code(b2_spec[1]["code"], workspace=paths.WORKSPACE_DIR)
    b2_oversized = execute_code(b2_spec[2]["oversized_code"], workspace=paths.WORKSPACE_DIR)
    b2_tainted = execute_code(b2_spec[2]["tainted_slice_code"], workspace=paths.WORKSPACE_DIR)
    b2_ui_demo = execute_code(DEMO_CODE, workspace=paths.WORKSPACE_DIR)

    report = {
        "project": "challenge-b-context-management",
        "branch": "feat/evidence",
        "config_limits": {
            "compact_trigger_chars": COMPACT_TRIGGER_CHARS,
            "code_mode_max_output_chars": CODE_MODE_MAX_OUTPUT_CHARS,
        },
        "b1_results": {
            "B1-SC01": {
                "triggered": sc01_trig,
                "trigger_reason": sc01_reason,
                "before_chars": sc01_res.before_chars,
                "after_chars": sc01_res.after_chars,
                "reduction_pct": round((1 - sc01_res.after_chars / sc01_res.before_chars) * 100, 2),
                "raw_tool_chars_before": raw_tool_chars(sc01_hist),
                "archived_messages": len(sc01_res.archived_messages),
                "archived_tool_chars": sc01_res.checkpoint.archived_tool_chars,
                "checkpoint": {
                    k: v for k, v in sc01_res.checkpoint.__dict__.items() if k != "created_at"
                },
            },
            "B1-SC02": {
                "triggered": sc02_trig,
                "trigger_reason": sc02_reason,
                "before_chars": sc02_res.before_chars,
                "after_chars": sc02_res.after_chars,
                "reduction_pct": round((1 - sc02_res.after_chars / sc02_res.before_chars) * 100, 2),
                "raw_tool_chars_before": raw_tool_chars(sc02_hist),
                "archived_messages": len(sc02_res.archived_messages),
                "archived_tool_chars": sc02_res.checkpoint.archived_tool_chars,
                "checkpoint": {
                    k: v for k, v in sc02_res.checkpoint.__dict__.items() if k != "created_at"
                },
            },
            "B1-UI-Demo": ui_stats,
        },
        "b2_results": {
            "B2-SC01": {
                "ok": b2_sc01_res["ok"],
                "files_read": b2_sc01_res["files_read"],
                "input_bytes_processed": b2_sc01_res["input_bytes_processed"],
                "output_json_chars": len(json.dumps(b2_sc01_res["result"], ensure_ascii=False)),
                "raw_log_chars_returned_to_model": b2_sc01_res["raw_log_chars_returned_to_model"],
                "result": b2_sc01_res["result"],
            },
            "B2-SC02": {
                "ok": b2_sc02_res["ok"],
                "files_read": b2_sc02_res["files_read"],
                "input_bytes_processed": b2_sc02_res["input_bytes_processed"],
                "output_json_chars": len(json.dumps(b2_sc02_res["result"], ensure_ascii=False)),
                "raw_log_chars_returned_to_model": b2_sc02_res["raw_log_chars_returned_to_model"],
                "result": b2_sc02_res["result"],
            },
            "B2-SC03": {
                "oversized_blocked": not b2_oversized["ok"],
                "oversized_error": b2_oversized["error"],
                "tainted_blocked": not b2_tainted["ok"],
                "tainted_error": b2_tainted["error"],
                "raw_log_chars_returned_to_model": (
                    b2_oversized.get("raw_log_chars_returned_to_model", 0)
                    + b2_tainted["raw_log_chars_returned_to_model"]
                ),
            },
            "B2-UI-Demo": {
                "ok": b2_ui_demo["ok"],
                "files_read": b2_ui_demo["files_read"],
                "input_bytes_processed": b2_ui_demo["input_bytes_processed"],
                "output_json_chars": len(json.dumps(b2_ui_demo["result"], ensure_ascii=False)),
                "raw_log_chars_returned_to_model": b2_ui_demo["raw_log_chars_returned_to_model"],
                "total_errors": b2_ui_demo["result"]["total_errors"],
            },
        },
    }

    out_path = evidence_dir / "observed-metrics.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Ghi thành công báo cáo đo lường tại: {out_path.relative_to(ROOT).as_posix()}")
    print(
        f"B1-SC01: {sc01_res.before_chars:,} -> {sc01_res.after_chars:,} ký tự "
        f"(-{report['b1_results']['B1-SC01']['reduction_pct']}%)"
    )
    print(
        f"B1-SC02: {sc02_res.before_chars:,} -> {sc02_res.after_chars:,} ký tự "
        f"(-{report['b1_results']['B1-SC02']['reduction_pct']}%)"
    )
    print(
        f"B2-SC01: files={b2_sc01_res['files_read']}, bytes={b2_sc01_res['input_bytes_processed']:,}, "
        f"output_chars={report['b2_results']['B2-SC01']['output_json_chars']:,}, "
        f"raw_log_chars={b2_sc01_res['raw_log_chars_returned_to_model']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
