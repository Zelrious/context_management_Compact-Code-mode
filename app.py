"""Streamlit chat UI: chat bên trái, State & Context bên phải."""

from __future__ import annotations

import json
import uuid

import streamlit as st
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import paths
import memory_store
from agent import (
    TOOLS,
    build_agent,
    build_model,
    capabilities,
    compact_messages,
    execute_code,
    message_chars,
    selected_tools,
    should_compact,
    system_prompt,
    DEFAULT_TRIGGER_CHARS,
)
from config import (
    APP_TITLE,
    AUTO_COMPACT_ENABLED,
    CAPABILITY_TEXT,
    CODE_MODE_MAX_CODE_CHARS,
    CODE_MODE_MAX_OUTPUT_CHARS,
    COMPACT_TRIGGER_CHARS,
    COMPACT_TRIGGER_MESSAGES,
    DEFAULT_MODE,
    PROJECT_NAME,
    RECURSION_LIMIT,
    load_settings,
)
from observer import (
    STATUS_DONE,
    STATUS_ERROR,
    STATUS_MODEL,
    STATUS_TOOL,
    Observer,
    message_text,
    serialize_message,
    tool_schema,
)
from reset_workspace import WorkspaceError, ensure_workspace
from trace import TraceWriter, log_exception

PANEL_HEIGHT = 600  # chiều cao dự phòng (px); CSS bên dưới kéo hai cột theo chiều cao cửa sổ
PAGE_CSS = """
<style>
[data-testid="stAppScrollToBottomContainer"] { overflow: auto !important; }
[data-testid="stMainBlockContainer"] { padding-top: 3rem; padding-bottom: 0; }
[data-testid="stBottomBlockContainer"] { padding-top: 0.5rem; padding-bottom: 1rem; }
[data-testid="stLayoutWrapper"]:has(> .st-key-chat_panel),
[data-testid="stLayoutWrapper"]:has(> .st-key-state_panel) {
    height: calc(100dvh - 15.5rem) !important;
    flex-basis: auto !important;
}
.st-key-chat_panel, .st-key-state_panel { height: 100% !important; }
</style>
"""

STATUS_BADGE = {
    STATUS_MODEL: ("orange", ":material/smart_toy:"),
    STATUS_TOOL: ("violet", ":material/build:"),
    STATUS_DONE: ("green", ":material/check_circle:"),
    STATUS_ERROR: ("red", ":material/error:"),
}
COUNTER_HELP = {
    "Lượt chat": "Một lần gửi input; bắt đầu từ 1 trong mỗi conversation.",
    "Lần gọi model trong lượt": (
        "Tăng ngay trước mỗi model request thực tế (middleware wrap_model_call); reset khi gửi lượt mới. "
        "Retry nội bộ của OpenAI SDK (nếu có) không quan sát được nên không được đếm riêng."
    ),
    "Bước sự kiện trong lượt": "Số thứ tự observer event trong lượt (user_submitted, model_request, …). Không phải LangGraph step.",
    "Tool calls trong lượt": "Số tool call đã thực thi; mỗi call có ID riêng. Một model response có thể yêu cầu nhiều tool calls.",
}
TOKEN_HELP = {
    "Input tokens": "Tổng input_tokens provider trả về trong các model response đã quan sát.",
    "Output tokens": "Tổng output_tokens provider trả về trong các model response đã quan sát.",
    "Cached input tokens": "Cached input tokens nếu provider đưa chi tiết cache read.",
}
CODE_MODE_HELP = {
    "Code runs": "Số lần run_code hoàn tất; runtime đọc log nhưng chỉ trả aggregate.",
    "Log files processed": "Tổng số file log runtime đã đọc qua SDK.",
    "Log bytes processed": "Tổng bytes log được xử lý bên trong runtime, không gửi nguyên văn cho model.",
    "Raw log chars returned": "Số ký tự log thô runtime trả về tool result; mục tiêu Code Mode là 0.",
}


