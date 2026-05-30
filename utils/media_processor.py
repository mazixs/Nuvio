"""
Модуль для обработки медиафайлов с использованием FFmpeg.
"""

import json
import subprocess
from pathlib import Path
from config import MAX_FILE_SIZE, BLOCKING_TASK_TIMEOUT
from utils.logger import setup_logger
from utils.temp_file_manager import get_temp_file_path

logger = setup_logger(__name__)


def check_ffmpeg_installed() -> bool:
    """
    Проверяет, установлен ли FFmpeg в системе.

    Returns:
        bool: True, если FFmpeg установлен, иначе False.
    """
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return result.returncode == 0
    except Exception as e:
        logger.error(f"Ошибка при проверке FFmpeg: {e}", exc_info=True)
        return False


def convert_to_format(
    input_path: Path,
    output_format: str,
    session_id: str,
    output_filename: str | None = None,
) -> Path:
    """
    Конвертирует файл в другой формат.

    Args:
        input_path (Path): Путь к входному файлу.
        output_format (str): Формат выходного файла (mp4, mp3, и т.д.).
        session_id (str): Идентификатор сессии.
        output_filename (str | None, optional): Имя выходного файла.
                                                  По умолчанию используется имя входного файла.

    Returns:
        Path: Путь к конвертированному файлу.

    Raises:
        Exception: Если произошла ошибка при конвертации.
    """
    logger.info(f"Конвертация файла {input_path} в формат {output_format}")

    if not check_ffmpeg_installed():
        raise Exception(
            "FFmpeg не установлен. Установите FFmpeg для конвертации файлов."
        )

    try:
        if output_filename is None:
            output_filename = f"{input_path.stem}.{output_format}"

        output_path = get_temp_file_path(session_id, output_filename)

        # Команда FFmpeg для конвертации
        cmd = [
            "ffmpeg",
            "-i",
            str(input_path),
            "-y",  # Перезаписать выходной файл, если он существует
            str(output_path),
        ]

        # Запуск процесса FFmpeg
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )

        try:
            stdout, stderr = process.communicate(timeout=BLOCKING_TASK_TIMEOUT)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            raise Exception(
                f"FFmpeg процесс превысил лимит времени в {BLOCKING_TASK_TIMEOUT} секунд."
            )

        if process.returncode != 0:
            logger.error(f"Ошибка FFmpeg: {stderr}")
            raise Exception(f"Ошибка при конвертации файла: {stderr}")

        # Проверяем, существует ли созданный файл
        if not output_path.exists():
            raise Exception("Конвертированный файл не был создан")

        # Проверяем размер файла
        file_size = output_path.stat().st_size
        if file_size > MAX_FILE_SIZE:
            logger.warning(
                f"Размер конвертированного файла превышает лимит: {file_size} байт"
            )
            # Вместо исключения, попробуем сжать файл
            compressed_path = compress_file(output_path, session_id)
            return compressed_path

        logger.info(f"Конвертация завершена: {output_path}")
        return output_path

    except Exception as e:
        e.add_note(
            f"input_path={input_path}, output_format={output_format}, session_id={session_id}"
        )
        logger.error(f"Ошибка при конвертации файла: {e}", exc_info=True)
        raise


def convert_webm_to_mp4(input_path: Path, session_id: str) -> Path:
    """Упрощённая обёртка для конвертации webm → mp4.

    Используется в youtube_utils; в тестах может вызываться без установленного ffmpeg,
    поэтому логика совпадает с convert_to_format, но оставляет исключения, если ffmpeg отсутствует.
    """
    return convert_to_format(input_path, "mp4", session_id)


