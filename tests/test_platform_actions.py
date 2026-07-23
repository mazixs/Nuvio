"""Тесты чистых платформенных решений."""

from utils.platform_actions import (
    cache_key_for_format_selection,
    cache_key_for_main_action,
)


def test_main_action_cache_keys_are_explicit():
    assert cache_key_for_main_action("tiktok", "tiktok_download") == "direct_video"
    assert cache_key_for_main_action("instagram", "instagram_download") == "direct_video"
    assert cache_key_for_main_action("youtube", "tg_video") == "tg_video"
    assert cache_key_for_main_action("youtube", "audio_m4a") is None


def test_format_selection_cache_keys_preserve_scope():
    assert cache_key_for_format_selection("combined", "18") == "combined:18"
    assert cache_key_for_format_selection("video_only", "137") == "video_only:137"
    assert cache_key_for_format_selection("best", "ignored") == "best"
    assert cache_key_for_format_selection("audio_only", "140") is None
