from services.notifier import TelegramNotifier


def test_oversized_callback_data_is_dropped_instead_of_failing_the_send():
    """Telegram caps callback_data at 64 bytes and rejects the whole message."""
    markup = {
        "inline_keyboard": [[
            {"text": "Duyệt", "callback_data": "approve_char:remake_1792818686_7fb1a8:người_dẫn_chuyện_(narrator)"},
            {"text": "Tiến độ", "callback_data": "gates:remake_1792818686_7fb1a8"},
        ]]
    }

    cleaned = TelegramNotifier._sanitize_markup(markup)

    assert cleaned == {
        "inline_keyboard": [[{"text": "Tiến độ", "callback_data": "gates:remake_1792818686_7fb1a8"}]]
    }


def test_markup_drops_to_none_when_every_button_is_invalid():
    markup = {"inline_keyboard": [[{"text": "x", "callback_data": "a" * 65}]]}

    assert TelegramNotifier._sanitize_markup(markup) is None


def test_reply_keyboard_is_left_alone():
    markup = {"keyboard": [[{"text": "📊 /status"}]], "resize_keyboard": True}

    assert TelegramNotifier._sanitize_markup(markup) is markup
