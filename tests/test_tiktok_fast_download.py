"""Тесты скачивания TikTok по быстрому пути и откатa на yt-dlp."""

from pathlib import Path

import pytest

from utils import tiktok_instagram_utils
from utils.tiktok_fast_path import FastPathUnavailable
from utils.ytdlp_common import FileSizeLimitError


VIDEO_URL = "https://v16m.tiktokcdn-us.com/play.mp4"
MUSIC_URL = "https://www.tikwm.com/music.mp3"


def _resolver_payload(**music_info_overrides):
    music_info = {
        "title": "original sound - tester",
        "duration": 60,
        "original": True,
        "play": MUSIC_URL,
    }
    music_info.update(music_info_overrides)
    return {
        "code": 0,
        "msg": "success",
        "data": {
            "id": "7639073022762175764",
            "title": "Клип",
            "duration": 60,
            "size": 5576522,
            "play": VIDEO_URL,
            "music": MUSIC_URL,
            "cover": "https://www.tikwm.com/cover.jpg",
            "author": {"unique_id": "tester"},
            "music_info": music_info,
        },
    }


class _FakeStream:
    """Минимальный ответ httpx.stream для проверок без сети."""

    def __init__(self, content_type: str, chunks=(b"media",)):
        self.headers = {"content-type": content_type}
        self._chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def raise_for_status(self):
        return None

    def iter_bytes(self):
        return iter(self._chunks)


@pytest.fixture
def fake_downloads(monkeypatch):
    """Подменяет сетевое скачивание записью заглушки на диск."""
    requested: list[str] = []

    def _fake_download(url, destination, referer=None, expected_content_type=None):
        requested.append(url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"media")
        return destination

    monkeypatch.setattr(
        tiktok_instagram_utils, "_download_remote_file", _fake_download
    )
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_resolve_tiktok_url",
        lambda url: "https://www.tiktok.com/@tester/video/7639073022762175764",
    )
    return requested


@pytest.mark.unit
def test_added_latency_before_ytdlp_fallback_is_bounded():
    """Недоступный резолвер не должен стоить пользователю минуты ожидания.

    Пока быстрый путь ждёт сокеты, воркер занят, а пользователь видит
    «Скачиваю…». Бюджет добавленной задержки до откатa — примерно 15 секунд.
    """
    worst_case = (
        tiktok_instagram_utils.TIKTOK_RESOLVER_TIMEOUT_SECONDS
        * tiktok_instagram_utils.TIKTOK_RESOLVER_MAX_ATTEMPTS
    )

    assert worst_case <= 15, f"добавленная задержка до откатa {worst_case} с"


@pytest.mark.unit
def test_resolve_tiktok_url_uses_bounded_timeouts(monkeypatch):
    """HEAD и запасной GET вместе не должны съедать бюджет ожидания."""
    timeouts: list = []

    def _record(url, **kwargs):
        timeouts.append(kwargs["timeout"])
        raise RuntimeError("сеть недоступна")

    monkeypatch.setattr(tiktok_instagram_utils.httpx, "head", _record)
    monkeypatch.setattr(tiktok_instagram_utils.httpx, "get", _record)

    assert (
        tiktok_instagram_utils._resolve_tiktok_url("https://vt.tiktok.com/example/")
        == "https://vt.tiktok.com/example/"
    )
    assert timeouts, "запросы должны получать явный таймаут"
    assert sum(timeouts) <= 15, f"развёртывание ссылки стоит {sum(timeouts)} с"


@pytest.mark.unit
def test_resolve_skips_network_for_canonical_url(monkeypatch):
    """Полный адрес разворачивать нечего — HEAD к нему был чистой потерей.

    Замер в продакшене: этот запрос стоил 0.84 с на каждой ссылке вида
    `www.tiktok.com/@user/video/<id>`, хотя редиректа за ней нет. Пользователь
    ждал его внутри «⏳ Обрабатываю ссылку...».
    """

    def _forbidden(*args, **kwargs):
        pytest.fail("для полного адреса сетевой запрос не нужен")

    monkeypatch.setattr(tiktok_instagram_utils.httpx, "head", _forbidden)
    monkeypatch.setattr(tiktok_instagram_utils.httpx, "get", _forbidden)

    for url in (
        "https://www.tiktok.com/@tester/video/7639073022762175764",
        "https://www.tiktok.com/@tester/video/7639073022762175764?is_from_webapp=1",
        "https://www.tiktok.com/@tester/photo/7639073022762175764",
        "https://tiktok.com/@tester/video/7639073022762175764",
    ):
        assert tiktok_instagram_utils._resolve_tiktok_url(url) == url