def compress_file(
    input_path: Path,
    session_id: str,
    target_size: int = MAX_FILE_SIZE,
    output_filename: str | None = None,
) -> Path:
    """
    Сжимает файл до указанного размера.

    Args:
        input_path (Path): Путь к входному файлу.
        session_id (str): Идентификатор сессии.
        target_size (int, optional): Целевой размер файла в байтах.
                                    По умолчанию MAX_FILE_SIZE.
        output_filename (str | None, optional): Имя выходного файла.
                                                  По умолчанию используется имя входного файла.

    Returns:
        Path: Путь к сжатому файлу.

    Raises:
        Exception: Если произошла ошибка при сжатии.
    """
    logger.info(f"Сжатие файла {input_path} до размера {target_size} байт")

    if not check_ffmpeg_installed():
        raise Exception("FFmpeg не установлен. Установите FFmpeg для сжатия файлов.")

    try:
        if output_filename is None:
            output_filename = f"compressed_{input_path.stem}{input_path.suffix}"

        output_path = get_temp_file_path(session_id, output_filename)

        # Получаем информацию о входном файле через JSON
        probe_cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_format",
            "-print_format",
            "json",
            str(input_path),
        ]

        probe_process = subprocess.Popen(
            probe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )

        try:
            probe_stdout, probe_stderr = probe_process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            probe_process.kill()
            probe_stdout, probe_stderr = probe_process.communicate()
            raise Exception("FFprobe процесс превысил лимит времени в 15 секунд.")

        if probe_process.returncode != 0:
            logger.error(f"Ошибка FFprobe: {probe_stderr}")
            raise Exception(f"Ошибка при получении информации о файле: {probe_stderr}")

        # Безопасно парсим JSON-вывод FFprobe
        try:
            probe_data = json.loads(probe_stdout)
            format_info = probe_data.get("format", {})
            duration_str = format_info.get("duration")
            duration = float(duration_str) if duration_str else 0.0

            bit_rate_str = format_info.get("bit_rate")
            bit_rate = (
                int(bit_rate_str) if (bit_rate_str and bit_rate_str != "N/A") else 0
            )
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Не удалось распарсить вывод FFprobe: {e}")
            raise Exception(f"Ошибка при разборе метаданных файла: {e}")

        if duration <= 0.0:
            raise Exception(
                "Не удалось определить длительность медиафайла или она равна нулю."
            )

        # Если bit_rate пусто, вычисляем его из размера файла и длительности
        if bit_rate <= 0:
            file_size = input_path.stat().st_size
            bit_rate = int(file_size * 8 / duration)

        # Вычисляем новый битрейт для достижения целевого размера
        target_bit_rate = int(
            (target_size * 0.95 * 8) / duration
        )  # 95% от целевого размера для запаса

        # Вычисляем видеобитрейт как остаток от целевого битрейта за вычетом аудио (128k).
        # Задаем нижний порог в 100 kbps, чтобы видео не превратилось в шум.
        video_bitrate = max(target_bit_rate - 128_000, 100_000)

        # Команда FFmpeg для сжатия
        cmd = [
            "ffmpeg",
            "-i",
            str(input_path),
            "-b:v",
            f"{video_bitrate}",
            "-maxrate",
            f"{video_bitrate}",
            "-bufsize",
            f"{video_bitrate // 2}",
            "-b:a",
            "128k",  # Фиксированный битрейт для аудио
            "-y",  # Перезаписать выходной файл, если он существует
            str(output_path),
        ]

        # Запуск процесса FFmpeg
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )

        try:
            stdout, stderr = process.communicate(timeout=BLOCKING_TASK_TIMEOUT)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            raise Exception(
                f"FFmpeg процесс превысил лимит времени в {BLOCKING_TASK_TIMEOUT} секунд."
            )

        if process.returncode != 0:
            logger.error(f"Ошибка FFmpeg: {stderr}")
            raise Exception(f"Ошибка при сжатии файла: {stderr}")

        # Проверяем, существует ли созданный файл
        if not output_path.exists():
            raise Exception("Сжатый файл не был создан")

        # Проверяем размер файла
        compressed_size = output_path.stat().st_size
        if compressed_size > target_size:
            logger.warning(
                f"Сжатие не достигло целевого размера: {compressed_size} байт"
            )
            # Можно попробовать сжать еще раз с более низким битрейтом или вернуть ошибку
            raise Exception(
                f"Не удалось сжать файл до {target_size // (1024 * 1024)} МБ."
            )

        logger.info(f"Сжатие завершено: {output_path}, размер: {compressed_size} байт")
        return output_path

    except Exception as e:
        e.add_note(
            f"input_path={input_path}, session_id={session_id}, target_size={target_size}"
        )
        logger.error(f"Ошибка при сжатии файла: {e}", exc_info=True)
        raise


