from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant


class MeasurementStore:
    def __init__(self, hass: HomeAssistant, path: str) -> None:
        self._hass = hass
        self._path = Path(path)

    async def async_load(self) -> dict[str, Any] | None:
        return await self._hass.async_add_executor_job(self._load)

    def _load(self) -> dict[str, Any] | None:
        try:
            with self._path.open(encoding="utf-8") as source:
                return json.load(source)
        except FileNotFoundError, json.JSONDecodeError:
            return None

    async def async_save(self, data: dict[str, Any]) -> None:
        await self._hass.async_add_executor_job(self._save, data)

    def _save(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self._path.parent, delete=False
            ) as target:
                temporary = Path(target.name)
                os.fchmod(target.fileno(), 0o600)
                json.dump(data, target, ensure_ascii=False, allow_nan=False)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self._path)
            temporary = None
            directory_fd = os.open(self._path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
