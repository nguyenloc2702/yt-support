"""Trigger a Mode-2 AI Genesis run over the same gate /remake uses.

The bot creates the row with create_job_if_no_active() before handing it to
run_ai_genesis(); calling run_ai_genesis() bare would create the row itself and
skip that gate, so a run started here could duplicate a job already analysing
the same video.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, "/app")

from pipeline.automated_factory import AutomatedFactory
from services.job_queue import JobQueueManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

url = sys.argv[1] if len(sys.argv) > 1 else "https://youtu.be/yon5fqz6-v8?si=Tadb04PKR0febjjd"

jq = JobQueueManager()
job_id = jq.create_job_if_no_active("remake", url, target_duration=180.0)
if not job_id:
    print(f"⛔ {url} đang có job non-terminal — /remake sẽ từ chối. Không tạo job mới.", flush=True)
    sys.exit(2)

print(f"🚀 Triggering AI Genesis Mode 2 for: {url} (job {job_id})", flush=True)
factory = AutomatedFactory()
factory.run_ai_genesis(youtube_url=url, job_id=job_id)

print(f"✅ Job reached Gate 1 WAITING_APPROVAL! Job ID: {job_id}", flush=True)
