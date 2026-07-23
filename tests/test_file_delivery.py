"""Тесты чистого выбора способа доставки."""

from utils.file_delivery import media_kind_for_suffix


def test_media_kind_is_selected_by_suffix():
    assert media_kind_for_suffix(".mp4") == "video"
    assert media_kind_for_suffix(".WEBM") == "video"
    assert media_kind_for_suffix(".mp3") == "audio"
    assert media_kind_for_suffix(".m4a") == "audio"
    assert media_kind_for_suffix(".jpg") == "document"