@pytest.mark.unit
def test_resolve_still_expands_short_links(monkeypatch):
    """Короткие ссылки без запроса развернуть нельзя — оптимизация их не трогает."""
    canonical = "https://www.tiktok.com/@tester/video/7639073022762175764"
    calls: list[str] = []

    class _Response:
        status_code = 200
        url = canonical

    def _fake_head(url, **kwargs):
        calls.append(url)
        return _Response()

    monkeypatch.setattr(tiktok_instagram_utils.httpx, "head", _fake_head)

    for short_url in (
        "https://vt.tiktok.com/ZSxGKk3yb/",
        "https://vm.tiktok.com/ZSxGKk3yb/",
    ):
        assert tiktok_instagram_utils._resolve_tiktok_url(short_url) == canonical

    assert len(calls) == 2, "короткие ссылки обязаны разворачиваться запросом"


@pytest.mark.unit
def test_tiktok_resolver_stops_retrying_within_budget(monkeypatch):
    """Число попыток к резолверу ограничено — дальше ждёт откат на yt-dlp."""
    timeouts: list = []

    def _failing_get(url, **kwargs):
        timeouts.append(kwargs["timeout"])
        raise RuntimeError("резолвер недоступен")

    monkeypatch.setattr(tiktok_instagram_utils.httpx, "get", _failing_get)
    monkeypatch.setattr(
        tiktok_instagram_utils.time,
        "sleep",
        lambda seconds: pytest.fail("пауза на пути быстрой доставки недопустима"),
    )

    with pytest.raises(RuntimeError):
        tiktok_instagram_utils._call_tiktok_resolver(
            "https://www.tiktok.com/@tester/video/1"
        )

    assert len(timeouts) == tiktok_instagram_utils.TIKTOK_RESOLVER_MAX_ATTEMPTS
    assert len(timeouts) <= 2
    assert max(timeouts) <= 8


@pytest.mark.unit
def test_fetch_tiktok_fast_media_parses_resolver_response(monkeypatch):
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_call_tiktok_resolver",
        lambda url: _resolver_payload(),
    )

    media = tiktok_instagram_utils.fetch_tiktok_fast_media(
        "https://vt.tiktok.com/example/"
    )

    assert media.video_url == VIDEO_URL
    assert media.audio_is_video_sound is True


@pytest.mark.unit
def test_remote_download_rejects_unexpected_content_type(monkeypatch, tmp_path):
    """Тело неожидаемого типа нельзя ни писать в .mp4, ни отдавать пользователю."""
    monkeypatch.setattr(
        tiktok_instagram_utils.httpx,
        "stream",
        lambda method, url, **kwargs: _FakeStream("text/html; charset=utf-8"),
    )
    destination = tmp_path / "clip.mp4"

    with pytest.raises(FastPathUnavailable, match="text/html"):
        tiktok_instagram_utils._download_remote_file(
            VIDEO_URL, destination, expected_content_type="video/"
        )

    assert not destination.exists()


@pytest.mark.unit
def test_remote_download_accepts_declared_content_type(monkeypatch, tmp_path):
    monkeypatch.setattr(
        tiktok_instagram_utils.httpx,
        "stream",
        lambda method, url, **kwargs: _FakeStream("video/mp4"),
    )
    destination = tmp_path / "clip.mp4"

    result = tiktok_instagram_utils._download_remote_file(
        VIDEO_URL, destination, expected_content_type="video/"
    )

    assert result.read_bytes() == b"media"


