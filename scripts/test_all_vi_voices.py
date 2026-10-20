import urllib.request
import json
from pathlib import Path
from services.settings_service import get_llm_api_key

api_key = get_llm_api_key()
url = "https://api.xkiro.com/v1/audio/speech"

voices = [
    "confident-male-vietnamese",
    "sweet-female-vietnamese",
    "gentle-female-vietnamese",
    "standard-female-vietnamese"
]

out_dir = Path("projects/0f1639d2/exports/xkiro_voices")
out_dir.mkdir(parents=True, exist_ok=True)

for voice in voices:
    payload = {
        "model": "xkiro-voice",
        "voice": voice,
        "input": "Chào mừng các bạn đến với Đảo Rắn Ilha da Queimada Grande, hòn đảo nguy hiểm bậc nhất hành tinh."
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0"
        }
    )
    try:
        with urllib.request.urlopen(req) as resp:
            audio = resp.read()
            out_file = out_dir / f"{voice}.mp3"
            out_file.write_bytes(audio)
            print(f"{voice}: OK -> {out_file} ({len(audio)} bytes)")
    except urllib.error.HTTPError as e:
        print(f"{voice}: HTTPError {e.code}: {e.read().decode('utf-8', errors='replace')}")
    except Exception as e:
        print(f"{voice}: Error: {e}")
