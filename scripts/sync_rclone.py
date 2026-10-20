import os
import sys
import subprocess
from pathlib import Path

# Load token from argument, environment, or default local file
token_json = os.environ.get("GDRIVE_TOKEN_JSON")
if not token_json and len(sys.argv) > 1:
    token_json = sys.argv[1]
elif not token_json and Path("token.json").exists():
    token_json = Path("token.json").read_text(encoding="utf-8")
elif not token_json:
    token_path = Path.home() / ".config" / "rclone" / "rclone.conf"
    if token_path.exists():
        conf_content = token_path.read_text(encoding="utf-8")
        token_json = conf_content
    else:
        print("Please provide token JSON via GDRIVE_TOKEN_JSON env or argument or token.json")
        sys.exit(1)

if not token_json.startswith("[gdrive]"):
    conf = f"""[gdrive]
type = drive
scope = drive
token = {token_json}
"""
else:
    conf = token_json


key_path = str(Path.home() / ".ssh" / "support_video_deploy")
cmd = [
    "ssh",
    "-o", "BatchMode=yes",
    "-i", key_path,
    "root@talk.gacdero.vn",
    "cat > /opt/support-video-edit/shared/rclone/rclone.conf && chmod 600 /opt/support-video-edit/shared/rclone/rclone.conf && docker exec support-video-worker rclone lsd gdrive:"
]

proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
stdout, stderr = proc.communicate(input=conf)

print("STDOUT:")
print(stdout)
if stderr:
    print("STDERR:")
    print(stderr)