@pytest.mark.unit
def test_fast_path_declares_expected_content_types(monkeypatch, tmp_path):
    """Видео обязано приходить как video/*, звук — как audio/*."""
    seen: list = []

    def _fake_download(url, destination, referer=None, expected_content_type=None):
        seen.append(expected_content_type)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"media")
        return destination

    monkeypatch.setattr(
        tiktok_instagram_utils, "_download_remote_file", _fake_download
    )
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_call_tiktok_resolver",
        lambda url: _resolver_payload(),
    )

    tiktok_instagram_utils.download_tiktok_video_fast(
        "https://vt.tiktok.com/example/",
        "session-ct-video",
        output_dir=tmp_path,
        resolved_url="https://www.tiktok.com/@tester/video/1",
    )
    tiktok_instagram_utils.download_tiktok_audio_fast(
        "https://vt.tiktok.com/example/",
        "session-ct-audio",
        output_dir=tmp_path,
        resolved_url="https://www.tiktok.com/@tester/video/1",
    )

    assert seen == ["video/", "audio/"]


@pytest.mark.unit
def test_fast_video_download_uses_direct_url(monkeypatch, tmp_path, fake_downloads):
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_call_tiktok_resolver",
        lambda url: _resolver_payload(),
    )

    result = tiktok_instagram_utils.download_tiktok_video_fast(
        "https://vt.tiktok.com/example/",
        "session-fast",
        output_dir=tmp_path,
    )

    assert Path(result).exists()
    assert fake_downloads == [VIDEO_URL]


@pytest.mark.unit
def test_fast_video_transcodes_hevc_from_resolver(monkeypatch, tmp_path, fake_downloads):
    """ADR-001: HEVC нельзя отдавать в Telegram, даже если его прислал резолвер.

    Резолвер может начать отдавать в `play` HEVC при смене тарифа/региона, и
    результат попадёт в кэш file_id на 90 дней.
    """
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_call_tiktok_resolver",
        lambda url: _resolver_payload(),
    )
    monkeypatch.setattr(tiktok_instagram_utils, "get_video_codec", lambda path: "hevc")
    converted = tmp_path / "converted.mp4"

    def _fake_convert(input_path, output_format, session_id, output_filename=None):
        assert output_format == "mp4"
        converted.write_bytes(b"h264")
        return converted

    monkeypatch.setattr(tiktok_instagram_utils, "convert_to_format", _fake_convert)

    result = tiktok_instagram_utils.download_tiktok_video_fast(
        "https://vt.tiktok.com/example/",
        "session-hevc",
        output_dir=tmp_path,
    )

    assert Path(result) == converted
    assert not (tmp_path / "Клип.mp4").exists(), "исходный HEVC должен удаляться"


@pytest.mark.unit
def test_fast_video_keeps_h264_without_transcode(monkeypatch, tmp_path, fake_downloads):
    """H.264 из резолвера — обычный случай, перекодирование запускать нельзя."""
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_call_tiktok_resolver",
        lambda url: _resolver_payload(),
    )
    monkeypatch.setattr(tiktok_instagram_utils, "get_video_codec", lambda path: "h264")

    def _forbidden_convert(*args, **kwargs):
        raise AssertionError("H.264 не должен перекодироваться")

    monkeypatch.setattr(
        tiktok_instagram_utils, "convert_to_format", _forbidden_convert
    )

    result = tiktok_instagram_utils.download_tiktok_video_fast(
        "https://vt.tiktok.com/example/",
        "session-h264",
        output_dir=tmp_path,
    )

    assert Path(result).name == "Клип.mp4"


@pytest.mark.unit
def test_fast_audio_uses_music_url_for_original_sound(
    monkeypatch, tmp_path, fake_downloads
):
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_call_tiktok_resolver",
        lambda url: _resolver_payload(),
    )

    result = tiktok_instagram_utils.download_tiktok_audio_fast(
        "https://vt.tiktok.com/example/",
        "session-fast",
        output_dir=tmp_path,
    )

    assert Path(result).exists()
    assert fake_downloads == [MUSIC_URL]


