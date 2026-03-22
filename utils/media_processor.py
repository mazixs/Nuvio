"""
Модуль для обработки медиафайлов с использованием FFmpeg.
"""

import subprocess
from pathlib import Path
from config import MAX_FILE_SIZE
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
            text=True
        )
        return result.returncode == 0
    except Exception as e:
        logger.error(f"Ошибка при проверке FFmpeg: {e}", exc_info=True)
        return False

def merge_audio_video(
    video_path: Path, 
    audio_path: Path, 
    session_id: str, 
    output_filename: str | None = None
) -> Path:
    """
    Объединяет видео и аудио файлы.
    
    Args:
        video_path (Path): Путь к видео файлу.
        audio_path (Path): Путь к аудио файлу.
        session_id (str): Идентификатор сессии.
        output_filename (str | None, optional): Имя выходного файла. 
                                                  По умолчанию используется имя видео файла.
    
    Returns:
        Path: Путь к объединенному файлу.
        
    Raises:
        Exception: Если произошла ошибка при объединении файлов.
    """
    logger.info(f"Объединение видео {video_path} и аудио {audio_path}")
    
    if not check_ffmpeg_installed():
        raise Exception("FFmpeg не установлен. Установите FFmpeg для объединения файлов.")
    
    try:
        if output_filename is None:
            output_filename = f"merged_{video_path.stem}.{video_path.suffix.lstrip('.')}"
        
        output_path = get_temp_file_path(session_id, output_filename)
        
        # Команда FFmpeg для объединения видео и аудио
        cmd = [
            "ffmpeg",
            "-i", str(video_path),
            "-i", str(audio_path),
            "-c:v", "copy",  # Копирование видео без перекодирования
            "-c:a", "aac",   # Аудио кодек AAC
            "-strict", "experimental",
            "-y",  # Перезаписать выходной файл, если он существует
            str(output_path)
        ]
        
        # Запуск процесса FFmpeg
        process = subprocess.Popen(
            cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            text=True
        )
        
        stdout, stderr = process.communicate()
        
        if process.returncode != 0:
            logger.error(f"Ошибка FFmpeg: {stderr}")
            raise Exception(f"Ошибка при объединении файлов: {stderr}")
        
        # Проверяем, существует ли созданный файл
        if not output_path.exists():
            raise Exception("Объединенный файл не был создан")
        
        # Проверяем размер файла
        file_size = output_path.stat().st_size
        if file_size > MAX_FILE_SIZE:
            logger.warning(f"Размер объединенного файла превышает лимит: {file_size} байт")
            # Вместо исключения, попробуем сжать файл
            compressed_path = compress_file(output_path, session_id)
            return compressed_path
        
        logger.info(f"Объединение завершено: {output_path}")
        return output_path
    
    except Exception as e:
        e.add_note(f"video_path={video_path}, audio_path={audio_path}, session_id={session_id}")
        logger.error(f"Ошибка при объединении файлов: {e}", exc_info=True)
        raise

