"""Модульные тесты границы FFmpeg/FFprobe."""

from types import SimpleNamespace

import pytest

from utils import media_processor


@pytest.fixture(autouse=True)
def _isolate_ffmpeg_probe_cache():
    """Кэш проверки FFmpeg живёт в модуле, поэтому изолируем его на каждый тест."""
    media_processor.reset_ffmpeg_probe_cache()
    yield
    media_processor.reset_ffmpeg_probe_cache()


class _Process:
    def __init__(self, stdout: str, stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.killed = False

    def communicate(self, timeout=None):
        return self.stdout, self.stderr

    def kill(self):
        self.killed = True


def _recording_popen(recorded: list):
    """Подменяет Popen, записывая построенную команду для проверки."""

    def _popen(cmd, *args, **kwargs):
        recorded.append(list(cmd))
        return _Process("")

    return _popen


def test_check_ffmpeg_installed_handles_success(monkeypatch):
    monkeypatch.setattr(
        media_processor.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )

    assert media_processor.check_ffmpeg_installed() is True


def test_check_ffmpeg_installed_handles_missing_binary(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(media_processor.subprocess, "run", missing)

    assert media_processor.check_ffmpeg_installed() is False


@pytest.mark.unit
def test_check_ffmpeg_installed_retries_after_transient_spawn_failure(monkeypatch):
    """Сбой порождения процесса транзиентен и не должен кэшироваться.

    При DOWNLOAD_WORKERS=8 subprocess.run падает по EAGAIN/ENOMEM/EMFILE.
    Если такой отказ закэшировать, ffmpeg останется «не установлен» до конца
    жизни процесса: HEVC перестанет перекодироваться (ADR-001), а
    has_audio_stream начнёт возвращать False для всех файлов.
    """
    calls: list = []

    def _run(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            raise BlockingIOError("Resource temporarily unavailable")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(media_processor.subprocess, "run", _run)

    assert media_processor.check_ffmpeg_installed() is False
    assert media_processor.check_ffmpeg_installed() is True
    assert len(calls) == 2, "после транзиентного сбоя нужна повторная проверка"


@pytest.mark.unit
def test_check_ffmpeg_installed_caches_missing_binary(monkeypatch):
    """Отсутствие бинаря стабильно, поэтому этот отказ кэшировать можно."""
    calls: list = []

    def _missing(*args, **kwargs):
        calls.append(args)
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(media_processor.subprocess, "run", _missing)

    assert media_processor.check_ffmpeg_installed() is False
    assert media_processor.check_ffmpeg_installed() is False
    assert len(calls) == 1


def test_get_video_codec_reads_first_stream(monkeypatch, tmp_path):
    monkeypatch.setattr(media_processor, "check_ffmpeg_installed", lambda: True)
    monkeypatch.setattr(
        media_processor.subprocess,
        "Popen",
        lambda *args, **kwargs: _Process('{"streams": [{"codec_name": "hevc"}]}'),
    )

    assert media_processor.get_video_codec(tmp_path / "clip.mp4") == "hevc"


def test_get_video_codec_returns_none_for_invalid_probe_json(monkeypatch, tmp_path):
    monkeypatch.setattr(media_processor, "check_ffmpeg_installed", lambda: True)
    monkeypatch.setattr(
        media_processor.subprocess,
        "Popen",
        lambda *args, **kwargs: _Process("not-json"),
    )

    assert media_processor.get_video_codec(tmp_path / "clip.mp4") is None


def test_has_audio_stream_distinguishes_empty_stream_list(monkeypatch, tmp_path):
    monkeypatch.setattr(media_processor, "check_ffmpeg_installed", lambda: True)
    monkeypatch.setattr(
        media_processor.subprocess,
        "Popen",
        lambda *args, **kwargs: _Process('{"streams": []}'),
    )

    assert media_processor.has_audio_stream(tmp_path / "clip.mp4") is False


def test_convert_to_format_rejects_missing_output(monkeypatch, tmp_path):
    input_path = tmp_path / "input.webm"
    input_path.write_bytes(b"input")
    output_path = tmp_path / "output.mp4"
    monkeypatch.setattr(media_processor, "check_ffmpeg_installed", lambda: True)
    monkeypatch.setattr(
        media_processor,
        "get_temp_file_path",
        lambda session_id, filename: output_path,
    )
    monkeypatch.setattr(
        media_processor.subprocess,
        "Popen",
        lambda *args, **kwargs: _Process(""),
    )

    with pytest.raises(Exception, match="не был создан"):
        media_processor.convert_to_format(
            input_path,
            "mp4",
            "session-1",
            "output.mp4",
        )


def test_convert_to_format_returns_created_output(monkeypatch, tmp_path):
    input_path = tmp_path / "input.webm"
    input_path.write_bytes(b"input")
    output_path = tmp_path / "output.mp4"
    output_path.write_bytes(b"output")
    monkeypatch.setattr(media_processor, "check_ffmpeg_installed", lambda: True)
    monkeypatch.setattr(
        media_processor,
        "get_temp_file_path",
        lambda session_id, filename: output_path,
    )
    monkeypatch.setattr(
        media_processor.subprocess,
        "Popen",
        lambda *args, **kwargs: _Process(""),
    )

    result = media_processor.convert_to_format(
        input_path,
        "mp4",
        "session-1",
        "output.mp4",
    )

    assert result == output_path


def _convert_harness(monkeypatch, tmp_path, video_codec, audio_codec):
    """Готовит convert_to_format к проверке построенной команды."""
    input_path = tmp_path / "input.webm"
    input_path.write_bytes(b"input")
    output_path = tmp_path / "output.mp4"
    output_path.write_bytes(b"output")
    recorded: list = []
    monkeypatch.setattr(media_processor, "check_ffmpeg_installed", lambda: True)
    monkeypatch.setattr(media_processor, "get_video_codec", lambda path: video_codec)
    monkeypatch.setattr(media_processor, "get_audio_codec", lambda path: audio_codec)
    monkeypatch.setattr(
        media_processor,
        "get_temp_file_path",
        lambda session_id, filename: output_path,
    )
    monkeypatch.setattr(
        media_processor.subprocess, "Popen", _recording_popen(recorded)
    )
    return input_path, recorded


def test_get_audio_codec_reads_first_audio_stream(monkeypatch, tmp_path):
    monkeypatch.setattr(media_processor, "check_ffmpeg_installed", lambda: True)
    monkeypatch.setattr(
        media_processor.subprocess,
        "Popen",
        lambda *args, **kwargs: _Process('{"streams": [{"codec_name": "opus"}]}'),
    )

    assert media_processor.get_audio_codec(tmp_path / "clip.webm") == "opus"


def test_convert_to_mp4_remuxes_when_already_telegram_ready(monkeypatch, tmp_path):
    """H.264 + AAC перекодировать не нужно — только сменить контейнер."""
    input_path, recorded = _convert_harness(monkeypatch, tmp_path, "h264", "aac")

    media_processor.convert_to_format(input_path, "mp4", "session-1", "output.mp4")

    command = recorded[0]
    assert "libx264" not in command, "H.264 не должен перекодироваться"
    assert command[command.index("-c") + 1] == "copy"
    assert "+faststart" in command


def test_convert_to_mp4_copies_video_and_reencodes_incompatible_audio(
    monkeypatch, tmp_path
):
    """Opus в MP4 Telegram не проигрывает, но видео копировать всё равно можно."""
    input_path, recorded = _convert_harness(monkeypatch, tmp_path, "h264", "opus")

    media_processor.convert_to_format(input_path, "mp4", "session-1", "output.mp4")

    command = recorded[0]
    assert "libx264" not in command, "видео должно копироваться"
    assert command[command.index("-c:v") + 1] == "copy"
    assert command[command.index("-c:a") + 1] == "aac"


def test_convert_to_mp4_transcodes_hevc_with_fast_preset(monkeypatch, tmp_path):
    """Путь ADR-001: HEVC обязан перекодироваться в H.264."""
    input_path, recorded = _convert_harness(monkeypatch, tmp_path, "hevc", "aac")

    media_processor.convert_to_format(input_path, "mp4", "session-1", "output.mp4")

    command = recorded[0]
    assert command[command.index("-c:v") + 1] == "libx264"
    assert command[command.index("-preset") + 1] == "veryfast"
    assert "+faststart" in command


@pytest.mark.unit
def test_convert_to_mp4_reencodes_mp3_audio_to_aac(monkeypatch, tmp_path):
    """MP3 внутри MP4 плеер Telegram на iOS проигрывает ненадёжно.

    Копировать такой поток в контейнер MP4 нельзя — звук должен стать AAC.
    """
    input_path, recorded = _convert_harness(monkeypatch, tmp_path, "h264", "mp3")

    media_processor.convert_to_format(input_path, "mp4", "session-1", "output.mp4")

    command = recorded[0]
    assert command[command.index("-c:a") + 1] == "aac"
    assert "-c" not in command, "MP3 нельзя копировать в MP4 целиком"


@pytest.mark.unit
def test_mp3_is_not_treated_as_telegram_ready_in_mp4():
    assert "mp3" not in media_processor.TELEGRAM_READY_AUDIO_CODECS
    assert "aac" in media_processor.TELEGRAM_READY_AUDIO_CODECS


def test_convert_to_mp4_transcodes_when_codec_unknown(monkeypatch, tmp_path):
    """Неизвестный кодек — консервативный путь с перекодированием."""
    input_path, recorded = _convert_harness(monkeypatch, tmp_path, None, None)

    media_processor.convert_to_format(input_path, "mp4", "session-1", "output.mp4")

    assert recorded[0][recorded[0].index("-c:v") + 1] == "libx264"


def test_check_ffmpeg_installed_probes_binary_only_once(monkeypatch):
    """Проверка наличия FFmpeg не должна порождать процесс на каждый вызов."""
    calls: list = []

    def _run(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0)

    media_processor.reset_ffmpeg_probe_cache()
    monkeypatch.setattr(media_processor.subprocess, "run", _run)

    assert media_processor.check_ffmpeg_installed() is True
    assert media_processor.check_ffmpeg_installed() is True
    assert len(calls) == 1


def test_extract_audio_copy_does_not_reencode(monkeypatch, tmp_path):
    """Извлечение звука обязано копировать поток, а не перекодировать его."""
    input_path = tmp_path / "clip.mp4"
    input_path.write_bytes(b"input")
    output_path = tmp_path / "clip.m4a"
    output_path.write_bytes(b"audio")
    recorded: list = []
    monkeypatch.setattr(media_processor, "check_ffmpeg_installed", lambda: True)
    monkeypatch.setattr(
        media_processor,
        "get_temp_file_path",
        lambda session_id, filename: output_path,
    )
    monkeypatch.setattr(
        media_processor.subprocess, "Popen", _recording_popen(recorded)
    )

    result = media_processor.extract_audio_copy(input_path, "session-1")

    assert result == output_path
    command = recorded[0]
    assert "-vn" in command, "видеопоток должен отбрасываться"
    assert command[command.index("-c:a") + 1] == "copy", "звук должен копироваться"
    for reencode_flag in ("-b:a", "-af", "libmp3lame"):
        assert reencode_flag not in command


def test_extract_audio_copy_rejects_missing_output(monkeypatch, tmp_path):
    input_path = tmp_path / "clip.mp4"
    input_path.write_bytes(b"input")
    monkeypatch.setattr(media_processor, "check_ffmpeg_installed", lambda: True)
    monkeypatch.setattr(
        media_processor,
        "get_temp_file_path",
        lambda session_id, filename: tmp_path / "missing.m4a",
    )
    monkeypatch.setattr(
        media_processor.subprocess,
        "Popen",
        lambda *args, **kwargs: _Process(""),
    )

    with pytest.raises(Exception, match="не был создан"):
        media_processor.extract_audio_copy(input_path, "session-1")
