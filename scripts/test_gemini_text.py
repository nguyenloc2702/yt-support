import asyncio
import json
from pathlib import Path
from gemini_webapi import GeminiClient

async def main():
    session_file = Path("sessions/acc_01.json")
    if not session_file.exists():
        print("acc_01.json not found")
        return
    data = json.loads(session_file.read_text(encoding="utf-8"))
    psid = data.get("__Secure-1PSID")
    psidts = data.get("__Secure-1PSIDTS")

    client = GeminiClient(secure_1psid=psid, secure_1psidts=psidts)
    await client.init()
    res = await client.generate_content("Dịch sang tiếng Việt tự nhiên câu: The bank creates money out of thin air when giving a loan.")
    print("Gemini Web text response:", res.text)

if __name__ == "__main__":
    asyncio.run(main())
