import mimetypes
from functools import lru_cache
from pathlib import Path

import httpx

from config import GOKAPI_API_KEY, GOKAPI_BASE_URL
from utils.logger import setup_logger

logger = setup_logger(__name__)


class GokapiConfigError(ValueError):
    """Выбрасывается, если конфигурация Gokapi не задана."""


def is_gokapi_configured() -> bool:
    """Проверяет, настроен ли Gokapi (без выполнения запроса)."""
    base_url = (GOKAPI_BASE_URL or "").strip()
    api_key = (GOKAPI_API_KEY or "").strip()
    return bool(base_url and api_key)


@lru_cache(maxsize=1)
def require_gokapi_config() -> tuple[str, str]:
    """Возвращает валидированный (base_url, api_key) или выбрасывает ошибку."""
    base_url = (GOKAPI_BASE_URL or "").strip()
    api_key = (GOKAPI_API_KEY or "").strip()

    if not base_url:
        raise GokapiConfigError("GOKAPI_BASE_URL не установлен в переменных окружении")
    if not api_key:
        raise GokapiConfigError("GOKAPI_API_KEY не установлен в переменных окружениях")

    if not base_url.endswith('/'):
        base_url += '/'

    return base_url, api_key


def upload_to_gokapi(file_path: Path) -> tuple[bool, str]:
    """
    Загружает файл на сервис Gokapi и возвращает ссылку для скачивания.
    Args:
        file_path (Path): Путь к файлу для загрузки.
    Returns:
        tuple[bool, str]: (успех, ссылка или сообщение об ошибке)
    """
    if not file_path.exists():
        logger.error("Файл не существует: %s", file_path)
        return False, "Файл не существует"

    try:
        base_url, api_key = require_gokapi_config()
    except GokapiConfigError as cfg_err:
        logger.error("Некорректная конфигурация Gokapi: %s", cfg_err)
        return False, "Сервер загрузки больших файлов не настроен. Обратитесь к администратору."
    try:
        url = base_url + "files/add"
        headers = {"apikey": api_key}

        content_type, _ = mimetypes.guess_type(file_path)
        if content_type is None:
            content_type = "application/octet-stream"

        with open(file_path, "rb") as f:
            files = {"file": (file_path.name, f, content_type)}
            upload_params = {
                "allowedDownloads": "1",
                "expiryDays": "7",
            }
            logger.info(f"Отправка файла на Gokapi: {url}, имя: {file_path.name}, размер: {file_path.stat().st_size}, Content-Type: {content_type}")
            response = httpx.post(url, headers=headers, files=files, data=upload_params, timeout=120.0)

        logger.info(f"Ответ Gokapi: статус={response.status_code}, заголовки={response.headers}, тело={response.text}")

        if response.status_code == 200:
            try:
                data = response.json()
                if data.get("Result") == "OK" and data.get("FileInfo") and "UrlDownload" in data["FileInfo"]:
                    download_url = data["FileInfo"]["UrlDownload"]
                    logger.info(f"Файл успешно загружен на Gokapi: {download_url}")
                    return True, download_url
                elif "UrlDownload" in data:
                    logger.warning(f"Структура ответа Gokapi отличается, но UrlDownload найден: {data.get('UrlDownload')}")
                    return True, data.get('UrlDownload')
                else:
                    logger.error(f"Ошибка Gokapi (неверный формат ответа): {data}")
                    return False, f"Ошибка Gokapi (неверный формат ответа): {data.get('ErrorMessage', 'Неизвестная ошибка')}"
            except ValueError:
                logger.error(f"Ошибка Gokapi (ответ не JSON): {response.text}")
                return False, f"Ошибка Gokapi (ответ не JSON): {response.text}"
        else:
            logger.error(f"Ошибка API: HTTP {response.status_code}, тело: {response.text}")

            if response.status_code == 502:
                return False, "Сервер загрузки временно недоступен. Попробуйте позже."
            elif response.status_code == 503:
                return False, "Сервер загрузки перегружен. Попробуйте через несколько минут."
            elif response.status_code == 401:
                return False, "Ошибка авторизации на сервере загрузки."
            elif response.status_code >= 500:
                return False, f"Ошибка сервера загрузки (код {response.status_code})."
            else:
                return False, f"Ошибка при загрузке файла (код {response.status_code})."

    except httpx.ConnectError as e:
        logger.error(f"Ошибка соединения с Gokapi: {str(e)}")
        return False, f"Ошибка соединения с Gokapi: {str(e)}"
    except httpx.TimeoutException as e:
        logger.error(f"Таймаут при загрузке файла на Gokapi: {str(e)}")
        return False, f"Таймаут при загрузке файла: {str(e)}"
    except httpx.HTTPError as e:
        logger.error(f"Ошибка HTTP запроса к Gokapi: {str(e)}")
        return False, f"Ошибка HTTP запроса: {str(e)}"
    except FileNotFoundError as e:
        logger.error(f"Файл не найден: {str(e)}")
        return False, f"Файл не найден: {str(e)}"
    except PermissionError as e:
        logger.error(f"Нет прав доступа к файлу: {str(e)}")
        return False, f"Нет прав доступа к файлу: {str(e)}"
    except Exception as e:
        e.add_note(f"file_path={file_path}")
        logger.error(f"Неожиданная ошибка при загрузке файла на Gokapi: {str(e)}", exc_info=True)
        return False, f"Неожиданная ошибка при загрузке файла: {str(e)}"

