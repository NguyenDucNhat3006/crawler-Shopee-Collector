# Shopee Collector — bản thử nghiệm cho Nguyễn Đức Nhật

**Trạng thái: đã triển khai mã và kiểm tra offline; chưa xác nhận crawl dữ liệu thật thành công.**
Ngày 23/10/2026, lần mở Shopee bằng trình duyệt thử nghiệm bị chuyển sang `/verify/captcha`, hiển thị “Xác nhận để tiếp tục”. Không có sản phẩm hay review thật nào được lấy trong lần thử này. Không có cơ sở để gọi đây là công cụ lấy đủ toàn sàn.

## 1. Chạy trên Windows

Yêu cầu Python 3.10 trở lên và kết nối Internet. Không cần GPU, Docker hay máy ảo.

1. Giải nén vào thư mục riêng, ví dụ `D:\Shopee_Collector`. Không đặt trong thư mục profile Chrome đang dùng.
2. Chạy `SETUP_WINDOWS.bat` một lần để tạo `.venv`, cài Playwright và Chromium.
3. Chạy `START_WINDOWS.bat`. Hai cửa sổ xuất hiện: trình duyệt và terminal.
4. Nếu cần đăng nhập/xác minh, tự thực hiện trong cửa sổ trình duyệt của công cụ. Không nhập mật khẩu, cookie hay OTP vào chat hoặc file cấu hình.
5. Trong terminal, nhập `retry` sau khi trang đã truy cập được, rồi nhập `run` để chạy hàng chờ.
6. Nhập `stats` xem số lượng và trường thiếu; `export` xuất file; `quit` thoát có lưu dữ liệu.

Nếu bị xác minh lặp lại, dừng phiên chạy. Hoàn thành CAPTCHA không bảo đảm Shopee sẽ cho phép thu thập tiếp. Không dùng công cụ để vượt quyền truy cập hoặc các giới hạn của sàn; kiểm tra quyền sử dụng dữ liệu trước khi chạy quy mô lớn.

Mặc định phiên trình duyệt là tạm thời, không chủ động lưu phiên đăng nhập qua lần chạy sau. Nếu bạn **muốn lưu phiên trên máy cá nhân của mình**, tự chạy:

```powershell
.\.venv\Scripts\python.exe -m shopee_collector run --remember-session
```

Lựa chọn này lưu phiên vào `browser-profile/`. Thư mục đó có thể chứa thông tin xác thực: không gửi, nén chung hoặc commit lên Git. Muốn xóa phiên, đóng công cụ rồi xóa thư mục đó trên máy bạn.

Nếu tải Chromium thất bại nhưng máy có Google Chrome, sửa `"browser_channel": "chrome"` trong `config/settings.json`. Phải cài thành công các gói Python trước; nếu file batch cài đặt dừng ở bước tải trình duyệt, có thể chạy `START_WINDOWS.bat` sau khi đổi cấu hình. Tùy chọn này dùng Chrome cài sẵn với profile riêng; không lấy phiên Chrome cá nhân đang mở.

## 2. Phạm vi thu thập và phân loại

- Mặc định: trang chủ để tìm link danh mục được hiển thị, cộng 20 từ khóa khởi đầu ở nhiều ngành hàng. Đây là danh sách khởi đầu do người triển khai đặt, không phải danh sách toàn bộ ngành hàng hiện tại của Shopee.
- Mỗi lần chạy giới hạn 60 trang/hàng chờ, tối đa 30 lần ghé trang sản phẩm, tối đa 2 trang danh sách và 2 trang review trên mỗi sản phẩm. Việc cuộn và phân trang có thể phát sinh thêm tải trang/tài nguyên. Giới hạn này phục vụ kiểm tra bản đầu; không phải số sản phẩm bảo đảm lấy được.
- Link sản phẩm được lấy từ các thẻ liên kết trên trang, chuẩn hóa bằng cặp `shop_id:item_id`, loại link trùng và đưa vào hàng chờ SQLite.
- Các sản phẩm xuất hiện trong phản hồi danh sách có thể được ghi nhận trước khi ghé trang chi tiết; `record_level=listing` giúp nhận ra chúng. Vì thế số dòng sản phẩm quan sát có thể lớn hơn 30.
- Danh mục sản phẩm: ưu tiên đường dẫn danh mục/ID thực sự có trong dữ liệu sản phẩm. Bảng `categories.csv` có thể bổ sung tên từ các link danh mục đã thấy, không suy diễn mọi sản phẩm trên một trang đều thuộc danh mục đó.
- Nếu chưa quan sát được tên danh mục, `suggested_category` dùng bộ luật từ khóa trong `config/taxonomy.json`. Có 20 nhãn khởi đầu. Đây là phỏng đoán, không phải mô hình AI đã được huấn luyện và không phải danh mục chính thức. Nhãn “Cần kiểm tra” và “Chưa phân loại” được giữ lại thay vì ép nhãn.

