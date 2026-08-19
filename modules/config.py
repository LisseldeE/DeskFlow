import json
from pathlib import Path


class Config:
    _instance = None

    # --- App info (used by the About page / check-for-update) ---
    APP_NAME = "DeskFlow"
    APP_VERSION = "1.1.1.0"
    APP_AUTHOR = "Lisselde_E"
    APP_AUTHOR_LINK = "https://github.com/"

    # Feature switch: show the "check for updates" button.
    ENABLE_CHECK_UPDATE = True

    # Repository info for the update checker.
    GITHUB_REPO = "LisseldeE/DeskFlow"
    GITEE_REPO = "Lisselde_E/DeskFlow"

    # Version metadata lives in a Renew.json at each repo root. GitHub is
    # checked when the app language is not zh_CN, Gitee otherwise — mirroring
    # the LANSyncBox reference behaviour.
    GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}/tags"
    GITEE_API = f"https://gitee.com/api/v5/repos/{GITEE_REPO}/tags"
    GITHUB_RELEASES = f"https://github.com/{GITHUB_REPO}/releases"
    GITEE_RELEASES = f"https://gitee.com/{GITEE_REPO}/releases"
    # Raw Renew.json exposing the latest version from each repo.
    GITHUB_RENEW_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/Renew.json"
    GITEE_RENEW_URL = f"https://gitee.com/{GITEE_REPO}/raw/main/Renew.json"

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