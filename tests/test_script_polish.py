"""Phase 8: the external xKiro prose polish and its re-ingest contract.

The editor model is untrusted: it may merge scenes, restate numbers or stretch a
line past its reading time. Every one of those must throw away the WHOLE polish
and keep the internally drafted script, so these tests drive ScriptFactory.polish_scenes
with a fake editor and assert what lands in the shipped scenes.
"""

import math
import re

import pytest

from services.script_factory import (
    POLISH_MODEL,
    ScriptFactory,
    _locked_tokens,
    _polish_violations,
)

REPEAT = "nhà kho dột"

# Every scene says one thing no other scene says, so the ONLY phrase recurring
# across scenes is REPEAT itself — that is the defect the polish must remove.
_NOUNS = [
    "thuyền buôn", "sổ cái", "lò thủy tinh", "bằng sáng chế", "dây chuyền",
    "kho linh kiện", "hợp đồng", "ca trực", "bảng lương", "xưởng hàn",
    "giấy phép", "cánh quạt",
]
_VERBS = [
    "mở đầu", "đứng lại", "cháy âm ỉ", "chờ duyệt", "kêu rầm rầm",
    "vá từng góc", "sang tay", "gáp đôi", "rỉ theo mùa", "rò từng giọt",
    "bật xanh", "cuốn theo",
]
_TAILS = [
    "người ta quên mất", "thợ rèn giữ lại", "cháy rụi mùa đông",
    "mưa dột ba đêm", "đếm từng xu lẻ", "ghi bằng tay",
    "bán hết cổ phần", "vỡ thành ba mảnh", "kêu còi tầm", "đổi sang máy",
    "dời về Eindhoven", "chờ tin phản hồi",
]
_SYNONYMS = [
    "trạm trung chuyển", "xưởng đóng tàu", "nhà để hàng", "vựa hàng cũ",
    "kho ven sông", "điểm tập kết", "nhà chứa gỗ", "xưởng cũ nát",
    "lán chứa hàng", "kho tạm mái tôn", "nhà xlúp xiết", "xưởng ven kênh",
]
_EN_SYNONYMS = [
    "rotting depot", "riverside shed", "old freight barn", "mouldy timber store",
    "cracked roof store", "shipping shed", "leaky goods yard", "broken warehouse",
    "damp cargo room", "tin roof depot", "timber storage", "canal-side store",
]
# Padding for the drift tests: function words only, so no new repeated gram can
# appear and the repetition measurement stays about REPEAT and nothing else.
_PAD = ["và", "trong", "khi", "đã"]


def vi_line(index):
    i = (index - 1) % len(_NOUNS)
    return (
        f"{_NOUNS[i]} của hãng {_VERBS[i]} trong {REPEAT} ở Delft năm 1602, "
        f"{_TAILS[i]}."
    )


def en_line(index):
    return (
        f"Ledger {index} records the leaky warehouse at Delft in 1602 before "
        "anyone had counted the shares the investors actually sold."
    )


def scenes(count, vi=None, en=None, duration=6.0):
    vi = vi or vi_line
    en = en or en_line
    return (
        [
            {
                "index": i + 1,
                "situation": f"tình huống {i + 1}",
                "text": vi(i + 1),
                "prompt": "flat vector cartoon frame",
                "duration": duration,
                "is_key_scene": True,
            }
            for i in range(count)
        ],
        [
            {
                "index": i + 1,
                "situation": f"tình huống {i + 1}",
                "text": en(i + 1),
                "prompt": "flat vector cartoon frame",
                "duration": duration,
                "is_key_scene": True,
            }
            for i in range(count)
        ],
    )


