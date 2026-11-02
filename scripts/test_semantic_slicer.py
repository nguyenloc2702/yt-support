import json
import os
from openai import OpenAI

api_key = os.environ.get("XKIRO_API_KEY")
client = OpenAI(
    api_key=api_key,
    base_url="https://api.xkiro.com/v1",
)

sample_text = """
In 2014, an economics professor walked into a small bank in Germany and convinced them to do something nobody had ever done in the history of banking. They let him sit right there in the room while they approved a loan. The books were open and the system was running. He watched the bankers, step-by-step, create money out of absolutely nothing and became the first person in history to prove it on paper. The man was Richard Werner. Hes a professor of banking and finance from Germany. Shortly after his study came out, the Bank of England admitted the exact same thing in their own official papers.
"""

prompt = f"""Bạn là Đạo diễn kiêm Biên kịch Phim tài liệu chuyên nghiệp.
Nhiệm vụ: Phân tích đoạn nội dung dưới đây và băm thành các CẢNH (Scenes) theo TÌNH HUỐNG CỤ THỂ, KHÔNG băm máy móc 5s.

Nội dung gốc:
{sample_text}

Yêu cầu cho mỗi cảnh:
1. situation: Mô tả tình huống cụ thể (hành động, bối cảnh diễn ra).
2. voiceover_vi: Lời thoại tiếng Việt thuyết minh truyền cảm, câu cú hoàn chỉnh, tuyệt đối không ngắt cụt câu, không lặp từ.
3. estimated_duration: Thời lượng tính bằng giây phù hợp tự nhiên với độ dài lời thoại (từ 4 đến 8 giây).
4. visual_prompt: Prompt tiếng Anh chi tiết để sinh video AI (Veo/Cinematic).

Trả về định dạng JSON array:
[
  {{
    "situation": "...",
    "voiceover_vi": "...",
    "estimated_duration": 6.0,
    "visual_prompt": "..."
  }}
]
Chỉ trả về JSON thuần túy.
"""

try:
    resp = client.chat.completions.create(
        model="qwen/qwen3.5-flash:free",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        timeout=30,
    )
    content = resp.choices[0].message.content.strip()
    if content.startswith("```json"):
        content = content[7:-3].strip()
    elif content.startswith("```"):
        content = content[3:-3].strip()
    parsed = json.loads(content)
    print("SUCCESSFUL SEMANTIC SCENE BREAKDOWN:")
    print(json.dumps(parsed, indent=2, ensure_ascii=False))
except Exception as e:
    print("FAILED:", type(e), e)
