# ReelForge Studio: roadmap tạo video AI và đăng mạng xã hội

> **Tài liệu lịch sử:** roadmap trước v1.0; mọi trạng thái bên dưới là của ngày 2026-09-24. Trạng thái phát hành hiện
> tại: [V1_RELEASE_STATUS.md](V1_RELEASE_STATUS.md); những gì v1.0 có: [RELEASE_NOTES_V1.md](RELEASE_NOTES_V1.md); sau
> v1.0: [POST_V1_ROADMAP.md](POST_V1_ROADMAP.md).

Ngày rà soát và cập nhật trạng thái: 2026-09-24. Đây là roadmap sản phẩm và kỹ thuật; mỗi giai đoạn là một phần mềm chạy được, có thể kiểm tra độc lập. Ước lượng thời gian và lựa chọn gói dịch vụ sẽ cần dữ liệu về ngân sách, lưu lượng và quyền truy cập API. Trạng thái dưới đây phản ánh mã nguồn và kiểm thử giả lập; chưa xác nhận upload hay tạo video với tài khoản dịch vụ thật.

## Mục tiêu và giả định

Người dùng chọn một dự án, nhập hoặc sửa chủ đề, tạo clip/video theo tỷ lệ mong muốn, xem và duyệt bản cuối, rồi đăng hoặc lên lịch cho YouTube, Facebook Page và TikTok. Mỗi workspace chỉ thấy tài sản, công cụ AI, số dư và kênh của mình.

Lộ trình giả định ReelForge dùng API key của hệ thống trước; khả năng dùng key riêng theo workspace được thêm khi có nhu cầu. Bản đầu tiên tạo một clip từ chủ đề với prompt cho phép sửa, mặc định 9:16 và yêu cầu người dùng duyệt trước khi đăng. Các nền tảng bên ngoài phải cấp quyền cho ứng dụng trước khi chức năng đăng tự động có thể hoạt động.

## Hiện trạng sau đợt triển khai này

- FastAPI, PostgreSQL/Alembic, dự án có thể sửa `topic`, kho media theo workspace, workflow dạng DAG, lịch sử run, thanh toán payOS và sổ credits đã có. `app/main.py` tạo job video ngoài request; `app/jobs.py` quản lý lease, retry và khóa kết quả theo lease token. Worker riêng là `app/video_worker.py`.
- Graph mẫu hiện là `idea → video → review`. Video của fal, Runware, Replicate, Runway Dev hoặc Dola thử nghiệm được tạo từ topic/prompt, lưu thành MP4 riêng tư gắn với project/run/step; credits được giữ trước khi gửi, usage ghi một lần khi lưu MP4 thành công. Yêu cầu bị từ chối rõ trước khi provider nhận sẽ được hoàn credits; kết quả không chắc chắn cần đối soát và giữ credits, không cho retry cùng run. Giao diện run đọc tiến độ định kỳ, phát clip và cho chủ workspace duyệt. `idea` và `assets` chạy nội bộ; script, nhiều cảnh, voice, subtitle và render cuối chưa có executor.
- `app/publications.py` và `app/youtube_worker.py` đã có bản ghi xuất bản riêng theo workspace/run/channel, hàng đợi upload, phiên resumable được mã hóa, kết quả/ID video YouTube và trạng thái cần kiểm tra khi upload không rõ kết quả. Google OAuth kết nối từng workspace; UI chỉ gửi video đã duyệt ở chế độ **private**. Chưa kiểm tra với Google channel thật hoặc xác minh/audit ứng dụng.
- `app/publishers/facebook.py` và `app/publishers/tiktok.py` là adapter HTTP với kiểm thử giả lập, chưa có kết nối tài khoản, job hay giao diện đăng cho hai kênh. Dola gateway đã nối với API/worker dưới cờ thử nghiệm; cần tự vận hành gateway và xác minh quyền truy cập media. Lịch đăng và analytics vẫn là màn hình dự kiến.
- Phụ thuộc kiểm thử đã được khai báo trong `requirements-dev.txt`, CI chạy kiểm thử Python và kiểm tra frontend. Bước tạo admin đầu tiên được khóa giao dịch; đăng nhập có giới hạn thử; media upload kiểm tra chữ ký tệp và quota. Đưa lên Internet vẫn cần cấu hình HTTPS, secure cookie, secret, backup và quyền API nền tảng.

