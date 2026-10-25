"""Streamlit chat UI: chat bên trái, State & Context bên phải."""

from __future__ import annotations

import json
import uuid

import streamlit as st
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import paths
import memory_store
from agent import TOOLS, build_agent, build_model, capabilities, selected_tools, system_prompt
from compaction import DEFAULT_TRIGGER_CHARS, compact_messages, message_chars, should_compact
from config import APP_TITLE, CAPABILITY_TEXT, COMPACT_TRIGGER_CHARS, PROJECT_NAME, RECURSION_LIMIT, load_settings
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
from tools.code_mode import execute_code

PANEL_HEIGHT = 600  # chiều cao dự phòng (px); CSS bên dưới kéo hai cột theo chiều cao cửa sổ
# Streamlit không có tùy chọn chiều cao theo viewport, nên dùng một khối CSS nhỏ:
# khóa cuộn của trang, hai cột (key chat_panel / state_panel) cao bằng phần còn lại của cửa sổ.
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
    ss.pop("snapshot_choice", None)
    ss.pop("message_choice", None)


def init_state() -> None:
    if "observer" not in st.session_state:
        new_conversation()


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
                help="Các mục chọn ở đây vẫn được giữ trong lịch sử nhưng chuyển thành superseded.",
            )
            content = st.text_area("Quy ước hiện hành", placeholder="Ví dụ: API dùng REST và trả lỗi theo RFC 9457.")
            source = st.text_input("Nguồn xác nhận", value="user confirmed")
            submitted = st.form_submit_button("Lưu / cập nhật quy ước", type="primary")
        if submitted:
            result = memory_store.remember(topic, content, source, replaces_topics=replaced_topics)
            if result.get("ok"):
                st.success(f"Đã cập nhật trong cùng file memory/memory.json; {len(result['superseded'])} phiên bản cũ được giữ trong lịch sử và đánh dấu superseded. Quy ước hiện hành được nạp vào request tiếp theo.")
                st.rerun()
            else:
                st.error(result["error"])
        left, right = st.columns(2)
        left.button("Tạo session mới", icon=":material/add_comment:", on_click=new_conversation, help="Giữ kho memory; xóa lịch sử chat của session hiện tại.")
        right.caption(f"Session hiện tại: `{st.session_state.conversation_id[:8]}`")
        st.markdown("**Memory sẽ được inject vào system context của session mới**")
        st.code(memory_store.render_injected_memory(), language="markdown", wrap_lines=True)
        entries = memory_store.history_entries()
        with st.expander(f"Lịch sử memory ({len(entries)} phiên bản)"):
            if entries:
                st.dataframe([
                    {"chủ đề": e["topic"], "trạng thái": e["status"], "quy ước": e["content"], "nguồn": e["source"], "cập nhật": e["updated_at"], "thay thế": ", ".join(e["supersedes"])}
                    for e in entries
                ], hide_index=True, width="stretch")
            else:
                st.caption("Chưa lưu quy ước. Có thể thêm tại đây để kiểm tra vòng đời memory mà không gọi API.")


# ---------------------------------------------------------------- trace rendering


def usage_text(usage: dict | None) -> str:
    return f" | {usage.get('input_tokens')} in / {usage.get('output_tokens')} out tokens" if usage else ""


def render_events(events: list[dict]) -> None:
    """Các bước thực hiện của một lượt: model calls và tool calls theo đúng thứ tự event."""
    finished = {e["tool_call_id"]: e for e in events if e["type"] == "tool_finished"}
    shown = False
    for event in events:
        kind = event["type"]
        if kind == "model_request":
            shown = True
            st.markdown(
                f":material/smart_toy: **Model call #{event['model_call_index']}** | "
                f"{event['message_count']} messages | {event['tool_count']} tools"
            )
        elif kind == "model_response":
            requested = event.get("tool_calls_requested") or []
            if requested:
                names = ", ".join(f"`{c['name']}`" for c in requested)
                st.caption(f"↳ yêu cầu {len(requested)} tool call: {names}{usage_text(event.get('usage'))}")
            else:
                st.caption(f"↳ trả câu trả lời, không yêu cầu tool{usage_text(event.get('usage'))}")
        elif kind == "tool_started":
            shown = True
            render_tool_step(event, finished.get(event["tool_call_id"]))
        elif kind == "run_failed":
            st.error(f"Lượt thất bại: {event.get('error')}", icon=":material/error:")
    if not shown:
        st.caption("Chưa có bước nào.")


