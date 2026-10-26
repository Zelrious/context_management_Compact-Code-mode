# Challenge B — Context Management Agent

## Chức năng gốc và các phần phát triển thêm

Agent gốc của bài thực hành là **agent sử dụng skills để làm việc với dữ liệu workspace**. Skill `csv-quality` hướng dẫn agent dùng script để kiểm tra file CSV, tính tổng giờ theo người và xác định người quá tải theo ngưỡng người dùng đưa ra. Skill `weekly-report` hướng dẫn agent đọc ghi chú tiến độ cùng template rồi tạo báo cáo Markdown. Các tool nền gồm `read_file`, `write_file` và `bash`; agent chỉ nhận metadata skills ban đầu, rồi đọc `SKILL.md` phù hợp qua `read_file` khi cần.

Sau chức năng gốc, bài Challenge A bổ sung memory quy ước bền vững qua nhiều session. Khi người dùng xác nhận thay đổi, quy ước mới thành `current`, bản cũ được lưu trong lịch sử với trạng thái `superseded`.

Challenge B phát triển tiếp trên nền đó. **B1 Compact** nén lịch sử dài thành checkpoint để agent giữ được yêu cầu, lỗi đang xử lý, lần thử và bước kế tiếp. **B2 Code Mode** là chế độ bổ sung để agent tổng hợp nhiều file log trong runtime mà không đưa log thô vào context. Ở chế độ **Native tools + Compact**, agent vẫn có các tools và skills gốc để xử lý CSV và báo cáo tuần. Code Mode hiện tập trung vào log và không thay thế luồng skill CSV.

## Chuẩn bị và chạy

1. Cài Python 3.11 trở lên và `uv`.
2. Từ thư mục project, cài dependencies:

   ```powershell
   uv sync
   ```

3. Tạo `.env` từ `.env.example`, rồi điền API key của provider tương thích OpenAI. Không commit `.env`, API key, trace cá nhân hoặc log thật.
4. Chạy giao diện:

   ```powershell
   uv run streamlit run app.py --server.port 8506
   ```

   Mở `http://localhost:8506`. Các nút demo B1 và B2 chạy không cần gọi model. API key chỉ cần khi muốn chat với model.

## Kịch bản B1 — Compact

1. Chọn **Native tools + Compact**.
2. Trong **B1 · Compact session dài**, bấm **Nạp session dài mẫu**. Demo tạo lịch sử phân tích `data/workload.csv` với ngưỡng 8 giờ, một lần chạy sai đường dẫn, yêu cầu không sửa CSV và tool output lớn; không gọi API. Đây là luồng CSV của agent gốc để nhìn thấy rõ các ràng buộc còn lại sau compact.
3. Quan sát số ký tự ở **Active message context**, sau đó bấm **Compact ngay**.
4. Mở **Checkpoint đang được dùng**. Kiểm tra mục tiêu, yêu cầu tương thích, lỗi, lần thử, file và bước kế tiếp.
5. So sánh số ký tự trước/sau. Phần cũ được giữ trong archive cục bộ; chỉ checkpoint và các lượt gần đây còn trong active messages.
6. Để xem tự động compact, giảm ngưỡng xuống thấp hơn kích thước context rồi gửi lượt chat tiếp theo. Việc gọi model cần API key hợp lệ.

Checkpoint có schema gồm `goal`, `user_requirements`, `current_state`, `unresolved_issues`, `findings`, `failed_attempts`, `relevant_files`, `next_action` và metadata archive. Compact giữ tối đa hai lượt gần nhất; tool result trên 2.000 ký tự được thay bằng bản tóm tắt trong active context, còn nội dung gốc được giữ trong archive cục bộ của session. Nếu session ngắn hơn hai lượt, các message vẫn được giữ; checkpoint không thay thế yêu cầu mới nhất. Trigger là ngưỡng ký tự ước tính hoặc lượng tool output; đây không phải phép đo token chính xác. Nếu provider trả usage, snapshot hiển thị input/output token thực tế.

## Kịch bản B2 — Code Mode (cộng điểm)

1. Chuyển sang **Code Mode (B2)**.
2. Ở **B2 · Code Mode**, bấm **Chạy thử runtime cục bộ**. Chương trình mẫu đọc các log giả trong `workspace/data/logs/`, đếm `ERROR`/`WARN` theo tệp rồi chỉ trả số liệu tổng hợp.
3. Kiểm tra kết quả có `files_read`, `input_bytes_processed` và `raw_log_chars_returned_to_model: 0`.
4. Muốn kiểm tra qua agent, hỏi một câu tổng hợp, ví dụ: “Thống kê số ERROR và WARN trong tất cả log, nêu tệp có nhiều ERROR nhất.” Quan sát tool `run_code` và snapshot request.
5. Code Mode chỉ cấp `run_code` cùng memory tools cho model; không cấp `read_file`, `write_file` hay `bash`. Runtime chỉ đọc `.log`/`.txt` trong `workspace/data/logs/`, giới hạn kích thước, số file, số bước và dung lượng kết quả.

Runtime dùng AST interpreter với tập lệnh Python giới hạn, không gọi `exec`/`eval`. Đây là guardrail cho demo lớp học, không phải sandbox hệ điều hành để chạy mã không tin cậy.

## Thành phần kỹ thuật

- **Context:** system prompt, memory hiện hành, metadata skills và active message history. B1 thay phần history cũ bằng checkpoint có cấu trúc.
- **Harness:** `app.py` quản lý session, hai chế độ, UI demo, history, traces và quan sát request.
- **Tools:** tool file/shell/memory của agent nền; B2 bổ sung `run_code` với SDK đọc log giới hạn.
- **Skills:** catalog skill của agent nền vẫn được giữ; nội dung skill được nạp theo nhu cầu qua `read_file` ở Native mode.
- **Loop:** model → tool call(s) → tool result(s) → model đến câu trả lời cuối. Middleware giới hạn lượt gọi và ghi sự kiện.
- **Lưu trữ:** archive compact ở session hiện tại; quy ước dài hạn ở `memory/memory.json`; log demo trong `workspace/data/logs/`.

## Phân công nhóm 5 người

Phân công chi tiết, file bàn giao và tiêu chí nghiệm thu nằm trong [`TEAM-PLAN.md`](TEAM-PLAN.md). Mỗi thành viên nên làm trên nhánh riêng, mở PR vào nhánh tích hợp và không commit secrets hoặc dữ liệu thật.

Bảng Excel để nhập người nhận việc, hạn chót và trạng thái: [Tải bảng phân công](outputs/01a15c38-883e-7e23-b009-88b7ed4709af/Phan-cong-Challenge-B.xlsx).

## Cấu trúc chính

- `app.py`, `agent.py`, `observer.py`, `prompts.py`: giao diện, loop và quan sát.
- `compaction.py`: trigger, checkpoint và selective compaction.
- `tools/code_mode.py`: runtime Code Mode và giới hạn SDK đọc log.
- `workspace/data/logs/`: dữ liệu log tổng hợp để demo.
- `TEAM-PLAN.md`: phân chia việc cho 5 thành viên và cách tích hợp.