## Kiến trúc đích

1. **Run bền vững:** API ghi run và job vào PostgreSQL rồi trả ngay. Worker riêng nhận job bằng lease, cập nhật trạng thái, tiếp tục sau khi khởi động lại và không giữ giao dịch DB trong suốt thời gian gọi nhà cung cấp. Lưu snapshot của graph, chủ đề/prompt, model, cấu hình và giá dự kiến tại thời điểm chạy. Chỉ truyền đầu ra của node phụ thuộc cho node tiếp theo.
2. **Provider adapter:** một hợp đồng cho `submit`, `status`, `fetch_result`, `capabilities` và lỗi chuẩn; mỗi model có schema/capability riêng về text/image input, thời lượng, tỷ lệ, âm thanh, vùng phục vụ và chi phí. ID job bên ngoài, số lần thử và mã yêu cầu được lưu trong DB. Với provider không có idempotency key, lần gửi có kết quả không rõ không được tự gửi lại ngay.
3. **Artifact riêng tư:** worker tải video kết quả về kho media của đúng workspace; asset lưu quan hệ với project, run, step, provider/model và metadata video. URL kết quả tạm hoặc công khai của provider không trở thành URL phát cuối cho người dùng. Ảnh đầu vào riêng tư cần luồng upload/signed URL phù hợp với từng provider.
4. **Chi phí và quyền:** khóa API/tokens ở backend, mã hóa token OAuth lưu DB bằng khóa giữ ngoài DB. Ước lượng và giữ credits trước khi gửi tác vụ tốn tiền; quyết toán một lần khi xác nhận usage, hoàn giữ khi chắc chắn provider từ chối trước khi chạy. Kết quả không chắc chắn giữ credits chờ đối soát. Giới hạn đồng thời, quota và trạng thái lỗi rõ theo workspace/provider.
5. **Đăng tải tách theo nền tảng:** bản video đã duyệt có một publication record cho từng kênh, lưu ID bài đăng bên ngoài, trạng thái, lịch UTC và kết quả từng lần thử. Chạy lại workflow hoặc nhận callback trùng không được đăng trùng.

## Thứ tự nâng cấp

