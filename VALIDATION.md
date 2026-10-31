# Kết quả kiểm tra — 23/10/2026

## Kết luận

Đây là **bản thử nghiệm có kiểm tra offline**, chưa phải crawler Shopee đã xác minh chạy thành công trên dữ liệu thật. Có thể dùng làm nền để thử trên máy cá nhân và điều chỉnh adapter theo cấu trúc trang thực tế.

## Quan sát trực tiếp trên Shopee

- Đã mở `https://shopee.vn/` bằng trình duyệt thử nghiệm.
- Shopee chuyển sang đường dẫn `/verify/captcha` và hiển thị “Xác nhận để tiếp tục”.
- Không giải CAPTCHA, không truy cập endpoint thay thế và không lấy được sản phẩm thật.
- Không lưu URL đầy đủ của thử thách vì chứa tham số phiên/xác minh.
- Không có file dataset thật trong gói này. Fixture trong `tests/` là dữ liệu giả lập, ghi nhãn rõ ràng.

## Kiểm tra đã chạy

Môi trường kiểm tra: Python 3.12.14 trên Linux, Playwright Python 1.64.1.

```text
python -m unittest discover -s tests -v
Ran 22 tests
OK
```

| Nhóm | Nội dung đã xác nhận bằng kiểm tra offline |
|---|---|
| URL | Chuẩn hóa ID sản phẩm, bỏ tracking, loại URL khác tên miền và URL đăng nhập |
| Giá | Chuyển đơn vị Decimal, giữ giá 0, loại số âm/NaN/Infinity, JSON-LD không chia lại |
| Phân loại mua | Giữ đúng model quan sát và chỉ số màu/size, giữ tồn kho 0, không tự sinh các tổ hợp chưa thấy |
| Review | Gắn đúng sản phẩm, loại trùng theo ID, không gắn review sản phẩm khác vào trang hiện tại, không xuất tên người review |
| Lưu trữ | Có lịch sử quan sát, hàng chờ chạy tiếp, không xóa phân loại chi tiết khi nhận thêm bản tóm tắt |
| Xuất dữ liệu | CSV UTF-8 BOM, chống công thức Excel trong nội dung, không tạo dòng dữ liệu giả khi kho trống |
| Đơn vị khi ghép | Giá JSON-LD và đơn vị được cập nhật cùng nhóm; không ghép với giá range cũ theo đơn vị khác |
| Pipeline | Chỉ đọc phản hồi phù hợp; bỏ dữ liệu tài khoản/khác tên miền; HTTP 429 làm dừng; dừng hàng chờ ngay khi trang đầu bị chặn |
| Phân ngành | Đối sánh tiếng Việt, giữ trường hợp nhập nhằng và không khớp thay vì ép nhãn |

Đã chạy lệnh demo riêng ngoài thư mục phát hành: 1 sản phẩm giả lập, 2 model và 2 review. Đây chỉ là kiểm tra parser → SQLite → xuất file, không kiểm tra chất lượng dữ liệu Shopee thực tế.

## Phần chưa được xác minh

1. Crawl sản phẩm thật, tên shop thật, giá model thật hoặc phân trang review thật.
2. Cấu trúc phản hồi hiện tại của Shopee và các selector phân trang trong cấu hình.
3. Giả định chia giá phản hồi mạng cho 100000. Có giữ giá thô, đơn vị và cờ giả định để kiểm tra thủ công.
4. Độ đầy đủ của các model, review và danh mục; ngữ nghĩa chính xác của các trường số đã bán.
5. Chạy dài, nhiều ngành hàng, khối lượng lớn; chưa có cơ sở đánh giá tốc độ/số sản phẩm mỗi giờ.
6. Chạy các file `.bat` trên Windows. Chúng được cung cấp để người dùng chạy; kiểm tra tự động hiện tại diễn ra trên Linux.
7. Khởi động trình duyệt từ gói Python tại môi trường kiểm tra: việc tải Chromium của Playwright thất bại vì file tải về không giải nén được. Trình duyệt dùng để quan sát CAPTCHA là một môi trường riêng, không phải kiểm tra end-to-end của gói Python.

## Lượt kiểm tra thực tế nên làm trên máy người dùng

1. Dùng cấu hình 3–5 link sản phẩm thật, bỏ các từ khóa khởi đầu.
2. Tự đăng nhập/xác minh trong trình duyệt do công cụ mở nếu cần; dùng `retry`, rồi `run`.
3. Đối chiếu tên, shop, 2–3 giá phân loại, đánh giá và các số đã bán với trang đang mở. Nếu giá sai đơn vị, sửa adapter/cấu hình trước khi tăng phạm vi.
4. Xem `coverage_report.json`, `record_level`, `variant_capture_status`, `field_observations`; trường thiếu là việc cần kiểm tra, không phải giá trị 0.
5. Chỉ tăng phạm vi sau khi các bước đối chiếu trên thành công và đã xác định quyền sử dụng nguồn dữ liệu. Nếu vẫn bị chặn liên tục thì dừng và chuyển sang nguồn được cấp quyền.
