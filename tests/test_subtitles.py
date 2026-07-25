"""Тесты выбора субтитров: язык, формат и превращение SRT в текст."""

import pytest

from utils.subtitles import (
    SUBTITLE_FORMATS,
    available_subtitle_languages,
    parse_subtitle_choice,
    srt_to_text,
)


pytestmark = pytest.mark.unit


def test_both_languages_are_offered_when_both_exist():
    info = {"subtitles": {"ru": [{}], "en": [{}], "de": [{}]}}

    languages = available_subtitle_languages(info)

    assert [language.code for language in languages] == ["ru", "en"]
    assert languages[0].label.startswith("🇷🇺")
    assert languages[1].label.startswith("🇬🇧")


def test_only_available_language_is_offered():
    info = {"subtitles": {"en": [{}]}}

    assert [language.code for language in available_subtitle_languages(info)] == ["en"]


def test_automatic_captions_count_as_available():
    """Автоперевод — это тоже субтитры; скрывать его значит терять их вовсе."""
    info = {"subtitles": {}, "automatic_captions": {"ru": [{}]}}

    languages = available_subtitle_languages(info)

    assert [language.code for language in languages] == ["ru"]
    assert languages[0].is_auto is True


def test_manual_track_wins_over_automatic():
    info = {"subtitles": {"ru": [{}]}, "automatic_captions": {"ru": [{}]}}

    assert available_subtitle_languages(info)[0].is_auto is False


def test_automatic_label_says_so():
    info = {"automatic_captions": {"en": [{}]}}

    assert "авто" in available_subtitle_languages(info)[0].label.lower()


def test_video_without_subtitles_offers_nothing():
    assert available_subtitle_languages({"subtitles": {}}) == []
    assert available_subtitle_languages({}) == []


def test_regional_variants_count_as_the_language():
    """У YouTube автоперевод часто приходит как `ru-orig` или `en-US`."""
    info = {"automatic_captions": {"en-US": [{}], "ru-orig": [{}]}}

    codes = {language.code for language in available_subtitle_languages(info)}

    assert codes == {"ru", "en"}


# --- разбор выбора ----------------------------------------------------------


def test_choice_is_parsed_into_language_and_format():
    assert parse_subtitle_choice("ru:srt") == ("ru", "srt")


@pytest.mark.parametrize("value", ["ru", "ru:", ":srt", "ru:doc", "de:srt", "", "a:b:c"])
def test_unknown_choice_is_rejected(value):
    """Значение приходит из callback_data — доверять ему нельзя."""
    assert parse_subtitle_choice(value) is None


def test_every_offered_format_parses():
    for subtitle_format in SUBTITLE_FORMATS:
        assert parse_subtitle_choice(f"en:{subtitle_format}") == ("en", subtitle_format)


# --- SRT в текст ------------------------------------------------------------

SRT = """1
00:00:01,000 --> 00:00:03,000
Привет, это первая строка

2
00:00:03,500 --> 00:00:06,000
<i>А это вторая</i>

3
00:00:06,000 --> 00:00:08,000
А это вторая
"""


def test_text_keeps_only_the_words():
    text = srt_to_text(SRT)

    assert "00:00:01" not in text
    assert "-->" not in text
    assert text.startswith("Привет, это первая строка")


def test_text_drops_markup():
    assert "<i>" not in srt_to_text(SRT)


def test_text_collapses_repeated_lines():
    """Автосубтитры повторяют строку в каждом кадре — читать это невозможно."""
    assert srt_to_text(SRT).count("А это вторая") == 1


def test_empty_input_gives_empty_text():
    assert srt_to_text("") == ""
