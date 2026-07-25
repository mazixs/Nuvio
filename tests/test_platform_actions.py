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


def test_main_action_cache_keys_cover_rutube_and_vk():
    assert cache_key_for_main_action("rutube", "rutube_download") == "direct_video"
    assert cache_key_for_main_action("vk", "vk_download") == "direct_video"


def test_main_action_cache_keys_cover_audio_actions():
    assert cache_key_for_main_action("tiktok", "tiktok_audio") == "tiktok_audio"
    assert cache_key_for_main_action("instagram", "instagram_audio") == "instagram_audio"
    assert cache_key_for_main_action("rutube", "rutube_audio") == "rutube_audio"
    assert cache_key_for_main_action("vk", "vk_audio") == "vk_audio"


def test_every_main_action_has_a_cache_key():
    """Ни одно действие главного меню не должно оставаться без ключа кэша."""
    actions = [
        ("tiktok", "tiktok_download"),
        ("tiktok", "tiktok_audio"),
        ("instagram", "instagram_download"),
        ("instagram", "instagram_audio"),
        ("rutube", "rutube_download"),
        ("rutube", "rutube_audio"),
        ("vk", "vk_download"),
        ("vk", "vk_audio"),
        ("youtube", "tg_video"),
    ]

    without_key = [
        action for platform, action in actions
        if cache_key_for_main_action(platform, action) is None
    ]

    assert without_key == []