def render_tool_step(started: dict, done: dict | None) -> None:
    with st.container(border=True):
        header = st.container(horizontal=True, vertical_alignment="center")
        header.markdown(f":material/build: **{started['tool_name']}**")
        if done is None:
            header.badge("đang chạy", color="orange")
        else:
            result = done.get("result")
            failed = done.get("status") in ("error", "exception") or (isinstance(result, dict) and result.get("ok") is False)
            header.badge("lỗi" if failed else "ok", color="red" if failed else "green")
            if done.get("elapsed_ms") is not None:
                header.caption(f"{done['elapsed_ms']} ms")
        st.caption(f"tool_call_id `{started['tool_call_id']}`")
        render_arguments(started.get("arguments", {}))
        if done is None:
            return
        if done.get("status") == "exception":
            st.error(f"Exception khi chạy tool: {done.get('error')}")
        elif failed:
            st.caption("Tool trả lỗi; lỗi được gửi lại model dưới dạng tool result.")
        render_result(done.get("result"))


def render_arguments(arguments: dict) -> None:
    if set(arguments) == {"command"}:
        st.code(arguments["command"], language="bash", wrap_lines=True)
    else:
        st.json(arguments, expanded=True)


TEXT_RESULT_FIELDS = ("content", "stdout", "stderr")


def render_result(result) -> None:
    """Result có cấu trúc bằng st.json; trường văn bản (content/stdout/stderr) bằng st.code, hiện đầy đủ."""
    st.caption("Result")
    if isinstance(result, dict):
        st.json({k: v for k, v in result.items() if k not in TEXT_RESULT_FIELDS}, expanded=True)
        for key in TEXT_RESULT_FIELDS:
            value = result.get(key)
            if isinstance(value, str) and value:
                st.caption(key)
                st.code(value, language=None, wrap_lines=True)
    elif isinstance(result, list):
        st.json(result, expanded=True)
    elif result is not None:
        st.code(str(result), language=None, wrap_lines=True)


def compact_event(event: dict) -> dict:
    """Bản gọn của event để gắn vào lượt chat trên UI (snapshot đầy đủ nằm ở observer/trace)."""
    item = {k: v for k, v in event.items() if k != "snapshot"}
    if event["type"] == "model_request":
        item["message_count"] = event["snapshot"]["message_count"]
        item["tool_count"] = len(event["snapshot"]["tools"])
    return item


def steps_label(events: list[dict]) -> str:
    model_calls = sum(1 for e in events if e["type"] == "model_request")
    tool_calls = sum(1 for e in events if e["type"] == "tool_started")
    return f"Các bước thực hiện ({model_calls} model call, {tool_calls} tool call)"


def render_history() -> None:
    for turn in st.session_state.ui_history:
        with st.chat_message("user"):
            st.markdown(turn["user"])
        with st.chat_message("assistant"):
            if turn["answer"]:
                st.markdown(turn["answer"])
            if turn["error"]:
                st.error(turn["error"], icon=":material/error:")
            with st.expander(steps_label(turn["events"]), icon=":material/route:"):
                render_events(turn["events"])


# ---------------------------------------------------------------- state & context panel


def message_rows(messages: list[dict]) -> list[dict]:
    rows = []
    for index, message in enumerate(messages):
        text = message_text(message.get("content", ""))
        calls = message.get("tool_calls") or []
        if calls:
            kind = "tool_calls"
            tool = ", ".join(f"{c['name']} ({c['id']})" for c in calls)
        elif message.get("role") == "tool":
            kind = "tool_result"
            tool = f"{message.get('name')} ({message.get('tool_call_id')})"
        else:
            kind = "text"
            tool = ""
        rows.append(
            {
                "index": index,
                "role": message.get("role"),
                "loại": kind,
                "preview": text[:120] + ("…" if len(text) > 120 else ""),
                "tool / ID": tool,
            }
        )
    return rows


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
            st.warning(diag)
    inventory = observer.inventory()
    with st.expander(f"Skill content đã vào history ({len(inventory['skills'])})", icon=":material/task_alt:"):
        st.caption("Nội dung đã vào history: ghi nhận từ tool result read_file SKILL.md thành công. Không khẳng định model đã hiểu hay làm theo.")
        if not inventory["skills"]:
            st.caption("Chưa có skill nào được đọc trong conversation này.")
        for item in inventory["skills"]:
            st.markdown(
                f"**{item['skill']}** | `{item['path']}` | message #{item['message_index']} | tool_call_id `{item['tool_call_id']}` | "
                f"{item['chars']} ký tự | request đầu tiên chứa nội dung: {item['first_request'] or 'chưa gửi model'}"
            )
    with st.expander(f"Tài nguyên đã đọc ({len(inventory['resources'])})", icon=":material/description:"):
        st.caption("Đọc thành công bằng read_file. Chạy script bằng bash không tính là đã đọc source.")
        if not inventory["resources"]:
            st.caption("Chưa đọc tài nguyên nào.")
        for item in inventory["resources"]:
            st.markdown(f"`{item['path']}` | message #{item['message_index']} | tool_call_id `{item['tool_call_id']}`")


