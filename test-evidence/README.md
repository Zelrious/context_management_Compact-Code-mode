# Danh mục Kịch bản Dữ liệu Tổng hợp & Minh chứng Kiểm thử (`feat/evidence`)

Tài liệu này là manifest chính thức cho phần việc của **Thành viên 4 — Dữ liệu và minh chứng** trên nhánh `feat/evidence` (tách từ `feat/context-ui`). Mọi dữ liệu đầu vào đều là **dữ liệu giả lập (synthetic), tất định (deterministic)**, không chứa log cá nhân, API key hay thông tin nhạy cảm.

---

## 1. Cấu trúc phân tách Dữ liệu — Kỳ vọng — Minh chứng

| Nhóm tài nguyên | Đường dẫn | Mục đích |
| --- | --- | --- |
| **Dữ liệu đầu vào B1 (đa tệp)** | `fixtures/data/scenarios/b1_multifile_incident/` *(đồng bộ sang `workspace/data/scenarios/b1_multifile_incident/`)* | 4 tệp sự cố phụ thuộc chéo (`service_sla_config.json`, `deploy_manifest.md`, `batch_workload.csv`, `runbook_notes.md`). |
| **Dữ liệu đầu vào B2 (log tổng hợp)** | `fixtures/data/logs/incident_cascade/` *(đồng bộ sang `workspace/data/logs/incident_cascade/`)* | 5 tệp log đa dịch vụ (`gateway-edge.log`, `auth-token.log`, `orders-core.log`, `payments-ledger.log`, `malformed-collector.txt` — tổng 540 dòng, 46.864 bytes), giữ nguyên 20 log gốc ở thư mục cha để không gây hồi quy test cũ. |
| **Đặc tả kịch bản & Checklist kỳ vọng** | `fixtures/data/scenarios/b1_checkpoint_scenarios.json`, `fixtures/data/scenarios/b2_code_mode_scenarios.json` | Định nghĩa ID kịch bản, câu hỏi đa tệp, chương trình Python subset và bảng đối chiếu kết quả kỳ vọng (reference checklist). |
| **Kiểm thử tự động (Pytest)** | `tests/test_evidence_scenarios.py` | Kiểm thử tích hợp cho B1 (`compact_messages`, `should_compact`, `Observer` snapshot sau compact) và B2 (`execute_code`, chặn taint, chặn vượt `6.000` ký tự, `Observer` snapshot Code Mode). |
| **Minh chứng thực thi B1 + B2** | `test-evidence/B1-B2-Evidence/` | Script tái lập số liệu (`verify_evidence.py`), số liệu quan sát thực tế (`observed-metrics.json`), log chạy `pytest`, và ảnh chụp màn hình giao diện Streamlit thật. |
| **Minh chứng kế thừa từ nhánh trước** | `test-evidence/B1-Compact/` | Minh chứng kiểm thử B1 trước đó (giữ nguyên, không ghi đè). |

---

## 2. Điều kiện tiên quyết (Preconditions)

1. Python `>= 3.11` đã cài dependencies trong `.venv` của project (`uv sync`).
2. Khôi phục `workspace/` từ `fixtures/` để đảm bảo trạng thái sạch và đồng nhất:
   ```powershell
   .\.venv\Scripts\python.exe -X utf8 reset_workspace.py
   ```
3. **Không cần `.env` / API key** cho toàn bộ bài kiểm thử tự động, script đo lường và các luồng demo cục bộ trên UI (**Nạp session dài mẫu**, **Compact ngay**, **Chạy thử runtime cục bộ**).
4. Chỉ khi muốn chạy **chat trực tiếp với LLM thật** qua ô *Nhập yêu cầu…*, tạo `.env` từ `.env.example` và điền `OPENAI_API_KEY`, `MODEL_NAME` hợp lệ.

---

## 3. Bảng tổng hợp Kịch bản Kiểm thử & Kết quả Quan sát