def convert_to_format(
    input_path: Path, 
    output_format: str, 
    session_id: str, 
    output_filename: str | None = None
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
        raise Exception("FFmpeg не установлен. Установите FFmpeg для конвертации файлов.")
    
    try:
        if output_filename is None:
            output_filename = f"{input_path.stem}.{output_format}"
        
        output_path = get_temp_file_path(session_id, output_filename)
        
        # Команда FFmpeg для конвертации
        cmd = [
            "ffmpeg",
            "-i", str(input_path),
            "-y",  # Перезаписать выходной файл, если он существует
            str(output_path)
        ]
        
        # Запуск процесса FFmpeg
        process = subprocess.Popen(
            cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            text=True
        )
        
        stdout, stderr = process.communicate()
        
        if process.returncode != 0:
            logger.error(f"Ошибка FFmpeg: {stderr}")
            raise Exception(f"Ошибка при конвертации файла: {stderr}")
        
        # Проверяем, существует ли созданный файл
        if not output_path.exists():
            raise Exception("Конвертированный файл не был создан")
        
        # Проверяем размер файла
        file_size = output_path.stat().st_size
        if file_size > MAX_FILE_SIZE:
            logger.warning(f"Размер конвертированного файла превышает лимит: {file_size} байт")
            # Вместо исключения, попробуем сжать файл
            compressed_path = compress_file(output_path, session_id)
            return compressed_path
        
        logger.info(f"Конвертация завершена: {output_path}")
        return output_path
    
    except Exception as e:
        e.add_note(f"input_path={input_path}, output_format={output_format}, session_id={session_id}")
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
    output_filename: str | None = None
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
        
        # Получаем информацию о входном файле
        probe_cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration,bit_rate",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(input_path)
        ]
        
        probe_process = subprocess.Popen(
            probe_cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            text=True
        )
        
        probe_stdout, probe_stderr = probe_process.communicate()
        
        if probe_process.returncode != 0:
            logger.error(f"Ошибка FFprobe: {probe_stderr}")
            raise Exception(f"Ошибка при получении информации о файле: {probe_stderr}")
        
        # Парсим вывод FFprobe
        duration, bit_rate = probe_stdout.strip().split('\n')
        duration = float(duration)
        
        # Если bit_rate пусто, вычисляем его из размера файла и длительности
        if not bit_rate or bit_rate == 'N/A':
            file_size = input_path.stat().st_size
            bit_rate = int(file_size * 8 / duration)
        else:
            bit_rate = int(bit_rate)
        
        # Вычисляем новый битрейт для достижения целевого размера
        target_bit_rate = int((target_size * 0.95 * 8) / duration)  # 95% от целевого размера для запаса
        
        # Команда FFmpeg для сжатия
        cmd = [
            "ffmpeg",
            "-i", str(input_path),
            "-b:v", f"{target_bit_rate // 2}",  # Половина битрейта для видео
            "-maxrate", f"{target_bit_rate // 2}",
            "-bufsize", f"{target_bit_rate // 4}",
            "-b:a", "128k",  # Фиксированный битрейт для аудио
            "-y",  # Перезаписать выходной файл, если он существует
            str(output_path)
        ]
        
        # Запуск процесса FFmpeg
        process = subprocess.Popen(
            cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            text=True
        )
        
        stdout, stderr = process.communicate()
        
        if process.returncode != 0:
            logger.error(f"Ошибка FFmpeg: {stderr}")
            raise Exception(f"Ошибка при сжатии файла: {stderr}")
        
        # Проверяем, существует ли созданный файл
        if not output_path.exists():
            raise Exception("Сжатый файл не был создан")
        
        # Проверяем размер файла
        compressed_size = output_path.stat().st_size
        if compressed_size > target_size:
            logger.warning(f"Сжатие не достигло целевого размера: {compressed_size} байт")
            # Можно попробовать сжать еще раз с более низким битрейтом или вернуть ошибку
            raise Exception(f"Не удалось сжать файл до {target_size // (1024 * 1024)} МБ.")
        
        logger.info(f"Сжатие завершено: {output_path}, размер: {compressed_size} байт")
        return output_path
    
    except Exception as e:
        e.add_note(f"input_path={input_path}, session_id={session_id}, target_size={target_size}")
        logger.error(f"Ошибка при сжатии файла: {e}", exc_info=True)
        raise

def convert_to_mp3_with_compression(
    input_path: Path,
    session_id: str,
    output_filename: str | None = None
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
    logger.info(f"Конвертация и сжатие файла {input_path} в MP3 с уменьшением размера на 50%")

    if not check_ffmpeg_installed():
        raise Exception("FFmpeg не установлен. Установите FFmpeg для конвертации файлов.")

    try:
        # Получаем информацию о входном файле
        probe_cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration,bit_rate",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(input_path)
        ]
        probe_process = subprocess.Popen(
            probe_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        probe_stdout, probe_stderr = probe_process.communicate()
        if probe_process.returncode != 0:
            logger.error(f"Ошибка FFprobe: {probe_stderr}")
            raise Exception(f"Ошибка при получении информации о файле: {probe_stderr}")
        duration, bit_rate = probe_stdout.strip().split('\n')
        duration = float(duration)
        if not bit_rate or bit_rate == 'N/A':
            file_size = input_path.stat().st_size
            bit_rate = int(file_size * 8 / duration)
        else:
            bit_rate = int(bit_rate)
        # Новый битрейт для уменьшения размера на 50%
        target_bit_rate = int(bit_rate // 2)
        if output_filename is None:
            output_filename = f"{input_path.stem}_compressed.mp3"
        output_path = get_temp_file_path(session_id, output_filename)
        # Команда FFmpeg для конвертации и сжатия
        cmd = [
            "ffmpeg",
            "-i", str(input_path),
            "-b:a", f"{target_bit_rate}",
            "-y",
            str(output_path)
        ]
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        stdout, stderr = process.communicate()
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