def render_observability_metrics(observer: Observer) -> None:
    st.markdown("##### Usage & Code Mode evidence")
    token_keys = list(TOKEN_HELP)
    for pair in (token_keys[:2], token_keys[2:]):
        for col, key in zip(st.columns(2, gap="small"), pair):
            col.metric(key, f"{getattr(observer, {'Input tokens': 'input_tokens', 'Output tokens': 'output_tokens', 'Cached input tokens': 'cached_input_tokens'}[key]):,}", help=TOKEN_HELP[key], border=True)
    code_keys = list(CODE_MODE_HELP)
    for pair in (code_keys[:2], code_keys[2:]):
        for col, key in zip(st.columns(2, gap="small"), pair):
            col.metric(key, f"{getattr(observer, {'Code runs': 'code_runs', 'Log files processed': 'code_files_read', 'Log bytes processed': 'code_input_bytes', 'Raw log chars returned': 'code_raw_log_chars_returned'}[key]):,}", help=CODE_MODE_HELP[key], border=True)
    st.caption("Token usage chỉ hiện khi provider trả usage. Log bytes là dữ liệu xử lý nội bộ; raw log chars returned phải bằng 0 để chứng minh không đưa log thô vào context.")


CONTEXT_NOTE = (
    "Request ở lớp LangChain (system prompt, messages, tool schemas), không phải provider wire payload, "
    "tokenizer output hay nội dung bên trong model."
)


# ---------------------------------------------------------------- session state helpers


def new_conversation() -> None:
    ss = st.session_state
    conv_id = uuid.uuid4().hex[:8]
    ss.conversation_id = conv_id
    ss.observer = Observer(conversation_id=conv_id)
    ss.ui_history = []
    ss.model_history = []
    ss.compaction_archive = []
    ss.compaction_checkpoint = None
    ss.compaction_count = 0
    ss.compaction_stats = None
    ss.code_mode_last_result = None
    ss.pop("snapshot_choice", None)
    ss.pop("message_choice", None)


def init_state() -> None:
    if "observer" not in st.session_state:
        new_conversation()
    st.session_state.setdefault("compact_trigger_chars", COMPACT_TRIGGER_CHARS)
    st.session_state.setdefault("compact_trigger_messages", COMPACT_TRIGGER_MESSAGES)
    st.session_state.setdefault("auto_compact_enabled", AUTO_COMPACT_ENABLED)
    st.session_state.setdefault("code_mode_last_result", None)


def render_memory_lab() -> None:
    with st.expander("Project memory (Challenge A)", expanded=False):
        st.caption("Trạng thái file `memory/memory.json`. Agent cập nhật qua tool `remember_convention` hoặc `search_memory`.")
        records = memory_store.load_records()
        current = [r for r in records if r.status == "current"]
        history = [r for r in records if r.status != "current"]
        st.markdown(f"**Đang áp dụng ({len(current)})**")
        if not current:
            st.info("Chưa có quy ước `current` nào.")
        for r in current:
            st.markdown(f"- **{r.topic}** (`v{r.version}`): {r.rule}")
        if history:
            with st.expander(f"Lịch sử quy ước cũ ({len(history)})", expanded=False):
                for r in history:
                    st.caption(f"- **{r.topic}** (`v{r.version}`, {r.status}): {r.rule}")


def render_tool_step(started: dict, finished: dict | None) -> None:
    tool_name = started.get("tool_name", "tool")
    args = started.get("arguments", {})
    args_preview = ", ".join(f"{k}={v!r}" for k, v in list(args.items())[:2])
    with st.status(f"`{tool_name}({args_preview})`", expanded=False) as status:
        st.caption(f"tool_call_id: `{started.get('tool_call_id')}`")
        st.markdown("**Arguments**")
        st.json(args)
        if finished is None:
            st.warning("Đang chạy hoặc lượt đã kết thúc trước khi có kết quả.")
            status.update(label=f"`{tool_name}`: đang chạy...", state="running")
        else:
            st.markdown("**Result**")
            result = finished.get("result", {})
            st.json(result)
            err = finished.get("error")
            if err:
                st.error(f"Error: {err}")
                status.update(label=f"`{tool_name}`: lỗi", state="error")
            else:
                status.update(label=f"`{tool_name}`: xong", state="complete")