| Scenario ID | Tính năng & Mục tiêu | Tệp dữ liệu liên quan | Kỳ vọng chính (Reference Checklist) | Kết quả thực tế quan sát | Trạng thái |
| --- | --- | --- | --- | --- | --- |
| **B1-SC01** | **B1 Compact** — Phiên phân tích CSV dài, nhiều lần thử lỗi và nhiễu lặp | `data/workload.csv`, `skills/csv-quality/SKILL.md`, `b1_checkpoint_scenarios.json` | Tự kích hoạt compact (`>= 24.000` ký tự & `>= 12.000` ký tự tool); giữ mục tiêu CSV, ngưỡng `8` giờ (`=8` không quá tải), ràng buộc không sửa CSV, lỗi `INPUT_NOT_FOUND`, `exit_code=2`, lỗi hiện hành `PATH_OUTSIDE_OUTPUT`, `row_count=6`, bước kế tiếp ghi `output/csv-quality.md`; loại bỏ nhiễu khỏi active context. | `should_compact=True`; giảm từ **43.561 → 3.163 ký tự (-92,74%)**; lưu archive `19` messages / `41.142` ký tự tool output; toàn bộ checklist đạt 100%; chuỗi nhiễu `DIAGNOSTIC_NOISE_PADDING_LINE` bằng `0` trong active messages. | **PASS** |
| **B1-SC02** | **B1 Compact + Đa tệp** — Điều tra sự cố nghẽn kết nối qua 4 tệp + tiếp nối qua Agent | `service_sla_config.json`, `deploy_manifest.md`, `batch_workload.csv`, `runbook_notes.md` | Bảo toàn tương quan 4 tệp (`12 * 3 = 36 > db_pool_max_connections=25`, `allow_pool_resize=false`, rollback `orders_sync_concurrency=4`, `Hieu=7.5h` quá tải ở ngưỡng `6h`, `Trang=6.0h` không quá tải, chọn `Khanh=2.5h < 4.0h`), giữ lỗi `FILE_NOT_FOUND`, `exit_code=2`, `PATH_OUTSIDE_OUTPUT`; snapshot gửi model sau compact không còn raw trace. | `should_compact=True`; giảm từ **50.927 → 3.073 ký tự (-93,97%)**; archive `19` messages / `48.473` ký tự tool; snapshot `Observer` ở lượt kế tiếp chỉ gửi checkpoint gọn và ghi thành công `output/incident-remediation.md`. | **PASS** |
| **B1-SC03** | **B1 Re-compaction & UI Demo** — Nén lặp 2 vòng liên tiếp và đối chiếu phiên mẫu UI | `b1_checkpoint_scenarios.json`, `app.py` (`load_long_session_demo`) | Vòng compact thứ 2 giữ nguyên `goal` ban đầu, hợp nhất ràng buộc của cả 2 vòng, tăng bộ đếm archive đơn điệu; demo UI giảm mạnh từ `120.012` ký tự. | Vòng 2 bảo toàn `goal` gốc + ràng buộc 2 giai đoạn; demo UI giảm **120.012 → 4.354 ký tự (-96,37%)**, archive `15` messages / `116.852` ký tự tool output. | **PASS** |
| **B1-SC04** | **B1 Boundary Audit** — Kiểm toán giới hạn thực tế của bộ compact rule-based | `compaction.py`, `tests/test_evidence_scenarios.py` | Xác nhận: (1) lược bỏ `error.message` tự do trong `ToolMessage` để chống rò rỉ raw log; (2) giữ tối đa `8` `user_requirements` gần nhất nhưng vẫn bảo toàn `goal` đầu tiên; (3) lỗi tool cũ vẫn nằm trong `unresolved_issues`; (4) dòng đơn `AIMessage` dài `> 240` ký tự bị lọc khỏi `current_state`. | Cả 4 hành vi biên được tái lập chính xác và kiểm chứng tự động bằng `pytest`. | **PASS** |
| **B2-SC01** | **B2 Code Mode** — Tương quan lỗi dây chuyền qua 5 tệp log, gộp nhiễu lặp & giữ lỗi nghiêm trọng đơn lẻ | `data/logs/incident_cascade/*` (5 tệp, 46.864 bytes), `b2_code_mode_scenarios.json` | Đọc đủ 5 tệp (`.log` + `.txt`), gộp `120` dòng `upstream_slow_retry` và `60` dòng `cache_miss_fallback`, giữ lỗi đơn lẻ `CRITICAL_POOL_EXHAUSTED` tại `payments-ledger.log`, tương quan `trc-9041` (4 tệp, không có `auth-token.log`) và `trc-9042` (3 tệp), `raw_log_chars_returned_to_model = 0`, JSON `<= 6.000` ký tự. | `ok=True`, `files_read=5`, `input_bytes_processed=46.864`, đầu ra JSON **1.283 ký tự** (`<= 6.000`), `total_errors=26`, `total_warnings=240`, phát hiện đúng `CRITICAL_POOL_EXHAUSTED` (`count=1`), `raw_log_chars_returned_to_model=0`. | **PASS** |
| **B2-SC02** | **B2 Code Mode** — Xử lý an toàn log `.txt` có dòng trống, cụt và sai định dạng | `data/logs/incident_cascade/malformed-collector.txt` | Không sập runtime khi gặp dòng rỗng / thiếu timestamp / JSON cắt dở; phân loại chính xác từng nhóm dòng. | `ok=True`, `total_lines=40` (`valid_info=20`, `valid_warn=3`, `valid_error=2`, `empty_lines=5`, `malformed_lines=10`, `backpressure_errors=1`), `raw_log_chars_returned_to_model=0`. | **PASS** |
| **B2-SC03** | **B2 Guardrails & Agent Snapshot** — Chặn kết quả quá khổ, chặn taint raw log & kiểm chứng qua `Observer` | `data/logs/incident_cascade/*`, `tools/code_mode.py`, `agent.py` | Chặn đầu ra `> 6.000` ký tự; chặn chuỗi tách trực tiếp từ raw log (`split`/slice mang cờ `tainted`); khi chạy qua `build_agent(mode="code")`, model không có `read_file`/`bash` và snapshot gửi model không chứa dòng log thô. | Cả hai trường hợp vi phạm đều trả `ok=False` đúng thông báo; snapshot `Observer` xác nhận chỉ cấp `["run_code", "search_memory", "remember_convention"]` và `code_raw_log_chars_returned=0`. | **PASS** |
| **LIVE-API** | **Chat trực tiếp với Provider ngoài** — Gửi lượt chat mạng thật sau compact / trong Code Mode | `.env` (chưa cấu hình trong môi trường hiện tại) | Gọi API OpenAI-compatible khi có `OPENAI_API_KEY` và `MODEL_NAME`. | Môi trường hiện tại không có tệp `.env` nên ô nhập chat bị khóa an toàn theo đúng thiết kế (`test_missing_config_blocks_model`). Minh chứng gọi API thật trước đó của nhóm được lưu tại `test-evidence/B1-Compact/automatic-compact-success.png`. | **BLOCKED (thiếu `.env`)** |

