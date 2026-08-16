"""Settings dialog for DeskFlow"""
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from modules.config import Config
from modules.i18n import I18n


class SettingsDialog(QDialog):
    """Settings dialog with language selection and hotkey info"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(I18n.tr("settings_title"))
        self.setFixedSize(320, 200)
        self.setWindowFlags(
            Qt.Dialog | Qt.WindowCloseButtonHint | Qt.WindowStaysOnTopHint
        )
        self.setup_ui()
        self.load_settings()
        self.center_on_screen()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 20, 24, 20)

        # Language selector
        lang_layout = QHBoxLayout()
        lang_label = QLabel(I18n.tr("language"))
        lang_label.setFixedWidth(80)
        lang_layout.addWidget(lang_label)

        self.lang_combo = QComboBox()
        self.lang_combo.addItem("中文", "zh_CN")
        self.lang_combo.addItem("English", "en")
        self.lang_combo.setFixedWidth(160)
        lang_layout.addWidget(self.lang_combo)
        lang_layout.addStretch()
        layout.addLayout(lang_layout)

        # Hotkey display (read-only)
        hotkey_layout = QHBoxLayout()
        hotkey_label = QLabel(I18n.tr("hotkey"))
        hotkey_label.setFixedWidth(80)
        hotkey_layout.addWidget(hotkey_label)

        hotkey_value = QLabel("Ctrl + `")
        hotkey_layout.addWidget(hotkey_value)
        hotkey_layout.addStretch()
        layout.addLayout(hotkey_layout)

        layout.addStretch()

        # Close button
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        close_btn = QPushButton(I18n.tr("close"))
        close_btn.setFixedSize(100, 34)
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

    def load_settings(self):
        config = Config()
        lang = config.get("language", "zh_CN")
        index = self.lang_combo.findData(lang)
        if index >= 0:
            self.lang_combo.setCurrentIndex(index)

    def accept(self):
        lang = self.lang_combo.currentData()
        I18n.set_language(lang)
        super().accept()

    def center_on_screen(self):
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(
            screen.center().x() - self.width() // 2,
            screen.center().y() - self.height() // 2
        )