| Giai đoạn | Phạm vi và đầu ra | Điều kiện hoàn thành |
| --- | --- | --- |
| 0. Ổn định nền | Khai báo phụ thuộc kiểm thử, đưa cả hai test về trạng thái chạy được; cập nhật README theo code; thiết lập CI cho Python và frontend. Trước khi mở public, bảo vệ bước tạo admin đầu tiên, giới hạn thử đăng nhập, dùng HTTPS/secure cookie, kiểm tra nội dung file tải lên và quota lưu trữ. Bắt đầu đăng ký/xác minh quyền API SNS. | Test hiện có chạy qua môi trường sạch; typecheck/build chạy trong CI; không có hai admin đầu tiên từ hai request đồng thời; đăng nhập và upload bị giới hạn hợp lý. |
| 1. Job và workflow bất đồng bộ | Migration cho job/attempt/lease, step đang chờ/đang chạy/thất bại/hoàn thành; worker riêng; endpoint đọc tiến độ; snapshot input và cơ chế tiếp tục sau restart. Giữ hành vi cũ cho graph không có node chạy ngoài. | Tác vụ nhiều phút không chặn request; restart worker vẫn tiếp tục hoặc kết thúc rõ; workspace khác không xem được job; gửi/nhận sự kiện trùng không tạo tác dụng phụ trùng. |
| 2. Từ chủ đề đến một clip | Làm trang dự án có thể mở/sửa chủ đề; prompt tạo từ topic và cho sửa; thêm node `video` vào template phù hợp; adapter đầu tiên qua fal.ai hoặc Runware để có nhiều model; lưu kết quả như asset và hiển thị trong project/run; giới hạn chi phí trước khi gửi. | Người dùng chạy một dự án 9:16, theo dõi tiến độ, xem/tải MP4 của mình; lỗi, hết credits và model không hỗ trợ được giải thích; charge chỉ xuất hiện một lần. |
| 3. Duyệt và YouTube | Lưu trạng thái duyệt thực, kết nối Google OAuth, metadata video, job upload YouTube và ID video; mỗi lần đăng ghi lại kết quả. Thiết kế lại `retry` để không chạy lại bước đã đăng thành công. | Người dùng duyệt một clip rồi đăng một lần lên kênh đã cấp quyền; mất kết nối/restart không đăng trùng; lỗi quyền hoặc upload hiện trong UI. |
| 4. Mở rộng provider | Registry model có cấu hình và capability validation; thêm ít nhất một API trực tiếp độc lập với aggregator, sau đó mở Google, Runway, Luma, Kling, MiniMax, BytePlus, Wan, xAI... theo quyền API thực tế. Cho chọn model theo tác vụ/workspace và hiển thị chi phí, tỷ lệ, thời lượng được hỗ trợ. | Cùng một flow chạy với ít nhất ba lựa chọn thuộc hai nhà cung cấp độc lập; thay model không sửa workflow engine; input không tương thích bị chặn trước khi tính tiền. |
| 5. Video nhiều cảnh | Thêm script/storyboard/scene có cấu trúc, tạo từng clip/ảnh/voice, phụ đề, timeline và ghép bằng FFmpeg; kiểm tra codec, độ dài và tỷ lệ; preview bản cuối trước duyệt. | Một topic tạo được video nhiều cảnh có âm thanh/phụ đề, bản MP4 cuối gắn đúng project/run và có thể sửa một cảnh rồi render lại phần cần thiết. |
| 6. Facebook, TikTok và lịch đăng | Thêm kết nối Facebook Page/Reels và TikTok Content Posting theo quyền được cấp; form caption/thumbnail/quyền riêng tư phù hợp từng kênh; lịch UTC, hàng đợi và kết quả riêng từng kênh. Nếu TikTok Direct Post chưa được duyệt, cung cấp xuất file hoặc inbox draft với nhãn rõ là cần thao tác thủ công. | Một video được duyệt có thể đăng hoặc đặt lịch từng kênh, thất bại của một kênh không làm mất kết quả kênh khác, và trạng thái thực tế được đối chiếu trước retry. |
| 7. Vận hành và phân tích | Giám sát job, chi phí, lỗi provider, lưu trữ, dọn file tạm, backup DB+media, quota, cảnh báo; analytics chỉ đồng bộ khi kênh đã cấp đủ quyền. | Có thể biết job nào tốn tiền/thất bại, khôi phục sau restart, và xác minh quyền sở hữu mọi file/job/publication theo workspace. |

Giai đoạn 4 có thể tiến hành song song với 3 sau khi giai đoạn 2 hoàn tất. Hồ sơ xin quyền YouTube/TikTok/Facebook nên bắt đầu ở giai đoạn 0 vì kết quả phụ thuộc nền tảng bên ngoài. Ưu tiên giữ một đường đi trọn vẹn `topic → clip → duyệt → đăng` trước khi mở rộng video nhiều cảnh.

### Trạng thái từng giai đoạn