def render_events(events: list[dict]) -> None:
    if not events:
        return
    finished = {e["tool_call_id"]: e for e in events if e.get("event") == "tool_finished"}
    shown = False
    for event in events:
        kind = event.get("event")
        if kind == "user_submitted":
            shown = True
            st.caption(f":material/person: {event.get('user_message', '')[:120]}")
        elif kind == "model_request":
            shown = True
            calls = event.get("model_call_index", "")
            msgs = len(event.get("messages", []))
            st.caption(f":material/send: Model request #{calls} ({msgs} messages)")
        elif kind == "model_response":
            shown = True
            tc = len(event.get("tool_calls", []))
            stop = event.get("finish_reason", "")
            st.caption(f":material/reply: Model response (tool_calls={tc}, finish_reason={stop})")
        elif kind == "tool_started":
            shown = True
            render_tool_step(event, finished.get(event["tool_call_id"]))
        elif kind == "context_compacted":
            shown = True
            st.info(
                f":material/compress: **Context đã compact trước lượt này**: "
                f"{event.get('before_chars', 0):,} → {event.get('after_chars', 0):,} ký tự | "
                f"lý do: {event.get('reason', 'compact thủ công')}",
                icon=":material/compress:",
            )
        elif kind == "run_failed":
            st.error(f"Lượt thất bại: {event.get('error')}", icon=":material/error:")
    if not shown:
        st.caption("Chưa có sự kiện nào được ghi nhận.")


def render_chat_column(trace_writer: TraceWriter | None) -> None:
    st.markdown("#### Trò chuyện")
    ss = st.session_state
    if not ss.ui_history:
        st.info("Chưa có tin nhắn. Nhập câu hỏi bên dưới để bắt đầu.")
        return

    for idx, turn in enumerate(ss.ui_history):
        with st.chat_message("user"):
            st.markdown(turn["user"])
        with st.chat_message("assistant"):
            if turn.get("error"):
                st.error(f"Lỗi: {turn['error']}")
            if turn.get("events"):
                with st.expander(f"Chi tiết lượt {turn['chat_turn']} ({len(turn['events'])} events)", expanded=False):
                    render_events(turn["events"])
            if turn.get("answer"):
                st.markdown(turn["answer"])
            elif not turn.get("error"):
                st.caption("Chưa có phản hồi hoàn tất.")


def render_counters(observer: Observer) -> None:
    metrics = observer.metrics_summary()
    keys = list(metrics.keys())
    row1 = keys[:2]
    row2 = keys[2:]
    for pair in (row1, row2):
        cols = st.columns(len(pair))
        for col, key in zip(cols, pair):
            help_text = COUNTER_HELP.get(key, "")
            col.metric(key, metrics[key], help=help_text)


def render_tools_card(observer: Observer) -> None:
    st.markdown("##### Tools đang cấp cho agent")
    mode = getattr(st.session_state, "agent_mode", "native")
    tools = selected_tools(mode)
    schemas = [tool_schema(t) for t in tools]
    for s in schemas:
        with st.expander(f"`{s['name']}`", expanded=False):
            st.caption(s["description"])
            st.json(s["parameters"])


def render_skills_card() -> None:
    st.markdown("##### Skills tìm thấy trong `workspace/skills/`")
    mode = getattr(st.session_state, "agent_mode", "native")
    caps = capabilities(mode)
    diagnostics = caps.get("skill_diagnostics", [])
    if diagnostics:
        for diag in diagnostics:
            level = diag.get("level", "warning")
            path = diag.get("path", "")
            msg = diag.get("message", "")
            if level == "error":
                st.error(f"Skill lỗi ({path}): {msg}")
            else:
                st.warning(f"Skill ({path}): {msg}")

    skills = caps.get("skills", [])
    if not skills:
        st.info("Không phát hiện skill nào trong `workspace/skills/`.")
        return
    for s in skills:
        with st.expander(f"Skill `{s['name']}`", expanded=False):
            st.caption(s.get("description", ""))
            st.caption(f"Location: `{s.get('location', '')}`")


