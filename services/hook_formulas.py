"""Vietnamese hook formula catalog + narration laws.

Adapted from youtube-agent-skill's hooks.json (21 formulas with shape and
failure mode) and anything2explainer's narration-guidance.md. Consumed by the
script factory prompts so rewrite models pick a formula instead of vibes.
"""

HOOK_RULES = (
    "Xác nhận đúng lời hứa của tiêu đề trong câu đầu tiên — hook bỏ quên tiêu đề là lý do người xem rời đi ở giây thứ 8.",
    "Một hook chỉ mang MỘT lời hứa; hook mang hai lời hứa thì không giữ được lời nào.",
    "Cụ thể thắng chung chung: '400 lên 200.000 sau 7 tháng' đè bẹp 'tăng trưởng khủng'.",
    "Đã hứa cho xem bằng chứng thì phải đưa vào trong 20 giây.",
    "Không mở đầu bằng giới thiệu bản thân hay kênh — đó là lý do phổ biến nhất khiến người xem rời ở giây 30.",
)

HOOK_FORMULAS = (
    {"id": "the-statistic", "name": "Con số",
     "shape": "Một con số người xem chưa biết, nói thẳng, rồi tới hệ quả.",
     "fails_when": "Chết nếu số mơ hồ hoặc không kiểm chứng được — phải nói rõ nguồn."},
    {"id": "someone-elses-result", "name": "Kết quả của người khác",
     "shape": "Kết quả gắn với một người/tổ chức có tên, rồi mới tới cơ chế.",
     "fails_when": "Không được bịa số. Không nêu được tên thì đổi công thức."},
    {"id": "the-mistake", "name": "Lỗi sai",
     "shape": "Nêu đúng lỗi người xem đang mắc ngay lúc này.",
     "fails_when": "Chỉ ăn nếu lỗi phổ biến và người xem kiểm tra được trong một giây."},
    {"id": "contrarian-flip", "name": "Lật ngược",
     "shape": "Nêu lời khuyên ai cũng tin, rồi bác bỏ nó.",
     "fails_when": "Cần bằng chứng trong 15 giây sau, nếu không thành phản biện hình thức."},
    {"id": "the-reveal", "name": "Hé lộ",
     "shape": "Hứa sẽ cho xem một thứ cụ thể, rồi cho xem ngay.",
     "fails_when": "Phải hé lộ sớm; để đến phút 8 là một vách rơi ở phút 1."},
    {"id": "the-superlative", "name": "Nhánh nhất",
     "shape": "Cái tốt nhất / tệ nhất / nhanh nhất của một nhóm có tên.",
     "fails_when": "Nhất là rẻ miệng — phải nêu tên thứ nó đã thắng."},
    {"id": "the-clock", "name": "Đồng hồ",
     "shape": "Gói kết quả trong một khung thời gian.",
     "fails_when": "Đồng hồ phải thật; thất hứa một lần thì hook video sau không ai tin."},
    {"id": "i-tried-it", "name": "Tôi đã thử",
     "shape": "Thí nghiệm ngôi thứ nhất, nêu rõ cái giá đã trả.",
     "fails_when": "Phải có giá thật (thời gian, tiền, rủi ro) thì mới có stakes."},
    {"id": "the-question", "name": "Câu hỏi",
     "shape": "Hỏi đúng câu người xem gõ vào ô tìm kiếm.",
     "fails_when": "Phải là câu hỏi của người xem, không phải câu hỏi của mình."},
    {"id": "the-before-after", "name": "Trước – sau",
     "shape": "Hai trạng thái, một cú cắt nằm giữa.",
     "fails_when": "Chỉ ăn khi có hình ảnh đối lập; video chỉ có lời thoại thì bỏ."},
    {"id": "the-teardown", "name": "Mổ xẻ",
     "shape": "Lấy một thứ có thật ra mổ công khai.",
     "fails_when": "Phải nêu đã lấy gì và bao nhiêu; mổ một ví dụ chỉ là giai thoại."},
    {"id": "the-stack", "name": "Ghép đôi",
     "shape": "Hai thứ có tên ghép lại ra một kết quả.",
     "fails_when": "Cả hai phải gọi được tên và kết quả phải cần cả hai."},
    {"id": "the-warning", "name": "Cảnh báo",
     "shape": "Một cái giá người xem sắp phải trả.",
     "fails_when": "Giá nhỏ thì hook thành nói dối — chỉ dành cho giá thật."},
    {"id": "the-list", "name": "Danh sách",
     "shape": "Một bộ đếm được, số lượng nằm ngay ở câu đầu.",
     "fails_when": "Số phải chính xác và mỗi mục phải khác loại."},
    {"id": "the-receipt", "name": "Bằng chứng",
     "shape": "Cho xem bằng chứng trước, giải thích sau.",
     "fails_when": "Cần thứ đó thật sự xuất hiện; mô tả suông không phải bằng chứng."},
    {"id": "the-insider", "name": "Người trong cuộc",
     "shape": "Thông tin từ bên trong một hệ thống.",
     "fails_when": "Không được nhận quyền tiếp cận mà mình không có."},
    {"id": "the-impossible", "name": "Điều vô lý",
     "shape": "Nêu điều nghe như không thể thật, rồi chứng minh nó thật.",
     "fails_when": "Bằng chứng phải bắt đầu trong 20 giây, nếu không thành clickbait."},
    {"id": "the-comparison", "name": "Đối đầu",
     "shape": "Hai lựa chọn có tên, một bên thắng.",
     "fails_when": "Phải gọi tên bên thua; so sánh không có kẻ thua là quảng cáo."},
    {"id": "the-origin", "name": "Nguồn gốc",
     "shape": "Kết quả này thật sự đến từ đâu.",
     "fails_when": "Mỗi kênh dùng được một lần — đây là reveal về chính mình."},
    {"id": "the-deadline", "name": "Hạn chót",
     "shape": "Có gì đó sẽ thay đổi vào một ngày.",
     "fails_when": "Chỉ dùng với ngày thật, kiểm chứng được — mất uy tín nhanh nhất."},
    {"id": "the-direct-address", "name": "Gọi thẳng",
     "shape": "Gọi đúng người xem trong sáu từ đầu tiên.",
     "fails_when": "Hẹp thắng rộng; 'nếu bạn làm video' chẳng gọi được ai."},
)


