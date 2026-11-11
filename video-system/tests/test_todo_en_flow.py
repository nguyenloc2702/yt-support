"""End-to-end tests for the EN-Only TODO & External Sequential Video Assembly flow."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.fixture()
def fast_offline_tts(monkeypatch):
    """Use fast deterministic offline WAV synthesis so tests do not depend on internet Edge-TTS."""
    monkeypatch.setenv("VIDEO_OFFLINE_TTS", "1")


def _make_test_mp4(path: Path, duration_sec: float, color: str = "steelblue") -> Path:
    """Create a real H.264 MP4 clip of exact duration using FFmpeg lavfi."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which("ffmpeg") or r"C:\ffmpeg\ffmpeg-8.1-essentials_build\bin\ffmpeg.exe"
    cmd = [
        ffmpeg, "-y",
        "-f", "lavfi",
        "-i", f"color=c={color}:s=640x360:r=24:d={duration_sec:.3f}",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "ultrafast",
        "-movflags", "+faststart",
        str(path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    assert res.returncode == 0, f"ffmpeg test mp4 failed: {res.stderr}"
    return path


def test_create_todo_from_english_script(client, ingest, read_key, fast_offline_tts):
    """Phase 1: Create EN TODO from English script text -> produces standardized EN audio + script JSON."""
    script_text = (
        "Global supply chains depend on precision semiconductor manufacturing. "
        "Every single wafer requires hundreds of lithography and etching steps. "
        "When one bottleneck appears, entire industries feel the shockwave."
    )
    r = client.post(
        "/api/v1/todos/from-script",
        headers=ingest,
        json={
            "title": "How Chip Supply Chains Work",
            "script_text": script_text,
            "bgm_enabled": False,
        },
    )
    assert r.status_code == 201, r.text
    pkg = r.json()
    item_id = pkg["item_id"]
    assert pkg["status"] == "todo"
    assert pkg["language"] == "en"
    assert pkg["audio_duration_sec"] > 0
    assert pkg["total_shots"] >= 2

    # Check exact shot durations sum up to audio_duration_sec
    shots = pkg["shots"]
    assert shots[0]["audio_start_sec"] == 0.0
    assert abs(shots[-1]["audio_end_sec"] - pkg["audio_duration_sec"]) <= 0.05
    for s in shots:
        assert s["duration_sec"] == round(s["audio_end_sec"] - s["audio_start_sec"], 3)
        assert s["narration"]
        assert s["visual_prompt"]

    # External system pulls GET /api/v1/todos/{item_id}
    r_todo = client.get(f"/api/v1/todos/{item_id}", headers=read_key)
    assert r_todo.status_code == 200
    todo_data = r_todo.json()
    assert todo_data["status"] == "todo"
    assert todo_data["video_progress"]["uploaded_count"] == 0
    assert todo_data["video_progress"]["ready_to_assemble"] is False

    # Download Audio EN, Script EN JSON, and Subtitles (.srt / .ass)
    r_aud = client.get(pkg["audio_url"], headers=read_key)
    assert r_aud.status_code == 200
    assert r_aud.headers["content-type"].startswith("audio/")
    assert len(r_aud.content) > 1000

    r_scr = client.get(pkg["script_url"], headers=read_key)
    assert r_scr.status_code == 200
    scr_json = r_scr.json()
    assert scr_json["language"] == "en"
    assert scr_json["audio_duration_sec"] == pkg["audio_duration_sec"]
    assert len(scr_json["shots"]) == len(shots)

    r_srt = client.get(pkg["subtitles_srt_url"], headers=read_key)
    assert r_srt.status_code == 200
    assert "-->" in r_srt.text

    r_ass = client.get(pkg["subtitles_ass_url"], headers=read_key)
    assert r_ass.status_code == 200
    assert "[Events]" in r_ass.text


def test_create_todo_from_youtube_segments(client, ingest, fast_offline_tts):
    """Phase 1 (YouTube): Rolling stutter is cleaned and converted into an EN TODO package."""
    r = client.post(
        "/api/v1/todos/from-youtube",
        headers=ingest,
        json={
            "title": "YouTube EN Remake Demo",
            "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "bgm_enabled": False,
            "transcript_segments": [
                {"start": 0.0, "end": 2.0, "text": "Why do central banks"},
                {"start": 1.5, "end": 4.5, "text": "Why do central banks raise interest rates quickly?"},
                {"start": 4.5, "end": 9.0, "text": "Because inflation erodes purchasing power across the economy."},
            ],
        },
    )
    assert r.status_code == 201, r.text
    pkg = r.json()
    assert pkg["status"] == "todo"
    assert pkg["language"] == "en"
    assert pkg["audio_duration_sec"] > 0
    combined_narration = " ".join(s["narration"] for s in pkg["shots"])
    # Rolling stutter "Why do central banks" should not be duplicated twice
    assert combined_narration.count("Why do central banks") == 1


def test_sequential_videos_shortfall_warning_and_confirm_assemble(
    client, ingest, read_key, fast_offline_tts, tmp_path
):
    """Phase 2 & 3: External system uploads merged videos sequentially (1, 2...),
    warns when total video duration < EN audio duration, and assembles final MP4 when complete."""
    r = client.post(
        "/api/v1/todos/from-script",
        headers=ingest,
        json={
            "title": "Sequential Video Assembly Test",
            "shots": [
                {"index": 1, "narration": "First sentence of the English narration.", "shot_type": "scene"},
                {"index": 2, "narration": "Second sentence continues the explanation.", "shot_type": "scene"},
                {"index": 3, "narration": "Third sentence concludes the entire video.", "shot_type": "quote"},
            ],
            "bgm_enabled": False,
        },
    )
    assert r.status_code == 201, r.text
    pkg = r.json()
    item_id = pkg["item_id"]
    audio_dur = pkg["audio_duration_sec"]
    assert audio_dur >= 4.0

    # 1. External system uploads Video #1 covering shots [1, 2], but shorter than full audio_dur
    clip1_dur = round(audio_dur * 0.45, 2)
    clip1_path = _make_test_mp4(tmp_path / "clip1.mp4", clip1_dur, color="navy")
    with open(clip1_path, "rb") as f:
        r_up1 = client.post(
            f"/api/v1/todos/{item_id}/videos",
            headers=ingest,
            files={"file": ("clip1.mp4", f, "video/mp4")},
            data={"seq_num": "1", "covered_shot_indexes": "1,2", "note": "Merged shots 1 and 2"},
        )
    assert r_up1.status_code == 201, r_up1.text
    up1 = r_up1.json()
    vp1 = up1["video_progress"]
    assert up1["video"]["seq_num"] == 1
    assert up1["video"]["covered_shot_indexes"] == [1, 2]
    assert vp1["uploaded_count"] == 1
    assert vp1["is_shortfall"] is True
    assert vp1["shortfall_sec"] > 0.5
    assert "CẢNH BÁO THIẾU THỜI LƯỢNG VIDEO" in vp1["shortfall_warning"]

    # 2. Attempting confirm-assemble without allow_shortfall must return 409 with shortfall warning
    r_confirm_blocked = client.post(
        f"/api/v1/todos/{item_id}/confirm-assemble",
        headers=ingest,
        json={"burn_subtitles": False, "allow_shortfall": False},
    )
    assert r_confirm_blocked.status_code == 409, r_confirm_blocked.text
    err_body = r_confirm_blocked.json()
    assert err_body["error"] == "video_duration_shortfall"
    assert "CẢNH BÁO THIẾU THỜI LƯỢNG VIDEO" in err_body["shortfall_warning"]

    # 3. External system uploads Video #2 (seq_num auto-incremented to 2) covering shot [3]
    clip2_dur = round(audio_dur * 0.65, 2)
    clip2_path = _make_test_mp4(tmp_path / "clip2.mp4", clip2_dur, color="darkgreen")
    with open(clip2_path, "rb") as f:
        r_up2 = client.post(
            f"/api/v1/todos/{item_id}/videos",
            headers=ingest,
            files={"file": ("clip2.mp4", f, "video/mp4")},
            data={"covered_shot_indexes": "[3]", "note": "Shot 3 video"},
        )
    assert r_up2.status_code == 201, r_up2.text
    up2 = r_up2.json()
    vp2 = up2["video_progress"]
    assert up2["video"]["seq_num"] == 2
    assert vp2["uploaded_count"] == 2
    assert vp2["is_shortfall"] is False
    assert vp2["shortfall_warning"] is None
    assert vp2["ready_to_assemble"] is True

    # 4. User presses Confirm & Assemble -> concatenates #1 + #2 and muxes with Audio EN
    r_confirm_ok = client.post(
        f"/api/v1/todos/{item_id}/confirm-assemble",
        headers=ingest,
        json={"burn_subtitles": False, "allow_shortfall": False},
    )
    assert r_confirm_ok.status_code == 200, r_confirm_ok.text
    assembled = r_confirm_ok.json()
    assert assembled["status"] == "completed"
    assert assembled["videos_concatenated"] == 2
    assert abs(assembled["final_duration_sec"] - audio_dur) <= 0.35

    # 5. Download final assembled MP4
    r_final = client.get(assembled["final_video_url"], headers=read_key)
    assert r_final.status_code == 200
    assert r_final.headers["content-type"] == "video/mp4"
    assert len(r_final.content) > 2000


def test_confirm_assemble_with_explicit_allow_shortfall(
    client, ingest, fast_offline_tts, tmp_path
):
    """If uploaded videos are shorter than EN audio, confirm-assemble succeeds when allow_shortfall=True."""
    r = client.post(
        "/api/v1/todos/from-script",
        headers=ingest,
        json={
            "title": "Shortfall Override Test",
            "script_text": "This English audio is longer than the single uploaded video clip.",
            "bgm_enabled": False,
        },
    )
    assert r.status_code == 201, r.text
    pkg = r.json()
    item_id = pkg["item_id"]
    audio_dur = pkg["audio_duration_sec"]

    short_clip = _make_test_mp4(tmp_path / "short.mp4", max(1.0, audio_dur * 0.4), color="darkred")
    with open(short_clip, "rb") as f:
        r_up = client.post(
            f"/api/v1/todos/{item_id}/videos",
            headers=ingest,
            files={"file": ("short.mp4", f, "video/mp4")},
        )
    assert r_up.status_code == 201
    assert r_up.json()["video_progress"]["is_shortfall"] is True

    # Confirm with allow_shortfall=True pads the last frame to cover the full audio duration
    r_confirm = client.post(
        f"/api/v1/todos/{item_id}/confirm-assemble",
        headers=ingest,
        json={"burn_subtitles": False, "allow_shortfall": True},
    )
    assert r_confirm.status_code == 200, r_confirm.text
    res = r_confirm.json()
    assert res["status"] == "completed"
    assert abs(res["final_duration_sec"] - audio_dur) <= 0.35


def test_skill_bundle_and_image_roundtrip(client, ingest, read_key, fast_offline_tts):
    """External image-gen agent can pull the complete skill bundle + script/audio
    and push generated keyframe/character images back up to the TODO item."""
    import base64
    import struct
    import zlib

    r = client.post(
        "/api/v1/todos/from-script",
        headers=ingest,
        json={
            "title": "Image Agent Roundtrip Test",
            "shots": [
                {"index": 1, "narration": "Wang Chuanfu founded BYD in Shenzhen.", "shot_type": "scene"},
                {"index": 2, "narration": "BlackRock manages fourteen trillion dollars.", "shot_type": "diagram"},
            ],
            "bgm_enabled": False,
        },
    )
    assert r.status_code == 201, r.text
    pkg = r.json()
    item_id = pkg["item_id"]

    # 1. Verify skill_bundle is embedded directly in GET /api/v1/todos/{item_id}
    assert "skill_bundle" in pkg
    assert "STYLE LOCKED:" in pkg["skill_bundle"]["style_lock_v2"]
    assert "scene" in pkg["skill_bundle"]["shot_types"]
    assert "diagram" in pkg["skill_bundle"]["shot_types"]

    # 2. Agent pulls full skill markdown documents via GET /api/v1/todos/{item_id}/skills
    r_skills = client.get(pkg["skills_url"], headers=read_key)
    assert r_skills.status_code == 200
    skills_data = r_skills.json()
    assert "shot_generation/SKILL.md" in skills_data["documents"]
    assert "shot_generation/UNIVERSAL_SHOT_FRAMEWORK.md" in skills_data["documents"]
    assert "video_production/SKILL.md" in skills_data["documents"]

    # 3. Build a minimal valid 1280x720 PNG header + payload and push back as Keyframe #1
    def _minimal_png(w: int = 1280, h: int = 720) -> bytes:
        ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
        def _chunk(ctype: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF)
        return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IEND", b"")

    png_bytes = _minimal_png(1280, 720)
    r_img1 = client.post(
        f"/api/v1/todos/{item_id}/images",
        headers=ingest,
        json={
            "kind": "keyframe",
            "seq_num": 1,
            "shot_index": 1,
            "base64_data": base64.b64encode(png_bytes).decode("ascii"),
            "prompt_used": "STYLE LOCKED, Wang Chuanfu peach skin face in Shenzhen lab",
            "covered_shot_indexes": [1],
        },
    )
    assert r_img1.status_code == 201, r_img1.text
    img_res = r_img1.json()
    assert img_res["image"]["width"] == 1280
    assert img_res["image"]["height"] == 720
    assert img_res["image_progress"]["keyframes_count"] == 1

    # 4. Pull GET /api/v1/todos/{item_id} and verify shot #1 now has keyframe_url & image is downloadable
    r_todo = client.get(f"/api/v1/todos/{item_id}", headers=read_key)
    assert r_todo.status_code == 200
    todo = r_todo.json()
    assert len(todo["uploaded_images"]) == 1
    assert todo["shots"][0]["keyframe_url"] is not None

    r_dl = client.get(todo["shots"][0]["keyframe_url"], headers=read_key)
    assert r_dl.status_code == 200
    assert r_dl.headers["content-type"] == "image/png"
    assert r_dl.content.startswith(b"\x89PNG")
    assert "inline" in r_dl.headers.get("content-disposition", "")


def test_shot_checklist_sparse_missing_shots_and_inline_viewers(
    client, ingest, read_key, fast_offline_tts, tmp_path
):
    """Verify:
    1. Sparse video uploads (e.g. Shot #1 and Shot #3 uploaded while Shot #2 is missing)
       preserve exact shot positions, mark Shot #2 as missing/empty, and compute exact
       per-shot delta_duration_sec and remaining_duration_sec.
    2. GET/PATCH /api/v1/todos/{item_id}/shot-checklist and PATCH /shots/{shot_index}
       allow the video gen agent and web UI to tick/untick shots and query remaining seconds.
    3. All media endpoints serve Content-Disposition: inline by default for in-browser viewing.
    """
    r = client.post(
        "/api/v1/todos/from-script",
        headers=ingest,
        json={
            "title": "Sparse Grid & Agent Checklist Test",
            "shots": [
                {"index": 1, "narration": "First shot narration for testing exact duration.", "shot_type": "scene"},
                {"index": 2, "narration": "Second shot is intentionally left empty in the grid.", "shot_type": "quote"},
                {"index": 3, "narration": "Third shot has a video with a slight duration overrun.", "shot_type": "diagram"},
                {"index": 4, "narration": "Fourth shot completes the 4-column row.", "shot_type": "crowd"},
            ],
            "bgm_enabled": False,
        },
    )
    assert r.status_code == 201, r.text
    pkg = r.json()
    item_id = pkg["item_id"]
    cl0 = pkg["shot_checklist"]
    assert cl0["total_shots"] == 4
    assert cl0["checked_shots_count"] == 0
    assert cl0["unchecked_shots_count"] == 4
    assert cl0["missing_shot_indexes"] == [1, 2, 3, 4]
    assert cl0["remaining_duration_to_generate_sec"] > 0

    shot1_exp = cl0["shots"][0]["expected_duration_sec"]
    shot2_exp = cl0["shots"][1]["expected_duration_sec"]
    shot3_exp = cl0["shots"][2]["expected_duration_sec"]

    # Upload video for Shot #1 (slightly shorter by ~0.6s if long enough, or exact)
    clip1_target = max(1.0, round(shot1_exp, 2))
    clip1 = _make_test_mp4(tmp_path / "shot1.mp4", clip1_target, color="navy")
    with open(clip1, "rb") as f:
        r_v1 = client.post(
            f"/api/v1/todos/{item_id}/videos",
            headers=ingest,
            files={"file": ("shot1.mp4", f, "video/mp4")},
            data={"seq_num": "1", "covered_shot_indexes": "[1]"},
        )
    assert r_v1.status_code == 201, r_v1.text

    # Upload video directly for Shot #3 (skipping Shot #2!) with +0.8s overrun
    clip3_target = round(shot3_exp + 0.8, 2)
    clip3 = _make_test_mp4(tmp_path / "shot3.mp4", clip3_target, color="darkgreen")
    with open(clip3, "rb") as f:
        r_v3 = client.post(
            f"/api/v1/todos/{item_id}/videos",
            headers=ingest,
            files={"file": ("shot3.mp4", f, "video/mp4")},
            data={"seq_num": "3", "covered_shot_indexes": "[3]"},
        )
    assert r_v3.status_code == 201, r_v3.text

    # Poll GET /api/v1/todos/{item_id}/shot-checklist as the Video Gen Agent
    r_cl = client.get(f"/api/v1/todos/{item_id}/shot-checklist", headers=read_key)
    assert r_cl.status_code == 200
    cl = r_cl.json()
    assert cl["checked_shots_count"] == 2
    assert cl["unchecked_shots_count"] == 2
    assert cl["checked_shot_indexes"] == [1, 3]
    assert cl["missing_shot_indexes"] == [2, 4]

    shots_by_idx = {s["index"]: s for s in cl["shots"]}
    # Shot #1 has video & is checked
    assert shots_by_idx[1]["has_video"] is True
    assert shots_by_idx[1]["checked"] is True
    # Shot #2 is empty/missing in its exact slot (#2)
    assert shots_by_idx[2]["has_video"] is False
    assert shots_by_idx[2]["checked"] is False
    assert shots_by_idx[2]["duration_status"] == "missing"
    assert abs(shots_by_idx[2]["remaining_duration_sec"] - shot2_exp) <= 0.05
    # Shot #3 has video & shows positive overrun delta
    assert shots_by_idx[3]["has_video"] is True
    assert shots_by_idx[3]["checked"] is True
    assert shots_by_idx[3]["delta_duration_sec"] > 0.4
    assert shots_by_idx[3]["duration_status"] == "overrun"

    # Agent manually ticks Shot #2 with generated_duration_sec via PATCH /shots/2
    r_patch2 = client.patch(
        f"/api/v1/todos/{item_id}/shots/2",
        headers=ingest,
        json={"checked": True, "generated_duration_sec": shot2_exp, "note": "Generated by external agent"},
    )
    assert r_patch2.status_code == 200
    cl_after = r_patch2.json()
    assert cl_after["checked_shots_count"] == 3
    assert 2 in cl_after["checked_shot_indexes"]
    s2_after = {s["index"]: s for s in cl_after["shots"]}[2]
    assert s2_after["checked"] is True
    assert abs(s2_after["generated_duration_sec"] - shot2_exp) <= 0.05
    assert s2_after["remaining_duration_sec"] == 0.0

    # Batch uncheck_missing resets shots that still have no uploaded video file (Shot #2 & #4)
    r_batch = client.patch(
        f"/api/v1/todos/{item_id}/shot-checklist",
        headers=ingest,
        json={"uncheck_missing": True},
    )
    assert r_batch.status_code == 200
    assert r_batch.json()["checked_shot_indexes"] == [1, 3]

    # Verify media/file endpoints use inline Content-Disposition by default
    for url in [
        pkg["audio_url"],
        pkg["subtitles_srt_url"],
        pkg["subtitles_ass_url"],
        shots_by_idx[1]["video_url"],
    ]:
        resp = client.get(url, headers=read_key)
        assert resp.status_code == 200
        assert "inline" in resp.headers.get("content-disposition", "")

    # And ?download=1 switches to attachment when explicitly requested
    resp_dl = client.get(pkg["audio_url"] + "?download=1", headers=read_key)
    assert resp_dl.status_code == 200
    assert "attachment" in resp_dl.headers.get("content-disposition", "")


def test_multi_shot_video_override_status_filters_and_ui_contracts(
    client, ingest, read_key, fast_offline_tts, tmp_path
):
    """Verify:
    1. Multi-shot merged video covering [1, 2, 3] where Shot #2 is later replaced by an
       individual single-shot upload (even if the merged video had a higher seq_num=5 and
       the single-shot replacement has seq_num=2) prioritizes the latest uploaded clip for
       Shot #2, updates owned covered_shot_indexes to [1, 3] on Shots #1 & #3, and falls
       back cleanly to the merged clip if the single-shot override is later deleted.
    2. Virtual status filters (`?status=ready_to_assemble`, `?status=assembled`),
       `total_delta_duration_sec`, `/skill` alias, and `character_sheet`/`style_ref`
       image kind aliases + deletion by `kind` work end-to-end.
    """
    import base64
    import struct
    import time
    import zlib

    r = client.post(
        "/api/v1/todos/from-script",
        headers=ingest,
        json={
            "title": "Multi-Shot Override & Filter Test",
            "shots": [
                {"index": 1, "narration": "First shot in a three shot merged sequence.", "shot_type": "scene"},
                {"index": 2, "narration": "Second shot that will be overridden individually.", "shot_type": "quote"},
                {"index": 3, "narration": "Third shot remaining in the initial merged clip.", "shot_type": "diagram"},
            ],
            "bgm_enabled": False,
            "collect_references": False,
        },
    )
    assert r.status_code == 201, r.text
    pkg = r.json()
    item_id = pkg["item_id"]
    shots0 = pkg["shot_checklist"]["shots"]
    exp1 = shots0[0]["expected_duration_sec"]
    exp2 = shots0[1]["expected_duration_sec"]
    exp3 = shots0[2]["expected_duration_sec"]
    total_exp = round(exp1 + exp2 + exp3, 2)

    # 1. Upload merged clip at seq_num=5 covering [1, 2, 3]
    merged_clip = _make_test_mp4(tmp_path / "merged_123.mp4", max(1.5, total_exp), color="navy")
    with open(merged_clip, "rb") as f:
        r_m = client.post(
            f"/api/v1/todos/{item_id}/videos",
            headers=ingest,
            files={"file": ("merged_123.mp4", f, "video/mp4")},
            data={"seq_num": "5", "covered_shot_indexes": "[1, 2, 3]"},
        )
    assert r_m.status_code == 201, r_m.text
    cl_m = r_m.json()["shot_checklist"]
    assert cl_m["checked_shot_indexes"] == [1, 2, 3]
    assert "total_delta_duration_sec" in cl_m
    assert cl_m["total_delta_duration_sec"] == cl_m["delta_total_duration_sec"]

    # 2. Later, replace Shot #2 with an individual clip at seq_num=2 (smaller seq_num, newer timestamp)
    time.sleep(1.05)  # ensure updated_at epoch second is strictly newer
    override_dur = round(exp2 + 0.9, 2)
    override_clip = _make_test_mp4(tmp_path / "shot2_override.mp4", override_dur, color="darkred")
    with open(override_clip, "rb") as f:
        r_ov = client.post(
            f"/api/v1/todos/{item_id}/videos",
            headers=ingest,
            files={"file": ("shot2_override.mp4", f, "video/mp4")},
            data={"seq_num": "2", "covered_shot_indexes": "[2]"},
        )
    assert r_ov.status_code == 201, r_ov.text
    cl_ov = r_ov.json()["shot_checklist"]
    by_idx = {s["index"]: s for s in cl_ov["shots"]}
    assert by_idx[2]["video_seq_num"] == 2
    assert by_idx[2]["covered_shot_indexes"] == [2]
    assert abs(by_idx[2]["actual_duration_sec"] - override_dur) <= 0.15
    assert by_idx[1]["video_seq_num"] == 5
    assert by_idx[1]["covered_shot_indexes"] == [1, 3]
    assert by_idx[3]["video_seq_num"] == 5
    assert by_idx[3]["covered_shot_indexes"] == [1, 3]

    # 3. Delete the override clip (seq_num=2) -> Shot #2 falls back to merged clip (seq_num=5) and stays checked
    r_del = client.delete(f"/api/v1/todos/{item_id}/videos/2", headers=ingest)
    assert r_del.status_code == 200, r_del.text
    cl_fb = r_del.json()["shot_checklist"]
    by_idx_fb = {s["index"]: s for s in cl_fb["shots"]}
    assert by_idx_fb[2]["has_video"] is True
    assert by_idx_fb[2]["checked"] is True
    assert by_idx_fb[2]["video_seq_num"] == 5
    assert by_idx_fb[2]["covered_shot_indexes"] == [1, 2, 3]

    # 4. Both /skills and /skill alias return 200
    assert client.get(f"/api/v1/todos/{item_id}/skills", headers=read_key).status_code == 200
    assert client.get(f"/api/v1/todos/{item_id}/skill", headers=read_key).status_code == 200

    # 5. Image upload with alias kind='character_sheet' normalizes to 'character' and deletes cleanly with ?kind=character
    ihdr = struct.pack(">IIBBBBB", 1280, 720, 8, 2, 0, 0, 0)
    def _chunk(ctype: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF)
    png_bytes = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IEND", b"")

    r_char = client.post(
        f"/api/v1/todos/{item_id}/images",
        headers=ingest,
        json={
            "kind": "character_sheet",
            "seq_num": 1,
            "base64_data": base64.b64encode(png_bytes).decode("ascii"),
        },
    )
    assert r_char.status_code == 201, r_char.text
    assert r_char.json()["kind"] == "character"
    r_del_char = client.delete(f"/api/v1/todos/{item_id}/images/1?kind=character", headers=ingest)
    assert r_del_char.status_code == 200

    # 6. Status filter ?status=ready_to_assemble finds this item before assembly, and ?status=assembled finds it after assembly
    r_ready_list = client.get("/api/v1/todos?status=ready_to_assemble", headers=read_key)
    assert r_ready_list.status_code == 200
    assert any(x["item_id"] == item_id for x in r_ready_list.json()["todos"])

    r_asm = client.post(
        f"/api/v1/todos/{item_id}/confirm-assemble",
        headers=ingest,
        json={"burn_subtitles": False, "allow_shortfall": True},
    )
    assert r_asm.status_code == 200, r_asm.text

    r_assembled_list = client.get("/api/v1/todos?status=assembled", headers=read_key)
    assert r_assembled_list.status_code == 200
    assert any(x["item_id"] == item_id for x in r_assembled_list.json()["todos"])


def test_assemble_trims_each_layer_to_planned_milliseconds_and_ignores_orphan_clips(
    client, ingest, read_key, fast_offline_tts, tmp_path
):
    """Verify:
    1. When Shot #1 has a huge overrun clip covering only [1] while Shots #2 & #3 are missing,
       video_progress immediately reports is_shortfall=True / ready_to_assemble=False,
       ?status=ready_to_assemble excludes the item, and confirm-assemble with allow_shortfall=False
       rejects with 409 because Shot #1 is trimmed to its planned script millisecond duration.
    2. When all planned shots [1, 2, 3] have clips with +1.5s overrun each AND an extra orphan
       clip (#99) is uploaded outside the script plan, confirm-assemble trims each clip to its
       exact planned millisecond window, ignores orphan clip #99, and produces an assembled MP4
       with zero cumulative overrun drift.
    """
    r = client.post(
        "/api/v1/todos/from-script",
        headers=ingest,
        json={
            "title": "Planned Millisecond Trim Test",
            "shots": [
                {"index": 1, "narration": "First shot with exact millisecond boundary.", "shot_type": "scene"},
                {"index": 2, "narration": "Second shot must start on time without drift.", "shot_type": "quote"},
                {"index": 3, "narration": "Third shot finishes at the exact audio end.", "shot_type": "diagram"},
            ],
            "bgm_enabled": False,
            "collect_references": False,
        },
    )
    assert r.status_code == 201, r.text
    pkg = r.json()
    item_id = pkg["item_id"]
    audio_dur = pkg["audio_duration_sec"]
    shots = pkg["shots"]
    exp2 = shots[1]["duration_sec"]
    exp3 = shots[2]["duration_sec"]

    # 0. Uploading ONLY an orphan clip (#99) must not mark the item ready or map #99 onto Shot #1
    orphan_clip99 = _make_test_mp4(tmp_path / "orphan99.mp4", round(audio_dur + 1.0, 2), color="yellow")
    with open(orphan_clip99, "rb") as f:
        r_orph = client.post(
            f"/api/v1/todos/{item_id}/videos",
            headers=ingest,
            files={"file": ("orphan99.mp4", f, "video/mp4")},
            data={"seq_num": "99", "covered_shot_indexes": "[]"},
        )
    assert r_orph.status_code == 201, r_orph.text
    assert r_orph.json()["video_progress"]["is_shortfall"] is True
    assert r_orph.json()["video_progress"]["ready_to_assemble"] is False

    r_orph_asm = client.post(
        f"/api/v1/todos/{item_id}/confirm-assemble",
        headers=ingest,
        json={"burn_subtitles": False, "allow_shortfall": True},
    )
    assert r_orph_asm.status_code == 422, r_orph_asm.text

    # 1. Upload Shot #1 with a massive overrun (audio_dur + 2.0s) while Shots #2 & #3 are still missing
    overrun_clip1 = _make_test_mp4(tmp_path / "overrun1.mp4", round(audio_dur + 2.0, 2), color="red")
    with open(overrun_clip1, "rb") as f:
        r_v1 = client.post(
            f"/api/v1/todos/{item_id}/videos",
            headers=ingest,
            files={"file": ("overrun1.mp4", f, "video/mp4")},
            data={"seq_num": "1", "covered_shot_indexes": "[1]"},
        )
    assert r_v1.status_code == 201, r_v1.text
    vp1 = r_v1.json()["video_progress"]
    assert vp1["is_shortfall"] is True
    assert vp1["is_duration_sufficient"] is False
    assert vp1["ready_to_assemble"] is False
    assert vp1["missing_shots_count"] == 2

    r_ready_check = client.get("/api/v1/todos?status=ready_to_assemble", headers=read_key)
    assert r_ready_check.status_code == 200
    assert not any(x["item_id"] == item_id for x in r_ready_check.json()["todos"])

    # Because Shot #1 is trimmed to exp1 ms, Shots #2 & #3 are still uncovered -> must return 409 when allow_shortfall=False
    r_blocked = client.post(
        f"/api/v1/todos/{item_id}/confirm-assemble",
        headers=ingest,
        json={"burn_subtitles": False, "allow_shortfall": False},
    )
    assert r_blocked.status_code == 409, r_blocked.text
    assert r_blocked.json()["error"] == "video_duration_shortfall"

    # 2. Upload Shot #2 and Shot #3 each with +1.5s overrun
    overrun_clip2 = _make_test_mp4(tmp_path / "overrun2.mp4", round(exp2 + 1.5, 2), color="green")
    overrun_clip3 = _make_test_mp4(tmp_path / "overrun3.mp4", round(exp3 + 1.5, 2), color="blue")

    for seq, pth, cov in [
        ("2", overrun_clip2, "[2]"),
        ("3", overrun_clip3, "[3]"),
    ]:
        with open(pth, "rb") as f:
            rv = client.post(
                f"/api/v1/todos/{item_id}/videos",
                headers=ingest,
                files={"file": (pth.name, f, "video/mp4")},
                data={"seq_num": seq, "covered_shot_indexes": cov},
            )
            assert rv.status_code == 201, rv.text

    # 3. Confirm & assemble -> must trim each of [1, 2, 3] to planned ms and ignore orphan #99
    r_asm = client.post(
        f"/api/v1/todos/{item_id}/confirm-assemble",
        headers=ingest,
        json={"burn_subtitles": False, "allow_shortfall": False},
    )
    assert r_asm.status_code == 200, r_asm.text
    asm = r_asm.json()
    assert asm["status"] == "completed"
    assert asm["trim_mode"] == "script_plan_ms"
    assert asm["videos_concatenated"] == 3
    assert asm["videos_assembled"] == [1, 2, 3]
    assert abs(asm["planned_duration_ms"] - round(audio_dur * 1000)) <= 5
    assert asm["trimmed_overrun_ms"] >= 2500
    assert abs(asm["final_duration_sec"] - audio_dur) <= 0.15


def test_assemble_non_contiguous_override_short_merged_clip_and_burn_subtitles(
    client, ingest, read_key, fast_offline_tts, tmp_path
):
    """Verify:
    1. When a merged clip covering [1, 2, 3] is slightly shorter than the sum of Shots #1 + #2
       (so naive unscaled seek for Shot #3 would land at EOF and produce 0 frames), overriding
       Shot #2 with a single-shot clip scales/clamps Shot #3's seek offset safely and renders
       all 3 layers cleanly.
    2. Assembling with burn_subtitles=True burns ASS subtitles and sets subtitles_burned=1.
    """
    import time

    r = client.post(
        "/api/v1/todos/from-script",
        headers=ingest,
        json={
            "title": "Short Merged Clip Override & Burn Subtitles Test",
            "shots": [
                {"index": 1, "narration": "Opening scene in the three-part sequence.", "shot_type": "scene"},
                {"index": 2, "narration": "Middle quote that gets overridden individually.", "shot_type": "quote"},
                {"index": 3, "narration": "Closing diagram from the merged clip.", "shot_type": "diagram"},
            ],
            "bgm_enabled": False,
            "collect_references": False,
        },
    )
    assert r.status_code == 201, r.text
    pkg = r.json()
    item_id = pkg["item_id"]
    audio_dur = pkg["audio_duration_sec"]
    shots = pkg["shots"]
    exp1 = shots[0]["duration_sec"]
    exp2 = shots[1]["duration_sec"]

    # Merged clip covering [1, 2, 3] whose duration is just exp1 + exp2 + 0.01s (where naive seek_ms = exp1+exp2 is within 10ms of EOF)
    merged_dur = round(exp1 + exp2 + 0.01, 3)
    merged_clip = _make_test_mp4(tmp_path / "merged_short.mp4", merged_dur, color="navy")
    with open(merged_clip, "rb") as f:
        r_m = client.post(
            f"/api/v1/todos/{item_id}/videos",
            headers=ingest,
            files={"file": ("merged_short.mp4", f, "video/mp4")},
            data={"seq_num": "5", "covered_shot_indexes": "[1, 2, 3]"},
        )
    assert r_m.status_code == 201, r_m.text

    time.sleep(1.05)
    override_clip2 = _make_test_mp4(tmp_path / "override2.mp4", round(exp2 + 2.0, 2), color="darkred")
    with open(override_clip2, "rb") as f:
        r_ov = client.post(
            f"/api/v1/todos/{item_id}/videos",
            headers=ingest,
            files={"file": ("override2.mp4", f, "video/mp4")},
            data={"seq_num": "2", "covered_shot_indexes": "[2]"},
        )
    assert r_ov.status_code == 201, r_ov.text

    r_asm = client.post(
        f"/api/v1/todos/{item_id}/confirm-assemble",
        headers=ingest,
        json={"burn_subtitles": True, "allow_shortfall": False},
    )
    assert r_asm.status_code == 200, r_asm.text
    asm = r_asm.json()
    assert asm["status"] == "completed"
    assert asm["videos_concatenated"] == 3
    assert asm["videos_assembled"] == [5, 2, 5]
    assert abs(asm["final_duration_sec"] - audio_dur) <= 0.15

    r_todo = client.get(f"/api/v1/todos/{item_id}", headers=read_key)
    assert r_todo.status_code == 200
    assert r_todo.json()["subtitles_burned"] == 1


def test_retime_and_mix_audio_avoids_bgm_slicing_and_double_pauses(tmp_path):
    """Verify:
    1. _build_narrative_bgm_cues covers [0, total_dur] continuously without a 10s silence hole.
    2. retime_and_mix_existing_audio prefers voice_clean.wav over a BGM-mixed audio_en.wav,
       trims pre-existing inter-shot pauses so pauses are never doubled, and applies smooth
       cosine fades at chunk boundaries.
    """
    import math
    import struct
    import wave
    from backend.app.pipeline_en.audio_generator_en import (
        _build_narrative_bgm_cues,
        _trim_and_smooth_voice_pcm,
        retime_and_mix_existing_audio,
    )

    cues = _build_narrative_bgm_cues(120.0, [20.0] * 6)
    assert cues[0][1] == 0.0
    assert cues[-1][2] == 120.0
    for i in range(len(cues) - 1):
        assert cues[i][2] == cues[i + 1][1], f"Gap found between BGM cues {cues[i]} and {cues[i+1]}"

    rate = 24000
    # Build a 1.0s tone followed by 0.60s dead silence for Shot 1, and 1.0s tone + 0.60s silence for Shot 2
    def _make_tone_with_pause(tone_s: float, pause_s: float, amp: int = 6000) -> bytes:
        n_t = int(rate * tone_s)
        n_p = int(rate * pause_s)
        buf = bytearray((n_t + n_p) * 2)
        for idx_s in range(n_t):
            val = int(amp * math.sin(2.0 * math.pi * 220.0 * (idx_s / rate)))
            struct.pack_into("<h", buf, idx_s * 2, val)
        return bytes(buf)

    s1_clean = _make_tone_with_pause(1.0, 0.60, amp=6000)
    s2_clean = _make_tone_with_pause(1.0, 0.60, amp=6000)
    voice_clean_wav = tmp_path / "voice_clean.wav"
    with wave.open(str(voice_clean_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(s1_clean + s2_clean)

    # Contaminated audio_en.wav has loud constant tone everywhere (simulating pre-mixed BGM)
    audio_en_wav = tmp_path / "audio_en.wav"
    loud_bgm = _make_tone_with_pause(3.2, 0.0, amp=12000)
    with wave.open(str(audio_en_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(loud_bgm)

    shots = [
        {"index": 1, "narration": "Shot one.", "audio_start_sec": 0.0, "audio_end_sec": 1.6},
        {"index": 2, "narration": "Shot two.", "audio_start_sec": 1.6, "audio_end_sec": 3.2},
    ]
    res = retime_and_mix_existing_audio(
        audio_path=audio_en_wav,
        shots=shots,
        out_dir=tmp_path,
        slow_factor=1.0,
        extra_pause_sec=0.04,
        bgm_enabled=False,
    )
    # Pre-existing 0.60s pauses on both shots must be trimmed; total duration ~ 1.0 + 0.24 + 1.0 = ~2.24s (well under 2.6s)
    assert 2.15 <= res["audio_duration_sec"] <= 2.40
    assert res["shots"][0]["audio_start_sec"] == 0.0
    assert res["shots"][0]["audio_end_sec"] == res["shots"][1]["audio_start_sec"]
    assert res["shots"][1]["audio_end_sec"] == res["audio_duration_sec"]

    # Check cosine fade-in/out on _trim_and_smooth_voice_pcm
    smoothed = _trim_and_smooth_voice_pcm(s1_clean, rate=rate)
    first_sample = abs(struct.unpack_from("<h", smoothed, 0)[0])
    last_sample = abs(struct.unpack_from("<h", smoothed, len(smoothed) - 2)[0])
    assert first_sample < 50
    assert last_sample < 50

    # Verify idempotency: calling retime_and_mix_existing_audio a second time with slow_factor=0.90
    # must slice from the preserved 1.0x voice_clean.wav rather than compounding slow_factor (0.90^2)
    res_slow1 = retime_and_mix_existing_audio(
        audio_path=audio_en_wav,
        shots=res["shots"],
        out_dir=tmp_path,
        slow_factor=0.90,
        extra_pause_sec=0.04,
        bgm_enabled=False,
    )
    res_slow2 = retime_and_mix_existing_audio(
        audio_path=audio_en_wav,
        shots=res_slow1["shots"],
        out_dir=tmp_path,
        slow_factor=0.90,
        extra_pause_sec=0.04,
        bgm_enabled=False,
    )
    assert abs(res_slow1["audio_duration_sec"] - res_slow2["audio_duration_sec"]) <= 0.02

    # Verify _snap_shot_boundaries_to_silence snaps a mid-word cut (e.g. 0.85s inside a 1.0s tone)
    # to the true silence midpoint (1.30s in [1.0s..1.6s])
    from backend.app.pipeline_en.audio_generator_en import _snap_shot_boundaries_to_silence

    snapped = _snap_shot_boundaries_to_silence(
        s1_clean + s2_clean, rate, [(0.0, 0.85), (0.85, 3.20)]
    )
    assert 1.15 <= snapped[0][1] <= 1.45
    assert snapped[0][1] == snapped[1][0]





