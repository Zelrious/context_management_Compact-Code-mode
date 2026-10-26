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
[data-testid="stAppScrollToBottomContainer"] { overflow: hidden; }
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


# ---------------------------------------------------------------- session state


def new_conversation() -> None:
    """Xóa conversation state; không xóa traces trên đĩa hay file output."""
    ss = st.session_state
    ss.conversation_id = uuid.uuid4().hex
    ss.model_history = []  # LangChain messages đầy đủ (AI, tool calls, tool results)
    ss.ui_history = []  # mỗi lượt: user text, final answer, trace events, lỗi
    ss.observer = Observer(conversation_id=ss.conversation_id)
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
    """Controls let the learner inspect and revise durable memory without an API call."""
    with st.expander("Persistent memory lab · lưu, đổi và kiểm tra qua session", expanded=True):
        st.caption("Tất cả quy ước được lưu trong cùng một file memory/memory.json. Khi cập nhật, hệ thống ghi phiên bản mới vào file đó và đánh dấu bản cũ là superseded để giữ lịch sử.")
        current_topics = [entry["topic"] for entry in memory_store.current_entries()]
        create_topic = "Tạo chủ đề mới…"
        with st.form("memory_form", clear_on_submit=True):
            selected_topic = st.selectbox("Chủ đề", [create_topic, *current_topics], help="Chọn chủ đề hiện có để cập nhật; chỉ tạo mới nếu đây là một quy ước khác.")
            topic = st.text_input("Tên chủ đề mới", placeholder="API, database, ngôn ngữ trả lời…") if selected_topic == create_topic else selected_topic
            replaced_topics = st.multiselect(
                "Gộp và thay thế thêm các chủ đề hiện hành cùng nghĩa",
                [value for value in current_topics if value != topic],
                help="Các mục chọn ở đây vẫn được giữ trong file với trạng thái superseded.",
            )
            rule = st.text_area("Nội dung quy ước", placeholder="Mọi câu trả lời bằng tiếng Việt; hoặc test dùng pytest -q…")
            submitted = st.form_submit_button("Lưu quy ước vào memory.json")
            if submitted and topic.strip() and rule.strip():
                try:
                    memory_store.record_rule(topic.strip(), rule.strip(), "manual_ui", replaced_topics=replaced_topics)
                    st.success(f"Đã lưu quy ước cho chủ đề `{topic.strip()}`.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
        current = memory_store.current_entries()
        st.markdown(f"**Quy ước hiện hành ({len(current)})**")
        if not current:
            st.caption("Chưa có quy ước hiện hành.")
        for item in current:
            st.markdown(f"- **{item['topic']}** (v{item['version']}): {item['rule']}")
        history = memory_store.superseded_entries()
        if history:
            with st.expander(f"Lịch sử quy ước đã thay thế ({len(history)})", expanded=False):
                for item in history:
                    st.caption(f"- **{item['topic']}** v{item['version']} [{item['status']}]: {item['rule']}")


def render_events(events: list[dict]) -> None:
    finished = {e["tool_call_id"]: e for e in events if e.get("event") == "tool_finished"}
    for event in events:
        kind = event.get("event")
        if kind == "user_submitted":
            st.caption(f":material/person: {event.get('user_message', '')[:120]}")
        elif kind == "model_request":
            calls = event.get("model_call_index", "")
            msgs = len(event.get("messages", []))
            st.caption(f":material/send: Model request #{calls} ({msgs} messages)")
        elif kind == "model_response":
            tc = len(event.get("tool_calls", []))
            stop = event.get("finish_reason", "")
            st.caption(f":material/reply: Model response (tool_calls={tc}, finish_reason={stop})")
        elif kind == "tool_started":
            render_tool_step(event, finished.get(event["tool_call_id"]))
        elif kind == "context_compacted":
            st.info(
                f":material/compress: **Context đã compact trước lượt này**: "
                f"{event.get('before_chars', 0):,} → {event.get('after_chars', 0):,} ký tự | "
                f"lý do: {event.get('reason', 'compact thủ công')}",
                icon=":material/compress:",
            )
        elif kind == "run_completed":
            st.caption(f":material/check_circle: Hoàn tất ({event.get('answer_chars', 0)} ký tự câu trả lời)")
        elif kind == "run_failed":
            st.error(f"Lượt thất bại: {event.get('error')}", icon=":material/error:")


def render_tool_step(started: dict, done: dict | None) -> None:
    with st.container(border=True):
        st.markdown(f":material/build: **`{started.get('tool_name')}`** `{started.get('tool_call_id', '')[:8]}`")
        render_arguments(started.get("arguments", {}))
        if done:
            render_result(done.get("result"))
        else:
            st.caption("Chưa có kết quả.")


def render_arguments(arguments: dict) -> None:
    if not arguments:
        st.caption("Không có tham số.")
        return
    for key, value in arguments.items():
        st.caption(f"Tham số `{key}`:")
        text = str(value)
        if "\n" in text or len(text) > 80:
            st.code(text, language=None, wrap_lines=True)
        else:
            st.text(text)


def render_result(result) -> None:
    if not isinstance(result, dict):
        st.caption("Kết quả:")
        st.text(str(result))
        return
    status = result.get("status")
    status_icon = ":material/check:" if status == "success" else ":material/error:"
    st.caption(f"Kết quả ({status_icon} `{status}`):")
    for key, value in result.items():
        if key in ("status", "path") and value is not None:
            continue
        text = str(value)
        if not text:
            continue
        st.caption(f"`{key}`:")
        if "\n" in text or len(text) > 80:
            st.code(text, language=None, wrap_lines=True)
        else:
            st.text(text)


def render_history() -> None:
    for turn in st.session_state.ui_history:
        with st.chat_message("user"):
            st.markdown(turn["user"])
        with st.chat_message("assistant"):
            if turn["events"]:
                with st.expander(f"Chi tiết lượt {turn['chat_turn']} ({len(turn['events'])} events)", expanded=False):
                    render_events(turn["events"])
            if turn["error"]:
                st.error(turn["error"], icon=":material/error:")
            elif turn["answer"]:
                st.markdown(turn["answer"])
            else:
                st.caption("Chưa có phản hồi hoàn tất.")


def render_counters(observer: Observer) -> None:
    counters = observer.counters()
    status = counters["Trạng thái"]
    color, icon = STATUS_BADGE.get(status, ("gray", ":material/radio_button_unchecked:"))
    row = st.container(horizontal=True, vertical_alignment="center")
    row.badge(status, icon=icon, color=color)
    if observer.running_tools:
        row.caption("đang chạy: " + ", ".join(observer.running_tools.values()))
    row.caption(
        f"conversation `{observer.conversation_id[:8]}` | run `{(observer.run_id or '-')[:8]}`",
        help=f"conversation_id: {observer.conversation_id}\n\nrun_id: {observer.run_id or '-'}",
    )
    keys = list(COUNTER_HELP)
    for pair in (keys[:2], keys[2:]):  # lưới 2x2 để nhãn không bị cắt trong cột hẹp
        for col, key in zip(st.columns(2, gap="small"), pair):
            col.metric(key, counters[key], help=COUNTER_HELP[key], border=True)
    if observer.turn_failed:
        st.error(f"Lượt {observer.chat_turn} lỗi: {observer.error}", icon=":material/error:")
    if observer.history_rolled_back:
        st.warning("Model history đã rollback về trước lượt lỗi. Bảng message giữ trạng thái quan sát cuối của lượt lỗi.")


def render_capabilities(observer: Observer, caps: dict, tool_schemas: list[dict]) -> None:
    with st.expander(f"Tools được cấp ({len(tool_schemas)})", icon=":material/build:"):
        if not tool_schemas:
            st.caption("Project này không đăng ký tool nào với model.")
        for schema in tool_schemas:
            st.markdown(f"**{schema['name']}**")
            st.json(schema, expanded=False)
    with st.expander(f"Skills trong catalog ({len(caps['skills'])})", icon=":material/menu_book:"):
        st.caption("Metadata nằm trong system prompt; có trong catalog chưa có nghĩa là đã load.")
        if not caps["skills"]:
            st.caption("Không có skill trong catalog.")
        for skill in caps["skills"]:
            st.markdown(f"**{skill['name']}** | `{skill['location']}`  \n{skill['description']}")
        for diag in caps["skill_diagnostics"]:
            st.caption(f":material/warning: `{diag['name']}`: {diag['message']}")


def render_messages(observer: Observer, interactive: bool) -> None:
    with st.expander(f"Lịch sử hội thoại ({len(observer.messages)})", icon=":material/chat:", expanded=True):
        if not observer.messages:
            st.caption("Chưa có message.")
            return
        if interactive:
            idx = st.selectbox(
                "Message",
                range(len(observer.messages)),
                index=len(observer.messages) - 1,
                format_func=lambda i: f"#{i} {observer.messages[i].get('role')} ({len(message_text(observer.messages[i]))} ký tự)",
                key="message_choice",
            )
        else:
            idx = len(observer.messages) - 1
        msg = observer.messages[idx]
        st.caption(f"Role: `{msg.get('role')}` | name: `{msg.get('name')}` | tool_call_id: `{msg.get('tool_call_id')}`")
        if msg.get("content"):
            st.code(message_text(msg), language=None, wrap_lines=True)
        if msg.get("tool_calls"):
            st.caption("Tool calls:")
            st.json(msg["tool_calls"], expanded=True)


def render_context(observer: Observer, interactive: bool) -> None:
    st.subheader("Context & Snapshot")
    st.caption(CONTEXT_NOTE)
    snapshots = observer.snapshots
    if not snapshots:
        st.caption("Chưa có model request nào trong session này.")
        render_observability_metrics(observer)
        return
    if interactive:
        choice = st.selectbox(
            "Chọn model request",
            range(len(snapshots)),
            index=len(snapshots) - 1,
            format_func=lambda i: (
                f"#{i + 1} lúc {snapshots[i]['timestamp'][11:19]} — lượt {snapshots[i]['chat_turn']} "
                f"({len(snapshots[i]['messages'])} msgs, {len(snapshots[i]['tools'])} tools)"
            ),
            key="snapshot_choice",
        )
    else:
        choice = len(snapshots) - 1
    snap = snapshots[choice]
    with st.expander(f"System prompt ({len(snap['system_prompt'])} ký tự)", icon=":material/description:"):
        st.code(snap["system_prompt"], language=None, wrap_lines=True)
    with st.expander(f"Messages gửi model ({len(snap['messages'])})", icon=":material/chat:"):
        for index, m in enumerate(snap["messages"]):
            st.caption(f"#{index} role: `{m.get('role')}`")
            text = message_text(m)
            if text:
                st.code(text, language=None, wrap_lines=True)
            if m.get("tool_calls"):
                st.caption("tool_calls:")
                st.json(m["tool_calls"], expanded=True)
    with st.expander(f"Tool schemas khai báo ({len(snap['tools'])})", icon=":material/handyman:"):
        for schema in snap["tools"]:
            st.markdown(f"- `{schema.get('name')}`: {schema.get('description', '')}")
    meta = {"run_id": snap["run_id"], "model_call_index": snap["model_call_index"], "timestamp": snap["timestamp"], "usage": snap["usage"]}
    with st.expander("Snapshot: metadata", icon=":material/info:"):
        st.json(meta, expanded=True)
        st.caption("usage chỉ có khi provider thực sự trả về sau response. Không có token estimate.")
    render_observability_metrics(observer)


def render_event_log(observer: Observer) -> None:
    with st.expander(f"Event log ({len(observer.events)} events)", icon=":material/list:"):
        if not observer.events:
            st.caption("Chưa có event.")
            return
        rows = []
        for e in observer.events:
            rows.append({
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
        st.dataframe(rows, use_container_width=True, hide_index=True)


def render_state_panel(interactive: bool) -> None:
    observer: Observer = st.session_state.observer
    st.subheader("State & Context")
    render_counters(observer)
    render_context(observer, interactive)
    mode = st.session_state.get("agent_mode", "native")
    render_capabilities(observer, capabilities(mode), [tool_schema(t) for t in selected_tools(mode)])
    render_messages(observer, interactive)
    render_event_log(observer)


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

    def refresh() -> None:
        with state_box.container():
            render_state_panel(interactive=False)

    def emit(event_type: str, data: dict | None = None) -> None:
        event = observer.record(event_type, data)
        turn["events"].append(event)
        refresh()

    emit("user_submitted", {"user_message": text})
    if compacted_before_turn is not None:
        emit("context_compacted", compacted_before_turn)

    trace_writer = None
    try:
        trace_writer = TraceWriter(paths.TRACES_DIR, observer.conversation_id, run_id, observer.chat_turn)
        observer.attach_trace(trace_writer)
    except Exception as exc:
        log_exception(paths.TRACES_DIR, exc)

    model = build_model(settings)
    agent = build_agent(model, mode=mode)
    inputs = {"messages": working}

    with st.chat_message("assistant"):
        status = st.status("Agent đang xử lý…", expanded=True)
        answer_box = st.empty()

    try:
        for chunk in agent.stream(inputs, stream_mode=["updates", "custom"], config={"recursion_limit": RECURSION_LIMIT}):
            mode_chunk, chunk = chunk
            if mode_chunk == "custom" and isinstance(chunk, dict) and chunk.get("observer"):
                emit(chunk["event"], chunk["data"])
            elif mode_chunk == "updates" and isinstance(chunk, dict):
                for update in chunk.values():
                    if isinstance(update, dict) and update.get("messages"):
                        working.extend(update["messages"])
                        observer.sync_messages(working)
            refresh()
        turn["answer"] = final_answer(working[len(ss.model_history) + 1 :])
        ss.model_history = working
        emit("run_completed", {"answer_chars": len(turn["answer"])})
        status.update(label="Hoàn tất", state="complete", expanded=False)
        answer_box.markdown(turn["answer"])
    except Exception as exc:  # lỗi provider/runtime: giữ trace, rollback history
        log_exception(paths.TRACES_DIR, exc)
        turn["error"] = f"{type(exc).__name__}: {exc}"
        emit("run_failed", {"error": turn["error"]})
        observer.history_rolled_back = True
        status.update(label="Lỗi", state="error", expanded=True)
        answer_box.error(turn["error"])
    finally:
        refresh()
        if trace_writer:
            trace_writer.close()

    ss.pop("snapshot_choice", None)
    ss.pop("message_choice", None)


# ---------------------------------------------------------------- output files (thao tác UI, không phải tool call)


def output_files() -> list[str]:
    output_dir = paths.WORKSPACE_DIR / paths.OUTPUT_SUBDIR
    if not output_dir.is_dir():
        return []
    return sorted(p.relative_to(paths.WORKSPACE_DIR).as_posix() for p in output_dir.rglob("*") if p.is_file())


def render_output_files() -> None:
    st.subheader("File đầu ra")
    files = output_files()
    if not files:
        st.caption("Chưa có file trong workspace/output/.")
        return
    choice = st.selectbox("Chọn file trong workspace/output/", files, key="output_choice")
    path = paths.WORKSPACE_DIR / choice
    if not path.is_file():
        st.warning("File không còn tồn tại.")
        return
    data = path.read_bytes()
    text = data.decode("utf-8", errors="replace")
    st.caption(f"{choice} | {len(data)} bytes | xem trước trên UI, không gửi vào context của model.")
    with st.container(border=True):
        if path.suffix.lower() in (".md", ".markdown"):
            st.markdown(text)
        else:
            st.code(text, language=None, wrap_lines=True)
    st.download_button("Tải file", data=data, file_name=path.name, on_click="ignore")


# ---------------------------------------------------------------- page


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon=":material/smart_toy:", layout="wide")
    st.html(PAGE_CSS)
    init_state()
    try:
        ensure_workspace()
    except WorkspaceError as exc:
        st.error(f"Không khởi tạo được workspace: {exc}")
        st.stop()
    header = st.container(horizontal=True, vertical_alignment="center")
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
    chat_col, state_col = st.columns([5, 4], gap="medium")
    with chat_col:
        chat_box = st.container(height=PANEL_HEIGHT, border=True, autoscroll=True, key="chat_panel")
        with chat_box:
            if missing:
                st.warning(
                    "Thiếu cấu hình: " + ", ".join(missing) + ". Tạo file .env từ .env.example trong thư mục project, "
                    "điền giá trị rồi chạy lại. App sẽ không gọi model khi thiếu cấu hình."
                )
            if not st.session_state.ui_history:
                st.caption("Gửi yêu cầu ở ô nhập bên dưới để bắt đầu.")
            render_history()
    with state_col:
        with st.container(height=PANEL_HEIGHT, border=True, key="state_panel"):
            render_compaction_lab()
            render_code_mode_lab(mode)
            render_memory_lab()
            state_box = st.empty()
            with state_box.container():
                render_state_panel(interactive=True)
            render_output_files()

    prompt = st.chat_input("Nhập yêu cầu…", disabled=bool(missing))
    if prompt and settings is not None:
        with chat_box:
            run_turn(prompt, settings, state_box, mode)
        st.rerun()


if __name__ == "__main__":
    main()