def render_context(observer: Observer, interactive: bool) -> None:
    st.markdown("#### Context & Snapshot")
    st.caption(CONTEXT_NOTE)
    snapshots = observer.snapshots
    if not snapshots:
        st.info("Chưa có snapshot model request nào. Gửi tin nhắn để quan sát request đầu tiên.")
        render_observability_metrics(observer)
        return

    latest_idx = len(snapshots) - 1
    if interactive:
        choice = st.selectbox(
            "Chọn model request",
            options=range(len(snapshots)),
            index=latest_idx,
            format_func=lambda i: (
                f"Lượt {snapshots[i]['chat_turn']} — Call #{snapshots[i]['model_call_index']} "
                f"({len(snapshots[i].get('messages', []))} msgs, {len(snapshots[i].get('tools', []))} tools)"
            ),
            key="snapshot_choice",
        )
    else:
        choice = latest_idx

    snap = snapshots[choice]
    st.caption(
        f"Conversation `{snap['conversation_id']}` | Lượt {snap['chat_turn']} | "
        f"Call #{snap['model_call_index']} lúc `{snap['timestamp']}`"
    )

    with st.expander("System prompt", icon=":material/description:"):
        st.code(snap.get("system_prompt", ""), language=None, wrap_lines=True)

    with st.expander(f"Messages gửi model ({len(snap.get('messages', []))})", icon=":material/chat:", expanded=True):
        raw_msgs = snap.get("messages", [])
        if not raw_msgs:
            st.caption("Danh sách message rỗng.")
        else:
            for i, m in enumerate(raw_msgs):
                role = m.get("role", "unknown")
                preview = message_text(m)
                tc = len(m.get("tool_calls", []))
                extra = f" (+{tc} tool_calls)" if tc else ""
                icon = ":material/person:" if role == "user" else (":material/smart_toy:" if role == "assistant" else ":material/build:")
                st.markdown(f"**#{i} {icon} {role}**{extra}")
                if preview:
                    st.text(preview[:500] + ("..." if len(preview) > 500 else ""))
                if m.get("tool_calls"):
                    st.caption("Tool calls:")
                    st.json(m["tool_calls"])

    with st.expander(f"Tool schemas khai báo ({len(snap.get('tools', []))})", icon=":material/handyman:"):
        for ts in snap.get("tools", []):
            st.markdown(f"- `{ts.get('name')}`: {ts.get('description', '')}")

    meta = {
        "run_id": snap.get("run_id"),
        "model_call_index": snap.get("model_call_index"),
        "timestamp": snap.get("timestamp"),
        "usage": snap.get("usage"),
    }
    with st.expander("Snapshot: metadata", icon=":material/info:"):
        st.json(meta, expanded=True)
        st.caption("usage chỉ có khi provider thực sự trả về sau response. Không có token estimate.")
    render_observability_metrics(observer)


def render_event_log(observer: Observer) -> None:
    st.markdown("#### Event log (toàn bộ phiên)")
    events = observer.events
    if not events:
        st.info("Chưa có event nào.")
        return
    st.caption(f"Tổng số {len(events)} events ghi nhận trong session này.")
    summary_rows = []
    for e in events:
        summary_rows.append({
            "seq": e.get("event_seq"),
            "event": e.get("event"),
            "chat_turn": e.get("chat_turn"),
            "time": e.get("timestamp", "")[11:19],
            "detail": (
                e.get("user_message", "")
                or e.get("tool_name", "")
                or e.get("finish_reason", "")
                or ("lỗi" if e.get("error") else "")
            )[:40],
        })
    st.dataframe(summary_rows, use_container_width=True, hide_index=True)


def render_state_column(observer: Observer) -> None:
    color, icon = STATUS_BADGE.get(observer.status, ("gray", ":material/help:"))
    st.markdown(
        f"**Trạng thái agent:** :{color}[{icon} {observer.status.upper()}] "
        f"— `{observer.conversation_id}`"
    )
    if observer.running_tools:
        names = ", ".join(observer.running_tools.values())
        st.caption(f"Tools đang xử lý: `{names}`")

    tab_context, tab_trace, tab_skills, tab_memory = st.tabs([
        "Context & Snapshot",
        "Event log",
        "Tools & Skills",
        "Project Memory",
    ])

    with tab_context:
        render_counters(observer)
        st.divider()
        render_context(observer, interactive=True)
    with tab_trace:
        render_event_log(observer)
    with tab_skills:
        render_tools_card(observer)
        st.divider()
        render_skills_card()
    with tab_memory:
        render_memory_lab()


