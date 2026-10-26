"""Skills-based CSV/report agent with persistent memory and Challenge B context modes."""

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain_openai import ChatOpenAI

import paths
from config import MODEL_CALL_LIMIT, TOOL_CALL_LIMIT, Settings
from observer import ObserverMiddleware
from prompts import build_system_prompt
from skill_catalog import Catalog, render_catalog, scan_skills
from tools import bash, read_file, write_file, remember_convention, search_memory, run_code

TOOLS = [read_file, write_file, bash, search_memory, remember_convention]
CODE_MODE_TOOLS = [run_code, search_memory, remember_convention]


def selected_tools(mode: str = "native"):
    """Code Mode deliberately withholds raw-file and shell tools from the model."""
    return CODE_MODE_TOOLS if mode == "code" else TOOLS


def build_model(settings: Settings) -> ChatOpenAI:
    kwargs = {
        "model": settings.model_name,
        "api_key": settings.api_key,
        "max_tokens": settings.max_model_output_tokens,
    }
    if settings.base_url:
        kwargs["base_url"] = settings.base_url
    return ChatOpenAI(**kwargs)


def load_catalog() -> Catalog:
    return scan_skills(paths.WORKSPACE_DIR)


def system_prompt(mode: str = "native") -> str:
    from memory_store import render_injected_memory
    prompt = build_system_prompt(render_catalog(load_catalog()), render_injected_memory())
    if mode == "code":
        prompt += "\n\nCode Mode:\n- Với câu hỏi trên log, gọi run_code bằng chương trình Python subset.\n- Runtime dùng tools.list_files(pattern) và tools.read_file(path) nội bộ; không yêu cầu đọc raw log qua tool khác.\n- Đếm/tổng hợp kết quả trong chương trình, gán findings cuối vào biến result. Không đưa dòng log thô vào result.\n- Nếu yêu cầu cần trích nguyên văn log, giải thích rằng Code Mode chỉ trả metadata/thống kê đã tổng hợp."
    return prompt


def capabilities(mode: str = "native") -> dict:
    catalog = load_catalog()
    return {"tools": [t.name for t in selected_tools(mode)], "skills": catalog.metadata(), "skill_diagnostics": catalog.diagnostics}


def build_agent(model, mode: str = "native"):
    return create_agent(
        model=model,
        tools=selected_tools(mode),
        system_prompt=system_prompt(mode),
        middleware=[
            ModelCallLimitMiddleware(run_limit=MODEL_CALL_LIMIT, exit_behavior="end"),
            ToolCallLimitMiddleware(run_limit=TOOL_CALL_LIMIT),
            ObserverMiddleware(),  # cuối danh sách = sát model/tool invocation nhất
        ],
    )
