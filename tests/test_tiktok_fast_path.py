"""Тесты быстрого пути TikTok: разбор ответа резолвера без сети.

Формы полезной нагрузки скопированы с реальных ответов резолвера
(см. docs/technical/latency-disk-network-research.md). URL заменены на
example.test, чтобы тесты не ходили в сеть и не зависели от подписей.
"""

import pytest

from utils.tiktok_fast_path import (
    FastPathUnavailable,
    audio_matches_video,
    fits_url_handoff,
    parse_fast_media,
)


def _payload(**overrides):
    """Ответ резолвера для обычного видео (форма реального ответа)."""
    data = {
        "id": "7639073022762175764",
        "title": "",
        "duration": 60,
        "size": 5576522,
        "wm_size": 5979791,
        "play": "https://cdn.example.test/play.mp4",
        "wmplay": "https://cdn.example.test/wmplay.mp4",
        "music": "https://cdn.example.test/music.mp3",
        "cover": "https://cdn.example.test/cover.jpg",
        "origin_cover": "https://cdn.example.test/origin.jpg",
        "author": {"unique_id": "user1509008012081", "nickname": "Кто-то"},
        "music_info": {
            "title": "original sound - user1509008012081",
            "duration": 60,
            "original": True,
            "play": "https://cdn.example.test/music.mp3",
        },
    }
    data.update(overrides)
    return {"code": 0, "msg": "success", "data": data}


# === parse_fast_media ===


@pytest.mark.unit
def test_parse_fast_media_extracts_direct_video_url():
    media = parse_fast_media(_payload())

    assert media.video_url == "https://cdn.example.test/play.mp4"
    assert media.size == 5576522
    assert media.duration == 60


@pytest.mark.unit
def test_parse_fast_media_rejects_error_response():
    payload = _payload()
    payload["code"] = -1
    payload["msg"] = "url parse err"

    with pytest.raises(FastPathUnavailable, match="url parse err"):
        parse_fast_media(payload)


@pytest.mark.unit
def test_parse_fast_media_rejects_response_without_play_url():
    payload = _payload()
    del payload["data"]["play"]

    with pytest.raises(FastPathUnavailable):
        parse_fast_media(payload)


@pytest.mark.unit
def test_parse_fast_media_rejects_photo_post():
    """Фото-посты идут своим путём и не должны попадать в быстрый путь видео."""
    payload = _payload(images=["https://cdn.example.test/1.jpg"])

    with pytest.raises(FastPathUnavailable, match="фото-пост"):
        parse_fast_media(payload)


@pytest.mark.unit
def test_parse_fast_media_keeps_audio_url_for_original_sound():
    media = parse_fast_media(_payload())

    assert media.audio_url == "https://cdn.example.test/music.mp3"
    assert media.audio_is_video_sound is True


@pytest.mark.unit
def test_parse_fast_media_drops_audio_url_for_licensed_track():
    """Реальный случай: 35-секундное видео с треком SAVAGE отдаёт music на 168 с."""
    media = parse_fast_media(
        _payload(
            duration=35,
            music_info={
                "title": "SAVAGE",
                "duration": 168,
                "original": False,
                "play": "https://cdn.example.test/savage.mp3",
            },
        )
    )

    assert media.audio_is_video_sound is False
    assert media.audio_url is None


# === audio_matches_video ===


@pytest.mark.unit
def test_audio_matches_video_accepts_original_sound_with_equal_duration():
    music_info = {"original": True, "duration": 166}

    assert audio_matches_video(music_info, video_duration=166) is True


@pytest.mark.unit
def test_audio_matches_video_rejects_licensed_track():
    music_info = {"original": False, "duration": 168}

    assert audio_matches_video(music_info, video_duration=35) is False


@pytest.mark.unit
def test_audio_matches_video_rejects_original_sound_with_diverging_duration():
    """Флаг original недостаточен — длительность тоже должна совпадать."""
    music_info = {"original": True, "duration": 168}

    assert audio_matches_video(music_info, video_duration=35) is False


@pytest.mark.unit
def test_audio_matches_video_tolerates_small_duration_drift():
    music_info = {"original": True, "duration": 60}

    assert audio_matches_video(music_info, video_duration=61) is True


@pytest.mark.unit
def test_audio_matches_video_rejects_missing_music_info():
    assert audio_matches_video({}, video_duration=60) is False


# === fits_url_handoff ===


@pytest.mark.unit
def test_fits_url_handoff_accepts_largest_verified_video():
    """19.53 МБ — проверенное реальное видео на 166 с, 97.6 % лимита."""
    assert fits_url_handoff(19528428) is True


@pytest.mark.unit
def test_fits_url_handoff_rejects_size_above_bot_api_limit():
    assert fits_url_handoff(20_000_001) is False