# ---------------------------------------------------------------- run one turn


def final_answer(messages: list) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage) and not message.tool_calls:
            return message.text
    return ""


DEMO_CODE = '''files = tools.list_files("**/*.log")
rows = []
total_errors = 0
for path in files:
    content = tools.read_file(path)
    errors = content.count("ERROR")
    warnings = content.count("WARN")
    total_errors += errors
    rows.append({"file": path, "errors": errors, "warnings": warnings})
result = {"files_scanned": len(files), "total_errors": total_errors, "by_file": rows}'''


def compact_current_session(force: bool = True) -> dict | None:
    """Compact thủ công hoặc kiểm tra ngưỡng để compact tự động."""
    ss = st.session_state
    messages = list(ss.model_history)
    if not messages:
        return None
    trigger_chars = int(ss.get("compact_trigger_chars", COMPACT_TRIGGER_CHARS))
    trigger_messages = int(ss.get("compact_trigger_messages", 0))
    needed, reason = should_compact(messages, trigger_chars)
    if not needed and trigger_messages > 0 and len(messages) >= trigger_messages:
        needed = True
        reason = f"số message đạt {len(messages)}, vượt ngưỡng {trigger_messages}"
    if not force and not needed:
        return None
    compacted = compact_messages(messages, prior_checkpoint=ss.get("compaction_checkpoint"))
    ss.compaction_archive.extend(compacted.archived_messages)
    ss.model_history = compacted.messages
    ss.compaction_checkpoint = compacted.checkpoint.__dict__
    ss.compaction_count += 1
    ss.compaction_stats = {
        "reason": reason if needed else "compact thủ công",
        "before_chars": compacted.before_chars,
        "after_chars": compacted.after_chars,
        "archived_messages": len(compacted.archived_messages),
        "archived_tool_chars": compacted.checkpoint.archived_tool_chars,
    }
    ss.observer.sync_messages(ss.model_history)
    return ss.compaction_stats


def load_long_session_demo() -> None:
    """Seed a long CSV-analysis session matching the agent's original skills, no API call."""
    ss = st.session_state
    history = [HumanMessage(content=(
        "Phân tích data/workload.csv theo ngưỡng quá tải 8 giờ; tổng đúng bằng 8 không tính quá tải. "
        "Không sửa CSV và ghi rõ các dòng bị loại cùng lý do."
    ))]
    for index in range(8):
        call_id = f"demo-call-{index:02d}"
        input_file = "data/missing.csv" if index == 0 else "data/workload.csv"
        command = f"python skills/csv-quality/scripts/check_csv.py --input {input_file} --max-hours 8"
        history.append(AIMessage(content="", tool_calls=[{"name": "bash", "args": {"command": command}, "id": call_id, "type": "tool_call"}]))
        if index == 0:
            payload = {"ok": False, "exit_code": 2, "stderr": "File not found: data/missing.csv", "error": {"code": "INPUT_NOT_FOUND"}}
            status = "error"
        else:
            payload = {
                "ok": True,
                "exit_code": 0,
                "path": "data/workload.csv",
                "row_count": 6,
                "overloaded_owners": ["Lan"],
                "stdout": (f"synthetic CSV analysis result {index}; owner totals and excluded row details\n" * 230),
            }
            status = "success"
        history.append(ToolMessage(content=json.dumps(payload, ensure_ascii=False), name="bash", tool_call_id=call_id, status=status))
        if index == 0:
            assistant_text = "Lần chạy thất bại vì đường dẫn data/missing.csv không tồn tại; chưa có kết quả phân tích. Bước tiếp theo: chạy lại với data/workload.csv."
        else:
            assistant_text = "Đã chạy script CSV thành công với ngưỡng 8 giờ; tổng bằng ngưỡng không bị tính quá tải. Bước tiếp theo: đối chiếu skill và template rồi ghi output/csv-quality.md, giữ nguyên CSV nguồn."
        history.append(AIMessage(content=assistant_text))
        if index in (2, 5):
            history.append(HumanMessage(content="Tiếp tục giữ ngưỡng 8 giờ, không sửa dữ liệu nguồn và liệt kê từng dòng bị loại với đầy đủ mã lý do."))
    ss.model_history = history
    ss.compaction_archive = []
    ss.compaction_checkpoint = None
    ss.compaction_stats = None
    ss.observer.sync_messages(history)