class Editor:
    """Fake chat_json for the polish stage: answers with `mode` applied to each line."""

    def __init__(self, mode="clean", notes=(), error=None):
        self.mode = mode
        self.notes = list(notes)
        self.error = error
        self.systems = []
        self.user_prompts = []
        self.kwargs = []

    def __call__(self, system_prompt, user_prompt, **kw):
        if self.error:
            raise RuntimeError(self.error)
        self.systems.append(system_prompt)
        self.user_prompts.append(user_prompt)
        self.kwargs.append(kw)
        assert "editor lời bình" in system_prompt
        vi = {int(m[0]): m[1] for m in re.findall(r"\[(\d+)\] VI: (.*)", user_prompt)}
        en = {int(m[0]): m[1] for m in re.findall(r"\[(\d+)\] EN: (.*)", user_prompt)}
        rows = []
        for index in sorted(vi):
            new_vi, new_en = vi[index], en[index]
            if self.mode != "echo":
                # Every line gets its own wording: the editor does not swap one
                # repeated phrase for another.
                new_vi = new_vi.replace(
                    REPEAT, _SYNONYMS[(index - 1) % len(_SYNONYMS)]
                )
                new_en = new_en.replace(
                    "leaky warehouse", _EN_SYNONYMS[(index - 1) % len(_EN_SYNONYMS)]
                )
            if self.mode == "number":
                new_vi = new_vi.replace("1602", "1702")
            elif self.mode in ("grow2", "grow3", "grow4"):
                pad = int(self.mode[-1])
                new_vi = " ".join(new_vi.split() + _PAD[:pad])
            elif self.mode == "en_long":
                new_en = new_en.rstrip(".") + " and later"
            rows.append({"index": index, "voiceover_vi": new_vi, "voiceover_en": new_en})
        if self.mode == "missing":
            rows.pop()
        elif self.mode == "extra":
            rows.append(
                {"index": rows[-1]["index"] + 50, "voiceover_vi": vi_line(99),
                 "voiceover_en": en_line(99)}
            )
        return {"scenes": rows, "notes": self.notes}


@pytest.fixture
def factory():
    return ScriptFactory()


@pytest.fixture
def polish_call(monkeypatch):
    """Patch the gateway so the polish stage talks to the fake editor."""

    def install(editor):
        monkeypatch.setattr("pipeline.llm_client.chat_json", editor)
        return editor

    return install


def test_clean_polish_is_kept_and_rewrites_every_scene(factory, polish_call):
    vi, en = scenes(10)
    before = [sc["text"] for sc in vi]
    editor = polish_call(Editor("clean"))

    report = factory.polish_scenes(vi, en)

    assert report["accepted"] is True
    assert report["decision"] == "repetition_reduced_within_hard_qa"
    assert report["model"] == POLISH_MODEL
    assert report["chunks"] == 2 and report["calls"] == 2
    assert report["scenes_rewritten"] == 10
    assert report["repetition_hits_before"] > 0
    assert report["repetition_hits_after"] == 0
    assert report["repeat_drop_pct"] == 100.0
    assert all(REPEAT not in sc["text"] for sc in vi)
    assert vi[0]["text"] != before[0]
    assert en[0]["text"] != en_line(1)
    assert "rotting depot" in en[0]["text"]
    # The approved prompt is sent verbatim, alongside both languages.
    assert "QUY TẮC CỨNG" in editor.systems[0]
    assert '"voiceover_vi"' in editor.systems[0]
    assert "VI: " in editor.user_prompts[0] and "EN: " in editor.user_prompts[0]
    assert editor.kwargs[0]["model"] == POLISH_MODEL


def test_polish_addresses_each_chunk_by_absolute_index(factory, polish_call):
    vi, en = scenes(20)
    editor = polish_call(Editor("clean"))

    factory.polish_scenes(vi, en)

    first = re.findall(r"\[(\d+)\] VI:", editor.user_prompts[0])
    second = re.findall(r"\[(\d+)\] VI:", editor.user_prompts[1])
    assert [int(i) for i in first] == list(range(1, 9))
    assert [int(i) for i in second] == list(range(9, 17))
    assert "Bài hoàn chỉnh có 20 cảnh" in editor.user_prompts[0]


def test_notes_from_the_editor_are_carried_into_the_report(factory, polish_call):
    vi, en = scenes(8)
    notes = [{"index": 4, "issue": "mâu thuẫn số liệu", "detail": "16% vs 8%"}]
    polish_call(Editor("clean", notes=notes))

    report = factory.polish_scenes(vi, en)

    assert report["accepted"] is True
    assert report["notes"] == [{**notes[0], "chunk_first_index": 1}]


def test_a_scene_that_moves_a_number_rejects_the_whole_polish(factory, polish_call):
    vi, en = scenes(17)
    before = [sc["text"] for sc in vi]
    polish_call(Editor("number"))

    report = factory.polish_scenes(vi, en)

    assert report["accepted"] is False
    assert report["decision"] == "rejected_contract"
    assert report["rejection"]["index"] == 1
    assert "locked_tokens" in report["rejection"]["detail"]
    # Not one scene of the good chunks was kept either.
    assert [sc["text"] for sc in vi] == before


def test_a_dropped_scene_rejects_the_whole_polish(factory, polish_call):
    vi, en = scenes(10)
    before = [sc["text"] for sc in vi]
    polish_call(Editor("missing"))

    report = factory.polish_scenes(vi, en)

    assert report["decision"] == "rejected_scene_count"
    assert report["rejection"]["stage"] == "scene_count"
    assert [sc["text"] for sc in vi] == before


