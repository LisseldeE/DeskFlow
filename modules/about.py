"""About page and check-for-update logic for DeskFlow.

The update check mirrors the reference LANSyncBox implementation: it picks
GitHub or Gitee based on the UI language, fetches that repo's Renew.json
(which carries the newest version), and compares it against the local one.
"""
import re
import json
import urllib.request
import urllib.error
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton, QMessageBox,
    QApplication, QFrame
)
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPalette

from modules.config import Config
from modules.i18n import I18n


# ---------- version helpers ----------

def _extract_version(text):
    """Pull a dotted version like '1.2.3' out of a tag name. Returns a tuple
    of ints, or None if no version-like token is found."""
    matches = re.findall(r'(\d+(?:\.\d+)*)', text)
    if not matches:
        return None
    best = max(matches, key=lambda t: t.count('.'))
    return tuple(int(x) for x in best.split('.'))


def _compare(a, b):
    """Compare two version tuples of possibly different lengths
    (zero-padded, segment by segment). Returns >0 / 0 / <0."""
    a = list(a)
    b = list(b)
    n = max(len(a), len(b))
    while len(a) < n:
        a.append(0)
    while len(b) < n:
        b.append(0)
    return (a > b) - (a < b)


def _show_message(parent, title, text, icon):
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(icon)
    box.addButton(I18n.tr("ok"), QMessageBox.AcceptRole)
    box.exec()


def check_update(parent=None):
    """Check for a newer version and prompt to download if one exists.

    Mirrors the LANSyncBox reference: pick GitHub or Gitee based on the UI
    language, fetch that repo's root Renew.json (which carries the latest
    version in its "Renew" field), and compare it against the local one.
    """
    try:
        if I18n.get_language() == "zh_CN":
            renew_url = Config.GITEE_RENEW_URL
            releases_url = Config.GITEE_RELEASES
        else:
            renew_url = Config.GITHUB_RENEW_URL
            releases_url = Config.GITHUB_RELEASES

        req = urllib.request.Request(renew_url)
        req.add_header('User-Agent', Config.APP_NAME)
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())

        # The Renew.json may spell the version under "Renew" or "renew";
        # fall back to scanning all string values for a dotted version.
        latest_version = None
        if isinstance(data, dict):
            for key in ("Renew", "renew"):
                raw = data.get(key, "")
                latest_version = _extract_version(str(raw))
                if latest_version:
                    break
        if latest_version is None:
            _show_message(parent, I18n.tr("about_check_update"),
                          I18n.tr("about_no_tags"), QMessageBox.Warning)
            return

        current = _extract_version(Config.APP_VERSION)
        if current is None:
            _show_message(parent, I18n.tr("about_check_update"),
                          I18n.tr("about_parse_error"), QMessageBox.Warning)
            return

        if _compare(latest_version, current) > 0:
            revision = ".".join(str(x) for x in latest_version)
            box = QMessageBox(parent)
            box.setWindowTitle(I18n.tr("about_check_update"))
            box.setText(I18n.tr("about_new_version", version=revision))
            box.setIcon(QMessageBox.NoIcon)
            yes = box.addButton(I18n.tr("about_yes"), QMessageBox.YesRole)
            box.addButton(I18n.tr("about_no"), QMessageBox.NoRole)
            box.exec()
            if box.clickedButton() == yes:
                QDesktopServices.openUrl(QUrl(releases_url))
        else:
            _show_message(parent, I18n.tr("about_check_update"),
                          I18n.tr("about_latest"), QMessageBox.Information)

    except urllib.error.URLError as e:
        _show_message(parent, I18n.tr("about_check_update"),
                      I18n.tr("about_network_error", error=str(e)),
                      QMessageBox.Warning)
    except Exception as e:
        _show_message(parent, I18n.tr("about_check_update"),
                      I18n.tr("about_check_failed", error=str(e)),
                      QMessageBox.Warning)


# ---------- about page widget ----------

def _section_label(text):
    label = QLabel(text)
    label.setStyleSheet("font-size: 12px; color: #868e96;")
    return label


class AboutPage(QWidget):
    """About settings page: app info + check-for-update button."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("about_page")
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(10)

        def _accent_hex():
            c = QApplication.palette().color(QPalette.Highlight)
            return f"#{c.red():02x}{c.green():02x}{c.blue():02x}"

        title = QLabel(Config.APP_NAME)
        title.setAlignment(Qt.AlignLeft)
        title.setStyleSheet(f"font-size: 26px; font-weight: bold; "
                            f"color: {_accent_hex()};")
        layout.addWidget(title)

        version = QLabel(f"{I18n.tr('about_version_label')}: "
                         f"{Config.APP_VERSION}")
        version.setStyleSheet("font-size: 12px; color: #495057;")
        layout.addWidget(version)

        author = QLabel(f"{I18n.tr('about_author')}: {Config.APP_AUTHOR}")
        author.setStyleSheet("font-size: 11px; color: #868e96;")
        layout.addWidget(author)

        repo = _section_label(f"GitHub: {Config.GITHUB_REPO}")
        layout.addWidget(repo)

        line = QFrame(self)
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: rgba(120,120,120,90);")
        layout.addSpacing(6)
        layout.addWidget(line)
        layout.addSpacing(6)

        if Config.ENABLE_CHECK_UPDATE:
            check_btn = QPushButton(I18n.tr("about_check_update"))
            check_btn.setCursor(Qt.PointingHandCursor)
            check_btn.setFixedSize(140, 34)
            check_btn.setStyleSheet(
                f"QPushButton {{ background: {_accent_hex()}; color: white;"
                f" border: none; border-radius: 8px; font-size: 13px; }}"
                f"QPushButton:hover {{ opacity: .9; }}")
            check_btn.clicked.connect(
                lambda: check_update(self.window()))
            layout.addWidget(check_btn, alignment=Qt.AlignLeft)

        layout.addStretch()