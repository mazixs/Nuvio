"""Модульные тесты границы FFmpeg/FFprobe."""

from types import SimpleNamespace

import pytest

from utils import media_processor


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
