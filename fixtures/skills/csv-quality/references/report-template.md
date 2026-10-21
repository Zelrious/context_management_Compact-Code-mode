# Báo cáo chất lượng dữ liệu và quá tải: `{đường dẫn CSV}`

Công cụ: `skills/csv-quality/scripts/check_csv.py` | exit code: {exit_code}

Ngưỡng: **{max_hours} giờ**. Quá tải khi tổng giờ lớn hơn ngưỡng; bằng ngưỡng không quá tải.

## Tổng quan
| Chỉ số | Giá trị |
|---|---|
| Số dòng dữ liệu (không tính header) | {row_count} |
| Dòng thiếu owner | {missing_owner_count} |
| Dòng hours không hợp lệ | {invalid_hours_count} |
| Số task_id bị lặp (distinct) | {duplicate_id_count} ({duplicate_ids}) |

## Tổng giờ theo người
Chỉ gồm dòng hợp lệ với lần xuất hiện đầu tiên của ID; không cộng các dòng bị loại.

| Owner | Tổng giờ |
|---|---|
| {owner} | {hours_by_owner[owner]} |

## Người quá tải
| Owner | Tổng giờ | Ngưỡng |
|---|---|---|
| {overloaded_owners.owner} | {overloaded_owners.total_hours} | {max_hours} |

Nếu danh sách rỗng, ghi rõ không có người vượt ngưỡng.

## Dòng bị loại khỏi tổng giờ
Mỗi dòng xuất hiện một lần; ghi đầy đủ mã lý do từ `excluded_rows`, theo thứ tự script trả về.

| Line | task_id | Lý do |
|---|---|---|
| {line} | {task_id hoặc null} | {reasons} |

Nếu danh sách rỗng, ghi rõ không có dòng bị loại.

## Chi tiết lỗi
| Line | Cột | Loại | task_id | Mô tả |
|---|---|---|---|---|
| {line} | {column} | {type} | {task_id} | {message} |

## Đánh giá
- Tổng giờ và kết luận quá tải dựa trên các dòng hợp lệ được giữ lại. Nếu còn lỗi, nêu ảnh hưởng của các dòng bị loại; không xem đây là tổng của toàn bộ dữ liệu chưa làm sạch.

## Khuyến nghị
- {Cách sửa đề xuất cho từng nhóm lỗi; không tự sửa file nguồn}
