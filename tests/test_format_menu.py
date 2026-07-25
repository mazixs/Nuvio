"""Тесты каскадного меню форматов.

Прежнее меню было плоским списком с жёсткими лимитами «не более 3 combined, не
более 3 без звука, не более 2 аудио» и дедупликацией по тексту кнопки: из 22
доступных форматов пользователь видел шесть, а два формата одного разрешения
скрывали друг друга. Здесь закреплено, что выбор идёт по разделам и ни одно
проходное разрешение не теряется.
"""

import pytest

from utils import telegram_utils


MB = 1024 * 1024
TOKEN = "abcd1234"

# Форматы реального видео (youtube.com/watch?v=NXNazpSQl6Q).
FORMATS = {
    "video_only": [
        {"format_id": "401", "height": 2160, "ext": "mp4", "vcodec": "av01.0.13M.08", "filesize": int(467.6 * MB)},
        {"format_id": "400", "height": 1440, "ext": "mp4", "vcodec": "av01.0.12M.08", "filesize": int(216.3 * MB)},
        {"format_id": "299", "height": 1080, "ext": "mp4", "vcodec": "avc1.64002a", "filesize": int(301.2 * MB)},
        {"format_id": "303", "height": 1080, "ext": "webm", "vcodec": "vp9", "filesize": int(135.1 * MB)},
        {"format_id": "298", "height": 720, "ext": "mp4", "vcodec": "avc1.640020", "filesize": int(183.9 * MB)},
    ],
    "audio_only": [
        {"format_id": "140", "ext": "m4a", "acodec": "mp4a.40.2", "filesize": int(29.7 * MB)},
        {"format_id": "251", "ext": "webm", "acodec": "opus", "filesize": int(25.0 * MB)},
    ],
    "combined": [
        {"format_id": "18", "height": 360, "ext": "mp4", "vcodec": "avc1.42001E", "filesize": int(61.7 * MB)},
    ],
}

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def local_delivery_budget(monkeypatch):
    """Меню считает лимит доставки, а в тестах режим по умолчанию облачный."""
    monkeypatch.setattr(telegram_utils, "MAX_FILE_SIZE", 2000 * MB)


def _labels(markup):
    return [button.text for row in markup.inline_keyboard for button in row]


def _callbacks(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def test_more_menu_offers_three_sections():
    markup = telegram_utils._build_more_menu(FORMATS, TOKEN)

    assert _labels(markup) == ["🎬 Видео", "🎵 Аудио", "📝 Субтитры", "⬅️ Назад"]


def test_more_menu_hides_audio_section_without_sound():
    markup = telegram_utils._build_more_menu({"combined": FORMATS["combined"]}, TOKEN)

    assert "🎵 Аудио" not in _labels(markup)


def test_video_menu_shows_every_resolution_once():
    """Два формата 1080p раньше скрывали друг друга — теперь разрешение одно."""
    markup = telegram_utils._build_video_menu(FORMATS, TOKEN)

    resolutions = [label for label in _labels(markup) if label.endswith("МБ")]
    assert [label.split(" · ")[0] for label in resolutions] == [
        "2160p",
        "1440p",
        "1080p",
        "720p",
        "360p",
    ]


def test_video_menu_shows_the_size_of_each_choice():
    markup = telegram_utils._build_video_menu(FORMATS, TOKEN)

    assert "1080p · 331 МБ" in _labels(markup)


def test_video_menu_asks_for_the_telegram_ready_pair():
    markup = telegram_utils._build_video_menu(FORMATS, TOKEN)

    assert any(callback.endswith("|combined|299+140") for callback in _callbacks(markup))


def test_video_menu_reports_nothing_fits(monkeypatch):
    """Пустой экран не годится: если ничего не проходит, нужен явный None."""
    assert telegram_utils._build_video_menu({}, TOKEN) is None

    # Облачный лимит 50 МБ: ни одна пара из этого набора в него не влезает.
    monkeypatch.setattr(telegram_utils, "MAX_FILE_SIZE", 50 * MB)
    assert telegram_utils._build_video_menu(FORMATS, TOKEN) is None


def test_audio_menu_offers_only_the_native_track():
    """Opus Telegram аудио не считает, поэтому в меню только M4A."""
    markup = telegram_utils._build_audio_menu(FORMATS, TOKEN)

    assert [label for label in _labels(markup) if label.startswith("🎵")] == [
        "🎵 M4A · 30 МБ"
    ]


def test_audio_menu_falls_back_to_transcoding_when_nothing_is_native():
    only_opus = {
        "audio_only": [
            {"format_id": "251", "ext": "webm", "acodec": "opus", "filesize": 25 * MB}
        ]
    }

    markup = telegram_utils._build_audio_menu(only_opus, TOKEN)

    assert "🎵 Звук (перекодируем в M4A)" in _labels(markup)


def test_audio_menu_is_absent_without_any_sound():
    assert telegram_utils._build_audio_menu({"combined": FORMATS["combined"]}, TOKEN) is None


def test_main_menu_hides_the_audio_button_for_a_silent_video():
    _text, markup = telegram_utils._build_main_menu(
        "youtube", {"title": "без звука"}, TOKEN, {"combined": FORMATS["combined"]}
    )

    assert "🎵 Скачать только звук" not in _labels(markup)


def test_main_menu_keeps_the_audio_button_when_sound_exists():
    _text, markup = telegram_utils._build_main_menu(
        "youtube", {"title": "со звуком"}, TOKEN, FORMATS
    )

    assert "🎵 Скачать только звук" in _labels(markup)


def test_removed_buttons_are_gone_for_good():
    """«Лучшее качество», «Лучшее аудио», MP3 и «без звука» удалены осознанно."""
    labels = _labels(telegram_utils._build_more_menu(FORMATS, TOKEN))
    labels += _labels(telegram_utils._build_video_menu(FORMATS, TOKEN))
    labels += _labels(telegram_utils._build_audio_menu(FORMATS, TOKEN))
    joined = " ".join(labels)

    assert "Лучшее" not in joined
    assert "MP3" not in joined
    assert "без звука" not in joined


def test_small_track_size_is_shown_in_kilobytes():
    """«0 МБ» на дорожке в полмегабайта читается как ошибка."""
    tiny = {
        "audio_only": [
            {"format_id": "139", "ext": "m4a", "acodec": "mp4a.40.5", "filesize": 480 * 1024}
        ]
    }

    markup = telegram_utils._build_audio_menu(tiny, TOKEN)

    assert "🎵 M4A · 480 КБ" in _labels(markup)


def test_vertical_video_menu_uses_familiar_resolutions():
    """Shorts показывались как «1920p»: пользователь знает это видео как 1080p."""
    shorts = {
        "video_only": [
            {"format_id": "299", "height": 1920, "width": 1080, "ext": "mp4",
             "vcodec": "avc1", "filesize": 27 * MB},
            {"format_id": "298", "height": 1280, "width": 720, "ext": "mp4",
             "vcodec": "avc1", "filesize": 17 * MB},
        ],
        "audio_only": FORMATS["audio_only"],
        "combined": [],
    }

    labels = _labels(telegram_utils._build_video_menu(shorts, TOKEN))

    assert any(label.startswith("1080p") for label in labels), labels
    assert not any(label.startswith("1920p") for label in labels), labels
