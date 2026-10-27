# Kịch bản kiểm thử B1 — Compact

## Mục tiêu kiểm thử

Xác nhận sau một session dài, agent vẫn giữ được mục tiêu, yêu cầu và trạng thái quan trọng trong checkpoint; lịch sử và tool output dài được giảm khỏi active context; các lượt gần nhất và dữ liệu gốc vẫn được giữ phù hợp.

## Kịch bản 1 — Compact thủ công qua giao diện (không cần API)

### Các bước

1. Mở project `challenge-b-context-management` và chạy giao diện:

   ```powershell
   uv run streamlit run app.py --server.port 8506
   ```

2. Mở `http://localhost:8506` và chọn chế độ **Native tools + Compact**.
3. Trong mục **B1 · Compact session dài (bắt buộc)**, bấm **Nạp session dài mẫu**.
4. Ghi lại số ở **Active message context** trước khi compact.
5. Bấm **Compact ngay**.
6. Mở **Checkpoint đang được dùng** và đối chiếu các trường bên dưới.
7. Ghi lại số ký tự sau compact và thông tin archive hiển thị trên giao diện.

### Kết quả mong đợi

| Hạng mục | Kết quả cần thấy |
| --- | --- |
| Mục tiêu | Nói rõ đang phân tích `data/workload.csv`. |
| Yêu cầu | Có ngưỡng 8 giờ; tổng bằng 8 không tính quá tải; không sửa CSV; báo các dòng bị loại và lý do. |
| Lỗi/lần thử | Có lần chạy thất bại với `data/missing.csv` và mã `INPUT_NOT_FOUND`; ghi nhận cần chạy lại với `data/workload.csv`. |
| Trạng thái/phát hiện | Ghi nhận script CSV đã chạy thành công và `row_count=6` từ kết quả tool giả lập. |
| File liên quan | Có `data/workload.csv`; lần chạy sai `data/missing.csv` được giữ trong mục lỗi/lần thử. |
| Bước tiếp theo | Đối chiếu skill/template rồi ghi `output/csv-quality.md`, không sửa file CSV nguồn. |
| Active context | Số ký tự sau compact nhỏ hơn trước. Tool result dài không còn ở active messages. |
| Archive | Số message và số ký tự tool output đã lưu lớn hơn 0. Bản tool result gốc được giữ ở archive session. |
| Lượt gần nhất | Hai message người dùng gần nhất vẫn còn trong active history. |

**Lưu ý:** phần nạp session và compact thủ công chỉ dùng dữ liệu giả, không gọi model và không cần API key.

## Kịch bản 2 — Tự động compact trước khi gọi model (cần API hoạt động)

Kịch bản này chứng minh **app tự compact khi bạn gửi câu hỏi mới**. Nút **Nạp session dài mẫu** chỉ tạo lịch sử giả, không gọi model. API chỉ được gọi ở bước gửi câu hỏi.

### Trước khi bắt đầu

1. Nếu trang `localhost:8506` báo mất kết nối, tải lại trang. Khởi động lại app sẽ tạo session trống; câu hỏi đầu tiên trong session trống **không có gì để compact**.
2. Xác nhận ô **Nhập yêu cầu…** đang dùng được. Nếu ô bị khóa, kiểm tra `.env` trong thư mục project có `OPENAI_API_KEY` và `MODEL_NAME`.
3. Chọn **Native tools + Compact**.
4. Bấm **Cuộc trò chuyện mới** để không lẫn lịch sử cũ vào lần trình diễn.

### Các bước chạy

1. Trong mục **B1 · Compact session dài**, để ngưỡng ở mặc định `24.000` ký tự.
2. Bấm **Nạp session dài mẫu** trước khi nhập câu hỏi. Xác nhận **Active message context** tăng lên khoảng `120.000` ký tự và giao diện báo context đã vượt ngưỡng. Đây là dữ liệu giả về phân tích CSV; chưa có request nào gửi tới model.
3. Ở ô **Nhập yêu cầu…**, nhập:

   ```text
   Dựa chỉ vào checkpoint vừa compact, hãy tóm tắt: mục tiêu, ngưỡng và ràng buộc, lỗi đã gặp, bước tiếp theo. Không đọc/ghi/chạy tệp hoặc gọi tool trong lượt này.
   ```

