"""Restricted Python-subset Code Mode for aggregating synthetic log files.

The interpreter walks an allow-listed AST instead of calling exec/eval. Raw file
contents are tainted: they can be counted/compared in the runtime, but cannot be
returned to the model. This is a classroom guardrail, not an OS security sandbox.
"""

from __future__ import annotations

import ast
import json
import operator
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

import paths

MAX_CODE_CHARS = 8_000
MAX_AST_NODES = 700
MAX_STEPS = 30_000
MAX_FILES = 100
MAX_FILE_BYTES = 500_000
MAX_TOTAL_BYTES = 5_000_000
MAX_RESULT_CHARS = 6_000
MAX_INTERMEDIATE_ITEMS = 20_000
MAX_INTERMEDIATE_TEXT = 1_000_000


class CodeModeError(ValueError):
    pass


@dataclass
class Value:
    data: Any
    tainted: bool = False


@dataclass
class Method:
    target: Value
    name: str


class ToolSDK:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self.root = (self.workspace / "data" / "logs").resolve()
        self.files_read = 0
        self.bytes_read = 0

    def list_files(self, pattern: str = "**/*.log") -> Value:
        if Path(pattern).is_absolute() or ".." in Path(pattern).parts or "\\" in pattern:
            raise CodeModeError("Mẫu tìm file chỉ được tương đối trong data/logs/.")
        normalized = pattern.removeprefix("data/logs/").removeprefix("logs/") or "**/*.log"
        matches = []
        if not self.root.is_dir():
            return Value(matches)
        for path in sorted(self.root.glob(normalized)):
            resolved = path.resolve()
            if not resolved.is_relative_to(self.root) or not resolved.is_file():
                continue
            if resolved.suffix.lower() not in (".log", ".txt"):
                continue
            rel = resolved.relative_to(self.workspace).as_posix()
            matches.append(Value(rel))
            if len(matches) >= MAX_FILES:
                break
        return Value(matches)

    def read_file(self, relative_path: str) -> Value:
        raw = (relative_path or "").strip()
        path = Path(raw)
        if path.is_absolute() or ".." in path.parts or "\\" in raw:
            raise CodeModeError("Chỉ được đọc đường dẫn tương đối trong data/logs/.")
        target = (self.workspace / path).resolve()
        if not target.is_relative_to(self.root) or not target.is_file():
            raise CodeModeError(f"Không tìm thấy log hoặc đường dẫn ngoài data/logs/: {raw}")
        if target.suffix.lower() not in (".log", ".txt"):
            raise CodeModeError("Code Mode chỉ đọc file .log và .txt.")
        size = target.stat().st_size
        if size > MAX_FILE_BYTES or self.bytes_read + size > MAX_TOTAL_BYTES:
            raise CodeModeError("Vượt giới hạn dung lượng log của Code Mode.")
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise CodeModeError("Log không phải UTF-8.") from exc
        self.files_read += 1
        self.bytes_read += size
        return Value(text, tainted=True)