| Giai đoạn | Trạng thái mã nguồn ngày 2026-09-24 | Còn thiếu để đạt điều kiện hoàn thành |
| --- | --- | --- |
| 0 | Đã có phụ thuộc test, CI, khóa setup, giới hạn đăng nhập, kiểm tra media và quota. | Cấu hình HTTPS/secure cookie khi triển khai và đăng ký/xác minh quyền ứng dụng SNS; kiểm thử vận hành production. |
| 1 | Đã có lease/retry/job bền vững cho video và publication, snapshot đầu vào, worker riêng, thời hạn job video, trạng thái cần đối soát khi kết quả gửi không chắc chắn và API đọc run. | Mở rộng executor cho nhiều loại node, quan sát/điều phối nhiều worker ở môi trường thật. |
| 2 | Đã có sửa topic, prompt, model video được allowlist, preview MP4, credits và duyệt. | Chạy thử với API key thật, đối chiếu chi phí/quota thực tế từng provider. |
| 3 | Đã có OAuth YouTube, upload private resumable, record/job chống trùng, ràng buộc job với lần kết nối kênh, retry hữu hạn và thao tác retry thủ công an toàn trước upload. | Xác minh Google OAuth/YouTube trên channel thật và các hạn chế của ứng dụng; chưa có tự chuyển video sang public. |
| 4 | fal, Runware, Replicate và API trực tiếp Runway Dev đã nối với video worker; Dola có thể bật thử nghiệm. | Kiểm thử các nhà cung cấp bằng tài khoản thật, thêm capability/báo giá theo từng model và mở rộng provider; kiểm thử Dola gateway trước khi vận hành. |
| 5 | Chưa triển khai. | Storyboard, clip nhiều cảnh, voice/phụ đề, FFmpeg và chỉnh sửa từng cảnh. |
| 6 | Adapter Facebook/TikTok đã có, chưa nối vào ứng dụng. | OAuth/quyền nền tảng, publication jobs, form metadata và lịch UTC từng kênh. |
| 7 | Đã có sổ credits, asset lineage, quota lưu trữ, trạng thái job và lệnh dọn tệp `.part` cũ mặc định dry-run. | Theo dõi vận hành, cảnh báo, khôi phục backup và analytics được cấp quyền. |

## Vị trí code và phần tiếp theo

- Backend đã có `app/jobs.py`, `app/video_worker.py`, `app/providers/`, `app/publications.py`, `app/youtube_worker.py`, `app/publishers/` và Alembic `0007`–`0010`. `app/main.py` vẫn chứa phần lớn API; tách thêm route/dịch vụ khi mở rộng nhiều cảnh và kênh.
- Frontend đã có sửa chi tiết project, theo dõi run, duyệt video, kết nối YouTube và upload private. `FlowEditor.tsx` chưa cấu hình input chi tiết cho từng node; lịch đăng, Facebook/TikTok và analytics vẫn cần UI và backend tương ứng.
- Kiểm thử dùng SQLite cách ly và HTTP giả cho provider/YouTube, với tùy chọn PostgreSQL qua `REELFORGE_TEST_DATABASE_URL` cho hành vi khóa hàng. Cần tiếp tục kiểm thử tích hợp có credentials riêng trên tài khoản thử nghiệm trước khi cho người dùng thật đăng video.

## Quyết định và rủi ro cần giữ rõ

- Mô hình kinh doanh giai đoạn đầu: API key do ReelForge trả tiền hay workspace tự cấp key. Roadmap này dùng key hệ thống; lựa chọn còn lại cần giao diện và quản lý bí mật riêng.
- Tỷ lệ/độ dài và chi phí khác nhau theo từng model. Catalog phải khai báo capability, không chỉ là ô nhập tên model tự do.
- TikTok Direct Post yêu cầu ứng dụng và người dùng được cấp quyền; quy trình kiểm duyệt áp đặt yêu cầu về preview, metadata và quyền riêng tư. YouTube upload cũng dùng OAuth người dùng và có hạn chế cho dự án API chưa được xác minh. Điều này ảnh hưởng lịch phát hành dù code đã hoàn tất.
- Dola đã có trong lựa chọn người dùng khi bật `DOLA_EXPERIMENTAL_ENABLED=1`, và video worker lưu MP4 riêng tư về ReelForge. Hạn mức tài khoản và cách vận hành Chromium/proxy của `dola-render-gateway` vẫn cần kiểm thử năng lực riêng; không lấy URL MP4 công khai của gateway làm nơi lưu file chính.
- OpenAI Docs ghi Videos API/Sora 2 có lịch ngừng 2026-09-24; không lên kế hoạch tích hợp mới với API đó.

Tài liệu nền tảng: [YouTube upload](https://developers.google.com/youtube/v3/docs/videos/insert), [Meta Facebook API collection](https://www.postman.com/meta/facebook/documentation/r56bjfd/facebook-api), [TikTok Content Posting](https://developers.tiktok.com/docs/en/content-posting-api-get-started), [TikTok content sharing guidelines](https://developers.tiktok.com/docs/en/content-sharing-guidelines), [OpenAI API deprecations](https://developers.openai.com/api/docs/deprecations).
