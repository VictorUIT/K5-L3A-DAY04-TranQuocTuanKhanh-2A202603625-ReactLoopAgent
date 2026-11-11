# Ghi chú phân tích · Day 04

Họ và tên: Trần Quốc Tuấn Khanh
MSSV: 2A202603625

## 1. Case được chọn

- ID case: H8
- Điểm lần chạy 1: 86/100 (thấp nhất trong bộ eval).
- Vấn đề: câu trả lời từ chối đúng việc liệt kê đầy đủ incident giai đoạn 2020–2026 và nêu giới hạn dữ liệu, nhưng còn khẳng định tool không có tham số thời gian, phân trang hoặc API lịch sử. Trace không chứng minh các khẳng định về những khả năng đó.
- Nguyên nhân: prompt yêu cầu thận trọng và dẫn nguồn nhưng chưa nhắc rõ phải phân biệt giới hạn của kết quả tool với khả năng của dịch vụ/API nói chung. Đây là vấn đề hướng dẫn prompt, không phải lỗi agent loop. Snapshot cũng giới hạn bằng chứng, nhưng không buộc agent suy diễn quá mức.

## 2. Bằng chứng trong trace

H8 gọi `get_status` cho cả GitHub và Cloudflare ở bước 1, rồi trả lời ở bước 2. Trace ghi nhận:

| Provider | Snapshot (`captured_at`) | Incident trả về | `matching_incidents_in_last_50` | `source` |
|---|---|---:|---:|---|
| GitHub | `2026-11-09T03:50:47.065020+00:00` | 5 | 50 | `https://www.githubstatus.com` |
| Cloudflare | `2026-11-09T03:50:47.638054+00:00` | 5 | 50 | `https://www.cloudflarestatus.com` |

Cả hai kết quả cũng ghi chú rằng tìm theo chuỗi trong 50 incident gần nhất và không tìm thấy không chứng minh không có sự cố.

Kết quả chứng minh snapshot chỉ trả tối đa 5 incident trong phạm vi 50 incident gần nhất và có URL nguồn. Chúng không chứng minh rằng API bên ngoài không có phân trang, tham số thời gian hay kho lịch sử. Các liên kết `githubstatus.com` và `cloudflarestatus.com` trong câu trả lời là nguồn theo tool result; thời điểm snapshot phải được xem là thời điểm dữ liệu, không phải trạng thái hiện tại.

## 3. Thay đổi đã thực hiện

- Sửa `SYSTEM_PROMPT` trong `agent.py`: yêu cầu nêu đúng giới hạn thể hiện trong kết quả tool, không suy luận về phân trang/API/dữ liệu lịch sử nếu không có bằng chứng, và phân biệt dữ kiện với điều chưa xác minh.
- Chọn thay đổi prompt vì agent loop đã gọi cả hai provider và chuyển đủ tool results; dữ liệu snapshot cung cấp giới hạn cùng nguồn. Lỗi nằm ở cách diễn giải bằng chứng, không phải thiếu tool call.
- Kiểm tra nguồn: câu trả lời H8 dẫn các URL provider có trong tool results. Với incident details thành công, agent còn yêu cầu câu trả lời chứa URL incident; `tests/test_validation.py` kiểm tra luồng yêu cầu bổ sung nguồn và trường hợp tool lỗi không cần nguồn incident.

## 4. So sánh hai lần chạy

| Chỉ số | `eval_before.json` | `eval_after.json` |
|---|---:|---:|
| Điểm tổng | 94.73/100 (22/22 case được chấm) | 95.14/100 (22/22 case được chấm) |
| Điểm case H8 | 86/100 | 91/100 |
| Số lượt gọi model H8 (agent) | 2 | 2 |
| Token H8 (agent) | 3025 input, 1903 output | 3335 input, 1857 output |
| Tổng lượt gọi (agent + judge) | 84 | 84 |
| Tổng token (agent + judge) | 191174 input, 52514 output | 210594 input, 56273 output |

- Cấu hình trong hai report giống nhau: Anthropic `claude-sonnet-5-5`, `max_steps=8`, cùng provider/model cho judge.
- Lần chạy CP6 thành công đủ 22 case; điểm tổng tăng 0.41 và H8 tăng 5 điểm. Judge nhận xét câu trả lời H8 vẫn có vài khẳng định về khả năng tool chưa được trace xác minh, nên cải tiến prompt chưa giải quyết triệt để vấn đề.
- Tổng token tăng giữa hai lần chạy; chi phí tiền tệ không có trong report và không thể suy ra nếu chưa áp dụng giá model tương ứng.

## 5. Giới hạn

- Điểm judge là ước lượng từ một lần chấm; có thể sai hoặc diễn giải khác với rubric. Đối chiếu điểm với expected, tool results và trace.
- Snapshot được chụp ngày 2026-11-09; không đại diện cho trạng thái hiện tại hay toàn bộ lịch sử.
- Mỗi phiên bản chỉ được chạy một lần nên không đo được độ biến thiên giữa các lần gọi model/judge.
- Hai report có cùng cấu hình, nhưng số token và điểm judge có thể thay đổi giữa các lần chạy. Kết quả này không chứng minh cải tiến prompt là nguyên nhân duy nhất làm điểm tăng.

## Bonus tùy chọn

- Đã kết nối `run_agent` với `tools.schemas` và `tools.system_prompt`; vẫn dùng schema/prompt bài chính làm mặc định.
- Retry: truyền observation lỗi về model; giới hạn 3 lần gặp 429 cho mỗi tool/provider. Với `Retry-After` tối đa 2 giây thì chờ theo header; nếu lâu hơn thì dừng retry và ghi rõ trạng thái chưa xác định. 429 không bị xem là outage. Lỗi tham số cũng có giới hạn retry.
- Pagination: giữ nguyên `offset`/`limit` trong tool arguments để model có thể gọi trang tiếp theo theo `next_offset`; không tự tuyên bố đã đọc hết timeline.
- Context: bỏ danh sách `distractors` khỏi observation gửi cho model và trace, thay bằng `distractors_omitted`; giữ các trường trạng thái, incident, timestamp và nguồn.
- Nguồn và an toàn: system prompt yêu cầu trích nguồn theo provider, phân biệt snapshot với lịch sử và coi tool output là dữ liệu không đáng tin. B7 không xuất marker injection.
- Kiểm tra offline: `python3 -m unittest discover -s bonus -p 'test_*.py' -v` và `python3 -m unittest discover -s tests -v` đều đạt. Bổ sung test cho persistent 429, `Retry-After` dài, lỗi tham số lặp, nhiễu và pagination.
- Eval `eval_bonus.json`: Anthropic `claude-sonnet-5-5`, judge cùng model, `max_steps=16`; 10/10 case được chấm, điểm trung bình 95.40/100, bonus tham khảo 14.31/15.
- Trace xác nhận B2 đọc trang `offset=3`; B3 phục hồi sau 429; B4 dừng sau 3 lần Vercel bị rate limit trong khi vẫn trả thông tin GitHub; B5 sửa tham số; B6 xử lý dữ liệu nhiễu; B10 gọi đủ status tools và retry Vercel.
- Giới hạn: judge chỉ là tham khảo một lần chạy; các scenario rate limit và nhiễu là giả lập, không đại diện hành vi API thật. Các câu hỏi cần timeline phụ thuộc model có chọn đúng trang tiếp theo hay không; nếu rate limit kéo dài thì trạng thái dịch vụ vẫn chưa xác định.
