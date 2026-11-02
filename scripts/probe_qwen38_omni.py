"""Probe qwen3.8-omni capabilities on the xKiro gateway (Phase 0 of the 9.5 plan).

Tests, in parallel where independent:
  1. Model discovery (/v1/models) — find the exact omni model id
  2. Chat (text) with candidate model ids
  3. JSON mode (response_format json_object)
  4. Vision (image_url input)
  5. Image generation via /v1/images/generations
  6. Image generation via chat (omni models may return image in content)
  7. TTS via /v1/audio/speech (qwen3.8-omni + xkiro-voice baseline)
  8. Chat latency (3 sequential runs)
  9. Rate limit (8 parallel small chat calls)

Writes data/probe/qwen38_omni_capability.json and prints a summary table.
The API key is resolved via services.settings_service (DB) with .env fallback;
it is never printed.
"""

from __future__ import annotations

import base64
import concurrent.futures
import json
import os
import sys
import time
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

DATA_PROBE = REPO_ROOT / "data" / "probe"
REPORT_PATH = DATA_PROBE / "qwen38_omni_capability.json"

CANDIDATE_MODELS = [
    "qwen3.8-omni",
    "qwen/qwen3.8-omni",
    "qwen3.8-omni:free",
    "qwen/qwen3.8-omni:free",
    "Qwen/qwen3.8-omni",
]

# 1x1 transparent PNG used for the vision probe.
TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def _load_env_file() -> None:
    """Load KEY=VALUE pairs from .env into os.environ (never printed/logged)."""
    env_path = REPO_ROOT / ".env"
    if not env_path.is_file():
        return
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and value and key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass


def _resolve_credentials() -> tuple[str, str, str | None]:
    """Return (base_url, api_key, preferred_chat_model). Raises on missing key."""
    base_url = "https://api.xkiro.com/v1"
    api_key = os.environ.get("XKIRO_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        try:
            from services.settings_service import get_llm_api_key, get_llm_base_url

            api_key = get_llm_api_key() or ""
            base_url = get_llm_base_url() or base_url
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] settings_service unavailable: {exc}")
    if not api_key:
        raise SystemExit(
            "No API key found. Set XKIRO_API_KEY in the environment or .env, "
            "or configure it via the app Settings page."
        )
    return base_url, api_key, None


def _headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    }


# ---------------------------------------------------------------------------
# Individual probes — each returns a result dict; must never raise.
# ---------------------------------------------------------------------------

def probe_models(base_url: str, api_key: str) -> dict:
    out: dict = {"ok": False, "model_ids": [], "omni_matches": [], "error": None}
    try:
        resp = requests.get(f"{base_url}/models", headers=_headers(api_key), timeout=30)
        resp.raise_for_status()
        data = resp.json()
        ids = sorted(m.get("id", "") for m in data.get("data", []))
        out["ok"] = True
        out["model_ids"] = ids
        out["omni_matches"] = [m for m in ids if "omni" in m.lower() or "qwen3.8" in m.lower()]
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:300]
    return out


def probe_chat(base_url: str, api_key: str, model: str) -> dict:
    out: dict = {"model": model, "ok": False, "latency_ms": None, "reply": None, "error": None}
    start = time.perf_counter()
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=base_url, timeout=60)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with exactly: PROBE_OK"}],
            max_tokens=16,
            temperature=0.0,
        )
        out["latency_ms"] = round((time.perf_counter() - start) * 1000)
        out["reply"] = (resp.choices[0].message.content or "")[:120] if resp.choices else None
        out["ok"] = "PROBE_OK" in (out["reply"] or "")
    except Exception as exc:  # noqa: BLE001
        out["latency_ms"] = round((time.perf_counter() - start) * 1000)
        out["error"] = str(exc)[:300]
    return out


def probe_json_mode(base_url: str, api_key: str, model: str) -> dict:
    out: dict = {"ok": False, "parsed": None, "error": None}
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=base_url, timeout=60)
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": 'Return JSON: {"status":"ok","n":7}'}],
                response_format={"type": "json_object"},
                max_tokens=64,
                temperature=0.0,
            )
        except Exception as exc:
            if "response_format" in str(exc):
                out["response_format_supported"] = False
                resp = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": 'Return ONLY this JSON, nothing else: {"status":"ok","n":7}'}],
                    max_tokens=64,
                    temperature=0.0,
                )
            else:
                raise
        content = resp.choices[0].message.content or "" if resp.choices else ""
        parsed = json.loads(content.strip().removeprefix("```json").removesuffix("```").strip())
        out["parsed"] = parsed
        out["ok"] = isinstance(parsed, dict) and parsed.get("status") == "ok"
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:300]
    return out