def render_messages(observer: Observer, interactive: bool) -> None:
    with st.expander(f"Message history hiện tại ({len(observer.messages)})", icon=":material/forum:"):
        if not observer.messages:
            st.caption("Chưa có message.")
            return
        st.dataframe(message_rows(observer.messages), hide_index=True, width="stretch", height="content")
        if interactive:
            index = st.selectbox(
                "Xem đầy đủ message",
                options=list(range(len(observer.messages))),
                index=len(observer.messages) - 1,
                key="message_choice",
            )
            st.json(observer.messages[index], expanded=True)
        else:
            st.caption("Message mới nhất:")
            st.json(observer.messages[-1], expanded=False)


def snapshot_label(snapshot: dict) -> str:
    time_part = (snapshot.get("captured_at") or "")[11:19]
    return f"Lượt {snapshot['chat_turn']}, model call #{snapshot['model_call_index']} ({time_part})"


def snapshot_summary(snapshot: dict) -> str:
    usage = snapshot.get("usage")
    tokens = f" | {usage.get('input_tokens')} in / {usage.get('output_tokens')} out tokens" if usage else " | chưa có usage"
    return (
        f"model `{snapshot.get('model_name') or '-'}` | {snapshot['message_count']} messages | "
        f"{len(snapshot['tools'])} tools | {snapshot['system_prompt_chars'] + snapshot['message_chars']} ký tự{tokens}"
    )


def render_context(observer: Observer, interactive: bool) -> None:
    mode = st.session_state.get("agent_mode", "native")
    st.markdown("##### Context gửi model (lớp LangChain)", help=CONTEXT_NOTE)
    snapshots = observer.snapshots
    if not snapshots:
        st.info("Chưa có request nào được gửi. Dưới đây là Context cấu hình, chưa phải request đã gửi.", icon=":material/info:")
        tools = [tool_schema(t) for t in selected_tools(mode)]
        with st.expander("Context cấu hình: system prompt", icon=":material/article:"):
            st.code(system_prompt(mode), language="markdown", wrap_lines=True)
        with st.expander(f"Context cấu hình: tools schema ({len(tools)})", icon=":material/data_object:"):
            st.json(tools, expanded=False)
        pending = [serialize_message(m) for m in st.session_state.model_history]
        if pending:
            with st.expander(f"Context cấu hình: messages đang chờ ({len(pending)})", icon=":material/forum:"):
                st.json(pending, expanded=False)
        return

    if interactive:
        row = st.container(horizontal=True, vertical_alignment="bottom")
        with row:
            choice = st.selectbox(
                "Snapshot request",
                options=list(range(len(snapshots))),
                index=len(snapshots) - 1,
                format_func=lambda i: snapshot_label(snapshots[i]),
                key="snapshot_choice",
            )
        snapshot = snapshots[choice]
        row.download_button(
            "Snapshot JSON",
            icon=":material/download:",
            data=json.dumps(snapshot, ensure_ascii=False, indent=2),
            file_name=f"snapshot_turn{snapshot['chat_turn']:02d}_call{snapshot['model_call_index']:02d}.json",
            mime="application/json",
            on_click="ignore",
        )
        row.download_button(
            "Conversation JSON",
            icon=":material/download:",
            data=json.dumps(observer.export(), ensure_ascii=False, indent=2),
            file_name=f"conversation_{observer.conversation_id[:8]}.json",
            mime="application/json",
            on_click="ignore",
        )
    else:
        snapshot = snapshots[-1]
        st.caption(f"Đang theo dõi snapshot mới nhất: {snapshot_label(snapshot)}")
    st.caption(snapshot_summary(snapshot))

    meta = {k: v for k, v in snapshot.items() if k not in ("system_prompt", "messages", "tools")}
    with st.expander(f"Snapshot: system prompt ({snapshot['system_prompt_chars']} ký tự)", icon=":material/article:"):
        st.code(snapshot["system_prompt"], language="markdown", wrap_lines=True)
    with st.expander(f"Snapshot: messages ({snapshot['message_count']})", icon=":material/forum:"):
        st.dataframe(message_rows(snapshot["messages"]), hide_index=True, width="stretch", height="content")
        st.caption("Nội dung đầy đủ (không rút gọn):")
        st.json(snapshot["messages"], expanded=False)
    with st.expander(f"Snapshot: tools schema ({len(snapshot['tools'])})", icon=":material/data_object:"):
        if not snapshot["tools"]:
            st.caption("Request này không có tool.")
        st.json(snapshot["tools"], expanded=False)
    with st.expander("Snapshot: metadata", icon=":material/info:"):
        st.json(meta, expanded=True)
        st.caption("usage chỉ có khi provider thực sự trả về sau response. Không có token estimate.")