4. Gửi câu hỏi. App sẽ compact phần lịch sử dài ngay trong lượt gửi, sau đó mới gọi model. Thao tác compact diễn ra nhanh nên có thể thấy model bắt đầu gần như ngay lập tức; hãy xác nhận thứ tự trong **Event log**: `context_compacted` phải đứng trước `model_request`.
5. Đợi model trả lời. Lượt này mới sử dụng API key trong `.env`. App giới hạn phần trả lời ở 1.024 token để chừa dung lượng trong hạn mức TPM của Groq.
6. Mở **Event log** và **Snapshot request** mới nhất ở bảng **State & Context** để đối chiếu.

### Dấu hiệu xác nhận đã tự compact

- Mục B1 hiện thông báo dạng **Đã compact: [số trước] → [số sau] ký tự** và cho biết số message/tool output đã đưa vào archive.
- Event log có `context_compacted` đứng trước `model_request` của lượt vừa gửi.
- Snapshot request mới nhất chứa checkpoint cùng câu hỏi bạn vừa gửi; tool output dài cũ không còn nằm nguyên văn trong active messages.
- Checkpoint vẫn có mục tiêu CSV, ngưỡng 8 giờ, ràng buộc không sửa file và bước tiếp theo tạo `output/csv-quality.md`.
- Agent trả lời ngắn từ checkpoint, không cần gọi tool. Kịch bản này chỉ kiểm tra compact và khả năng tiếp tục từ checkpoint; việc chạy lại phân tích CSV là một bài kiểm tra riêng.

**Nếu gặp lỗi `413` / `Requested ... tokens` vượt `TPM`:** đây là giới hạn token theo phút của Groq, không phải lỗi API key. Thử lại sau khi hết lượt giới hạn; nếu vẫn gặp, giảm `MAX_MODEL_OUTPUT_TOKENS` trong `.env` (ví dụ `512`) rồi khởi động lại app. Ngưỡng compact tính theo ký tự ước tính, không phải token chính xác.

**Nếu gặp `Model call limits exceeded: run limit (8/8)`:** compact vẫn thành công nếu Event log có `context_compacted` trước `model_request`. Lỗi này nghĩa là tác vụ tiếp tục đã gọi model đủ 8 lần; hãy bắt đầu session mới, nạp lại session mẫu và dùng câu hỏi ngắn ở trên để kiểm tra riêng checkpoint. Không tăng giới hạn model call cho kịch bản compact.

**Nếu chưa thấy compact:** kiểm tra bạn đã bấm **Nạp session dài mẫu** trong session hiện tại chưa; gửi câu hỏi sau thao tác đó. Chỉ giảm ngưỡng khi thử với hội thoại thật có kích thước nhỏ hơn ngưỡng mặc định.

## Kịch bản 3 — Kiểm tra tự động, không gọi API

Từ thư mục `challenge-b-context-management`, chạy:

```powershell
..\.venv\Scripts\python.exe -m pytest tests\test_compaction.py -q
```

Kết quả mong đợi:

```text
3 passed
```

Ba kiểm tra này xác nhận:

1. Session ngắn vẫn giữ message người dùng mới nhất; tool output dài được rút gọn và bản gốc đưa vào archive.
2. Checkpoint giữ mục tiêu CSV, ràng buộc, lỗi lần chạy, file liên quan và bước tiếp theo; active context nhỏ hơn trước.
3. Compact lần nữa vẫn kế thừa mục tiêu và yêu cầu cũ từ checkpoint trước.

## Mẫu ghi nhận kết quả khi trình bày

```text
Session mẫu đã được nạp: [có/không]
Ký tự trước compact: ______
Ký tự sau compact: ______
Yêu cầu/ngưỡng còn trong checkpoint: ______
Lỗi và lần thử còn trong checkpoint: ______
File liên quan còn trong checkpoint: ______
Bước tiếp theo còn trong checkpoint: ______
Tool output gốc có trong archive: [có/không]
Kết luận: [đạt/chưa đạt]
```

Nếu một trường không xuất hiện, ghi rõ trường đó thiếu và chụp checkpoint cùng active messages để xác minh.
