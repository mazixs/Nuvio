"""Тесты чистых платформенных решений и границ покрытия кэша."""

import ast
from pathlib import Path

import pytest

from utils.platform_actions import (
    cache_key_for_format_selection,
    cache_key_for_main_action,
)


ROOT = Path(__file__).resolve().parents[1]
TELEGRAM_UTILS_PATH = ROOT / "utils" / "telegram_utils.py"

# Способы прочитать кэш file_id в обработчиках: прямой вызов и хелпер аудио.
_CACHE_READER_NAMES = ("telegram_cache.get", "_deliver_cached_audio")


def _called_name(node: ast.Call) -> str:
    """Возвращает читаемое имя вызываемого объекта."""
    func = node.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return f"{func.value.id}.{func.attr}"
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _cache_readers_in_actions(actions: tuple[str, ...]) -> list[str]:
    """Ищет чтение кэша внутри ветвей `match` указанных действий.

    Проверка идёт по исходнику, потому что решение «читать кэш» видно только
    в обработчике: чистая функция ключей о читателях ничего не знает.
    """
    tree = ast.parse(TELEGRAM_UTILS_PATH.read_text(encoding="utf-8"))
    found: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.match_case):
            continue
        pattern = node.pattern
        if not (
            isinstance(pattern, ast.MatchValue)
            and isinstance(pattern.value, ast.Constant)
            and pattern.value.value in actions
        ):
            continue

        for statement in node.body:
            for inner in ast.walk(statement):
                if isinstance(inner, ast.Call) and (
                    _called_name(inner) in _CACHE_READER_NAMES
                ):
                    found.append(f"{pattern.value.value}: {_called_name(inner)}")

    return found


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


@pytest.mark.unit
def test_listed_main_actions_have_a_key_for_writing_to_cache():
    """Проверяет ТОЛЬКО наличие ключа, под которым запись попадёт в кэш.

    Граница проверки: наличие ЧИТАТЕЛЯ кэша здесь не проверяется и из
    зелёного результата не следует. Действие может иметь ключ и при этом
    писать в кэш «в одну сторону» — так сейчас у YouTube, см.
    test_youtube_cache_is_write_only.
    """
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


@pytest.mark.unit
def test_youtube_audio_m4a_writes_cache_under_literal_key():
    """`audio_m4a` — единственное действие меню вне списка выше.

    Ключ записи у него есть, но прописан литералом в обработчике, а не выдан
    чистой функцией: cache_key_for_main_action для него возвращает None.
    Читателя у этого ключа нет (см. test_youtube_cache_is_write_only).
    """
    assert cache_key_for_main_action("youtube", "audio_m4a") is None

    source = TELEGRAM_UTILS_PATH.read_text(encoding="utf-8")
    assert 'cache_format_id="audio_m4a"' in source


@pytest.mark.unit
def test_youtube_cache_is_write_only():
    """Фиксирует пробел, а не покрытие: YouTube пишет в кэш и не читает его.

    Ветви `tg_video` и `audio_m4a` вызывают send_file с cache_format_id, но ни
    telegram_cache.get, ни _deliver_cached_audio в них нет. Добавление чтения —
    отдельное решение владельца проекта; тест сторожит текущее состояние,
    чтобы контракт ключей его не маскировал.
    """
    assert _cache_readers_in_actions(("tg_video", "audio_m4a")) == []


@pytest.mark.unit
def test_cache_reader_detection_is_not_vacuous():
    """Контроль инструмента: в ветвях с чтением кэша он читателей находит."""
    readers = _cache_readers_in_actions(("tiktok_download", "tiktok_audio"))

    assert "tiktok_download: telegram_cache.get" in readers
    assert "tiktok_audio: _deliver_cached_audio" in readers
