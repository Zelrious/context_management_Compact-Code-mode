# Nhật ký triển khai bản phát hành 2026.10.24-rc3 (dữ liệu tổng hợp)

- **Mã bản dựng:** `2026.10.24-rc3`
- **Môi trường:** `staging-eu-01`
- **Thời điểm triển khai:** `2026-10-25T13:55:00Z`

## Thay đổi cấu hình dịch vụ

1. **`orders-sync-v3` (đồng bộ đơn hàng theo lô)**
   - Tham số `orders_sync_concurrency`: tăng từ `4` (mức ổn định trước đó) lên `12` để thử nghiệm xử lý lô nhanh.
   - Mỗi worker giữ kết nối trực tiếp vào pool `payments-db` theo hệ số `connections_per_sync_worker` trong `service_sla_config.json`.
2. **`auth-token` (xác thực phiên)**
   - Không đổi cấu hình kết nối DB (`uses_db_pool = false`, dùng bộ nhớ đệm nội bộ, `cache_ttl_sec = 300`).
3. **`gateway-edge` (cổng API)**
   - Giữ nguyên `retry_max = 2` và timeout theo `checkout_timeout_ms` của cấu hình SLA.
