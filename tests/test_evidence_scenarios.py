"""Kiểm thử tích hợp cho bộ dữ liệu tổng hợp và minh chứng B1 (Compact) + B2 (Code Mode)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import streamlit as st
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

import paths
from agent import build_agent
from app import DEMO_CODE, compact_current_session, load_long_session_demo
from compaction import (
    RAW_TOOL_CHARS_TRIGGER,
    compact_messages,
    message_chars,
    raw_tool_chars,
    should_compact,
)
from config import CODE_MODE_MAX_OUTPUT_CHARS, COMPACT_TRIGGER_CHARS
from observer import Observer
from tests.conftest import ScriptedChatModel
from tools.code_mode import execute_code
from tools.files import _read


def _load_json_fixture(rel_path: str) -> dict:
    target = paths.FIXTURES_DIR / rel_path
    return json.loads(target.read_text(encoding="utf-8"))


def _stream_agent_turn(agent_graph, messages: list[BaseMessage]) -> tuple[list[dict], list[BaseMessage]]:
    events: list[dict] = []
    produced: list[BaseMessage] = []
    for mode_name, chunk in agent_graph.stream({"messages": messages}, stream_mode=["updates", "custom"]):
        if mode_name == "custom" and isinstance(chunk, dict) and chunk.get("observer"):
            events.append(chunk)
        elif mode_name == "updates" and isinstance(chunk, dict):
            for update in chunk.values():
                if isinstance(update, dict) and update.get("messages"):
                    produced.extend(update["messages"])
    return events, produced


def build_b1_sc01_history() -> list[BaseMessage]:
    """Tạo lịch sử hội thoại dài cho B1-SC01 (>35.000 ký tự) có lỗi thử nghiệm, nhiễu lặp và lỗi hiện hành."""
    spec = _load_json_fixture("data/scenarios/b1_checkpoint_scenarios.json")["scenarios"][0]
    noise_block = ("DIAGNOSTIC_NOISE_PADDING_LINE: verbose csv trace owner_bucket_scan status=ok\n" * 130)

    messages: list[BaseMessage] = [
        HumanMessage(content=spec["goal"]),
        # Lần thử 0: đọc SKILL.md thành công
        AIMessage(
            content="",
            tool_calls=[{"name": "read_file", "args": {"path": "skills/csv-quality/SKILL.md"}, "id": "sc01-c0", "type": "tool_call"}],
        ),
        ToolMessage(
            content=json.dumps({"ok": True, "path": "skills/csv-quality/SKILL.md", "content": "# CSV quality\n" + noise_block}, ensure_ascii=False),
            name="read_file",
            tool_call_id="sc01-c0",
            status="success",
        ),
        AIMessage(content="Đã đọc hướng dẫn skill csv-quality. Tiến hành chạy script kiểm tra dữ liệu."),
        # Lần thử 1 (thất bại): sai đường dẫn file
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "bash",
                    "args": {"command": "python skills/csv-quality/scripts/check_csv.py --input data/missing.csv --max-hours 8"},
                    "id": "sc01-c1",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content=json.dumps(
                {"ok": False, "exit_code": 1, "path": "data/missing.csv", "error": {"code": "INPUT_NOT_FOUND"}, "stderr": "File not found"},
                ensure_ascii=False,
            ),
            name="bash",
            tool_call_id="sc01-c1",
            status="error",
        ),
        AIMessage(content="Lần chạy đầu thất bại vì data/missing.csv không tồn tại (INPUT_NOT_FOUND). Sẽ thử lại trên data/workload.csv."),
        # Lần thử 2 (thất bại): truyền ngưỡng âm không hợp lệ
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "bash",
                    "args": {"command": "python skills/csv-quality/scripts/check_csv.py --input data/workload.csv --max-hours -5"},
                    "id": "sc01-c2",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content=json.dumps(
                {"ok": True, "exit_code": 2, "path": "data/workload.csv", "stdout": "", "stderr": "--max-hours must be non-negative"},
                ensure_ascii=False,
            ),
            name="bash",
            tool_call_id="sc01-c2",
            status="success",
        ),
        AIMessage(content="Lệnh với --max-hours -5 trả lỗi exit_code=2. Cần dùng đúng ngưỡng dương 8 giờ người dùng yêu cầu."),
    ]

    # Các lượt 3..5: chạy đúng nhưng sinh nhiều log chẩn đoán lặp làm phình ngữ cảnh
    for turn_idx in range(3, 6):
        call_id = f"sc01-c{turn_idx}"
        messages.append(
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "bash",
                        "args": {"command": "python skills/csv-quality/scripts/check_csv.py --input data/workload.csv --max-hours 8"},
                        "id": call_id,
                        "type": "tool_call",
                    }
                ],
            )
        )
        messages.append(
            ToolMessage(
                content=json.dumps(
                    {
                        "ok": True,
                        "exit_code": 0,
                        "path": "data/workload.csv",
                        "row_count": 6,
                        "stdout": noise_block,
                    },
                    ensure_ascii=False,
                ),
                name="bash",
                tool_call_id=call_id,
                status="success",
            )
        )
        messages.append(
            AIMessage(content=f"Lượt kiểm tra #{turn_idx}: xác nhận row_count=6, Lan có 9 giờ (>8 quá tải), Minh có 3 giờ.")
        )

    # Người dùng nhắc lại ràng buộc
    messages.append(HumanMessage(content=spec["followup_constraints"][0]))

    # Lần thử 6 (lỗi hiện hành chưa giải quyết): ghi nhầm vào data/csv-quality.md ngoài output/
    messages.extend(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"path": "data/csv-quality.md", "content": "# Bản nháp báo cáo"},
                        "id": "sc01-c6",
                        "type": "tool_call",
                    }
                ],
            ),
            ToolMessage(
                content=json.dumps(
                    {
                        "ok": False,
                        "path": "data/csv-quality.md",
                        "error": {"code": "PATH_OUTSIDE_OUTPUT", "message": "Chỉ được ghi file dưới output/"},
                    },
                    ensure_ascii=False,
                ),
                name="write_file",
                tool_call_id="sc01-c6",
                status="error",
            ),
            AIMessage(
                content=(
                    "Ghi báo cáo vào data/csv-quality.md bị từ chối với mã lỗi PATH_OUTSIDE_OUTPUT.\n"
                    "Bước tiếp theo: đọc template skills/csv-quality/references/report-template.md rồi dùng write_file "
                    "ghi báo cáo vào output/csv-quality.md, giữ ngưỡng 8 giờ và không sửa data/workload.csv."
                )
            ),
            HumanMessage(content=spec["followup_constraints"][1]),
        ]
    )
    return messages


def build_b1_sc02_history(workspace: Path) -> list[BaseMessage]:
    """Tạo lịch sử điều tra sự cố đa tệp cho B1-SC02 sử dụng dữ liệu thật trong workspace."""
    spec = _load_json_fixture("data/scenarios/b1_checkpoint_scenarios.json")["scenarios"][1]
    sla_res = _read(workspace, "data/scenarios/b1_multifile_incident/service_sla_config.json")
    manifest_res = _read(workspace, "data/scenarios/b1_multifile_incident/deploy_manifest.md")
    missing_redis_res = _read(workspace, "data/scenarios/b1_multifile_incident/redis_cluster.json")
    runbook_res = _read(workspace, "data/scenarios/b1_multifile_incident/runbook_notes.md")

    script_path = workspace / "skills" / "csv-quality" / "scripts" / "check_csv.py"
    csv_proc = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(script_path),
            "--input",
            str(workspace / "data" / "scenarios" / "b1_multifile_incident" / "batch_workload.csv"),
            "--max-hours",
            "6",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=True,
    )
    csv_json = json.loads(csv_proc.stdout)
    trace_padding = ("VERBOSE_POOL_TRACE_DUMP: conn_slot_check state=busy wait_queue_depth=14\n" * 150)

    messages: list[BaseMessage] = [
        HumanMessage(content=spec["goal"]),
        # Đọc cấu hình SLA
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {"path": "data/scenarios/b1_multifile_incident/service_sla_config.json"},
                    "id": "sc02-c1",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content=json.dumps({**sla_res, "diagnostic_pad": trace_padding}, ensure_ascii=False),
            name="read_file",
            tool_call_id="sc02-c1",
            status="success",
        ),
        AIMessage(content="Đã đọc service_sla_config.json: db_pool_max_connections=25, connections_per_sync_worker=3, allow_pool_resize=false."),
        # Giả thuyết 1 thất bại: thử đọc file cấu hình Redis không tồn tại
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {"path": "data/scenarios/b1_multifile_incident/redis_cluster.json"},
                    "id": "sc02-c2",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content=json.dumps(
                {"path": "data/scenarios/b1_multifile_incident/redis_cluster.json", **missing_redis_res},
                ensure_ascii=False,
            ),
            name="read_file",
            tool_call_id="sc02-c2",
            status="error",
        ),
        AIMessage(content="Không có redis_cluster.json (FILE_NOT_FOUND); loại bỏ giả thuyết lỗi cụm Redis."),
        # Đọc deploy_manifest.md + runbook_notes.md kèm nhiễu lớn
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {"path": "data/scenarios/b1_multifile_incident/deploy_manifest.md"},
                    "id": "sc02-c3",
                    "type": "tool_call",
                },
                {
                    "name": "read_file",
                    "args": {"path": "data/scenarios/b1_multifile_incident/runbook_notes.md"},
                    "id": "sc02-c4",
                    "type": "tool_call",
                },
            ],
        ),
        ToolMessage(
            content=json.dumps({**manifest_res, "diagnostic_pad": trace_padding}, ensure_ascii=False),
            name="read_file",
            tool_call_id="sc02-c3",
            status="success",
        ),
        ToolMessage(
            content=json.dumps({**runbook_res, "diagnostic_pad": trace_padding}, ensure_ascii=False),
            name="read_file",
            tool_call_id="sc02-c4",
            status="success",
        ),
        AIMessage(
            content=(
                "Đối chiếu deploy_manifest.md và service_sla_config.json: orders_sync_concurrency tăng từ 4 lên 12, "
                "chiếm 12 * 3 = 36 kết nối, vượt db_pool_max_connections=25."
            )
        ),
        HumanMessage(content=spec["followup_constraints"][0]),
        # Chạy check_csv.py trên batch_workload.csv
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "bash",
                    "args": {
                        "command": (
                            "python skills/csv-quality/scripts/check_csv.py "
                            "--input data/scenarios/b1_multifile_incident/batch_workload.csv --max-hours 6"
                        )
                    },
                    "id": "sc02-c5",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content=json.dumps(
                {
                    "ok": True,
                    "exit_code": 0,
                    "path": "data/scenarios/b1_multifile_incident/batch_workload.csv",
                    "row_count": csv_json["row_count"],
                    "stdout": json.dumps(csv_json, ensure_ascii=False) + "\n" + trace_padding,
                },
                ensure_ascii=False,
            ),
            name="bash",
            tool_call_id="sc02-c5",
            status="success",
        ),
        AIMessage(
            content=(
                "Kết quả batch_workload.csv ở ngưỡng 6 giờ: Hieu=7.5 giờ (quá tải), Trang=6.0 giờ (đúng bằng ngưỡng nên không quá tải), "
                "Khanh=2.5 giờ (< 4.0 giờ nên đủ điều kiện nhận việc xác minh)."
            )
        ),
        # Thử chạy lệnh kiểm tra sai cờ CLI -> exit_code=2, và ghi nhầm đường dẫn ngoài output/
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "bash",
                    "args": {"command": "python skills/csv-quality/scripts/check_csv.py --verify-pool"},
                    "id": "sc02-c6",
                    "type": "tool_call",
                },
                {
                    "name": "write_file",
                    "args": {"path": "scenarios/incident-remediation.md", "content": "# Kế hoạch khắc phục"},
                    "id": "sc02-c7",
                    "type": "tool_call",
                },
            ],
        ),
        ToolMessage(
            content=json.dumps({"ok": True, "exit_code": 2, "stdout": "", "stderr": "unrecognized arguments: --verify-pool"}, ensure_ascii=False),
            name="bash",
            tool_call_id="sc02-c6",
            status="success",
        ),
        ToolMessage(
            content=json.dumps(
                {
                    "ok": False,
                    "path": "scenarios/incident-remediation.md",
                    "error": {"code": "PATH_OUTSIDE_OUTPUT", "message": "Chỉ được ghi dưới output/"},
                },
                ensure_ascii=False,
            ),
            name="write_file",
            tool_call_id="sc02-c7",
            status="error",
        ),
        AIMessage(
            content=(
                "Lần ghi scenarios/incident-remediation.md thất bại với lỗi PATH_OUTSIDE_OUTPUT nên báo cáo chưa được lưu.\n"
                "Bước tiếp theo: dùng write_file ghi kế hoạch vào output/incident-remediation.md "
                "(12*3=36 vượt db_pool_max_connections=25, rollback orders_sync_concurrency=4, phân công Khanh, không sửa data/)."
            )
        ),
        HumanMessage(content=spec["followup_constraints"][1]),
    ]
    return messages


# ================================================================ B1 TESTS


def test_b1_sc01_long_diagnostic_flood_triggers_and_preserves_checklist():
    spec = _load_json_fixture("data/scenarios/b1_checkpoint_scenarios.json")["scenarios"][0]
    checklist = spec["expected_checklist"]
    history = build_b1_sc01_history()

    before = message_chars(history)
    tool_total = raw_tool_chars(history)
    assert before >= checklist["min_before_chars"]
    assert tool_total >= checklist["min_raw_tool_chars"]

    needed, reason = should_compact(history, COMPACT_TRIGGER_CHARS)
    assert needed is checklist["must_trigger_auto_compact"]
    assert "vượt ngưỡng" in reason

    result = compact_messages(history)
    cp = result.checkpoint

    assert result.after_chars < result.before_chars
    assert result.after_chars < result.before_chars * 0.25

    for sub in checklist["goal_substrings"]:
        assert sub in cp.goal
    joined_reqs = "\n".join(cp.user_requirements)
    for sub in checklist["requirement_substrings"]:
        assert sub in joined_reqs
    joined_failures = "\n".join(cp.failed_attempts)
    for sub in checklist["failed_attempt_substrings"]:
        assert sub in joined_failures
    joined_unresolved = "\n".join(cp.unresolved_issues)
    for sub in checklist["unresolved_issue_substrings"]:
        assert sub in joined_unresolved
    joined_findings = "\n".join(cp.findings)
    for sub in checklist["finding_substrings"]:
        assert sub in joined_findings
    for expected_file in checklist["relevant_files"]:
        assert expected_file in cp.relevant_files
    for sub in checklist["next_action_substrings"]:
        assert sub in cp.next_action

    active_serialized = "\n".join(str(m.content) for m in result.messages)
    for forbidden in checklist["forbidden_active_substrings"]:
        assert forbidden not in active_serialized
    archived_serialized = "\n".join(str(m.content) for m in result.archived_messages)
    for forbidden in checklist["forbidden_active_substrings"]:
        assert forbidden in archived_serialized


def test_b1_sc02_multifile_incident_workspace_consistency(lab_dirs):
    workspace = paths.WORKSPACE_DIR
    sla = json.loads((workspace / "data/scenarios/b1_multifile_incident/service_sla_config.json").read_text(encoding="utf-8"))
    manifest = (workspace / "data/scenarios/b1_multifile_incident/deploy_manifest.md").read_text(encoding="utf-8")
    runbook = (workspace / "data/scenarios/b1_multifile_incident/runbook_notes.md").read_text(encoding="utf-8")

    assert sla["db_pool_max_connections"] == 25
    assert sla["connections_per_sync_worker"] == 3
    assert sla["allow_pool_resize"] is False
    assert "`12`" in manifest and "`4`" in manifest
    assert 12 * sla["connections_per_sync_worker"] > sla["db_pool_max_connections"]
    assert "orders_sync_concurrency=4" in runbook

    script_path = workspace / "skills/csv-quality/scripts/check_csv.py"
    proc = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(script_path),
            "--input",
            str(workspace / "data/scenarios/b1_multifile_incident/batch_workload.csv"),
            "--max-hours",
            "6",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=True,
    )
    report = json.loads(proc.stdout)
    assert report["row_count"] == 7
    assert report["hours_by_owner"] == {"Hieu": 7.5, "Khanh": 2.5, "Trang": 6}
    assert report["overloaded_owners"] == [{"owner": "Hieu", "total_hours": 7.5}]
    assert report["excluded_rows"] == [
        {"line": 6, "task_id": "INC-05", "reasons": ["invalid_hours"]},
        {"line": 7, "task_id": "INC-02", "reasons": ["duplicate_id"]},
        {"line": 8, "task_id": "INC-06", "reasons": ["missing_owner"]},
    ]
    overloaded_names = {item["owner"] for item in report["overloaded_owners"]}
    eligible_verifiers = [
        owner for owner, total in report["hours_by_owner"].items() if owner not in overloaded_names and total < 4.0
    ]
    assert eligible_verifiers == ["Khanh"]


def test_b1_sc02_multifile_compaction_and_agent_snapshot_continuation(lab_dirs):
    spec = _load_json_fixture("data/scenarios/b1_checkpoint_scenarios.json")["scenarios"][1]
    checklist = spec["expected_checklist"]
    history = build_b1_sc02_history(paths.WORKSPACE_DIR)

    needed, _ = should_compact(history, COMPACT_TRIGGER_CHARS)
    assert needed is True

    compacted = compact_messages(history, keep_turns=1)
    cp = compacted.checkpoint
    assert compacted.before_chars >= checklist["min_before_chars"]
    assert compacted.after_chars < 6_000

    for sub in checklist["goal_substrings"]:
        assert sub in cp.goal
    joined_reqs = "\n".join(cp.user_requirements)
    for sub in checklist["requirement_substrings"]:
        assert sub in joined_reqs
    joined_failures = "\n".join(cp.failed_attempts)
    for sub in checklist["failed_attempt_substrings"]:
        assert sub in joined_failures
    joined_unresolved = "\n".join(cp.unresolved_issues)
    for sub in checklist["unresolved_issue_substrings"]:
        assert sub in joined_unresolved
    joined_findings = "\n".join(cp.findings)
    for sub in checklist["finding_substrings"]:
        assert sub in joined_findings
    for expected_file in checklist["relevant_files"]:
        assert expected_file in cp.relevant_files
    for sub in checklist["next_action_substrings"]:
        assert sub in cp.next_action

    # Chạy lượt tiếp theo qua Agent + ObserverMiddleware trên ngữ cảnh đã compact
    remediation_md = (
        "# Kế hoạch khắc phục sự cố 2026.10.24-rc3\n"
        "- Nguyên nhân gốc: orders_sync_concurrency=12 * 3 = 36 > db_pool_max_connections=25\n"
        "- Hành động: rollback orders_sync_concurrency=4 (không tăng pool DB)\n"
        "- Kỹ sư xác minh: Khanh (2.5 giờ < 4.0 giờ; Hieu quá tải 7.5 giờ; Trang 6.0 giờ không quá tải)\n"
        "- Dòng loại trừ: INC-05 (invalid_hours), INC-02 (duplicate_id), INC-06 (missing_owner)\n"
    )
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"path": "output/incident-remediation.md", "content": remediation_md},
                        "id": "post-compact-w1",
                    }
                ],
            ),
            AIMessage(content="Đã ghi kế hoạch khắc phục vào output/incident-remediation.md dựa trên checkpoint."),
        ]
    )
    observer = Observer(conversation_id="conv-b1-sc02")
    observer.start_turn("run-post-compact")
    events, new_messages = _stream_agent_turn(build_agent(model, mode="native"), compacted.messages)
    for event in events:
        observer.record(event["event"], event["data"])
    observer.sync_messages(compacted.messages + new_messages)

    first_snapshot = observer.snapshots[0]
    assert first_snapshot["message_chars"] < compacted.before_chars * 0.25
    serialized_snapshot = json.dumps(first_snapshot["messages"], ensure_ascii=False)
    assert "VERBOSE_POOL_TRACE_DUMP" not in serialized_snapshot
    assert "CHECKPOINT COMPACTION" in first_snapshot["messages"][0]["content"]
    assert "orders_sync_concurrency=4" in first_snapshot["messages"][0]["content"]
    assert "Khanh" in first_snapshot["messages"][0]["content"]
    assert (paths.WORKSPACE_DIR / "output/incident-remediation.md").read_text(encoding="utf-8") == remediation_md


def test_b1_sc03_multi_round_recompaction_and_ui_demo_parity():
    round1 = compact_messages(build_b1_sc01_history())
    phase2_messages = [
        *round1.messages,
        HumanMessage(content="Bổ sung mục so sánh tổng giờ hợp lệ giữa Lan và Minh; vẫn không được sửa CSV gốc."),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {"path": "skills/csv-quality/references/missing-template.md"},
                    "id": "sc03-err",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content=json.dumps(
                {
                    "ok": False,
                    "path": "skills/csv-quality/references/missing-template.md",
                    "error": {"code": "FILE_NOT_FOUND"},
                },
                ensure_ascii=False,
            ),
            name="read_file",
            tool_call_id="sc03-err",
            status="error",
        ),
        AIMessage(
            content=(
                "Đọc nhầm missing-template.md (FILE_NOT_FOUND). "
                "Bước tiếp theo: đọc đúng skills/csv-quality/references/report-template.md và ghi output/csv-quality.md."
            )
        ),
        HumanMessage(content="Tiếp tục hoàn thành báo cáo với đầy đủ ràng buộc của cả hai giai đoạn."),
    ]
    round2 = compact_messages(phase2_messages, keep_turns=1, prior_checkpoint=round1.checkpoint.__dict__)

    assert round2.checkpoint.goal == round1.checkpoint.goal
    joined_reqs = "\n".join(round2.checkpoint.user_requirements)
    assert "tổng đúng bằng 8 không tính quá tải" in joined_reqs
    assert "so sánh tổng giờ hợp lệ giữa Lan và Minh" in joined_reqs
    assert round2.checkpoint.archived_message_count > round1.checkpoint.archived_message_count
    assert round2.checkpoint.archived_tool_chars >= round1.checkpoint.archived_tool_chars
    assert any("INPUT_NOT_FOUND" in item for item in round2.checkpoint.failed_attempts)
    assert any("FILE_NOT_FOUND" in item for item in round2.checkpoint.failed_attempts)

    # Đối chiếu tính tái lập của demo UI tích hợp sẵn trong app.py
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
        stats = compact_current_session(force=True)

    assert stats is not None
    assert stats["before_chars"] == 120_012
    assert stats["after_chars"] < 6_000
    assert stats["archived_messages"] == 15


def test_b1_sc04_documented_compaction_boundaries():
    """Kiểm chứng trung thực các giới hạn thiết kế hiện hành của compaction.py."""
    messages: list[BaseMessage] = [HumanMessage(content="Mục tiêu gốc ban đầu: kiểm tra an toàn log.")]
    for idx in range(1, 10):
        messages.append(HumanMessage(content=f"Ràng buộc bổ sung thứ {idx}"))
    messages.extend(
        [
            AIMessage(
                content="",
                tool_calls=[{"name": "bash", "args": {"command": "python check.py"}, "id": "lim-1", "type": "tool_call"}],
            ),
            ToolMessage(
                content=json.dumps(
                    {
                        "ok": False,
                        "error": {"code": "PARSE_ERR", "message": "SECRET_RAW_STACK_LINE_SHOULD_NOT_ENTER_CHECKPOINT"},
                    }
                ),
                name="bash",
                tool_call_id="lim-1",
                status="error",
            ),
            AIMessage(content="Đã khắc phục lỗi PARSE_ERR thành công. Bước tiếp theo: xuất báo cáo cuối."),
        ]
    )

    result = compact_messages(messages, keep_turns=1)
    cp = result.checkpoint
    rendered = cp.render()

    # 1. Chuỗi lỗi tự do trong ToolMessage được lược bỏ khỏi checkpoint để chống rò rỉ raw log
    assert "SECRET_RAW_STACK_LINE_SHOULD_NOT_ENTER_CHECKPOINT" not in rendered
    assert any("PARSE_ERR" in item for item in cp.failed_attempts)
    # 2. goal giữ từ message đầu tiên dù user_requirements chỉ giữ tối đa 8 message gần nhất
    assert cp.goal == "Mục tiêu gốc ban đầu: kiểm tra an toàn log."
    assert len(cp.user_requirements) == 8
    # 3. Lỗi lịch sử của tool vẫn nằm trong unresolved_issues (rule-based không tự suy diễn đã đóng lỗi)
    assert any("PARSE_ERR" in item for item in cp.unresolved_issues)

    # 4. Dòng AIMessage đơn lẻ dài > 240 ký tự bị bộ lọc chống raw-log của compaction.py lược bỏ khỏi current_state
    long_line_msg = "Bước tiếp theo: " + ("x" * 240)
    res_long = compact_messages([HumanMessage(content="Mục tiêu kiểm tra giới hạn 240 ký tự."), AIMessage(content=long_line_msg)])
    assert res_long.checkpoint.current_state == ""


# ================================================================ B2 TESTS


def test_b2_sc01_incident_cascade_correlation_deduplication_and_critical_error(lab_dirs):
    scenario = _load_json_fixture("data/scenarios/b2_code_mode_scenarios.json")["scenarios"][0]
    expected = scenario["expected"]

    res = execute_code(scenario["code"], workspace=paths.WORKSPACE_DIR)
    assert res["ok"] is True, res
    assert res["files_read"] == expected["files_read"]
    assert res["input_bytes_processed"] > 45_000
    assert res["raw_log_chars_returned_to_model"] == 0

    serialized = json.dumps(res["result"], ensure_ascii=False)
    assert len(serialized) <= CODE_MODE_MAX_OUTPUT_CHARS
    assert len(serialized) < res["input_bytes_processed"] * 0.05

    payload = res["result"]
    assert payload["total_errors"] == expected["total_errors"]
    assert payload["total_warnings"] == expected["total_warnings"]
    assert payload["deduplicated_noise"] == expected["deduplicated_noise"]
    assert payload["critical_findings"] == expected["critical_findings"]
    assert [item["file"] for item in payload["correlated_traces"]["trc-9041"]] == expected["trc_9041_files"]
    assert [item["file"] for item in payload["correlated_traces"]["trc-9042"]] == expected["trc_9042_files"]
    assert "data/logs/incident_cascade/auth-token.log" not in [
        item["file"] for item in payload["correlated_traces"]["trc-9041"]
    ]


def test_b2_sc02_malformed_txt_log_safe_aggregation(lab_dirs):
    scenario = _load_json_fixture("data/scenarios/b2_code_mode_scenarios.json")["scenarios"][1]
    expected = scenario["expected"]

    res = execute_code(scenario["code"], workspace=paths.WORKSPACE_DIR)
    assert res["ok"] is True, res
    assert res["files_read"] == expected["files_read"]
    assert res["raw_log_chars_returned_to_model"] == 0
    assert res["result"] == expected["result"]


def test_b2_sc03_output_limit_taint_guard_and_agent_snapshot_isolation(lab_dirs):
    scenarios = _load_json_fixture("data/scenarios/b2_code_mode_scenarios.json")["scenarios"]
    sc01 = scenarios[0]
    sc03 = scenarios[2]

    # 1. Chặn kết quả vượt 6.000 ký tự (nhánh early-return chỉ trả ok=False và error, không có trường result)
    oversized = execute_code(sc03["oversized_code"], workspace=paths.WORKSPACE_DIR)
    assert oversized["ok"] is False
    assert "result" not in oversized
    assert sc03["expected"]["oversized_error_substring"] in oversized["error"]
    assert oversized.get("raw_log_chars_returned_to_model", 0) == 0

    # 1b. Kiểm thử sát biên 6.000 ký tự (chuỗi 5.998 ký tự + 2 dấu nháy kép JSON = đúng 6.000 ký tự -> hợp lệ)
    exact_6000 = execute_code("result = 'a' * 5998", workspace=paths.WORKSPACE_DIR)
    assert exact_6000["ok"] is True
    assert len(json.dumps(exact_6000["result"], ensure_ascii=False)) == CODE_MODE_MAX_OUTPUT_CHARS
    assert exact_6000["raw_log_chars_returned_to_model"] == 0

    # 1c. Vượt ngưỡng đúng 1 ký tự (chuỗi 5.999 ký tự + 2 dấu nháy kép JSON = 6.001 ký tự -> bị chặn)
    over_6001 = execute_code("result = 'a' * 5999", workspace=paths.WORKSPACE_DIR)
    assert over_6001["ok"] is False
    assert "result" not in over_6001
    assert sc03["expected"]["oversized_error_substring"] in over_6001["error"]
    assert over_6001.get("raw_log_chars_returned_to_model", 0) == 0

    # 2. Chặn rò rỉ chuỗi con cắt trực tiếp từ raw log (taint propagation qua split/subscript)
    tainted = execute_code(sc03["tainted_slice_code"], workspace=paths.WORKSPACE_DIR)
    assert tainted["ok"] is False
    assert sc03["expected"]["tainted_error_substring"] in tainted["error"]
    assert tainted["raw_log_chars_returned_to_model"] == 0

    # 3. Chạy thực tế qua Agent ở chế độ Code Mode + ObserverMiddleware
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"name": "run_code", "args": {"code": sc01["code"]}, "id": "b2-call-1"}],
            ),
            AIMessage(
                content=(
                    "Tổng hợp 5 tệp incident_cascade: 26 ERROR, 240 WARN (gồm 120 upstream_slow_retry và 60 cache_miss_fallback đã gộp). "
                    "Phát hiện lỗi nghiêm trọng CRITICAL_POOL_EXHAUSTED tại payments-ledger.log tương quan trace_id=trc-9041."
                )
            ),
        ]
    )
    observer = Observer(conversation_id="conv-b2-sc03")
    observer.start_turn("run-b2")
    user_msg = HumanMessage(content=sc01["question"])
    events, produced = _stream_agent_turn(build_agent(model, mode="code"), [user_msg])
    for event in events:
        observer.record(event["event"], event["data"])
    observer.sync_messages([user_msg, *produced])

    assert [t["name"] for t in observer.snapshots[0]["tools"]] == ["run_code", "search_memory", "remember_convention"]
    assert observer.code_runs == 1
    assert observer.code_files_read == 5
    assert observer.code_input_bytes > 45_000
    assert observer.code_raw_log_chars_returned == 0

    second_snapshot_msgs = json.dumps(observer.snapshots[1]["messages"], ensure_ascii=False)
    for raw_marker in ("gateway_upstream_timeout", "ledger_heartbeat", "sync_worker_poll", "jwt_verified"):
        assert raw_marker not in second_snapshot_msgs
    assert "CRITICAL_POOL_EXHAUSTED" in second_snapshot_msgs


def test_b2_ui_demo_code_works_with_expanded_synthetic_logs(lab_dirs):
    res = execute_code(DEMO_CODE, workspace=paths.WORKSPACE_DIR)
    assert res["ok"] is True, res
    assert res["files_read"] == 24  # 20 log gốc + 4 tệp .log trong incident_cascade
    assert res["input_bytes_processed"] > 48_000
    assert res["raw_log_chars_returned_to_model"] == 0
    assert res["result"]["total_errors"] == 33  # 9 lỗi gốc + 24 lỗi trong incident_cascade/*.log
    assert len(json.dumps(res["result"], ensure_ascii=False)) <= CODE_MODE_MAX_OUTPUT_CHARS