@pytest.mark.unit
def test_fast_audio_extracts_from_video_for_licensed_track(
    monkeypatch, tmp_path, fake_downloads
):
    """Лицензированный трек нельзя отдавать вместо звука видео."""
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_call_tiktok_resolver",
        lambda url: _resolver_payload(
            title="SAVAGE", duration=168, original=False
        ),
    )
    extracted: list[Path] = []

    def _fake_extract(input_path, session_id, output_filename=None):
        extracted.append(input_path)
        target = tmp_path / "extracted.m4a"
        target.write_bytes(b"audio")
        return target

    monkeypatch.setattr(
        tiktok_instagram_utils, "extract_audio_copy", _fake_extract
    )

    result = tiktok_instagram_utils.download_tiktok_audio_fast(
        "https://vt.tiktok.com/example/",
        "session-fast",
        output_dir=tmp_path,
    )

    assert fake_downloads == [VIDEO_URL]
    assert len(extracted) == 1
    assert Path(result).name == "extracted.m4a"


@pytest.mark.unit
def test_fast_audio_removes_video_when_extraction_fails(
    monkeypatch, tmp_path, fake_downloads
):
    """Иначе откат на yt-dlp качает видео повторно в тот же каталог.

    Пиковый расход диска при этом удваивается на каждом сбое извлечения.
    """
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_call_tiktok_resolver",
        lambda url: _resolver_payload(title="SAVAGE", duration=168, original=False),
    )

    def _failing_extract(input_path, session_id, output_filename=None):
        raise RuntimeError("ffmpeg упал")

    monkeypatch.setattr(
        tiktok_instagram_utils, "extract_audio_copy", _failing_extract
    )

    with pytest.raises(RuntimeError, match="ffmpeg упал"):
        tiktok_instagram_utils.download_tiktok_audio_fast(
            "https://vt.tiktok.com/example/",
            "session-extract-fail",
            output_dir=tmp_path,
        )

    assert list(tmp_path.iterdir()) == [], "скачанное видео должно удаляться"


@pytest.mark.unit
def test_download_tiktok_video_falls_back_to_ytdlp(monkeypatch, fake_downloads):
    """Отказ быстрого пути не должен ломать скачивание."""

    def _unavailable(*args, **kwargs):
        raise FastPathUnavailable("резолвер недоступен")

    monkeypatch.setattr(
        tiktok_instagram_utils, "TIKTOK_FAST_PATH", True, raising=False
    )
    monkeypatch.setattr(
        tiktok_instagram_utils, "download_tiktok_video_fast", _unavailable
    )
    monkeypatch.setattr(
        tiktok_instagram_utils, "_get_tiktok_base_configs", lambda: [{}]
    )
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_smart_retry",
        lambda func, max_attempts=2, context="": Path("/tmp/ytdlp-fallback.mp4"),
    )

    result = tiktok_instagram_utils.download_tiktok_video(
        "https://vt.tiktok.com/example/", "session-fallback"
    )

    assert result == Path("/tmp/ytdlp-fallback.mp4")


@pytest.mark.unit
def test_size_gate_does_not_block_fast_path(monkeypatch, tmp_path, fake_downloads):
    """Гейт по размеру HQ-формата не должен отклонять то, что качается иначе.

    `cached_info["filesize"]` приходит из yt-dlp и описывает 1080p/HEVC формат,
    который на быстром пути не скачивается: `play` того же ролика вчетверо
    меньше и в лимит облачного режима укладывается.
    """
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_call_tiktok_resolver",
        lambda url: _resolver_payload(),
    )
    monkeypatch.setattr(tiktok_instagram_utils, "MAX_FILE_SIZE", 50 * 1024 * 1024)
    monkeypatch.setattr(
        tiktok_instagram_utils, "TIKTOK_FAST_PATH", True, raising=False
    )

    result = tiktok_instagram_utils.download_tiktok_video(
        "https://vt.tiktok.com/example/",
        "session-size-gate",
        output_dir=tmp_path,
        cached_info={"filesize": 60 * 1024 * 1024},
    )

    assert Path(result).exists()
    assert fake_downloads == [VIDEO_URL]


