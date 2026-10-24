"""Tests for the Vietnamese machine hook scorer (services.hook_scoring)."""

from services.hook_scoring import score_hook


STRONG_HOOK = (
    "84% tiền trong ngân hàng không tồn tại vật lý. Bạn đang cầm tờ tiền đó trong tay. "
    "Điều gì xảy ra khi tất cả cùng đến rút?"
)

WEAK_HOOK = "Hôm nay chúng ta sẽ nói về ngân hàng cực kỳ tuyệt vời nhé."


def test_strong_hook_scores_high_and_names_the_leak():
    report = score_hook(STRONG_HOOK)

    assert report["specificity"] >= 55  # 84% is concrete
    assert report["address"] >= 45  # "bạn" inside the first six words
    assert report["curiosity"] >= 55  # open question mark + "điều gì"
    assert report["brevity"] == 100  # inside the 9-28 word band
    assert 0 <= report["verdict"] <= 100
    assert report["weakest"] in {"specificity", "address", "stakes", "curiosity", "brevity"}


def test_vague_channel_intro_scores_low():
    report = score_hook(WEAK_HOOK)

    assert report["specificity"] < 40  # vague + filler, no number
    assert report["brevity"] == 100  # 14 words sits inside the 9-28 band
    assert report["verdict"] < score_hook(STRONG_HOOK)["verdict"]  # weak everywhere else


def test_stakes_and_closed_loop_penalties():
    with_stake = score_hook("Bạn sẽ mất 3 triệu đô nếu rút tiền đúng ngày này. Tại sao?")
    no_stake = score_hook("Bạn có thể rút tiền vào ngày này. Bạn thấy sao?")

    assert with_stake["stakes"] > no_stake["stakes"]

    closed = score_hook("97% ngân hàng phá sản vì vậy hệ thống an toàn. Bạn thấy đúng không?")
    assert closed["curiosity"] < 60  # resolves its own question


def test_brevity_band_follows_the_hook_window():
    in_band = score_hook(" ".join(["từ"] * 28))
    over_band = score_hook(" ".join(["từ"] * 40))
    under_band = score_hook(" ".join(["từ"] * 4))

    assert in_band["brevity"] == 100
    assert 0 < over_band["brevity"] < 100
    assert 0 < under_band["brevity"] < 100


def test_empty_text_is_safe():
    report = score_hook("")

    assert report["verdict"] >= 0
    assert report["weakest"] in {"specificity", "address", "stakes", "curiosity", "brevity"}
