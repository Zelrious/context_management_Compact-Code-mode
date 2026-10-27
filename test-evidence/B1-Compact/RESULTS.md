# Kết quả kiểm thử B1 — Compact

**Ngày chạy:** 24/10/2026

**Môi trường:** Windows, Python 3.11.9, pytest 9.1.1
**Project:** `challenge-b-context-management`

## Kiểm tra API từ `.env`

- `config.py` đã đọc đúng `.env` của project; có `OPENAI_API_KEY`, `OPENAI_BASE_URL` và `MODEL_NAME`. Giá trị API key không được ghi vào log.
- Đã gửi một request ngắn tới endpoint Groq cấu hình trong `.env`. Model `openai/gpt-oss-20b` trả `OK`, `finish_reason=stop`.
- Kết luận: API key, endpoint và model đều hoạt động tại thời điểm kiểm tra.
- Log: [`api-check.log`](api-check.log). Script có thể chạy lại: [`check-api-connection.py`](check-api-connection.py).

Giao diện không test được trước đó vì tiến trình Streamlit đã dừng sau lần chụp ảnh. Đã khởi chạy lại trên `http://localhost:8506` và xác nhận endpoint local trả HTTP 200; có thể cần tải lại tab đang mở.

## Kết quả chính

### Kiểm thử riêng B1

Lệnh:

```powershell
..\.venv\Scripts\python.exe -m pytest tests\test_compaction.py -q
```

Kết quả: **3 passed**.

Ba kiểm tra xác nhận:

1. Session ngắn vẫn giữ message người dùng; tool output dài được rút gọn trong active context và bản gốc được lưu trong archive.
2. Checkpoint giữ mục tiêu, ràng buộc, lỗi/lần thử, file liên quan và bước tiếp theo; active context giảm so với trước compact.
3. Compact lần tiếp theo kế thừa mục tiêu và yêu cầu từ checkpoint trước.

Log nguyên văn: [`pytest-b1.log`](pytest-b1.log).

### Nhóm kiểm thử hồi quy chạy được trong môi trường hiện tại

Kết quả: **59 passed, 2 skipped, 2 deselected**.

- Hai bài skipped cần tạo symlink; Windows trả `WinError 1314` vì tiến trình không được cấp quyền tạo symlink.
- Hai bài deselected phụ thuộc tool Bash; môi trường Windows hiện gọi `C:\Windows\System32\bash.exe` (launcher WSL), và Git Bash không khởi động được trong sandbox.
- Log nguyên văn: [`pytest-regression.log`](pytest-regression.log).

## Kiểm tra giao diện B1

Demo đã chạy trong giao diện thật, không gọi API:

- Active message context trước compact: **120,012 ký tự**.
- Sau compact: **4,354 ký tự**.
- Đưa vào archive: **15 messages**, gồm **116,852 ký tự tool output**.
- Checkpoint quan sát được mục tiêu `data/workload.csv`, ngưỡng 8 giờ, yêu cầu không sửa CSV, lần chạy lỗi `INPUT_NOT_FOUND`, kết quả `row_count=6` và bước tạo `output/csv-quality.md`.

Ảnh chụp giao diện sau compact được đính kèm trong phản hồi Codex cùng yêu cầu này; tab demo được giữ mở để có thể xem lại.

### Tự động compact trước model request — chạy thành công

**Ngày chạy:** 25/10/2026
**Kết quả:** đạt. Đã nạp session mẫu, gửi câu hỏi yêu cầu tóm tắt checkpoint và nhận câu trả lời hoàn tất từ model.

Event log hiển thị đúng thứ tự:

1. `user_submitted`
2. `context_compacted`
3. `model_request`
4. `model_response`
5. `run_completed`

Checkpoint trong câu trả lời giữ được mục tiêu phân tích `data/workload.csv`, ngưỡng 8 giờ, ràng buộc không sửa file nguồn, lỗi `INPUT_NOT_FOUND` ở đường dẫn mẫu không tồn tại và bước tiếp theo. Lượt xác minh này không gọi tool; như vậy kiểm tra được việc tự compact và tiếp tục từ checkpoint mà không bị nhiễu bởi giới hạn 8 model call của tác vụ phân tích CSV đầy đủ.

Ảnh bằng chứng: [`automatic-compact-success.png`](automatic-compact-success.png).

## Giới hạn của lần xác minh

- Chưa có kết quả toàn bộ test suite. `AppTest` bị kẹt trước khi chạy code app: `LocalScriptRunner` tạo asyncio Proactor event loop và chờ ở `socket.socketpair` trên Windows trong sandbox. Stack trace bằng `faulthandler_timeout` xác nhận điểm dừng; đây là giới hạn socket của môi trường test, không phải lỗi xác thực API.
- `AppTest` và toàn bộ test suite chưa chạy hoàn chỉnh vì giới hạn môi trường nêu trên. Luồng tự compact trước model request đã chạy thành công với model thật theo ảnh bằng chứng ở trên.
- `uv run` không khởi chạy được vì uv bị từ chối ghi interpreter cache trong thư mục AppData; các lệnh pytest dùng Python đã cài trong `.venv` của project.

## Tệp bằng chứng

- [`pytest-b1.log`](pytest-b1.log) — log kiểm thử riêng B1.
- [`pytest-regression.log`](pytest-regression.log) — log nhóm hồi quy chạy được.
- [`api-check.log`](api-check.log) — kết quả kiểm tra API đã che key.
- [`automatic-compact-success.png`](automatic-compact-success.png) — ảnh Event log và câu trả lời sau khi tự compact trước model request.
- Ảnh giao diện compact — ảnh chụp inline trong phản hồi Codex của lần chạy này.
