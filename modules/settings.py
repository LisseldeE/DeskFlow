"""Settings dialog for DeskFlow.

Split-pane layout: a left navigation column lets the user switch between the
General / Translate / System / About sub-cards, and the right pane shows the
settings for the selected section (task 4). The About page also carries the
check-for-update logic (task 5)."""
import sys
import os
import winreg
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QCheckBox,
    QListWidget, QListWidgetItem, QStackedWidget, QWidget, QFrame,
    QApplication
)
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QGuiApplication, QPalette
from modules.config import Config
from modules.i18n import I18n
from modules.about import AboutPage


def _get_app_cmd():
    """Command registered to HKCU Run.

    For a source launch, prefer pythonw.exe (windowless) when available so no
    black console flashes at logon alongside the app; a frozen build registers
    only the exe itself (sys.executable is already the entry point)."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    exe = sys.executable
    if os.path.basename(exe).lower() == "python.exe":
        alt = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(alt):
            exe = alt
    return f'"{exe}" "{os.path.abspath(sys.argv[0])}"'


def apply_autostart(enabled):
    """Set or clear the auto-start registry entry for the current user.

    Returns True on success, False on failure (logged to stderr) so callers
    can surface the problem instead of it being silently swallowed."""
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE
            )
        except OSError:
            # Run key missing (rare) — create it so the entry can be written.
            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path)
        try:
            if enabled:
                winreg.SetValueEx(key, "DeskFlow", 0, winreg.REG_SZ, _get_app_cmd())
            else:
                try:
                    winreg.DeleteValue(key, "DeskFlow")
                except FileNotFoundError:
                    pass
        finally:
            winreg.CloseKey(key)
        return True
    except Exception as e:
        print(f"Failed to set autostart: {e}")
        return False


def _accent_hex():
    c = QApplication.palette().color(QPalette.Highlight)
    return f"#{c.red():02x}{c.green():02x}{c.blue():02x}"


class _Sidebar(QListWidget):
    """Compact navigation column for the settings dialog."""

    def __init__(self, entries, parent=None):
        super().__init__(parent)
        self.setFixedWidth(132)
        self.setFocusPolicy(Qt.NoFocus)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSelectionMode(QListWidget.SingleSelection)
        for key, label in entries:
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, key)
            item.setSizeHint(QSize(0, 40))
            self.addItem(item)
        self.setStyleSheet(f"""
            QListWidget {{
                background: transparent;
                border: none;
                padding: 10px 6px;
                font-size: 13px;
                outline: none;
            }}
            QListWidget::item {{
                color: {self._fg()};
                border-radius: 8px;
                padding-left: 4px;
            }}
            QListWidget::item:hover {{ background: rgba(128,128,128,45); }}
            QListWidget::item:selected {{
                background: {_accent_hex()};
                color: #ffffff;
            }}
        """)
        if self.count() > 0:
            self.setCurrentRow(0)

    def _fg(self):
        c = QApplication.palette().color(QPalette.WindowText)
        return f"#{c.red():02x}{c.green():02x}{c.blue():02x}"


class SettingsDialog(QDialog):
    """Settings dialog split into a left navigation sidebar and a right pane."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(I18n.tr("settings_title"))
        self.setFixedSize(520, 400)
        self.setWindowFlags(
            Qt.Dialog | Qt.WindowCloseButtonHint | Qt.WindowStaysOnTopHint
        )
        self.setup_ui()
        self.load_settings()
        self._connect_signals()
        self.center_on_screen()

    def _connect_signals(self):
        # Changes apply immediately as the user edits each control, rather than
        # waiting for the dialog to close. done() still re-persists as a safety
        # net. Load-order note: signals are wired after load_settings() so the
        # initial population does not trigger redundant saves.
        self.lang_combo.currentIndexChanged.connect(self._persist)
        self.autostart_check.toggled.connect(self._persist)
        self.translate_lang_combo.currentIndexChanged.connect(self._persist)

    def setup_ui(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # --- left navigation ---
        self.sidebar = _Sidebar([
            ("general", I18n.tr("settings_general")),
            ("translate", I18n.tr("settings_translate")),
            ("system", I18n.tr("settings_system")),
            ("about", I18n.tr("settings_about")),
        ])
        outer.addWidget(self.sidebar)

        # --- right content + a menu divider ---
        divider = QFrame(self)
        divider.setFixedWidth(1)
        divider.setStyleSheet("background: rgba(120,120,120,80);")
        outer.addWidget(divider)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_general_page())
        self.stack.addWidget(self._build_translate_page())
        self.stack.addWidget(self._build_system_page())
        self.stack.addWidget(AboutPage())

        # Right pane = content stack only. Settings are persisted automatically
        # whenever the dialog closes (see done()), so no Save button is needed.
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addWidget(self.stack, 1)

        outer.addLayout(right, 1)

        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)

    # ----- pages -----

    def _row(self, label_text, widget):
        row = QHBoxLayout()
        row.setSpacing(12)
        label = QLabel(label_text)
        label.setFixedWidth(90)
        row.addWidget(label)
        row.addWidget(widget, 1)
        row.addStretch()
        return row

    def _build_general_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(18)

        self.lang_combo = QComboBox()
        self.lang_combo.addItem("中文", "zh_CN")
        self.lang_combo.addItem("English", "en")
        self.lang_combo.setFixedWidth(180)
        layout.addLayout(self._row(I18n.tr("language"), self.lang_combo))

        hotkey_value = QLabel("Ctrl + `")
        layout.addLayout(self._row(I18n.tr("hotkey"), hotkey_value))

        layout.addStretch()
        return page

    def _build_translate_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(18)

        self.translate_lang_combo = QComboBox()
        self.translate_lang_combo.addItem("简体中文", "zh-CN")
        self.translate_lang_combo.addItem("繁體中文", "zh-TW")
        self.translate_lang_combo.addItem("English", "en")
        self.translate_lang_combo.addItem("日本語", "ja")
        self.translate_lang_combo.addItem("한국어", "ko")
        self.translate_lang_combo.setFixedWidth(180)
        layout.addLayout(
            self._row(I18n.tr("translate_target_lang"), self.translate_lang_combo))

        hint = QLabel(I18n.tr("translate_source_hint"))
        hint.setStyleSheet("font-size: 11px; color: #868e96;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addStretch()
        return page

    def _build_system_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(18)

        row = QHBoxLayout()
        row.setSpacing(12)
        label = QLabel(I18n.tr("autostart"))
        label.setFixedWidth(90)
        row.addWidget(label)
        self.autostart_check = QCheckBox()
        row.addWidget(self.autostart_check)
        row.addStretch()
        layout.addLayout(row)

        layout.addStretch()
        return page

    # ----- load / save -----

    def load_settings(self):
        config = Config()
        lang = config.get("language", "zh_CN")
        index = self.lang_combo.findData(lang)
        if index >= 0:
            self.lang_combo.setCurrentIndex(index)

        autostart = config.get("autostart", False)
        self.autostart_check.setChecked(autostart)

        translate_lang = config.get("translate_target_lang", "zh-CN")
        index = self.translate_lang_combo.findData(translate_lang)
        if index >= 0:
            self.translate_lang_combo.setCurrentIndex(index)

    def _persist(self, *_):
        lang = self.lang_combo.currentData()
        I18n.set_language(lang)

        autostart = self.autostart_check.isChecked()
        Config().set("autostart", autostart)
        apply_autostart(autostart)

        Config().set("translate_target_lang", self.translate_lang_combo.currentData())

    def done(self, result):
        # Settings already apply instantly via _connect_signals(); ending here
        # re-persists once more as a safety net so nothing is ever lost.
        self._persist()
        super().done(result)

    def center_on_screen(self):
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(
            screen.center().x() - self.width() // 2,
            screen.center().y() - self.height() // 2
        )