---

## 4. Hướng dẫn Tái lập Từng Bước (Reproducibility Guide)

### 4.1. Chạy kiểm thử tự động bằng CLI (không cần API key)

Từ thư mục gốc của repository:

```powershell
# 1. Đồng bộ dữ liệu từ fixtures/ sang workspace/
.\.venv\Scripts\python.exe -X utf8 reset_workspace.py

# 2. Chạy riêng bộ kịch bản dữ liệu & minh chứng B1 + B2
.\.venv\Scripts\python.exe -X utf8 -m pytest tests\test_evidence_scenarios.py -v

# 3. Sinh lại tệp đo lường thực tế test-evidence/B1-B2-Evidence/observed-metrics.json
.\.venv\Scripts\python.exe -X utf8 test-evidence\B1-B2-Evidence\verify_evidence.py

# 4. Chạy toàn bộ test suite hồi quy của dự án
.\.venv\Scripts\python.exe -X utf8 -m pytest -q
```

### 4.2. Tái lập trên Giao diện Streamlit (không cần API key)

1. Khởi chạy ứng dụng:
   ```powershell
   .\.venv\Scripts\python.exe -X utf8 -m streamlit run app.py --server.port 8506
   ```
2. Mở `http://localhost:8506`.
3. **Kiểm chứng B1 Compact:**
   - Giữ chế độ **Native tools + Compact**.
   - Trong khung **B1 · Compact session dài (bắt buộc)**, bấm **Nạp session dài mẫu** → quan sát `Active message context = 120,012 ký tự` và thông báo vượt ngưỡng `24,000` ký tự.
   - Bấm **Compact ngay** → quan sát thông báo `Đã compact: 120,012 → 4,354 ký tự; archive 15 messages / 116,852 ký tự tool output.`
   - Mở **Checkpoint đang được dùng** để kiểm tra `goal`, `user_requirements`, `unresolved_issues`, `failed_attempts`, `relevant_files` và `next_action`.
4. **Kiểm chứng B2 Code Mode:**
   - Ở thanh **Chế độ tool**, chọn **Code Mode**.
   - Trong khung **B2 · Code Mode (điểm cộng)**, bấm **Chạy thử runtime cục bộ** → chương trình mặc định quét `24` tệp `.log` (`48,882` bytes), trả `total_errors = 33`, `Raw log chars → model = 0`.
   - Để kiểm chứng kịch bản tương quan sự cố đa tệp **B2-SC01**, dán đoạn mã từ trường `scenarios[0].code` trong `fixtures/data/scenarios/b2_code_mode_scenarios.json` vào ô trình soạn thảo rồi bấm **Chạy thử runtime cục bộ** → kiểm tra `critical_findings` (`CRITICAL_POOL_EXHAUSTED` tại `payments-ledger.log`), `correlated_traces` (`trc-9041`, `trc-9042`), `deduplicated_noise` và `Raw log chars → model = 0`.

### 4.3. Tái lập kịch bản câu hỏi đa tệp với API thật (tùy chọn khi có `.env`)

