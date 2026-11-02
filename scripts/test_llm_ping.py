import os
from pipeline.llm_client import get_llm_client
try:
    client = get_llm_client()
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "Hello"}],
        timeout=10,
    )
    print("LLM RESPONSE:", resp.choices[0].message.content)
except Exception as e:
    print("LLM ERROR:", type(e), e)
