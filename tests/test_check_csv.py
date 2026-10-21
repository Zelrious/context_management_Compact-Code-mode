"""csv-quality script: thống kê fixture, lỗi input/schema/parse exit 1, lỗi dữ liệu exit 0."""

import json
import subprocess
import sys

import pytest

import paths

SCRIPT = paths.FIXTURES_DIR / "skills" / "csv-quality" / "scripts" / "check_csv.py"


def run(path, max_hours="8"):
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPT), "--input", str(path), "--max-hours", str(max_hours)],
        capture_output=True, text=True, encoding="utf-8", timeout=10,
    )


def write_csv(tmp_path, text):
    path = tmp_path / "t.csv"
    path.write_text(text, encoding="utf-8")
    return path


def test_fixture_statistics():
    result = run(paths.FIXTURES_DIR / "data" / "tasks.csv")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["row_count"] == 6
    assert data["missing_owner_count"] == 1
    assert data["invalid_hours_count"] == 1
    assert data["duplicate_id_count"] == 1
    assert data["duplicate_ids"] == ["T02"]
    assert [(i["line"], i["column"], i["type"]) for i in data["issues"]] == [
        (4, "owner", "missing_owner"),
        (5, "hours", "invalid_hours"),
        (6, "task_id", "duplicate_id"),
    ]
    assert data["hours_by_owner"] == {"An": 5, "Lan": 4, "Minh": 3}


@pytest.mark.parametrize("hours", ["NaN", "nan", "Infinity", "-inf", "-1", ""])
def test_non_finite_negative_or_empty_hours_rejected(tmp_path, hours):
    data = json.loads(run(write_csv(tmp_path, f"task_id,owner,hours\nT01,Lan,{hours}\n")).stdout)
    assert data["invalid_hours_count"] == 1
    assert data["issues"][0]["line"] == 2


def test_clean_data_exit_0_without_issues(tmp_path):
    result = run(write_csv(tmp_path, "task_id,owner,hours\nT01,Lan,4\nT02,Minh,2.5\n"))
    assert result.returncode == 0
    assert json.loads(result.stdout)["issues"] == []


def test_missing_file_exit_1(tmp_path):
    result = run(tmp_path / "khong-co.csv")
    assert result.returncode == 1
    assert result.stdout == ""
    assert "Không đọc được file" in result.stderr


def test_missing_column_exit_1(tmp_path):
    result = run(write_csv(tmp_path, "task_id,owner\nT01,Lan\n"))
    assert result.returncode == 1
    assert "Thiếu cột bắt buộc: hours" in result.stderr


def test_parse_error_exit_1(tmp_path):
    result = run(write_csv(tmp_path, 'task_id,owner,hours\nT01,"La"n,4\n'))
    assert result.returncode == 1
    assert "Lỗi parse CSV" in result.stderr


def test_script_does_not_modify_input():
    source = paths.FIXTURES_DIR / "data" / "tasks.csv"
    before = source.read_bytes()
    run(source)
    assert source.read_bytes() == before


@pytest.mark.parametrize("threshold, overloaded", [
    (8, [{"owner": "Lan", "total_hours": 9}]),
    (9, []),
])
def test_workload_threshold_and_exclusions(threshold, overloaded):
    source = paths.FIXTURES_DIR / "data" / "workload.csv"
    before = source.read_bytes()
    result = run(source, threshold)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["max_hours"] == threshold
    assert data["hours_by_owner"] == {"Lan": 9, "Minh": 3}
    assert data["overloaded_owners"] == overloaded
    assert data["excluded_rows"] == [
        {"line": 5, "task_id": "T04", "reasons": ["invalid_hours"]},
        {"line": 6, "task_id": "T02", "reasons": ["duplicate_id"]},
        {"line": 7, "task_id": "T05", "reasons": ["missing_owner"]},
    ]
    assert (data["row_count"], data["missing_owner_count"], data["invalid_hours_count"]) == (6, 1, 1)
    assert data["duplicate_ids"] == ["T02"]
    assert source.read_bytes() == before


def test_first_invalid_id_still_excludes_later_valid_occurrence():
    result = run(paths.FIXTURES_DIR / "data" / "workload-edge.csv", 0)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["hours_by_owner"] == {"Minh": 0}
    assert data["overloaded_owners"] == []
    assert data["excluded_rows"] == [
        {"line": 2, "task_id": "E01", "reasons": ["invalid_hours"]},
        {"line": 3, "task_id": "E01", "reasons": ["duplicate_id"]},
    ]
    assert (data["invalid_hours_count"], data["duplicate_id_count"]) == (1, 1)


def test_all_reasons_once_per_row_in_contract_order(tmp_path):
    source = write_csv(tmp_path, "task_id,owner,hours\nX, ,abc,extra\n X ,Lan,5\n,,NaN\nZ\n\n")
    data = json.loads(run(source).stdout)
    assert data["hours_by_owner"] == {}
    assert data["row_count"] == 4
    assert data["excluded_rows"] == [
        {"line": 2, "task_id": "X", "reasons": ["wrong_field_count", "missing_owner", "invalid_hours"]},
        {"line": 3, "task_id": "X", "reasons": ["duplicate_id"]},
        {"line": 4, "task_id": None, "reasons": ["missing_task_id", "missing_owner", "invalid_hours"]},
        {"line": 5, "task_id": "Z", "reasons": ["wrong_field_count", "missing_owner", "invalid_hours"]},
    ]


def test_normalization_distinct_owner_names_and_sorted_overloads(tmp_path):
    source = write_csv(tmp_path, "task_id,owner,hours\n Z1 , lan , 2 \nA1,Lan,3\nA2,Lan,0\n Z1 ,Minh,10\n")
    data = json.loads(run(source, 1).stdout)
    assert data["hours_by_owner"] == {"Lan": 3, "lan": 2}
    assert data["overloaded_owners"] == [
        {"owner": "Lan", "total_hours": 3}, {"owner": "lan", "total_hours": 2},
    ]
    assert data["excluded_rows"] == [{"line": 5, "task_id": "Z1", "reasons": ["duplicate_id"]}]


@pytest.mark.parametrize("threshold", ["NaN", "Infinity", "-inf", "-1", "1e309", "abc", ""])
def test_invalid_threshold_is_cli_error(threshold):
    result = run(paths.FIXTURES_DIR / "data" / "workload.csv", threshold)
    assert result.returncode != 0
    assert result.stdout == ""
    assert "--max-hours" in result.stderr


def test_missing_threshold_is_cli_error():
    result = subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPT), "--input", str(paths.FIXTURES_DIR / "data" / "workload.csv")],
        capture_output=True, text=True, encoding="utf-8", timeout=10,
    )
    assert result.returncode != 0
    assert result.stdout == ""
    assert "--max-hours" in result.stderr
