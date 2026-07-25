"""Тесты скачивания Instagram по быстрому пути и откатa на yt-dlp."""

from pathlib import Path

import pytest

from utils import tiktok_instagram_utils
from utils.fast_path import FastPathUnavailable
from utils.ytdlp_common import FileSizeLimitError


REEL_URL = "https://www.instagram.com/reel/DbMSKU5Ba9g/"
CDN_URL = "https://scontent-ams2-1.cdninstagram.com/o1/v/t2/f2/m86/video.mp4"


def _graphql_media(**overrides):
    """Элемент GraphQL-ответа в форме, снятой с реального рилса."""
    media = {
        "code": "DbMSKU5Ba9g",
        "media_type": 2,
        "caption": {"text": "Spider-Snoop"},
        "video_versions": [{"width": 720, "height": 1280, "type": 101, "url": CDN_URL}],
    }
    media.update(overrides)
    return media


@pytest.fixture
def fake_instagram_fast(monkeypatch):
    """Подменяет GraphQL и сетевое скачивание, возвращает список запросов."""
    requested: list[tuple[str, str | None]] = []

    def _fake_download(url, destination, referer=None, expected_content_type=None):
        requested.append((url, expected_content_type))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"media")
        return destination

    monkeypatch.setattr(
        tiktok_instagram_utils, "_download_remote_file", _fake_download
    )
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_fetch_public_instagram_graphql_media",
        lambda canonical_url, shortcode, timeout=None: _graphql_media(),
    )
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_ensure_ios_compatible_video",
        lambda path, session_id, label: path,
    )
    return requested


@pytest.mark.unit
def test_fast_path_downloads_direct_link(fake_instagram_fast, tmp_path):
    """Быстрый путь берёт ссылку из GraphQL и не трогает yt-dlp."""
    result = tiktok_instagram_utils.download_instagram_video_fast(
        REEL_URL, "session-ig-fast", output_dir=tmp_path
    )

    assert Path(result).exists()
    assert fake_instagram_fast == [(CDN_URL, "video/")]


@pytest.mark.unit
def test_fast_path_requires_video_content_type(fake_instagram_fast, tmp_path):
    """HTML вместо видео нельзя ни писать в .mp4, ни отдавать пользователю."""
    tiktok_instagram_utils.download_instagram_video_fast(
        REEL_URL, "session-ig-ct", output_dir=tmp_path
    )

    assert fake_instagram_fast[0][1] == "video/"


@pytest.mark.unit
def test_fast_path_verifies_codec(monkeypatch, tmp_path):
    """ADR-001 требует проверки кодека на обоих путях скачивания.

    Состав ссылки Instagram — не наш контракт: смена кодировщика на HEVC не
    должна вернуть дефект с растянутым видео на iOS.
    """
    checked: list[str] = []

    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_fetch_public_instagram_graphql_media",
        lambda canonical_url, shortcode, timeout=None: _graphql_media(),
    )
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_download_remote_file",
        lambda url, destination, referer=None, expected_content_type=None: (
            destination.parent.mkdir(parents=True, exist_ok=True),
            destination.write_bytes(b"media"),
            destination,
        )[-1],
    )

    def _record(path, session_id, label):
        checked.append(label)
        return path

    monkeypatch.setattr(
        tiktok_instagram_utils, "_ensure_ios_compatible_video", _record
    )

    tiktok_instagram_utils.download_instagram_video_fast(
        REEL_URL, "session-ig-codec", output_dir=tmp_path
    )

    assert checked, "кодек скачанного файла не проверяется"


@pytest.mark.unit
def test_download_instagram_video_prefers_fast_path(monkeypatch, tmp_path):
    monkeypatch.setattr(
        tiktok_instagram_utils, "INSTAGRAM_FAST_PATH", True, raising=False
    )
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "download_instagram_video_fast",
        lambda *args, **kwargs: Path("/tmp/ig-fast.mp4"),
    )
    monkeypatch.setattr(
        tiktok_instagram_utils.yt_dlp,
        "YoutubeDL",
        lambda opts: pytest.fail("yt-dlp не должен вызываться на быстром пути"),
    )

    result = tiktok_instagram_utils.download_instagram_video(REEL_URL, "session-ig")

    assert result == Path("/tmp/ig-fast.mp4")