def convert_to_mp3_with_compression(
    input_path: Path, session_id: str, output_filename: str | None = None
) -> Path:
    """
    Конвертирует аудиофайл в MP3 с уменьшением размера примерно на 50%.

    Args:
        input_path (Path): Путь к входному аудиофайлу.
        session_id (str): Идентификатор сессии.
        output_filename (str | None, optional): Имя выходного файла. По умолчанию используется имя входного файла с расширением .mp3.

    Returns:
        Path: Путь к сжатому MP3-файлу.

    Raises:
        Exception: Если произошла ошибка при конвертации или сжатии.
    """
    logger.info(
        f"Конвертация и сжатие файла {input_path} в MP3 с уменьшением размера на 50%"
    )

    if not check_ffmpeg_installed():
        raise Exception(
            "FFmpeg не установлен. Установите FFmpeg для конвертации файлов."
        )

    try:
        # Получаем информацию о входном файле через JSON
        probe_cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_format",
            "-print_format",
            "json",
            str(input_path),
        ]
        probe_process = subprocess.Popen(
            probe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        try:
            probe_stdout, probe_stderr = probe_process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            probe_process.kill()
            probe_stdout, probe_stderr = probe_process.communicate()
            raise Exception("FFprobe процесс превысил лимит времени в 15 секунд.")

        if probe_process.returncode != 0:
            logger.error(f"Ошибка FFprobe: {probe_stderr}")
            raise Exception(f"Ошибка при получении информации о файле: {probe_stderr}")

        try:
            probe_data = json.loads(probe_stdout)
            format_info = probe_data.get("format", {})
            duration_str = format_info.get("duration")
            duration = float(duration_str) if duration_str else 0.0

            bit_rate_str = format_info.get("bit_rate")
            bit_rate = (
                int(bit_rate_str) if (bit_rate_str and bit_rate_str != "N/A") else 0
            )
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Не удалось распарсить вывод FFprobe: {e}")
            raise Exception(f"Ошибка при разборе метаданных файла: {e}")

        if duration <= 0.0:
            raise Exception(
                "Не удалось определить длительность медиафайла или она равна нулю."
            )

        if bit_rate <= 0:
            file_size = input_path.stat().st_size
            bit_rate = int(file_size * 8 / duration)

        # Новый битрейт для уменьшения размера на 50% с защитным порогом 64 kbps
        target_bit_rate = max(int(bit_rate // 2), 64_000)
        if output_filename is None:
            output_filename = f"{input_path.stem}_compressed.mp3"
        output_path = get_temp_file_path(session_id, output_filename)
        # Команда FFmpeg для конвертации и сжатия
        cmd = [
            "ffmpeg",
            "-i",
            str(input_path),
            "-b:a",
            f"{target_bit_rate}",
            "-y",
            str(output_path),
        ]
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        try:
            stdout, stderr = process.communicate(timeout=BLOCKING_TASK_TIMEOUT)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            raise Exception(
                f"FFmpeg процесс превысил лимит времени в {BLOCKING_TASK_TIMEOUT} секунд."
            )
        if process.returncode != 0:
            logger.error(f"Ошибка FFmpeg: {stderr}")
            raise Exception(f"Ошибка при конвертации файла: {stderr}")
        if not output_path.exists():
            raise Exception("Сжатый MP3-файл не был создан")
        logger.info(f"Конвертация и сжатие завершены: {output_path}")
        return output_path
    except Exception as e:
        e.add_note(f"input_path={input_path}, session_id={session_id}")
        logger.error(f"Ошибка при конвертации и сжатии файла: {e}", exc_info=True)
        raise
