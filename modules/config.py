import json
from pathlib import Path


class Config:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._config = {}
            cls._instance._loaded = False
        return cls._instance

    def _get_config_path(self):
        config_dir = Path.home() / "DeskFlow"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / "config.json"

    def _load(self):
        if self._loaded:
            return
        path = self._get_config_path()
        if path.exists():
            try:
                self._config = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                self._config = {}
        else:
            self._config = {}
        self._loaded = True

    def save(self):
        path = self._get_config_path()
        path.write_text(
            json.dumps(self._config, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

    def get(self, key, default=None):
        self._load()
        return self._config.get(key, default)

    def set(self, key, value):
        self._load()
        self._config[key] = value
        self.save()