def probe_vision(base_url: str, api_key: str, model: str) -> dict:
    out: dict = {"ok": False, "reply": None, "error": None}
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=base_url, timeout=60)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What is in this image? Answer in one short sentence."},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{TINY_PNG_B64}"}},
                    ],
                }
            ],
            max_tokens=64,
            temperature=0.0,
        )
        out["reply"] = (resp.choices[0].message.content or "")[:200] if resp.choices else None
        out["ok"] = bool(out["reply"])
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:300]
    return out


def probe_images_endpoint(base_url: str, api_key: str, model: str) -> dict:
    out: dict = {"ok": False, "url": None, "b64_bytes": 0, "error": None}
    try:
        resp = requests.post(
            f"{base_url}/images/generations",
            headers=_headers(api_key),
            json={"model": model, "prompt": "A simple red circle on white background", "n": 1, "size": "1024x1024"},
            timeout=180,
        )
        if resp.status_code != 200:
            out["error"] = f"HTTP {resp.status_code}: {resp.text[:300]}"
            return out
        data = resp.json()
        items = data.get("data") or []
        if items:
            item = items[0]
            if item.get("url"):
                out["url"] = item["url"][:200]
                out["ok"] = True
            elif item.get("b64_json"):
                out["b64_bytes"] = len(item["b64_json"])
                out["ok"] = True
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:300]
    return out


def probe_image_via_chat(base_url: str, api_key: str, model: str) -> dict:
    out: dict = {"ok": False, "has_image_output": False, "content_head": None, "error": None}
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=base_url, timeout=180)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": "Generate an image of a simple red circle on a white background. Return the image directly (URL or base64).",
                }
            ],
            max_tokens=2000,
            temperature=0.3,
        )
        content = (resp.choices[0].message.content or "") if resp.choices else ""
        out["content_head"] = content[:200]
        # Only a real raster image counts as image generation. LLMs can fake an
        # SVG text blob (not generated media), so SVG must not pass this check.
        out["is_svg_response"] = "data:image/svg" in content.lower() or content.lstrip().startswith("<svg")
        raster_data_uri = "data:image/png" in content.lower() or "data:image/jpeg" in content.lower() or "data:image/webp" in content.lower()
        raster_url = any(ext in content.lower() for ext in (".png", ".jpg", ".jpeg", ".webp")) and "http" in content.lower()
        raw_base64_blob = len(content) > 5000 and "svg" not in content.lower()[:200]
        out["has_image_output"] = raster_data_uri or raster_url or raw_base64_blob
        out["ok"] = out["has_image_output"]
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:300]
    return out


def probe_tts(base_url: str, api_key: str, model: str, voice: str) -> dict:
    out: dict = {"model": model, "voice": voice, "ok": False, "audio_bytes": 0, "error": None}
    try:
        resp = requests.post(
            f"{base_url}/audio/speech",
            headers=_headers(api_key),
            json={"model": model, "input": "Xin chào, đây là bài kiểm tra giọng đọc.", "voice": voice},
            timeout=90,
        )
        if resp.status_code != 200:
            # Keep more of the body — the gateway lists available TTS models here.
            out["error"] = f"HTTP {resp.status_code}: {resp.text[:800]}"
            return out
        audio = resp.content or b""
        out["audio_bytes"] = len(audio)
        out["ok"] = len(audio) > 1000 and not audio.lstrip()[:1].startswith(b"{")
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:300]
    return out


def probe_latency(base_url: str, api_key: str, model: str, runs: int = 3) -> dict:
    out: dict = {"latencies_ms": [], "min_ms": None, "mean_ms": None, "max_ms": None, "error": None}
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=base_url, timeout=60)
        for _ in range(runs):
            start = time.perf_counter()
            client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Say OK"}],
                max_tokens=8,
                temperature=0.0,
            )
            out["latencies_ms"].append(round((time.perf_counter() - start) * 1000))
        out["min_ms"] = min(out["latencies_ms"])
        out["mean_ms"] = round(sum(out["latencies_ms"]) / len(out["latencies_ms"]))
        out["max_ms"] = max(out["latencies_ms"])
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:300]
    return out