def test_an_extra_scene_rejects_the_whole_polish(factory, polish_call):
    vi, en = scenes(10)
    before = [sc["text"] for sc in vi]
    polish_call(Editor("extra"))

    report = factory.polish_scenes(vi, en)

    assert report["decision"] == "rejected_scene_count"
    assert [sc["text"] for sc in vi] == before


def test_a_polish_that_adds_three_words_is_still_inside_the_contract(factory, polish_call):
    vi, en = scenes(9)
    polish_call(Editor("grow3"))

    report = factory.polish_scenes(vi, en)

    assert report["accepted"] is True
    assert report["scenes_rewritten"] == 9


def test_a_line_grown_past_three_words_is_rejected(factory, polish_call):
    vi, en = scenes(9)
    before = [sc["text"] for sc in vi]
    polish_call(Editor("grow4"))

    report = factory.polish_scenes(vi, en)

    assert report["decision"] == "rejected_contract"
    assert report["rejection"]["detail"].startswith("vi_drift:4")
    assert [sc["text"] for sc in vi] == before


def test_a_polish_that_breaks_the_word_budget_is_reverted(factory, polish_call):
    vi, en = scenes(9)
    before_en = [sc["text"] for sc in en]
    polish_call(Editor("en_long"))

    report = factory.polish_scenes(vi, en)

    assert report["accepted"] is False
    assert report["offenders_before"] == 0
    assert report["decision"] == "hard_qa_regressed:word_budget"
    assert report["offenders_after"] > report["offenders_before"]
    assert [sc["text"] for sc in en] == before_en


def test_a_polish_that_keeps_the_repeats_is_not_worth_shipping(factory, polish_call):
    vi, en = scenes(10)
    before = [sc["text"] for sc in vi]
    polish_call(Editor("echo"))

    report = factory.polish_scenes(vi, en)

    assert report["accepted"] is False
    assert report["decision"] == "repetition_not_reduced"
    assert report["rejection"] is None
    assert report["repetition_hits_after"] == report["repetition_hits_before"]
    assert [sc["text"] for sc in vi] == before


def test_kept_polish_never_plans_a_scene_faster_than_it_reads(factory, polish_call):
    vi, en = scenes(9)
    polish_call(Editor("grow2"))

    report = factory.polish_scenes(vi, en)

    assert report["accepted"] is True
    assert report["planned_duration_after"] >= report["planned_duration_before"]
    for scene in vi:
        words = len(scene["text"].split())
        assert scene["duration"] >= min(8.0, words / 3.5) - 1e-6
        assert scene["duration"] <= 8.0


def test_a_gateway_failure_keeps_the_internal_script(factory, polish_call):
    vi, en = scenes(9)
    before = [sc["text"] for sc in vi]
    polish_call(Editor("clean", error="gateway 503"))

    report = factory.polish_scenes(vi, en)

    assert report["decision"] == "rejected_call"
    assert report["accepted"] is False
    assert [sc["text"] for sc in vi] == before


def test_polish_needs_two_scripts_to_run(factory):
    assert factory.polish_scenes([], []) == {
        "applicable": False,
        "accepted": False,
        "decision": "no scenes",
    }


def test_locked_tokens_hold_numbers_and_all_caps_names():
    assert _locked_tokens("ASML đạt 16% năm 1602") == _locked_tokens("16% — ASML, 1602")
    assert _locked_tokens("ASML đạt 16%") != _locked_tokens("hãng đạt 16%")


def test_polish_violations_accepts_an_equal_length_rewrite():
    assert _polish_violations(vi_line(1), en_line(1), vi_line(1), en_line(1)) == []
    assert _polish_violations(vi_line(1), en_line(1), "", en_line(1)) == ["empty"]
    kinds = _polish_violations(vi_line(1), en_line(1), vi_line(1).replace("1602", "1603"), en_line(1))
    assert any(kind.startswith("locked_tokens") for kind in kinds)


def test_mechanical_metrics_after_polish_describe_the_shipped_script(factory, polish_call):
    vi, en = scenes(10)
    polish_call(Editor("clean"))

    report = factory.polish_scenes(vi, en)

    assert report["accepted"] is True
    assert report["metrics_after"]["repeat_hits"] == 0
    assert report["metrics_after"]["scene_count"] == 10
    assert math.isclose(
        report["metrics_after"]["total_duration_sec"],
        round(sum(sc["duration"] for sc in vi), 1),
    )