**Toàn bộ Shopee không phải phạm vi có thể chứng minh bằng cách này.** Kết quả tìm kiếm/đề xuất có thể thay đổi, sản phẩm có thể thêm/xóa, một số dữ liệu không được trả về. Hết hàng chờ chỉ có nghĩa đã xử lý các URL đã phát hiện. Không có tổng số sản phẩm toàn sàn để tính phần trăm bao phủ.

## 3. Dữ liệu đầu ra

Các file thật được tạo trong `output/live/` khi bạn chạy:

| File | Nội dung |
|---|---|
| `products.csv` | Tên, mô tả, shop, giá/range được quan sát, điểm đánh giá, các trường số đã bán, danh mục, thuộc tính, ảnh và chất lượng dữ liệu |
| `variants.csv` | Mỗi model/phân loại được quan sát là một dòng: tên, tổ hợp tùy chọn, giá, giá gốc thô, đơn vị, tồn kho nếu có |
| `reviews.csv` | ID review, sản phẩm, điểm, nội dung, thời gian gốc và phân loại mua nếu có |
| `shops.csv` | ID shop, tên hiển thị, tên tài khoản công khai của shop nếu có |
| `categories.csv` | Danh mục/ID/tên thực sự quan sát được |
| `products.jsonl` | Sản phẩm với cấu trúc lồng nhau, bao gồm các model/phân loại |
| `catalog.sqlite3` | Dữ liệu gốc chuẩn hóa, lịch sử quan sát, hàng chờ và lỗi để chạy tiếp |
| `coverage_report.json` | Số dòng, hàng chờ và trường còn thiếu; luôn ghi độ bao phủ toàn sàn là chưa biết |

CSV là UTF-8 BOM để Excel đọc tiếng Việt. Các trường danh sách/đối tượng trong CSV được mã hóa JSON. CSV thêm dấu nháy đơn trước chuỗi có khả năng là công thức Excel; nội dung nguyên bản giữ trong SQLite/JSONL.

Không lưu tài khoản cá nhân người review, cookie, request header, dữ liệu đơn hàng hoặc toàn bộ phản hồi mạng. Nội dung review công khai có thể tự chứa thông tin cá nhân; cần rà soát trước khi công bố dataset.

## 4. Những điểm cần đọc trước khi dùng dữ liệu

### Giá và phân loại mua

- Phản hồi mạng dùng bộ chuyển đổi có cấu hình `network_price_divisor=100000`. **Đây là giả định adapter chưa đối chiếu với phiên Shopee thật hiện tại.** Luôn giữ `price_raw` và `price_divisor`; đối chiếu vài sản phẩm trực tiếp trước khi dùng cho phân tích/train model. Không suy đơn vị bằng độ lớn con số.
- Giá trong JSON-LD dùng đơn vị tiền tệ khai báo, không chia thêm 100000. Nguồn đơn vị được ghi ở `price_unit_status`.
- Chỉ xuất các model thực sự thấy trong phản hồi trình duyệt. Không lấy giá thấp nhất rồi gán cho mọi màu/size; không tự sinh tích Descartes từ các tùy chọn.
- Không thấy `models` được ghi `variant_capture_status=not_observed`; điều đó không chứng minh sản phẩm không có phân loại. Công cụ chưa tự bấm mọi tổ hợp màu/size để bù dữ liệu.
- `models_observed` chỉ có nghĩa đã thấy danh sách model, **chưa chứng minh danh sách đó đầy đủ**.
- Giá này chưa tính chắc chắn được voucher cá nhân, ưu đãi theo tài khoản, vận chuyển hoặc tổng thanh toán. Không gọi đây là giá checkout cuối cùng.
- Phiên bản hiện tại ghép các trường khác nhau theo lần quan sát gần nhất có dữ liệu. `field_observations` ghi độ mới của từng trường; một dòng có thể chứa thông tin quan sát ở nhiều thời điểm. Giá và đơn vị giá luôn được cập nhật cùng một nhóm. Mỗi model còn giữ đơn vị giá riêng.

### Số đã bán, điểm và review

