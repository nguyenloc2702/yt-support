import pytest
from services.english_script_normalizer import (
    EnglishScriptNormalizer,
    year_to_words,
    normalize_english_narration,
)

def test_year_to_words():
    assert year_to_words(1966) == "nineteen sixty-six"
    assert year_to_words(1993) == "nineteen ninety-three"
    assert year_to_words(1995) == "nineteen ninety-five"
    assert year_to_words(2000) == "two thousand"
    assert year_to_words(2003) == "two thousand three"
    assert year_to_words(2008) == "two thousand eight"
    assert year_to_words(2020) == "twenty twenty"
    assert year_to_words(2023) == "twenty twenty-three"
    assert year_to_words(2025) == "twenty twenty-five"

def test_vietnamese_leak_removal():
    normalizer = EnglishScriptNormalizer()
    
    # Currency leak
    report = normalizer.normalize_sentence("Warren Buffett signed a check worth more than two billion dong.")
    assert "two hundred thirty million dollars" in report.normalized_text
    assert "dong" not in report.normalized_text.lower()
    
    # Accented character detection
    report_accent = normalizer.normalize_sentence("The founder received 10 tỷ đồng from his cousin.")
    assert report_accent.total_modifications > 0
    assert "dong" not in report_accent.normalized_text.lower()

def test_phonetic_proper_noun_substitutions():
    normalizer = EnglishScriptNormalizer()
    
    # Qinchuan -> Chinchuan
    res1 = normalizer.normalize_sentence("In 2003, Wang announced the Qinchuan Automobile purchase.")
    assert "Chinchuan Automobile" in res1.normalized_text
    assert "two thousand three" in res1.normalized_text
    
    # Lou Shiangyang -> Lu Xiangyang
    res2 = normalizer.normalize_sentence("He made a call directly to his cousin Lou Shiangyang.")
    assert "Lu Xiangyang" in res2.normalized_text
    
    # Wang Chuanfu -> Wang Chuan-fu
    res3 = normalizer.normalize_sentence("Wang Chuanfu founded the battery empire.")
    assert "Wang Chuan-fu" in res3.normalized_text
    
    # F-150 -> F-one-fifty
    res4 = normalizer.normalize_sentence("Ford said the F-150 Lightning would use LFP cells.")
    assert "F-one-fifty" in res4.normalized_text

def test_clean_english_untouched():
    normalizer = EnglishScriptNormalizer()
    clean_text = "Tesla sold fifty-thousand-dollar dreams while BYD served everyday needs."
    report = normalizer.normalize_sentence(clean_text)
    assert report.normalized_text == clean_text
    assert report.total_modifications == 0
    assert report.is_clean is True

def test_normalize_shots_plan():
    normalizer = EnglishScriptNormalizer()
    shots = [
        {"index": 1, "text": "In 1966, Anhui was poor."},
        {"index": 2, "text": "Wang acquired Qinchuan Automobile in 2003."},
        {"index": 3, "text": "Warren Buffett signed a check worth more than two billion dong."}
    ]
    norm_shots, stats = normalizer.normalize_shots_plan(shots)
    assert stats["modified_scenes_count"] == 3
    assert norm_shots[0]["text"] == "In nineteen sixty-six, Anhui was poor."
    assert norm_shots[1]["text"] == "Wang acquired Chinchuan Automobile in two thousand three."
    assert norm_shots[2]["text"] == "Warren Buffett signed a check worth two hundred thirty million dollars."
    assert stats["is_100_percent_clean_english"] is True
