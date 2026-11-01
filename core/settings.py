"""Small persistent user preferences (``settings.json`` in the user data folder from :mod:`core.paths`)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

SETTINGS_FILE = "settings.json"
THEMES = ("dark", "light")
DEFAULTS: dict[str, Any] = {"theme": "dark"}


class Settings:
    """Load-on-create, save-on-change settings store. Unknown keys are preserved; bad values fall back to
    defaults, and a corrupt file never stops the app from starting."""

    def __init__(self, folder: Path | str):
        self.path = Path(folder) / SETTINGS_FILE
        self._data: dict[str, Any] = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        self._data = dict(DEFAULTS)
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(raw, dict):
            self._data.update(raw)
        if self._data.get("theme") not in THEMES:
            self._data["theme"] = DEFAULTS["theme"]

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".settings", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2)
                fh.write("\n")
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @property
    def theme(self) -> str:
        return self._data["theme"]

    @theme.setter
    def theme(self, value: str) -> None:
        if value not in THEMES:
            raise ValueError(f"unknown theme {value!r}")
        self.set("theme", value)
