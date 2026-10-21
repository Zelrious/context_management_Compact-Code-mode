---
name: csv-quality
description: Kiểm tra chất lượng CSV công việc (task_id, owner, hours), tính tổng giờ theo người và xác định người quá tải theo ngưỡng người dùng cung cấp bằng script có sẵn; ghi báo cáo Markdown dưới output/. Dùng khi người dùng yêu cầu rà soát dữ liệu CSV, tính tổng giờ hoặc kiểm tra quá tải.
---

# CSV quality

Kiểm tra CSV và tính tổng giờ bằng script, không tự đếm hay tự cộng bằng mắt.

## Xác định ngưỡng

- Tham số `--max-hours` bắt buộc và phải là số hữu hạn không âm; giá trị 0 hợp lệ.
- Đọc ngưỡng ngay trong yêu cầu hiện tại, kể cả diễn đạt tự nhiên: “vượt X giờ”, “ngưỡng X giờ” hoặc “tối đa X giờ” đều cung cấp `max_hours = X`. X là giá trị số người dùng nêu, không phải ngưỡng mặc định.
- Khi yêu cầu đã có ngưỡng hợp lệ, chạy script với giá trị đó; không hỏi lại ngưỡng. Chỉ hỏi khi yêu cầu thực sự chưa cung cấp ngưỡng hoặc giá trị không hợp lệ.
- Nếu yêu cầu chưa cung cấp ngưỡng, hỏi người dùng ngưỡng giờ trước khi chạy script và kết luận quá tải. Không tự chọn ngưỡng, không lấy ngưỡng từ cuộc trò chuyện cũ.
- Nếu đã có ngưỡng trong yêu cầu hiện tại, truyền đúng giá trị đó vào lệnh. Nếu ngưỡng không hợp lệ, yêu cầu người dùng cung cấp lại.

## Chạy script

Dùng tool `bash` (cwd là workspace). Lệnh đầy đủ:

```
python skills/csv-quality/scripts/check_csv.py --input <đường dẫn CSV> --max-hours <ngưỡng người dùng cung cấp>
```

Không cần đọc source script để chạy. Chỉ đọc `scripts/check_csv.py` khi cần hiểu một hành vi mà phần dưới không mô tả.

## Kiểm tra kết quả

- `exit_code` 0: phân tích thành công. `stdout` là JSON giữ các trường `input`, `row_count`, `missing_owner_count`, `invalid_hours_count`, `duplicate_id_count`, `duplicate_ids`, `issues` và thêm `max_hours`, `hours_by_owner`, `overloaded_owners`, `excluded_rows`. Dữ liệu có lỗi chất lượng hoặc người quá tải vẫn là exit 0.
- `exit_code` khác 0: **lỗi thực thi** (file không tồn tại, thiếu cột bắt buộc, lỗi parse hoặc tham số ngưỡng thiếu/không hợp lệ). Đọc `stderr`, báo không phân tích được. Không đưa ra tổng giờ, không ghi báo cáo như thể đã phân tích thành công.
- `timed_out` true hoặc `ok` false: lệnh không chạy xong; báo lỗi, không suy đoán kết quả.
- Phân biệt rõ **lỗi dữ liệu** (nằm trong `issues`, script vẫn chạy thành công) với **lỗi thực thi** (exit khác 0).

## Hiểu tổng giờ và dòng bị loại

- Script bỏ khoảng trắng đầu/cuối ở ID, owner và hours; không gộp owner khác tên hoặc khác hoa/thường.
- Mỗi ID không rỗng chỉ giữ lần xuất hiện đầu tiên trong file, kể cả lần đầu có dữ liệu không hợp lệ. Không thay bằng lần hợp lệ đầu tiên.
- Chỉ cộng dòng đúng số trường, có ID và owner, hours hữu hạn không âm. Dòng trống được bỏ qua. Owner chỉ xuất hiện trong `hours_by_owner` khi có ít nhất một dòng được cộng, kể cả tổng bằng 0.
- `overloaded_owners` gồm `{owner, total_hours}`, sắp xếp theo tên. Quá tải khi tổng lớn hơn `max_hours`; bằng ngưỡng không phải quá tải.
- `excluded_rows` gồm `{line, task_id, reasons}`, sắp theo dòng; ID rỗng là `null`. Mỗi dòng xuất hiện một lần, đủ lý do theo thứ tự `wrong_field_count`, `missing_task_id`, `duplicate_id`, `missing_owner`, `invalid_hours`.
- Thống kê chất lượng và `issues` vẫn xét toàn bộ dòng dữ liệu, bao gồm các dòng bị loại khỏi tổng giờ. Header là dòng 1.

## Viết báo cáo

1. Đọc template `references/report-template.md` trong thư mục skill này, tức `skills/csv-quality/references/report-template.md`.
2. Lấy mọi con số từ JSON: ngưỡng, tổng giờ từng owner, danh sách quá tải, thống kê chất lượng. Liệt kê từng dòng bị loại cùng tất cả mã lý do; không tự cộng lại các dòng bị loại.
3. Không sửa file CSV khi người dùng chỉ yêu cầu kiểm tra. Có thể đề xuất cách sửa trong mục khuyến nghị.
4. Tổng giờ đã tính trên các dòng hợp lệ được giữ lại; ghi rõ phạm vi này và các dòng bị loại. Không diễn giải thành tổng giờ của toàn bộ dữ liệu chưa làm sạch hoặc tự tính KPI khác.
5. Ghi báo cáo bằng `write_file` vào đường dẫn người dùng yêu cầu (mặc định `output/csv-quality.md`), rồi trả lời đường dẫn và tóm tắt ngắn.
