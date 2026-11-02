import os
from openai import OpenAI

api_key = os.environ.get("XKIRO_API_KEY")
client = OpenAI(
    api_key=api_key,
    base_url="https://api.xkiro.com/v1",
)

prompt = """Bạn là biên tập viên phóng sự tài liệu chuyên nghiệp.
Hãy viết lại đoạn mở đầu sau đây thành lời thoại MC truyền cảm, mạch lạc, câu cú hoàn chỉnh (không lặp từ, đúng thuật ngữ tài chính):
'In 2014, an economics professor walked into a small bank in Germany and convinced them to do something nobody had ever done in the history of banking. They let him sit right there in the room while they approved a loan. The books were open and the system was running. He watched the bankers, step-by-step, create money out of absolutely nothing and became the first person in history to prove it on paper. The man was Richard Werner.'
"""

resp = client.chat.completions.create(
    model="qwen/qwen3.5-flash:free",
    messages=[{"role": "user", "content": prompt}],
    timeout=20,
)
print("TEST REWRITE RESULT:")
print(resp.choices[0].message.content)