class SafePythonSubset:
    """Small AST interpreter: assignments, for/if and bounded aggregate expressions."""

    BINOPS = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Mod: operator.mod,
    }
    CMPOPS = {
        ast.Eq: operator.eq,
        ast.NotEq: operator.ne,
        ast.Lt: operator.lt,
        ast.LtE: operator.le,
        ast.Gt: operator.gt,
        ast.GtE: operator.ge,
        ast.In: lambda a, b: a in b,
        ast.NotIn: lambda a, b: a not in b,
    }

    def __init__(self, sdk: ToolSDK):
        self.sdk = sdk
        self.env: dict[str, Value] = {
            "tools": Value(sdk),
            **{name: Value(name) for name in ("len", "sum", "min", "max", "sorted", "range", "enumerate", "abs")},
        }
        self.steps = 0
        self.result: Value | None = None

    def tick(self) -> None:
        self.steps += 1
        if self.steps > MAX_STEPS:
            raise CodeModeError(f"Chương trình vượt {MAX_STEPS:,} bước xử lý.")

    @staticmethod
    def val(value: Any) -> Value:
        return value if isinstance(value, Value) else Value(value)

    @staticmethod
    def raw(value: Any) -> Any:
        return value.data if isinstance(value, Value) else value

    def run(self, source: str) -> Any:
        if not source.strip():
            raise CodeModeError("Chưa có chương trình.")
        if len(source) > MAX_CODE_CHARS:
            raise CodeModeError(f"Chương trình vượt {MAX_CODE_CHARS:,} ký tự.")
        try:
            tree = ast.parse(source, mode="exec")
        except SyntaxError as exc:
            raise CodeModeError(f"Lỗi cú pháp dòng {exc.lineno}: {exc.msg}") from exc
        if sum(1 for _ in ast.walk(tree)) > MAX_AST_NODES:
            raise CodeModeError(f"Chương trình vượt {MAX_AST_NODES} nút cú pháp.")
        for statement in tree.body:
            self.statement(statement)
        if self.result is None:
            self.result = self.env.get("result")
        if self.result is None:
            raise CodeModeError("Hãy gán kết quả cuối cùng cho biến result.")
        if self.contains_tainted(self.result):
            raise CodeModeError("Đầu ra có raw log. Chỉ trả số liệu tổng hợp, đường dẫn và metadata.")
        return self.to_jsonable(self.result)

    def statement(self, node: ast.stmt) -> None:
        self.tick()
        if isinstance(node, ast.Assign):
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                raise CodeModeError("Chỉ hỗ trợ gán vào một biến đơn giản.")
            value = self.expression(node.value)
            self.env[node.targets[0].id] = value
            if node.targets[0].id == "result":
                self.result = value
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            left = self.env.get(node.target.id)
            if left is None or type(node.op) not in self.BINOPS:
                raise CodeModeError("Phép gán cộng không được hỗ trợ.")
            right = self.expression(node.value)
            self.env[node.target.id] = self.binary(node.op, left, right)
        elif isinstance(node, ast.For) and isinstance(node.target, ast.Name):
            if node.orelse:
                raise CodeModeError("Không hỗ trợ else của vòng lặp.")
            iterable = self.expression(node.iter)
            values = self.raw(iterable)
            if not isinstance(values, (list, tuple, str, range)):
                raise CodeModeError("Vòng lặp chỉ nhận danh sách, chuỗi hoặc range có giới hạn.")
            if len(values) > MAX_FILES * 100:
                raise CodeModeError("Vòng lặp vượt giới hạn số phần tử.")
            for item in values:
                self.tick()
                # Preserve taint when iterating over text derived from a raw log.
                self.env[node.target.id] = Value(item, iterable.tainted and isinstance(item, str)) if not isinstance(item, Value) else item
                for child in node.body:
                    self.statement(child)
        elif isinstance(node, ast.If):
            if node.orelse:
                # else is supported, but elif is just a nested If node.
                branch = node.body if bool(self.raw(self.expression(node.test))) else node.orelse
            else:
                branch = node.body if bool(self.raw(self.expression(node.test))) else []
            for child in branch:
                self.statement(child)
        elif isinstance(node, ast.Expr):
            self.result = self.expression(node.value)
        elif isinstance(node, ast.Pass):
            return
        else:
            raise CodeModeError(f"Câu lệnh {type(node).__name__} không được phép trong Code Mode.")

    def expression(self, node: ast.expr) -> Value:
        self.tick()
        if isinstance(node, ast.Constant):
            return Value(node.value)
        if isinstance(node, ast.Name):
            if node.id not in self.env:
                raise CodeModeError(f"Biến chưa được khai báo: {node.id}")
            return self.env[node.id]
        if isinstance(node, ast.List):
            values = [self.expression(item) for item in node.elts]
            return Value(values, any(v.tainted for v in values))
        if isinstance(node, ast.Tuple):
            values = [self.expression(item) for item in node.elts]
            return Value(tuple(values), any(v.tainted for v in values))
        if isinstance(node, ast.Dict):
            values = {self.raw(self.expression(k)): self.expression(v) for k, v in zip(node.keys, node.values)}
            return Value(values, any(v.tainted for v in values.values()))
        if isinstance(node, ast.BinOp):
            return self.binary(node.op, self.expression(node.left), self.expression(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.Not, ast.USub, ast.UAdd)):
            value = self.raw(self.expression(node.operand))
            result = not value if isinstance(node.op, ast.Not) else (-value if isinstance(node.op, ast.USub) else +value)
            return Value(result)
        if isinstance(node, ast.Compare):
            left = self.raw(self.expression(node.left))
            for op_node, comp in zip(node.ops, node.comparators):
                right = self.raw(self.expression(comp))
                function = self.CMPOPS.get(type(op_node))
                if function is None:
                    raise CodeModeError("Phép so sánh này không được hỗ trợ.")
                if not function(left, right):
                    return Value(False)
                left = right
            return Value(True)
        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            values = [self.expression(v) for v in node.values]
            raw = [bool(self.raw(v)) for v in values]
            return Value(all(raw) if isinstance(node.op, ast.And) else any(raw))
        if isinstance(node, ast.Subscript):
            target_value = self.expression(node.value)
            target = self.raw(target_value)
            index = self.raw(self.expression(node.slice))
            if isinstance(target, dict):
                if index not in target:
                    raise CodeModeError(f"Không có khóa kết quả: {index}")
                selected = self.val(target[index])
                return Value(selected.data, selected.tainted or target_value.tainted)
            selected = self.val(target[index])
            return Value(selected.data, selected.tainted or target_value.tainted)
        if isinstance(node, ast.Slice):
            lower = self.raw(self.expression(node.lower)) if node.lower else None
            upper = self.raw(self.expression(node.upper)) if node.upper else None
            step = self.raw(self.expression(node.step)) if node.step else None
            return Value(slice(lower, upper, step))
        if isinstance(node, ast.Attribute):
            target = self.expression(node.value)
            if isinstance(target.data, ToolSDK) and node.attr in {"list_files", "read_file"}:
                return Value(Method(target, node.attr))
            if node.attr.startswith("_") or node.attr not in {
                "append", "extend", "count", "get", "keys", "values", "items", "lower", "upper",
                "strip", "split", "splitlines", "startswith", "endswith", "replace", "sort",
            }:
                raise CodeModeError(f"Không cho phép truy cập thuộc tính: {node.attr}")
            return Value(Method(target, node.attr))
        if isinstance(node, ast.Call):
            function = self.expression(node.func)
            if node.keywords:
                raise CodeModeError("Không hỗ trợ keyword arguments.")
            args = [self.expression(arg) for arg in node.args]
            return self.call(function, args)
        if isinstance(node, ast.IfExp):
            branch = node.body if bool(self.raw(self.expression(node.test))) else node.orelse
            return self.expression(branch)
        raise CodeModeError(f"Biểu thức {type(node).__name__} không được phép trong Code Mode.")

    def binary(self, op_node: ast.operator, left: Value, right: Value) -> Value:
        function = self.BINOPS.get(type(op_node))
        if function is None:
            raise CodeModeError("Phép toán này không được hỗ trợ.")
        result = function(self.raw(left), self.raw(right))
        if isinstance(result, (str, bytes)) and len(result) > MAX_INTERMEDIATE_TEXT:
            raise CodeModeError("Kết quả trung gian dạng văn bản vượt giới hạn.")
        if isinstance(result, (list, tuple)) and len(result) > MAX_INTERMEDIATE_ITEMS:
            raise CodeModeError("Kết quả trung gian vượt giới hạn phần tử.")
        if isinstance(result, int) and result.bit_length() > 4096:
            raise CodeModeError("Số nguyên trung gian vượt giới hạn.")
        return Value(result, left.tainted or right.tainted)

    def call(self, function: Value, args: list[Value]) -> Value:
        target = function.data
        raw_args = [self.raw(arg) for arg in args]
        if isinstance(target, Method):
            receiver = target.target
            if isinstance(receiver.data, ToolSDK):
                if target.name == "list_files":
                    return receiver.data.list_files(str(raw_args[0]) if raw_args else "**/*.log")
                if target.name == "read_file":
                    if len(raw_args) != 1:
                        raise CodeModeError("tools.read_file cần đúng một path.")
                    return receiver.data.read_file(str(raw_args[0]))
            obj = receiver.data
            name = target.name
            if name == "append" and isinstance(obj, list) and len(args) == 1:
                obj.append(args[0]); return Value(None)
            if name == "extend" and isinstance(obj, list) and len(args) == 1:
                obj.extend(self.raw(args[0])); return Value(None)
            if name == "count" and isinstance(obj, (str, list, tuple)) and len(raw_args) == 1:
                return Value(obj.count(raw_args[0]))
            if name == "get" and isinstance(obj, dict):
                return self.val(obj.get(raw_args[0], args[1] if len(args) > 1 else Value(None)))
            if name in {"keys", "values", "items"} and isinstance(obj, dict):
                val = list(getattr(obj, name)())
                return Value([self.val(v) for v in val], receiver.tainted)
            if isinstance(obj, str) and name in {"lower", "upper", "strip", "split", "splitlines", "startswith", "endswith", "replace"}:
                result = getattr(obj, name)(*raw_args)
                return Value(result, receiver.tainted)
            if name == "sort" and isinstance(obj, list):
                obj.sort(); return Value(None)
            raise CodeModeError(f"Phương thức {name} không hợp lệ với kiểu dữ liệu này.")

        if not isinstance(target, str):
            raise CodeModeError("Chỉ gọi được các API được cho phép.")
        functions = {
            "len": len, "sum": sum, "min": min, "max": max, "sorted": sorted,
            "range": range, "enumerate": enumerate, "abs": abs,
        }
        if target not in functions:
            raise CodeModeError(f"Hàm {target} không được phép.")
        try:
            result = functions[target](*raw_args)
        except Exception as exc:
            raise CodeModeError(f"Lỗi khi gọi {target}: {exc}") from exc
        if target in {"min", "max"} and isinstance(result, Value):
            return result
        if target == "sorted":
            return Value([self.val(v) for v in result], any(a.tainted for a in args))
        if target in {"range", "enumerate"}:
            if len(result) > MAX_STEPS:
                raise CodeModeError("range/enumerate vượt giới hạn bước.")
            return Value([self.val(v) for v in result])
        return Value(result)

    def contains_tainted(self, value: Any) -> bool:
        if isinstance(value, Value):
            return value.tainted or self.contains_tainted(value.data)
        if isinstance(value, dict):
            return any(self.contains_tainted(k) or self.contains_tainted(v) for k, v in value.items())
        if isinstance(value, (list, tuple, set)):
            return any(self.contains_tainted(v) for v in value)
        return False

    def to_jsonable(self, value: Any) -> Any:
        if isinstance(value, Value):
            return self.to_jsonable(value.data)
        if isinstance(value, dict):
            return {str(self.to_jsonable(k)): self.to_jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set, range)):
            return [self.to_jsonable(v) for v in value]
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        raise CodeModeError(f"Không thể chuyển kết quả kiểu {type(value).__name__} sang JSON.")


