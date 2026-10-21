# Kế hoạch chia việc Challenge B

## Mục tiêu theo slide

- **B1 — Compact (bắt buộc):** nén session dài thành checkpoint có cấu trúc, để agent tiếp tục đúng việc. Checkpoint giữ mục tiêu, yêu cầu/ràng buộc, trạng thái, phát hiện, lỗi chưa xử lý, cách đã thử, file liên quan và bước tiếp theo. Log thô và kết quả trùng lặp được lưu ngoài working context.
- **B2 — Code Mode (điểm cộng):** agent sinh một chương trình; runtime gọi các tool đọc nhiều file log và tổng hợp; model chỉ nhận findings cuối, không nhận raw log ở từng bước.

## Phân công cho 5 thành viên

| Thành viên | Phụ trách | Tệp/đầu ra | Tiêu chí bàn giao |
| --- | --- | --- | --- |
| 1 — Compact | Schema checkpoint, selective keep/drop, trigger theo ngưỡng và lưu archive | `compaction.py`, luồng compact trong harness | Sau compact còn đủ lỗi đang điều tra, các cách đã thử và bước tiếp; context nhỏ hơn; checkpoint xem được trong UI |
| 2 — Code Mode | Runtime chạy chương trình giới hạn, SDK chỉ đọc log, giới hạn quyền/thời gian/kích thước | `tools/code_mode.py` | Script tổng hợp được nhiều log; raw output không xuất khỏi runtime; code sai/đầu ra quá lớn bị chặn |
| 3 — UI và tích hợp | Chế độ Compact/Code Mode, nút nạp session dài, nút compact, hiển thị checkpoint, context và usage | `app.py`, `agent.py`, `prompts.py`, cấu hình | Có thể chạy từng demo qua giao diện; thấy rõ context trước/sau và findings sau code |
| 4 — Dữ liệu và minh chứng | Log giả, kịch bản lỗi dài, câu hỏi nhiều file, kịch bản kiểm chứng token/context | `workspace/data/logs/`, `fixtures/`, `tests/`, ảnh chụp | Có dữ liệu tái lập; chứng minh agent nhớ lỗi/cách đã thử/bước tiếp; chứng minh log thô không vào messages |
| 5 — Tích hợp và phát hành | Rà soát PR, xử lý xung đột, cập nhật hướng dẫn/báo cáo, ghép minh chứng và phát hành | `README.md`, `TEAM-PLAN.md`, kết quả tích hợp | Cả B1 và B2 chạy cùng một bản; README có lệnh chạy, câu hỏi demo, giới hạn và phân công |

## Thứ tự phối hợp

1. Thành viên 1 và 2 chốt schema/interfaces trước: `compact_messages(messages, threshold)` và tool `run_code(code)`.
2. Thành viên 3 tích hợp hai module vào app sau khi interfaces được thống nhất.
3. Thành viên 4 chuẩn bị dữ liệu/kịch bản song song; không sửa `app.py` hay module của thành viên 1–3.
4. Thành viên 5 nhận PR lần lượt, rà tiêu chí, cập nhật hướng dẫn và tạo bản tích hợp cuối.

Mỗi thành viên làm trên nhánh riêng: `feat/b1-compaction`, `feat/b2-code-mode`, `feat/context-ui`, `feat/evidence`, `docs/integration`. PR nhỏ, ghi cách chạy và bằng chứng; không commit `.env`, API key, trace cá nhân hoặc dữ liệu thật.

## Điều kiện nghiệm thu

- Compact tự kích hoạt trước lượt kế tiếp khi vượt ngưỡng cấu hình và có nút kích hoạt thủ công.
- Sau compact, checkpoint giữ nguyên lỗi đang điều tra, cách thử đã thất bại/thành công, file liên quan và hành động tiếp theo; tool output lớn không còn trong active messages.
- Gửi câu hỏi tiếp theo sau compact cho kết quả phù hợp với checkpoint; giao diện cho xem checkpoint, số ký tự trước/sau và usage thực tế nếu provider trả về.
- Code Mode trả lời được câu hỏi tổng hợp trên nhiều file log; request model chỉ có chương trình và kết quả tổng hợp, không có nội dung raw log.
- Runtime chỉ đọc trong thư mục log demo, có giới hạn file/kích thước/số bước/đầu ra; lỗi chương trình được trả thành thông báo ngắn.
- README ghi rõ API key chỉ dùng local, cách chạy B1/B2 và giới hạn bảo mật của runtime.