def render_compaction_lab() -> None:
    with st.expander("B1 · Compact session dài (bắt buộc)", expanded=True):
        st.caption("Compaction tạo checkpoint có cấu trúc; giữ mục tiêu, ràng buộc, lỗi, cách đã thử, file và bước kế tiếp. Tool output dài được lưu trong archive của session, không đưa lại vào active messages.")
        ss = st.session_state
        auto_col, threshold_col = st.columns([1, 2])
        with auto_col:
            ss.auto_compact_enabled = st.toggle(
                "Compact tự động",
                value=ss.get("auto_compact_enabled", AUTO_COMPACT_ENABLED),
                key="auto_compact_toggle",
                help="Khi bật, context sẽ tự compact trước lượt gọi model kế tiếp nếu vượt ngưỡng.",
            )
        with threshold_col:
            ss.compact_trigger_chars = st.number_input(
                "Ngưỡng tự compact (ký tự context ước tính)", min_value=2_000, max_value=200_000,
                value=int(ss.get("compact_trigger_chars", COMPACT_TRIGGER_CHARS)), step=2_000, key="compact_trigger_input",
                help="Đây là ngưỡng ký tự, không phải số token. Snapshot API vẫn hiển thị usage do provider trả về.",
            )
        ss.compact_trigger_messages = st.number_input(
            "Ngưỡng tự compact (số message, 0 = tắt)", min_value=0, max_value=500,
            value=int(ss.get("compact_trigger_messages", COMPACT_TRIGGER_MESSAGES)), step=5, key="compact_trigger_msg_input",
            help="Nếu > 0, compact khi số message trong history đạt ngưỡng này (bổ sung ngưỡng ký tự).",
        )

        before = message_chars(ss.model_history)
        st.metric("Active message context", f"{before:,} ký tự", help="Ước lượng theo nội dung message; không tính tokenizer của provider.")
        if not ss.model_history:
            st.info("Session đang trống. Câu hỏi đầu tiên sẽ gọi model ngay vì chưa có lịch sử để compact. Muốn thử tự compact, bấm **Nạp session dài mẫu** trước rồi xác nhận context tăng lên khoảng 120.000 ký tự.")
        elif before < int(ss.compact_trigger_chars):
            st.caption(f"Context hiện dưới ngưỡng {int(ss.compact_trigger_chars):,} ký tự; lượt chat tiếp theo có thể gọi model mà không compact.")
        else:
            st.info("Context đã vượt ngưỡng. Khi gửi câu hỏi tiếp theo, app sẽ compact trước rồi mới gọi model.")
        left, right = st.columns(2)
        if left.button("Nạp session dài mẫu", key="load_compact_demo", help="Tạo history giả có lỗi và log lớn; không gọi API."):
            load_long_session_demo()
            st.rerun()
        if right.button("Compact ngay", key="compact_now", disabled=not ss.model_history):
            compact_current_session(force=True)
            st.rerun()
        if ss.compaction_stats:
            st.success(
                f"Đã compact: {ss.compaction_stats['before_chars']:,} → {ss.compaction_stats['after_chars']:,} ký tự; "
                f"archive {ss.compaction_stats['archived_messages']} messages / {ss.compaction_stats['archived_tool_chars']:,} ký tự tool output."
            )
            with st.expander("Checkpoint đang được dùng"):
                st.json(ss.compaction_checkpoint, expanded=True)
            st.caption(f"Archive cục bộ có {len(ss.compaction_archive)} message; các message này không nằm trong active context gửi model.")