def hook_formula_catalog() -> str:
    return "\n".join(
        f"- {f['id']} ({f['name']}): {f['shape']} HỎNG KHI: {f['fails_when']}"
        for f in HOOK_FORMULAS
    )


# Condensed from anything2explainer's narration-guidance.md; binds every
# sentence the rewrite models write, not just the hook.
NARRATION_LAWS = (
    "Mỗi câu phải khiến người nghe biết / thấy / cảm thấy thêm một điều; câu nào không thì bỏ.",
    "Mở bằng tình huống cụ thể, không mở bằng định nghĩa; định nghĩa phải đến như câu trả lời cho câu hỏi người nghe đã có sẵn.",
    "Mỗi ý theo thứ tự: thiếu nó thì điều gì đổ vỡ → cơ chế hoạt động ra sao → nó đem lại gì. Không giải thích thứ chưa ai cần giải thích.",
    "Số liệu không đọc lên suông: đổi thành thang cảm giác ('bằng 3 năm lương của một người lính') hoặc bỏ đi.",
    "Nhịp: câu dài giải thích rồi chốt bằng câu ngắn dứt khoát; hỏi rồi dừng nhịp rồi mới hé lộ; không để 3 câu liên tiếp cùng độ dài cùng cấu trúc.",
    "Giọng người kể vừa phát hiện ra điều thú vị, đang nói với một người bạn thông minh: chữ bình dân, có chính kiến, dám thừa nhận chỗ chưa chắc chắn.",
    "Thuật ngữ: giải thích bằng chữ thường trước rồi mới gắn tên; tên gắn ra mà không dùng lại thì không cần gắn.",
    "Stakes phải xuất hiện trong phút đầu; kết quay về cảnh mở đầu với hiểu biết mới (callback), không liệt kê tổng kết.",
    "Mỗi cảnh có ít nhất một câu dài có mệnh đề phụ ('khi mà', 'vì', 'nên', 'nhưng rồi'); nối ý bằng từ nối tự nhiên, cấm 3 câu liên tiếp mở đầu bằng chủ ngữ + động từ.",
    "Mỗi câu phải đọc lên trôi như lời nói: đủ chủ ngữ vị ngữ, không băm một ý thành nhiều câu cụt; câu ngắn chỉ dùng để chốt sau câu dài.",
)


def narration_laws_block() -> str:
    return "LUẬT NARRATION (ràng buộc mọi câu thoại):\n" + "\n".join(
        f"- {law}" for law in NARRATION_LAWS
    )
