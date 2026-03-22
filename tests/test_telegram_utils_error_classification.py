"""Unit tests for YouTube error classification in telegram_utils."""

from utils.telegram_utils import _classify_youtube_error, _youtube_error_code


def test_classify_requested_format_not_available():
    message = _classify_youtube_error("ERROR: Requested format is not available")
    assert message is not None
    assert "Выберите другой формат" in message


def test_classify_timeout_error():
    message = _classify_youtube_error("Read timed out while downloading")
    assert message == "🌐 Проблемы с сетью. Попробуйте позже."


def test_classify_ffmpeg_missing():
    message = _classify_youtube_error("ffmpeg is not installed")
    assert message == "❌ FFmpeg не найден в системе. Установите FFmpeg и добавьте его в PATH."


def test_classify_extractor_runtime_issue():
    message = _classify_youtube_error("nsig extraction failed: requires a javascript runtime")
    assert message is not None
    assert "extractor" in message.lower()


def test_classify_unknown_error_returns_none():
    assert _classify_youtube_error("some unexpected internal error") is None


def test_youtube_error_code_format_unavailable():
    assert _youtube_error_code("Requested format is not available") == "FORMAT_UNAVAILABLE"


def test_youtube_error_code_access_restricted():
    assert _youtube_error_code("HTTP Error 403: Forbidden") == "ACCESS_RESTRICTED"


def test_youtube_error_code_network_timeout():
    assert _youtube_error_code("Connection timed out") == "NETWORK_TIMEOUT"


def test_youtube_error_code_ffmpeg_missing():
    assert _youtube_error_code("ffmpeg is not installed") == "FFMPEG_MISSING"


def test_youtube_error_code_extractor_runtime():
    assert _youtube_error_code("nsig extraction failed") == "EXTRACTOR_RUNTIME"


def test_youtube_error_code_unknown():
    assert _youtube_error_code("totally unrelated message") == "UNKNOWN"