def render_code_mode_results(result: dict) -> None:
    if not result:
        return
    if not result.get("ok"):
        st.error(f"Lỗi runtime: {result.get('error', 'Lỗi không xác định')}", icon=":material/error:")
    else:
        st.success("Chương trình thực thi thành công!", icon=":material/check_circle:")
        st.markdown("**Kết quả (gán từ `result`):**")
        st.json(result["result"], expanded=True)
        output_text = json.dumps(result["result"], ensure_ascii=False, default=str)
        if len(output_text) > CODE_MODE_MAX_OUTPUT_CHARS:
            st.warning(
                f"⚠️ Đầu ra có {len(output_text):,} ký tự, vượt giới hạn {CODE_MODE_MAX_OUTPUT_CHARS:,}. "
                f"Runtime sẽ yêu cầu giảm kết quả.",
                icon=":material/warning:",
            )

    meta_cols = st.columns(3)
    meta_cols[0].metric("Files đã đọc", result.get("files_read", "N/A"))
    meta_cols[1].metric("Bytes xử lý", f"{result.get('input_bytes_processed', 0):,}")
    meta_cols[2].metric("Runtime", f"{result.get('runtime_ms', 0)} ms")
    st.metric("Raw log chars → model", result.get("raw_log_chars_returned_to_model", 0),
              help="Code Mode không trả raw log về model; giá trị này luôn là 0.")
    st.caption("Chạy thử trực tiếp không gọi model; khi chat Code Mode, usage input/output thật hiện trong snapshot nếu provider trả về.")


def render_code_mode_lab(mode: str) -> None:
    if mode != "code":
        return
    with st.expander("B2 · Code Mode (điểm cộng)", expanded=True):
        st.caption("Model chỉ được cấp run_code cùng memory tools. Chương trình đọc log qua SDK nội bộ, tổng hợp kết quả rồi mới trả findings; raw log không được trả lại model.")
        code = st.text_area("Chương trình tổng hợp log (Python subset giới hạn)", value=DEMO_CODE, height=210, key="code_mode_editor")
        col_run, col_reset = st.columns([1, 1])
        if col_run.button("Chạy thử runtime cục bộ", key="run_code_demo", icon=":material/play_arrow:"):
            res = execute_code(code)
            st.session_state.code_mode_last_result = res
        if col_reset.button("Xóa kết quả chạy thử", key="clear_code_demo"):
            st.session_state.code_mode_last_result = None

        if st.session_state.get("code_mode_last_result"):
            render_code_mode_results(st.session_state.code_mode_last_result)