def execute_code(source: str, workspace: Path | None = None) -> dict[str, Any]:
    sdk = ToolSDK(workspace or paths.WORKSPACE_DIR)
    started = time.perf_counter()
    try:
        result = SafePythonSubset(sdk).run(source)
        output_text = json.dumps(result, ensure_ascii=False, default=str)
        if len(output_text) > MAX_RESULT_CHARS:
            return {"ok": False, "error": f"Kết quả vượt {MAX_RESULT_CHARS:,} ký tự; hãy chỉ trả thống kê/finding top-N."}
        return {
            "ok": True,
            "result": result,
            "files_read": sdk.files_read,
            "input_bytes_processed": sdk.bytes_read,
            "raw_log_chars_returned_to_model": 0,
            "runtime_ms": round((time.perf_counter() - started) * 1000, 2),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {str(exc)[:500]}",
            "files_read": sdk.files_read,
            "input_bytes_processed": sdk.bytes_read,
            "raw_log_chars_returned_to_model": 0,
            "runtime_ms": round((time.perf_counter() - started) * 1000, 2),
        }


@tool
def run_code(code: str) -> str:
    """Chạy một chương trình Python subset để tổng hợp nhiều log trong data/logs/.

    SDK cho phép tools.list_files(pattern) và tools.read_file(path). Nội dung log chỉ ở runtime;
    hãy đếm/tổng hợp và gán findings cuối cho biến result. Không trả dòng log thô.
    Runtime chặn import, file ghi, mạng, tiến trình, thuộc tính ngoài allowlist và giới hạn số bước.
    """
    return json.dumps(execute_code(code), ensure_ascii=False)
