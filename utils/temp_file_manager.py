"""
Модуль для управления временными файлами.
"""

import shutil
import uuid
from pathlib import Path
from config import TEMP_DIR
from utils.logger import setup_logger

logger = setup_logger(__name__)

def create_temp_dir(session_id: str | None = None) -> Path:
    """
    Создаёт временную директорию для пользовательской сессии.
    
    Args:
        session_id (str, optional): Идентификатор сессии. Если не указан, 
                                    создается случайный идентификатор.
    
    Returns:
        Path: Путь к созданной временной директории
    """
    if session_id is None:
        session_id = str(uuid.uuid4())
    
    temp_path = TEMP_DIR / session_id
    temp_path.mkdir(parents=True, exist_ok=True)
    
    logger.debug(f"Создана временная директория: {temp_path}")
    return temp_path

def get_temp_file_path(session_id: str, filename: str) -> Path:
    """
    Получает путь к временному файлу.
    
    Args:
        session_id (str): Идентификатор сессии.
        filename (str): Имя файла.
    
    Returns:
        Path: Путь к временному файлу
    """
    temp_path = TEMP_DIR / session_id
    temp_path.mkdir(parents=True, exist_ok=True)
    return temp_path / filename

def cleanup_temp_files(session_id=None):
    """
    Очищает временные файлы.
    
    Args:
        session_id (str, optional): Идентификатор сессии. Если не указан, 
                                    очищаются все временные файлы.
    """
    try:
        if session_id:
            temp_path = TEMP_DIR / session_id
            if temp_path.exists():
                shutil.rmtree(temp_path)
                logger.debug(f"Удалена временная директория: {temp_path}")
        else:
            # Проверяем, существует ли директория перед удалением содержимого
            if TEMP_DIR.exists():
                # Удаляем только содержимое, сохраняя саму директорию
                for item in TEMP_DIR.iterdir():
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
                logger.debug(f"Очищена директория временных файлов: {TEMP_DIR}")
    except FileNotFoundError as e:
        logger.warning(f"Файл или директория не найдены при очистке: {e}")
    except PermissionError as e:
        logger.error(f"Нет прав доступа для удаления временных файлов: {e}")
    except OSError as e:
        logger.error(f"Ошибка операционной системы при очистке временных файлов: {e}")
    except Exception as e:
        logger.error(f"Неожиданная ошибка при очистке временных файлов: {e}", exc_info=True)