@pytest.mark.unit
def test_download_instagram_video_falls_back_to_ytdlp(monkeypatch, tmp_path):
    """Отказ быстрого пути не должен ломать скачивание."""
    reached: list[bool] = []

    def _unavailable(*args, **kwargs):
        raise FastPathUnavailable("GraphQL не отдал ссылку")

    class _FakeYoutubeDL:
        def __init__(self, opts):
            self.target = tmp_path / "ytdlp.mp4"

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def extract_info(self, url, download=True):
            reached.append(True)
            self.target.write_bytes(b"ytdlp")
            return {"title": "ytdlp"}

        def prepare_filename(self, info):
            return str(self.target)

    monkeypatch.setattr(
        tiktok_instagram_utils, "INSTAGRAM_FAST_PATH", True, raising=False
    )
    monkeypatch.setattr(
        tiktok_instagram_utils, "download_instagram_video_fast", _unavailable
    )
    monkeypatch.setattr(
        tiktok_instagram_utils.yt_dlp, "YoutubeDL", _FakeYoutubeDL
    )
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_ensure_ios_compatible_video",
        lambda path, session_id, label: path,
    )

    result = tiktok_instagram_utils.download_instagram_video(
        REEL_URL, "session-ig-fallback", output_dir=tmp_path
    )

    assert reached == [True], "откат на yt-dlp не выполнен"
    assert Path(result).exists()


@pytest.mark.unit
def test_file_size_limit_is_not_swallowed_by_fallback(monkeypatch):
    """Превышение лимита — это отказ по существу, а не повод идти в yt-dlp.

    Иначе бот скачал бы тот же слишком большой файл второй раз, вдвое дольше,
    и всё равно упёрся в лимит Telegram.
    """

    def _too_big(*args, **kwargs):
        raise FileSizeLimitError("файл больше лимита")

    monkeypatch.setattr(
        tiktok_instagram_utils, "INSTAGRAM_FAST_PATH", True, raising=False
    )
    monkeypatch.setattr(
        tiktok_instagram_utils, "download_instagram_video_fast", _too_big
    )
    monkeypatch.setattr(
        tiktok_instagram_utils.yt_dlp,
        "YoutubeDL",
        lambda opts: pytest.fail("после отказа по размеру yt-dlp не нужен"),
    )

    with pytest.raises(FileSizeLimitError):
        tiktok_instagram_utils.download_instagram_video(REEL_URL, "session-ig-big")


@pytest.mark.unit
def test_flag_off_skips_fast_path(monkeypatch, tmp_path):
    """С выключенным флагом быстрый путь не должен вызываться вовсе."""
    monkeypatch.setattr(
        tiktok_instagram_utils, "INSTAGRAM_FAST_PATH", False, raising=False
    )
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "download_instagram_video_fast",
        lambda *args, **kwargs: pytest.fail("флаг выключен, а быстрый путь вызван"),
    )
    monkeypatch.setattr(
        tiktok_instagram_utils.yt_dlp,
        "YoutubeDL",
        lambda opts: (_ for _ in ()).throw(RuntimeError("до yt-dlp дошли")),
    )

    with pytest.raises(Exception, match="до yt-dlp дошли|Instagram"):
        tiktok_instagram_utils.download_instagram_video(REEL_URL, "session-ig-off")


@pytest.mark.unit
def test_added_latency_before_ytdlp_fallback_is_bounded():
    """Недоступный GraphQL не должен стоить пользователю минуты ожидания.

    Пока быстрый путь ждёт сокеты, воркер занят, а пользователь видит
    «Скачиваю…». Бюджет добавленной задержки до откатa — как у TikTok.
    """
    worst_case = (
        tiktok_instagram_utils.INSTAGRAM_FAST_PATH_TIMEOUT_SECONDS
        * tiktok_instagram_utils.INSTAGRAM_FAST_PATH_MAX_ATTEMPTS
    )

    assert worst_case <= 15, f"добавленная задержка до откатa {worst_case} с"


@pytest.mark.unit
def test_fast_path_rejects_link_outside_allowlist(monkeypatch, tmp_path):
    """Подменённый адрес не должен уводить бота во внутреннюю сеть Docker."""
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_fetch_public_instagram_graphql_media",
        lambda canonical_url, shortcode, timeout=None: _graphql_media(
            video_versions=[{"url": "http://telegram-bot-api:8081/secret"}]
        ),
    )
    monkeypatch.setattr(
        tiktok_instagram_utils,
        "_download_remote_file",
        lambda *args, **kwargs: pytest.fail("скачивание вне allowlist недопустимо"),
    )

    with pytest.raises(FastPathUnavailable, match="allowlist"):
        tiktok_instagram_utils.download_instagram_video_fast(
            REEL_URL, "session-ig-evil", output_dir=tmp_path
        )