def render_event_log(observer: Observer) -> None:
    with st.expander(f"Event log ({len(observer.events)})", icon=":material/list:"):
        rows = [
            {
                "lượt": e["chat_turn"],
                "seq": e["sequence"],
                "event": e["type"],
                "tool": e.get("tool_name", ""),
                "tool_call_id": e.get("tool_call_id", ""),
                "time": e["timestamp"][11:23],
            }
            for e in observer.events
        ]
        if rows:
            st.dataframe(rows, hide_index=True, width="stretch", height="content")
        else:
            st.caption("Chưa có event.")


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
    ss = st.session_state
    messages = list(ss.model_history)
    if not messages:
        return None
    needed, reason = should_compact(messages, int(ss.get("compact_trigger_chars", COMPACT_TRIGGER_CHARS)))
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
        ss.setdefault("compact_trigger_chars", COMPACT_TRIGGER_CHARS)
        ss.compact_trigger_chars = st.number_input(
            "Ngưỡng tự compact (ký tự context ước tính)", min_value=2_000, max_value=200_000,
            value=int(ss.compact_trigger_chars), step=2_000, key="compact_trigger_input",
            help="Đây là ngưỡng ký tự, không phải số token. Snapshot API vẫn hiển thị usage do provider trả về.",
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


def render_code_mode_lab(mode: str) -> None:
    if mode != "code":
        return
    with st.expander("B2 · Code Mode (điểm cộng)", expanded=True):
        st.caption("Model chỉ được cấp run_code cùng memory tools. Chương trình đọc log qua SDK nội bộ, tổng hợp kết quả rồi mới trả findings; raw log không được trả lại model.")
        code = st.text_area("Chương trình tổng hợp log (Python subset giới hạn)", value=DEMO_CODE, height=210, key="code_mode_editor")
        if st.button("Chạy thử runtime cục bộ", key="run_code_demo"):
            result = execute_code(code)
            st.json(result, expanded=True)
            st.caption("Chạy thử trực tiếp không gọi model; khi chat Code Mode, usage input/output thật hiện trong snapshot nếu provider trả về.")


def run_turn(text: str, settings, state_box, mode: str = "native") -> None:
    """Chạy agent đúng một lần cho input này; cập nhật trace và state theo event thực tế."""
    ss = st.session_state
    observer: Observer = ss.observer
    run_id = uuid.uuid4().hex
    observer.start_turn(run_id)
    writer = TraceWriter(paths.TRACES_DIR, PROJECT_NAME, observer.conversation_id, run_id, observer.chat_turn)
    turn = {"run_id": run_id, "chat_turn": observer.chat_turn, "user": text, "answer": "", "error": None, "events": []}
    ss.ui_history.append(turn)
    working = list(ss.model_history) + [HumanMessage(content=text)]
    compacted_before_turn = None
    needed, reason = should_compact(working, int(ss.get("compact_trigger_chars", COMPACT_TRIGGER_CHARS)))
    if needed and len(ss.model_history) > 0:
        before_chars = message_chars(working)
        # The live-model path keeps only the newest complete turn, leaving more TPM
        # headroom than the manual demo while the checkpoint preserves older constraints.
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
        writer.write(event)
        turn["events"].append(compact_event(event))

    caps = capabilities(mode)
    emit("user_submitted", {"text": text, "tools": caps["tools"], "skills": caps["skills"]})
    observer.sync_messages(working)
    if compacted_before_turn:
        emit("context_compacted", compacted_before_turn)

    with st.chat_message("user"):
        st.markdown(text)
    with st.chat_message("assistant"):
        status = st.status("Đang xử lý…", expanded=True)
        trace_box = status.empty()
        answer_box = st.empty()

    def refresh() -> None:
        with trace_box.container():
            render_events(turn["events"])
        with state_box.container():
            render_state_panel(interactive=False)

    refresh()
    try:
        agent = build_agent(build_model(settings), mode)
        stream = agent.stream(
            {"messages": working},
            config={"recursion_limit": RECURSION_LIMIT},
            stream_mode=["updates", "custom"],
        )
        for mode, chunk in stream:
            if mode == "custom" and isinstance(chunk, dict) and chunk.get("observer"):
                emit(chunk["event"], chunk["data"])
            elif mode == "updates" and isinstance(chunk, dict):
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
    ss.pop("snapshot_choice", None)  # sau lượt mới, mặc định xem snapshot mới nhất
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
    mode_label = st.radio(
        "Chế độ tool",
        ["Native tools + Compact", "Code Mode (B2)"],
        horizontal=True,
        key="challenge_mode_label",
        help="Compaction luôn hoạt động ở cả hai chế độ. Code Mode thay các tool file/shell bằng một tool chạy chương trình tổng hợp log.",
    )
    mode = "code" if mode_label == "Code Mode (B2)" else "native"
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


main()
