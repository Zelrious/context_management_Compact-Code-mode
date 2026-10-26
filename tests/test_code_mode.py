"""Coverage for the restricted Python aggregation runtime used by Code Mode."""

from pathlib import Path

from agent import selected_tools
from tools.code_mode import execute_code, run_code
from tools.memory import remember_convention, search_memory
import paths


def run(source: str):
    return execute_code(source, paths.WORKSPACE_DIR)


def assert_rejected(source: str, text: str | None = None):
    result = run(source)
    assert result["ok"] is False, result
    if text is not None:
        assert text in result["error"], result
    return result


# A) Taint rejection


def test_raw_log_content_in_result_rejected(lab_dirs):
    result = assert_rejected(
        'result = tools.read_file("data/logs/api-gateway.log")',
        "raw log",
    )
    assert "result" not in result or result.get("result") is None


def test_raw_line_in_result_rejected(lab_dirs):
    assert_rejected(
        """
lines = tools.read_file("data/logs/api-gateway.log").splitlines()
for line in lines:
    result = line
""",
        "raw log",
    )


def test_substring_of_raw_content_tainted(lab_dirs):
    assert_rejected(
        'content = tools.read_file("data/logs/api-gateway.log")\nresult = content[0:5]',
        "raw log",
    )


def test_aggregated_count_passes(lab_dirs):
    result = run(
        """
files = tools.list_files("*.log")
error_count = 0
for path in files:
    content = tools.read_file(path)
    for line in content.splitlines():
        if "ERROR" in line:
            error_count += 1
result = error_count
"""
    )
    assert result["ok"] is True, result
    assert result["result"] == 9


def test_aggregated_dict_passes(lab_dirs):
    result = run(
        """
api_errors = 0
auth_errors = 0
worker_errors = 0
content = tools.read_file("data/logs/api-gateway.log")
for line in content.splitlines():
    if "ERROR" in line:
        api_errors += 1
content = tools.read_file("data/logs/auth-service.log")
for line in content.splitlines():
    if "ERROR" in line:
        auth_errors += 1
content = tools.read_file("data/logs/worker.log")
for line in content.splitlines():
    if "ERROR" in line:
        worker_errors += 1
result = {"api-gateway.log": api_errors, "auth-service.log": auth_errors, "worker.log": worker_errors}
"""
    )
    assert result["ok"] is True, result
    assert result["result"] == {
        "api-gateway.log": 1,
        "auth-service.log": 1,
        "worker.log": 1,
    }


# B) SDK scoping


def test_list_files_only_in_data_logs(lab_dirs):
    result = run('result = tools.list_files("*")')
    assert result["ok"] is True, result
    assert len(result["result"]) == 20
    assert {
        "data/logs/api-gateway.log",
        "data/logs/auth-service.log",
        "data/logs/worker.log",
    }.issubset(set(result["result"]))
    assert all(path.startswith("data/logs/") for path in result["result"])


def test_read_file_outside_logs_blocked(lab_dirs):
    assert_rejected('result = tools.read_file("../tasks.csv")')
    assert_rejected('result = tools.read_file("data/tasks.csv")')


def test_read_nonexistent_file_error(lab_dirs):
    assert_rejected('result = tools.read_file("data/logs/does-not-exist.log")', "Không tìm thấy")


def test_file_extension_filter(lab_dirs):
    logs = paths.WORKSPACE_DIR / "data" / "logs"
    (logs / "not-a-log.csv").write_text("ERROR should not be readable\n", encoding="utf-8")
    (logs / "notes.md").write_text("ERROR should not be readable\n", encoding="utf-8")
    (logs / "allowed.txt").write_text("INFO allowed\n", encoding="utf-8")

    listed = run('result = tools.list_files("*")')
    assert listed["ok"] is True, listed
    assert "data/logs/allowed.txt" in listed["result"]
    assert "data/logs/not-a-log.csv" not in listed["result"]
    assert "data/logs/notes.md" not in listed["result"]
    assert_rejected('result = tools.read_file("data/logs/not-a-log.csv")', ".log và .txt")


# C) AST and resource limits


def test_code_too_long_rejected(lab_dirs):
    assert_rejected("result = 1\n" + (" " * 8_001), "8,000")


def test_too_many_steps_rejected(lab_dirs):
    assert_rejected(
        """
count = 0
for item in range(10000):
    count += 1
result = count
""",
        "30,000",
    )


def test_import_not_allowed(lab_dirs):
    assert_rejected("import os\nresult = 1")


def test_exec_not_allowed(lab_dirs):
    assert_rejected('exec("result = 1")')


def test_eval_not_allowed(lab_dirs):
    assert_rejected('eval("1 + 1")')


# D) Approved builtins


def test_len_works(lab_dirs):
    result = run("result = len([1, 2, 3])")
    assert result["ok"] is True and result["result"] == 3


def test_sum_works(lab_dirs):
    result = run("result = sum([1, 2, 3])")
    assert result["ok"] is True and result["result"] == 6


def test_sorted_works(lab_dirs):
    result = run("result = sorted([3, 1, 2])")
    assert result["ok"] is True and result["result"] == [1, 2, 3]


def test_range_works(lab_dirs):
    result = run("result = range(5)")
    assert result["ok"] is True and result["result"] == [0, 1, 2, 3, 4]


def test_string_methods_work(lab_dirs):
    result = run('result = "hello world".split()')
    assert result["ok"] is True and result["result"] == ["hello", "world"]


# E) Successful aggregations and metadata


def test_count_errors_across_files(lab_dirs):
    result = run(
        """
error_count = 0
for path in tools.list_files("*.log"):
    for line in tools.read_file(path).splitlines():
        if "ERROR" in line:
            error_count += 1
result = error_count
"""
    )
    assert result["ok"] is True, result
    assert result["result"] == 9
    assert result["files_read"] == 20


def test_aggregate_by_service(lab_dirs):
    result = run(
        """
api = 0
auth = 0
worker = 0
for path in tools.list_files("*.log"):
    errors = 0
    for line in tools.read_file(path).splitlines():
        if "ERROR" in line:
            errors += 1
    if "api-gateway.log" in path:
        api = errors
    if "auth-service.log" in path:
        auth = errors
    if "worker.log" in path:
        worker = errors
result = {"api-gateway": api, "auth-service": auth, "worker": worker}
"""
    )
    assert result["ok"] is True, result
    assert result["result"] == {"api-gateway": 1, "auth-service": 1, "worker": 1}


def test_result_metadata(lab_dirs):
    result = run(
        'result = len(tools.read_file("data/logs/api-gateway.log").splitlines())'
    )
    assert result["ok"] is True, result
    assert result["files_read"] == 1
    assert result["input_bytes_processed"] > 0
    assert result["raw_log_chars_returned_to_model"] == 0
    assert isinstance(result["runtime_ms"], (int, float))


# F) Agent integration


def test_code_mode_tool_selection():
    assert selected_tools("code") == [run_code, search_memory, remember_convention]


def test_native_mode_excludes_run_code():
    assert run_code not in selected_tools("native")