- Giữ riêng `sold_period_reported` từ trường `sold` và `sold_total_reported` từ `historical_sold`. Ngữ nghĩa thời gian chưa được xác minh; không cộng hai trường, không coi đó là doanh số chính xác hoặc số đơn hàng duy nhất.
- Điểm sản phẩm và điểm từng review là hai trường khác nhau. Giá trị thiếu để trống, không đổi thành 0.
- Review trùng ID được cập nhật thay vì nhân bản. Review không có ID ổn định bị bỏ qua, tránh gộp nhầm các review có cùng nội dung.
- Công cụ chỉ đọc review được trang thực sự tải. Có thể thiếu do phân trang, lọc, giới hạn hoặc thay đổi giao diện. Không coi không thu được review là sản phẩm chưa có review.
- Bộ phân trang mặc định là selector ứng viên chưa kiểm tra trên Shopee thật hiện tại. Nếu không thấy đúng một nút Next đang bật, công cụ dừng phân trang. Có thể sửa `selectors.review_next` và `selectors.listing_next` sau khi kiểm tra giao diện thực tế.

## 5. Sửa nguồn và tăng phạm vi dần

Để thử vài sản phẩm trước, thay `seeds` bằng các link của bạn và để `keywords` rỗng:

```json
{
  "seeds": ["https://shopee.vn/product/SHOP_ID/ITEM_ID"],
  "keywords": []
}
```

Đây chỉ là **phần cần thay trong cấu hình đầy đủ**, không phải toàn bộ file hợp lệ. Thay `SHOP_ID` và `ITEM_ID` bằng số trong link thật. Công cụ cũng hỗ trợ URL có dạng tên sản phẩm `-i.SHOP_ID.ITEM_ID`. Với link rút gọn, tự mở trong trình duyệt và lấy link sản phẩm đầy đủ.

Shop/danh mục có thể dùng làm seed URL; công cụ lấy các link sản phẩm thấy trên trang và thử phân trang bằng selector cấu hình. Không tự đoán API riêng của shop. Danh sách từ khóa có thể mở rộng, nhưng thêm từ khóa không chứng minh lấy đủ toàn bộ ngành hàng.

Chạy tiếp dùng cùng `--output`; để bắt đầu một lượt độc lập dùng thư mục mới:

```powershell
.\.venv\Scripts\python.exe -m shopee_collector run --output output/lan_2
.\.venv\Scripts\python.exe -m shopee_collector export --output output/live
```

`--auto` xử lý hàng chờ rồi thoát, gặp chặn là dừng. Chế độ tương tác mặc định thuận tiện hơn khi cần đăng nhập/xác minh. Không có proxy rotation, giả dấu vân tay trình duyệt, CAPTCHA solver hoặc tái tạo token để vượt chặn.

## 6. Kiểm tra offline

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m shopee_collector demo
```

Lệnh demo tạo `output/demo/` từ `tests/synthetic_payload.json`. **Tất cả dữ liệu demo là giả lập**, ghi rõ “GIẢ LẬP” và `SYNTHETIC_DATA_NOT_LIVE`. Không dùng làm bằng chứng đã crawl Shopee.

## 7. Cách hoạt động và giới hạn kỹ thuật

Playwright mở trình duyệt có giao diện. Công cụ quan sát một số phản hồi JSON mà trang tự tải khi điều hướng/cuộn/phân trang và đọc JSON-LD nếu có. Không gọi trực tiếp endpoint API không được công bố. Chỉ giữ các trường sản phẩm/shop/review đã chọn.

Bộ đọc hỗ trợ một số tên trường như `itemid`, `shopid`, `models`, `tier_variations`, `item_rating`, `cmtid`. Những cấu trúc này là adapter thử nghiệm, không phải hợp đồng API chính thức. Nếu Shopee thay cấu trúc hoặc không trả JSON phù hợp, có thể không thu được dữ liệu. Không lấy được tên shop/giá model thì báo thiếu; không đoán để điền đủ.

Gặp URL xác minh/đăng nhập, thông báo xác minh, hoặc HTTP 401/403/429 ở các yêu cầu dữ liệu được theo dõi, công cụ tạm dừng toàn bộ hàng chờ. Các mã HTTP đó không tự chứng minh bị phát hiện bot; chương trình chỉ dùng làm tín hiệu dừng để người dùng kiểm tra. Không chuyển endpoint hoặc tài khoản để tránh chặn.

Tài liệu kỹ thuật chính thức dùng để triển khai:

- https://playwright.dev/python/docs/network
- https://playwright.dev/python/docs/api/class-browsertype
- Kênh API chính thức của Shopee: https://open.shopee.com/ — phạm vi truy cập tùy ứng dụng/shop được cấp quyền, không mặc định bao phủ dữ liệu toàn sàn.

Chi tiết kiểm tra và giới hạn thực tế xem `VALIDATION.md`.
