"""Тесты каскада субтитров и кнопок возврата.

Раньше субтитры были одной кнопкой «Скачать субтитры (SRT)»: язык подбирался
за пользователя, формат был один. Теперь это два экрана — язык, затем формат, —
и с каждого можно вернуться назад.
"""

import pytest

from utils import telegram_utils


TOKEN = "abcd1234"
BOTH = {"subtitles": {"ru": [{}], "en": [{}]}}
ONLY_AUTO = {"automatic_captions": {"en-US": [{}]}}

pytestmark = pytest.mark.unit


def _labels(markup):
    return [button.text for row in markup.inline_keyboard for button in row]


def _callbacks(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def test_language_menu_offers_both_with_flags():
    markup = telegram_utils._build_subtitle_language_menu(BOTH, TOKEN)

    assert _labels(markup)[:2] == ["🇷🇺 Русский", "🇬🇧 English"]


def test_language_menu_marks_automatic_track():
    markup = telegram_utils._build_subtitle_language_menu(ONLY_AUTO, TOKEN)

    assert _labels(markup)[0] == "🇬🇧 English (авто)"


def test_language_menu_is_absent_without_subtitles():
    assert telegram_utils._build_subtitle_language_menu({}, TOKEN) is None


def test_language_menu_can_go_back():
    assert "⬅️ Назад" in _labels(telegram_utils._build_subtitle_language_menu(BOTH, TOKEN))


def test_format_menu_offers_three_formats():
    markup = telegram_utils._build_subtitle_format_menu("ru", TOKEN)

    assert _labels(markup)[:3] == ["SRT", "VTT", "Текст"]


def test_format_menu_carries_the_chosen_language():
    markup = telegram_utils._build_subtitle_format_menu("en", TOKEN)

    assert any(callback.endswith("|subs|en:vtt") for callback in _callbacks(markup))


def test_format_menu_returns_to_the_language_choice():
    """Назад с формата ведёт к языкам, а не сразу в главное меню."""
    markup = telegram_utils._build_subtitle_format_menu("ru", TOKEN)

    assert any(
        callback.endswith("|main|subtitles") for callback in _callbacks(markup)
    ), _callbacks(markup)


# --- отмена -----------------------------------------------------------------


def test_every_main_menu_offers_cancel():
    """Передумал после ссылки — должен быть выход, а не выбор из загрузок."""
    for platform in ("youtube", "tiktok", "instagram", "rutube", "vk"):
        _text, markup = telegram_utils._build_main_menu(
            platform, {"title": "видео"}, TOKEN, {"audio_only": [{}]}
        )

        assert "❌ Отмена" in _labels(markup), platform


def test_cancel_button_carries_the_session():
    _text, markup = telegram_utils._build_main_menu(
        "youtube", {"title": "видео"}, TOKEN, {}
    )

    assert f"s|{TOKEN}|main|cancel" in _callbacks(markup)


def test_waiting_screen_offers_cancel():
    """Пока идёт разбор ссылки, отмена — единственное осмысленное действие."""
    markup = telegram_utils._build_cancel_markup(TOKEN)

    assert _labels(markup) == ["❌ Отмена"]
    assert _callbacks(markup) == [f"s|{TOKEN}|main|cancel"]