@pytest.mark.unit
def test_size_gate_still_rejects_when_fast_path_disabled(monkeypatch, tmp_path):
    """При выключенном быстром пути прежнее поведение обязано сохраниться."""
    monkeypatch.setattr(tiktok_instagram_utils, "MAX_FILE_SIZE", 50 * 1024 * 1024)
    monkeypatch.setattr(
        tiktok_instagram_utils, "TIKTOK_FAST_PATH", False, raising=False
    )
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_resolve_tiktok_url",
        lambda url: "https://www.tiktok.com/@tester/video/1",
    )

    with pytest.raises(FileSizeLimitError):
        tiktok_instagram_utils.download_tiktok_video(
            "https://vt.tiktok.com/example/",
            "session-size-gate-off",
            output_dir=tmp_path,
            cached_info={"filesize": 60 * 1024 * 1024},
        )


@pytest.mark.unit
def test_size_gate_applies_after_fast_path_failure(monkeypatch, tmp_path):
    """Откат ведёт на yt-dlp, где размер HQ-формата снова релевантен."""
    monkeypatch.setattr(tiktok_instagram_utils, "MAX_FILE_SIZE", 50 * 1024 * 1024)
    monkeypatch.setattr(
        tiktok_instagram_utils, "TIKTOK_FAST_PATH", True, raising=False
    )
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_resolve_tiktok_url",
        lambda url: "https://www.tiktok.com/@tester/video/1",
    )

    def _unavailable(*args, **kwargs):
        raise FastPathUnavailable("резолвер недоступен")

    monkeypatch.setattr(
        tiktok_instagram_utils, "download_tiktok_video_fast", _unavailable
    )

    with pytest.raises(FileSizeLimitError):
        tiktok_instagram_utils.download_tiktok_video(
            "https://vt.tiktok.com/example/",
            "session-size-gate-fallback",
            output_dir=tmp_path,
            cached_info={"filesize": 60 * 1024 * 1024},
        )


@pytest.mark.unit
def test_download_tiktok_video_skips_fast_path_when_disabled(
    monkeypatch, fake_downloads
):
    calls: list[str] = []

    def _record(*args, **kwargs):
        calls.append("fast")
        return Path("/tmp/fast.mp4")

    monkeypatch.setattr(
        tiktok_instagram_utils, "TIKTOK_FAST_PATH", False, raising=False
    )
    monkeypatch.setattr(
        tiktok_instagram_utils, "download_tiktok_video_fast", _record
    )
    monkeypatch.setattr(
        tiktok_instagram_utils, "_get_tiktok_base_configs", lambda: [{}]
    )
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_smart_retry",
        lambda func, max_attempts=2, context="": Path("/tmp/ytdlp.mp4"),
    )

    result = tiktok_instagram_utils.download_tiktok_video(
        "https://vt.tiktok.com/example/", "session-disabled"
    )

    assert calls == []
    assert result == Path("/tmp/ytdlp.mp4")


@pytest.mark.unit
def test_fast_video_reuses_already_resolved_url(monkeypatch, tmp_path, fake_downloads):
    """Ссылку уже развернул вызывающий код — повторный запрос лишний."""
    resolves: list[str] = []

    def _resolve(url):
        resolves.append(url)
        return "https://www.tiktok.com/@tester/video/1"

    monkeypatch.setattr(tiktok_instagram_utils, "_resolve_tiktok_url", _resolve)
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_call_tiktok_resolver",
        lambda url: _resolver_payload(),
    )

    tiktok_instagram_utils.download_tiktok_video_fast(
        "https://vt.tiktok.com/short/",
        "session-reuse",
        output_dir=tmp_path,
        resolved_url="https://www.tiktok.com/@tester/video/1",
    )

    assert resolves == []


@pytest.mark.unit
def test_download_tiktok_video_resolves_url_once(monkeypatch, tmp_path, fake_downloads):
    """На одну доставку должно приходиться одно развёртывание ссылки."""
    resolves: list[str] = []

    def _resolve(url):
        resolves.append(url)
        return "https://www.tiktok.com/@tester/video/1"

    monkeypatch.setattr(tiktok_instagram_utils, "_resolve_tiktok_url", _resolve)
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_call_tiktok_resolver",
        lambda url: _resolver_payload(),
    )
    monkeypatch.setattr(
        tiktok_instagram_utils, "TIKTOK_FAST_PATH", True, raising=False
    )

    tiktok_instagram_utils.download_tiktok_video(
        "https://vt.tiktok.com/short/", "session-once", output_dir=tmp_path
    )

    assert len(resolves) == 1
