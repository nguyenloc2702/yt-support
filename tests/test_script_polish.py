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


def punchy_line(index):
    """Three sentences, two of them short: the rhythm QA S07 asks for."""
    i = (index - 1) % len(_NOUNS)
    return (
        f"{_NOUNS[i]} của hãng {_VERBS[i]} ngay giữa kho hàng cũ kỹ của thành Delft. "
        "Bốn đêm liền. Không ai hay."
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
            elif self.mode == "grow_one":
                if index == 3:
                    new_vi = " ".join(new_vi.split() + _PAD[:4])
            elif self.mode == "en_long":
                new_en = new_en.rstrip(".") + " and later"
            elif self.mode == "en_edge_one":
                # Legal drift (+3 words), illegal band: the scene sat one word off
                # the EN ceiling, so the extra words make it a QA offender.
                if index == 3:
                    new_en = new_en.rstrip(".") + " and later"
            elif self.mode == "flatten":
                # A prose editor's favourite habit: weld the punch lines into one
                # flowing sentence. Same words, no short sentence left.
                new_vi = re.sub(
                    r"\. ([A-ZÀ-Ỹ])", lambda m: ", " + m.group(1).lower(), new_vi
                )
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
    assert report["decision"] == "polish_within_hard_qa"
    assert report["model"] == POLISH_MODEL
    assert report["chunks"] == 2 and report["calls"] == 2
    assert report["scenes_rewritten"] == 10
    assert report["scenes_changed"] == 10
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


def test_an_editor_that_moves_every_number_is_not_an_editor(factory, polish_call):
    """Scene-level drops still cannot ship a rewrite that fails everywhere."""
    vi, en = scenes(17)
    before = [sc["text"] for sc in vi]
    editor = polish_call(Editor("number"))

    report = factory.polish_scenes(vi, en)

    assert report["accepted"] is False
    assert report["decision"] == "rejected_scene_drops"
    assert report["scenes_skipped"] == 17
    assert "locked_tokens" in report["rejection"]["detail"]
    # every chunk asked twice: the first answer, then the same answer again
    assert report["calls"] == 6 and report["retries"] == 3
    assert "[1] VI 18 từ / EN 20 từ" in editor.user_prompts[0]
    assert "LẦN TRƯỚC bản này bị LOẠI" in editor.user_prompts[1]
    assert "[1] locked_tokens" in editor.user_prompts[1]
    assert [sc["text"] for sc in vi] == before


def test_one_broken_scene_drops_while_its_neighbours_are_kept(factory, polish_call):
    """The contract is per scene, so one stubborn line must not cost 130 good ones."""
    vi, en = scenes(9)
    before = [sc["text"] for sc in vi]
    polish_call(Editor("grow_one"))

    report = factory.polish_scenes(vi, en)

    assert report["accepted"] is True, report
    assert report["scenes_rewritten"] == 8
    assert report["scenes_skipped"] == 1
    assert report["skipped"][0]["index"] == 3
    assert report["skipped"][0]["detail"].startswith("vi_drift:4")
    assert vi[2]["text"] == before[2]
    assert all(REPEAT not in sc["text"] for i, sc in enumerate(vi) if i != 2)


def test_a_polish_that_adds_three_words_is_still_inside_the_contract(factory, polish_call):
    vi, en = scenes(9)
    polish_call(Editor("grow3"))

    report = factory.polish_scenes(vi, en)

    assert report["accepted"] is True
    assert report["scenes_rewritten"] == 9


def test_a_line_grown_past_three_words_is_dropped(factory, polish_call):
    vi, en = scenes(9)
    before = [sc["text"] for sc in vi]
    polish_call(Editor("grow4"))

    report = factory.polish_scenes(vi, en)

    assert report["accepted"] is False
    assert report["decision"] == "rejected_scene_drops"
    assert "vi_drift:4" in report["skipped"][0]["detail"]
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


def test_a_polish_that_walks_every_scene_to_the_band_edge_is_refused(factory, polish_call):
    """The live failure: 2 offenders became 13 because +3 words is legal drift."""
    vi, en = scenes(9)
    before_en = [sc["text"] for sc in en]
    polish_call(Editor("en_long"))

    report = factory.polish_scenes(vi, en)

    assert report["accepted"] is False
    assert report["offenders_before"] == 0
    assert report["decision"] == "rejected_scene_drops"
    assert report["scenes_skipped"] == 5
    assert "budget:en_high" in report["skipped"][0]["detail"]
    assert [sc["text"] for sc in en] == before_en


def test_a_scene_pushed_over_the_band_edge_is_dropped_and_the_rest_ships(
    factory, polish_call
):
    """One legal-drift-but-over-band line must cost that line, not the script."""
    vi, en = scenes(9)
    before_en = [sc["text"] for sc in en]
    polish_call(Editor("en_edge_one"))

    report = factory.polish_scenes(vi, en)

    assert report["accepted"] is True, report
    assert report["scenes_rewritten"] == 8
    assert report["skipped"][0]["detail"].startswith("budget:en_high")
    assert report["offenders_after"] == report["offenders_before"] == 0
    assert en[2]["text"] == before_en[2]


def test_a_polish_that_welds_the_punch_lines_shut_is_reverted(factory, polish_call):
    """QA S07 grades the short-sentence share: green before, red after is a regress."""
    vi, en = scenes(9, vi=punchy_line)
    before = [sc["text"] for sc in vi]
    polish_call(Editor("flatten"))

    report = factory.polish_scenes(vi, en)

    assert report["naturalness_before"]["punch_pct"] >= 35.0
    assert report["naturalness_after"]["punch_pct"] < 35.0
    assert report["accepted"] is False
    assert report["decision"] == "hard_qa_regressed:short_sentences"
    assert [sc["text"] for sc in vi] == before


def test_a_polish_under_an_already_red_s07_is_still_judged_on_qa(factory, polish_call):
    """The script came in under the S07 floor, so the editor cannot regress it.

    This is the live shape: the draft sits at 28% short sentences and the prose
    editor naturally lengthens clauses. Grading the raw number would be a gate
    the scorecard does not run, and it is what threw away a 115-scene rewrite.
    """
    vi, en = scenes(9, vi=punchy_line)
    polish_call(Editor("flatten"))

    lenient = ScriptFactory()
    report = lenient.polish_scenes(vi, en)
    assert report["decision"] == "hard_qa_regressed:short_sentences"

    vi2, en2 = scenes(9)
    polish_call(Editor("clean"))
    shipped = ScriptFactory().polish_scenes(vi2, en2)
    assert shipped["naturalness_before"]["punch_pct"] < 35.0
    assert shipped["naturalness_after"]["punch_pct"] < 35.0
    assert shipped["accepted"] is True


def test_a_polish_that_changes_no_words_is_not_worth_shipping(factory, polish_call):
    vi, en = scenes(10)
    before = [sc["text"] for sc in vi]
    polish_call(Editor("echo"))

    report = factory.polish_scenes(vi, en)

    assert report["accepted"] is False
    assert report["decision"] == "editor_changed_nothing"
    assert report["scenes_changed"] == 0
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


def test_locked_tokens_are_the_value_and_not_the_typography():
    """Thousands grouping and a sentence-final full stop are formatting."""
    assert _locked_tokens("50,000 đơn hàng") == _locked_tokens("50.000 đơn hàng")
    assert _locked_tokens("Tương lai AI.") == _locked_tokens("tương lai AI")
    assert _locked_tokens("lãi 2,5%") != _locked_tokens("lãi 25%")
    assert _locked_tokens("EUV") == _locked_tokens("EUV.")
    assert _locked_tokens("EUV") != _locked_tokens("euv")


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
