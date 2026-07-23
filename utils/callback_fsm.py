"""Типизированное ядро callback FSM и хранилище пользовательских сессий."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CallbackEvent:
    """Разобранное событие inline-кнопки."""

    scope: str
    action: str
    session_token: str | None = None
    value: str | None = None

    @classmethod
    def parse(cls, data: str) -> CallbackEvent | None:
        parts = data.split("|")
        match parts:
            case ["s", token, "main", action] if token and action:
                return cls(scope="main", action=action, session_token=token)
            case ["s", token, "format", action, value] if token and action and value:
                return cls(
                    scope="format",
                    action=action,
                    session_token=token,
                    value=value,
                )
            case ["csi", rating] if rating.isdigit() and 0 <= int(rating) <= 10:
                return cls(scope="csi", action="rate", value=rating)
            case _:
                return None


class SessionStore:
    """Ограниченное хранилище FSM-сессий внутри ``context.user_data``."""

    def __init__(
        self,
        user_data: MutableMapping[str, Any],
        *,
        key: str = "sessions",
        max_active: int = 5,
        token_factory: Callable[[], str] | None = None,
        cleanup_session: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self._user_data = user_data
        self._key = key
        self._max_active = max_active
        self._token_factory = token_factory or (lambda: uuid.uuid4().hex[:8])
        self._cleanup_session = cleanup_session
        self._clock = clock

    @property
    def data(self) -> dict[str, dict[str, Any]]:
        store = self._user_data.get(self._key)
        if not isinstance(store, dict):
            store = {}
            self._user_data[self._key] = store
        return store

    def create(
        self,
        *,
        url: str,
        video_info: dict,
        session_id: str,
        platform: str,
        formats: dict,
    ) -> str:
        store = self.data
        token = self._token_factory()
        while token in store:
            token = self._token_factory()

        store[token] = {
            "url": url,
            "video_info": video_info,
            "session_id": session_id,
            "platform": platform,
            "formats": formats,
            "created_at": self._clock(),
        }

        while len(store) > self._max_active:
            oldest = min(
                store,
                key=lambda current: float(store[current].get("created_at", 0.0)),
            )
            evicted = store.pop(oldest)
            evicted_session_id = evicted.get("session_id")
            if evicted_session_id and self._cleanup_session:
                self._cleanup_session(evicted_session_id)
        return token

    def get(self, token: str) -> dict[str, Any] | None:
        return self.data.get(token)

    def remove(self, token: str) -> dict[str, Any] | None:
        return self.data.pop(token, None)