Nếu giảng viên hoặc thành viên tích hợp cấu hình `.env` hợp lệ:
- **Ở chế độ Native tools + Compact:** sử dụng các câu hỏi `MF-Q1`, `MF-Q2` trong `fixtures/data/scenarios/b1_checkpoint_scenarios.json` để kiểm tra khả năng đọc kết hợp 4 tệp trong `data/scenarios/b1_multifile_incident/`.
- **Ở chế độ Code Mode:** hỏi câu hỏi tại `scenarios[0].question` trong `fixtures/data/scenarios/b2_code_mode_scenarios.json` và đối chiếu **Snapshot request** trong bảng **State & Context** để xác nhận không có dòng raw log nào đi vào messages của model.

---

## 5. Giới hạn Đã Ghi nhận (Known Limitations)

1. **`compaction.py` lọc bỏ dòng đơn dài quá 240 ký tự trong `AIMessage`:** tại dòng `214` của `compaction.py` (`if len(line) <= 240`), nếu câu trả lời của assistant là một đoạn văn đơn không xuống dòng dài hơn `240` ký tự (kể cả chứa cụm `Bước tiếp theo:`), dòng đó bị bỏ qua khi trích xuất `current_state` và `next_action`, khiến `next_action` chuyển sang fallback theo câu hỏi gần nhất của người dùng. Khuyến nghị nhỏ cho nhóm: có thể cắt `line[:240]` cho câu văn thường không có tiền tố timestamp log thay vì loại bỏ cả dòng.
2. **Trạng thái `unresolved_issues` trong `compaction.py` là tích lũy theo quy tắc tĩnh:** vì không gọi LLM để suy diễn ngữ nghĩa lúc compact, mọi lỗi tool trước đó (tối đa `8` mục) vẫn được giữ trong cả `failed_attempts` lẫn `unresolved_issues` ngay cả khi lượt thử sau đã thành công.
3. **Đồng nhất metadata khi vượt `MAX_RESULT_CHARS` trong `tools/code_mode.py`:** tại dòng `388` của `tools/code_mode.py`, nhánh trả về khi kết quả vượt `6.000` ký tự chỉ trả `{"ok": False, "error": "..."}` mà không kèm các khóa `files_read`, `input_bytes_processed`, `raw_log_chars_returned_to_model` như nhánh `except`. Giao diện `app.py` vẫn hiển thị an toàn nhờ `.get(..., 0)`, nhưng có thể bổ sung các khóa này nếu muốn đồng nhất schema tuyệt đối.
4. **Môi trường kiểm thử không có `.env`:** chưa thực thi gọi LLM qua mạng trong lần chạy này; toàn bộ logic compact, runtime Code Mode, LangGraph middleware snapshot và giao diện Streamlit đã được kiểm chứng đầy đủ không phụ thuộc API ngoài.

---

## 6. Vị trí Các Tệp Bằng chứng (`test-evidence/B1-B2-Evidence/`)

- [`verify_evidence.py`](B1-B2-Evidence/verify_evidence.py) — Script tái lập toàn bộ phép đo B1 & B2.
- [`observed-metrics.json`](B1-B2-Evidence/observed-metrics.json) — Kết quả đo lường JSON chi tiết của các kịch bản.
- [`pytest-evidence.txt`](B1-B2-Evidence/pytest-evidence.txt) — Kết quả chạy chi tiết `tests/test_evidence_scenarios.py`.
- [`pytest-full-suite.txt`](B1-B2-Evidence/pytest-full-suite.txt) — Kết quả chạy toàn bộ test suite hồi quy của repository.
- [`ui-b1-before-compact.png`](B1-B2-Evidence/ui-b1-before-compact.png) — Ảnh chụp UI thật sau khi nạp session dài mẫu (`120,012 ký tự`, vượt ngưỡng `24,000`).
- [`ui-b1-after-compact-checkpoint.png`](B1-B2-Evidence/ui-b1-after-compact-checkpoint.png) — Ảnh chụp UI thật sau khi bấm **Compact ngay** (`120,012 → 4,354 ký tự`) cùng JSON **Checkpoint đang được dùng**.
- [`ui-b2-code-mode-demo.png`](B1-B2-Evidence/ui-b2-code-mode-demo.png) — Ảnh chụp UI thật ở chế độ **Code Mode** chạy tổng hợp `24` tệp `.log` (`48,882` bytes, `Raw log chars → model = 0`).
- [`ui-b2-incident-correlation.png`](B1-B2-Evidence/ui-b2-incident-correlation.png) — Ảnh chụp UI thật chạy kịch bản tương quan đa tệp `incident_cascade/*` phát hiện `CRITICAL_POOL_EXHAUSTED` và `trc-9041`/`trc-9042` (`Raw log chars → model = 0`).
