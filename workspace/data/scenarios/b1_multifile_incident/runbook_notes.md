# Runbook xử lý sự cố nghẽn kết nối giao dịch (dữ liệu tổng hợp)

## 1. Quy tắc chẩn đoán đa tệp
- Đối chiếu `service_sla_config.json` và `deploy_manifest.md` để tính tổng kết nối mà `orders-sync-v3` chiếm dụng:
  `occupied_connections = orders_sync_concurrency * connections_per_sync_worker`.
- Nếu `occupied_connections > db_pool_max_connections` và `allow_pool_resize = false`, **nghiêm cấm đề xuất tăng kích thước pool DB**. Phải đề xuất rollback `orders_sync_concurrency` về giá trị ổn định trước khi phát hành (`4`).

## 2. Quy tắc phân công người xác minh bản vá
- Chạy kiểm tra chất lượng trên `data/scenarios/b1_multifile_incident/batch_workload.csv` với ngưỡng quá tải `--max-hours 6` (tổng đúng bằng `6` giờ không bị tính quá tải).
- Chỉ định kỹ sư trực nhận nhiệm vụ xác minh rollback thỏa đồng thời hai điều kiện trên dữ liệu hợp lệ:
  1. Không nằm trong danh sách quá tải ở ngưỡng `6` giờ.
  2. Có tổng giờ hợp lệ hiện tại **nhỏ hơn `4.0` giờ**.

## 3. Đầu ra bắt buộc
- Không chỉnh sửa bất kỳ file nào trong `data/`.
- Ghi báo cáo khắc phục vào `output/incident-remediation.md`, nêu rõ: nguyên nhân gốc, phép tính kết nối (`12 * 3 = 36 > 25`), tham số rollback (`orders_sync_concurrency=4`), người xác minh (`Khanh`), và danh sách dòng CSV bị loại kèm mã lý do.