def run_turn(text: str, settings, state_box, mode: str = "native") -> None:
    """Chạy agent đúng một lần cho input này; cập nhật trace và state theo event thực tế."""
    ss = st.session_state
    observer: Observer = ss.observer
    run_id = uuid.uuid4().hex
    observer.start_turn(run_id)

    turn = {"run_id": run_id, "chat_turn": observer.chat_turn, "user": text, "answer": "", "error": None, "events": []}
    ss.ui_history.append(turn)
    working = list(ss.model_history) + [HumanMessage(content=text)]

    compacted_before_turn = None
    if ss.get("auto_compact_enabled", AUTO_COMPACT_ENABLED) and len(ss.model_history) > 0:
        trigger_chars = int(ss.get("compact_trigger_chars", COMPACT_TRIGGER_CHARS))
        trigger_messages = int(ss.get("compact_trigger_messages", 0))
        needed, reason = should_compact(working, trigger_chars)
        if not needed and trigger_messages > 0 and len(working) >= trigger_messages:
            needed = True
            reason = f"số message đạt {len(working)}, vượt ngưỡng {trigger_messages}"
        if needed:
            before_chars = message_chars(working)
            result = compact_messages(working, keep_turns=1, prior_checkpoint=ss.get("compaction_checkpoint"))
            ss.compaction_archive.extend(result.archived_messages)
            ss.compaction_checkpoint = result.checkpoint.__dict__
            ss.compaction_count += 1
            compacted_before_turn = {
                "reason": reason,
                "before_chars": before_chars,
                "after_chars": result.after_chars,
                "archived_messages": len(result.archived_messages),
                "archived_tool_chars": result.checkpoint.archived_tool_chars,
            }
            ss.compaction_stats = compacted_before_turn
            ss.model_history = result.messages[:-1]
            working = result.messages

    def emit(event_type: str, data: dict | None = None) -> None:
        event = observer.record(event_type, data)
        turn["events"].append(event)
        with state_box.container():
            render_state_column(observer)

    emit("user_submitted", {"user_message": text})
    if compacted_before_turn is not None:
        emit("context_compacted", compacted_before_turn)

    trace_writer = None
    try:
        trace_writer = TraceWriter(paths.TRACES_DIR, observer.conversation_id, run_id, observer.chat_turn)
        observer.attach_trace(trace_writer)
    except Exception as exc:
        log_exception(f"Trace writer init failed: {exc}")

    model = build_model(settings)
    agent = build_agent(model, mode=mode)
    inputs = {"messages": working}

    try:
        observer.status = STATUS_MODEL
        for raw_event in agent.stream(inputs, stream_mode="updates", config={"recursion_limit": RECURSION_LIMIT}):
            for node_name, update in raw_event.items():
                if node_name == "agent":
                    messages = update.get("messages", [])
                    working.extend(messages)
                    observer.sync_messages(working)
                    for m in messages:
                        if isinstance(m, AIMessage) and m.tool_calls:
                            observer.status = STATUS_TOOL
                            for call in m.tool_calls:
                                emit("tool_started", {
                                    "tool_name": call.get("name"),
                                    "arguments": call.get("args", {}),
                                    "tool_call_id": call.get("id"),
                                })
                elif node_name == "tools":
                    messages = update.get("messages", [])
                    working.extend(messages)
                    observer.sync_messages(working)
                    for m in messages:
                        if isinstance(m, ToolMessage):
                            emit("tool_finished", {
                                "tool_call_id": m.tool_call_id,
                                "tool_name": m.name,
                                "result": serialize_message(m),
                                "status": getattr(m, "status", "success"),
                            })
                    observer.status = STATUS_MODEL

        ss.model_history = working
        turn["answer"] = final_answer(working)
        emit("run_completed")
    except Exception as exc:
        log_exception(f"Agent run failed: {exc}")
        observer.status = STATUS_ERROR
        turn["error"] = str(exc)
        emit("run_failed", {"error": str(exc)})
    finally:
        if trace_writer:
            trace_writer.close()


# ---------------------------------------------------------------- main layout


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    st.html(PAGE_CSS)

    try:
        ensure_workspace()
    except WorkspaceError as exc:
        st.error(f"Workspace reset failed: {exc}")
        return

    init_state()
    ss = st.session_state
    observer: Observer = ss.observer

    header = st.container()
    header.title(APP_TITLE)
    header.button("Cuộc trò chuyện mới", icon=":material/add_comment:", on_click=new_conversation)
    st.caption(CAPABILITY_TEXT)

    default_index = 1 if DEFAULT_MODE == "code" else 0
    mode_label = st.radio(
        "Chế độ tool",
        ["Native tools + Compact", "Code Mode"],
        index=default_index,
        horizontal=True,
        key="challenge_mode_label",
        help="Compaction luôn hoạt động ở cả hai chế độ. Code Mode thay các tool file/shell bằng một tool chạy chương trình tổng hợp log.",
    )
    mode = "code" if "Code Mode" in mode_label else "native"
    st.session_state.agent_mode = mode

    settings, missing = load_settings()
    if missing:
        st.error(
            f"Thiếu cấu hình trong `.env`: `{', '.join(missing)}`. "
            f"Vui lòng tạo hoặc bổ sung `.env` từ `.env.example` trước khi chat."
        )

    render_compaction_lab()
    render_code_mode_lab(mode)

    chat_col, state_col = st.columns([1, 1], gap="medium")
    with chat_col:
        with st.container(key="chat_panel"):
            render_chat_column(None)
    with state_col:
        state_box = st.container(key="state_panel")
        with state_box:
            render_state_column(observer)

    user_input = st.chat_input("Nhập câu hỏi hoặc yêu cầu xử lý...")
    if user_input:
        if not settings:
            st.error("Không thể gửi request: chưa đủ cấu hình `.env`.")
            return
        run_turn(user_input.strip(), settings, state_box, mode=mode)
        st.rerun()


if __name__ == "__main__":
    main()