def probe_rate_limit(base_url: str, api_key: str, model: str, burst: int = 8) -> dict:
    out: dict = {"burst": burst, "success": 0, "rate_limited": 0, "other_errors": 0, "error": None}

    def _one(_: int) -> str:
        try:
            from openai import OpenAI

            client = OpenAI(api_key=api_key, base_url=base_url, timeout=60)
            client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Say OK"}],
                max_tokens=8,
                temperature=0.0,
            )
            return "ok"
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if "429" in msg or "rate" in msg.lower():
                return "429"
            return "other"

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=burst) as pool:
            results = list(pool.map(_one, range(burst)))
        out["success"] = results.count("ok")
        out["rate_limited"] = results.count("429")
        out["other_errors"] = results.count("other")
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:300]
    return out


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def main() -> None:
    _load_env_file()
    base_url, api_key, _preferred = _resolve_credentials()
    DATA_PROBE.mkdir(parents=True, exist_ok=True)

    report: dict = {"probed_at": time.strftime("%Y-%m-%d %H:%M:%S"), "base_url": base_url, "api_key_masked": api_key[:3] + "..." + api_key[-4:] if len(api_key) > 8 else "***"}

    print("== Phase 0 probe: qwen3.8-omni on xKiro ==")
    print(f"base_url: {base_url}")

    # 1. Model discovery first — needed to pick the exact model id.
    models = probe_models(base_url, api_key)
    report["models"] = models
    if models["ok"]:
        print(f"models listed: {len(models['model_ids'])}")
        if models["omni_matches"]:
            print(f"omni matches: {models['omni_matches']}")
        else:
            print("no *omni* / *qwen3.8* model id found in /models listing")
    else:
        print(f"/models failed: {models['error']}")

    omni_matches = models.get("omni_matches") or []
    # Prefer the exact qwen3.8 omni variant the user asked for, then any qwen omni, then anything else.
    qwen38 = [m for m in omni_matches if "qwen3.8" in m.lower() and "omni" in m.lower()]
    qwen_any = [m for m in omni_matches if "qwen" in m.lower() and "omni" in m.lower()]
    omni_model = (qwen38 or qwen_any or omni_matches or [None])[0] or next(iter(CANDIDATE_MODELS))

    # 2. Find a working model id among candidates (chat probe decides).
    working_model: str | None = None
    chat_results: dict = {}
    for candidate in dict.fromkeys([omni_model, *CANDIDATE_MODELS]):
        res = probe_chat(base_url, api_key, candidate)
        chat_results[candidate] = res
        status = "OK" if res["ok"] else f"FAIL ({(res['error'] or '')[:80]})"
        print(f"chat[{candidate}]: {status} ({res['latency_ms']}ms) reply={res['reply']!r}")
        if res["ok"] and working_model is None:
            working_model = candidate
        if res["ok"]:
            break  # first working candidate is enough
    report["chat_candidates"] = chat_results
    report["working_model"] = working_model

    if working_model is None:
        print("\nRESULT: no working qwen3.8-omni model id — model likely NOT available on this gateway.")
        report["conclusion"] = {"available": False}
        REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"report saved: {REPORT_PATH}")
        return

    print(f"\nusing model: {working_model}\n")

    # 3. Parallel capability probes on the working model.
    futures = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures["json_mode"] = pool.submit(probe_json_mode, base_url, api_key, working_model)
        futures["vision"] = pool.submit(probe_vision, base_url, api_key, working_model)
        futures["images_endpoint"] = pool.submit(probe_images_endpoint, base_url, api_key, working_model)
        futures["image_via_chat"] = pool.submit(probe_image_via_chat, base_url, api_key, working_model)
        futures["tts_omni"] = pool.submit(probe_tts, base_url, api_key, working_model, "confident-male-vietnamese")
        futures["tts_xkiro_voice"] = pool.submit(probe_tts, base_url, api_key, "xkiro-voice", "confident-male-vietnamese")
        futures["latency"] = pool.submit(probe_latency, base_url, api_key, working_model)
        futures["rate_limit"] = pool.submit(probe_rate_limit, base_url, api_key, working_model)
        results = {name: fut.result() for name, fut in futures.items()}

    report.update(results)

    print("== capability results ==")
    for name, res in results.items():
        if name == "latency":
            print(f"latency: min={res['min_ms']}ms mean={res['mean_ms']}ms max={res['max_ms']}ms err={res['error']}")
        elif name == "rate_limit":
            print(f"rate_limit: burst={res['burst']} ok={res['success']} 429={res['rate_limited']} other={res['other_errors']}")
        else:
            ok = res.get("ok")
            extra = {k: v for k, v in res.items() if k not in ("ok", "error") and v not in (None, 0, [], {})}
            print(f"{name}: {'OK' if ok else 'FAIL'} {json.dumps(extra, ensure_ascii=False)[:160]} err={(res.get('error') or '')[:120]}")

    conclusion = {
        "available": True,
        "model_id": working_model,
        "chat": bool(results["json_mode"].get("ok") or chat_results[working_model]["ok"]),
        "json_mode": bool(results["json_mode"].get("ok")),
        "vision": bool(results["vision"].get("ok")),
        "image_gen": bool(results["images_endpoint"].get("ok") or results["image_via_chat"].get("ok")),
        "tts": bool(results["tts_omni"].get("ok")),
        "tts_xkiro_voice_baseline": bool(results["tts_xkiro_voice"].get("ok")),
        "latency_mean_ms": results["latency"].get("mean_ms"),
        "rate_limit_burst_ok": results["rate_limit"].get("success"),
    }
    report["conclusion"] = conclusion
    print("\n== conclusion ==")
    print(json.dumps(conclusion, indent=2))

    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nreport saved: {REPORT_PATH}")


if __name__ == "__main__":
    main()
