"""
DeskFlow - PySide6 quick tool launcher
Main entry point: Ctrl+` to call, capsule UI with multiple tools
"""
import sys
import os
import ctypes
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PySide6.QtGui import QIcon
from PySide6.QtCore import Qt
from modules.config import Config
from modules.i18n import I18n
from modules.hotkey import WinHotkeyFilter, register_hotkey, unregister_hotkey
from modules.capsule import CapsuleBar
from modules.screenshot import ScreenshotOverlay
from modules.annotation import AnnotationOverlay
from modules.translate import TranslateOverlay
from modules.settings import SettingsDialog, apply_autostart
from modules.clipboard_manager import ClipboardManager


def set_app_user_model_id():
    """Set the Windows AppUserModelID so the taskbar groups the window with
    the tray icon under DeskFlow instead of the generic python icon.

    Must run before the QApplication (i.e. before any window) is created,
    mirroring icon_set.md. Safe no-op on non-Windows / when it fails."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "LisseldeE.DeskFlow.Version")
    except Exception:
        pass


def load_app_icon():
    """Load icon.ico from the app directory.

    Handles both dev (python DeskFlow.py) and PyInstaller onefile builds.
    In a onefile build, sys._MEIPASS points to the temp extraction directory
    where --add-data placed icon.ico. In dev mode, __file__'s directory is
    the project root. Fall back to the exe directory as a last resort."""
    icon_path = None
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        icon_path = os.path.join(sys._MEIPASS, "icon.ico")
    if not icon_path or not os.path.exists(icon_path):
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
    if not os.path.exists(icon_path):
        icon_path = os.path.join(os.path.dirname(sys.executable), "icon.ico")
    if os.path.exists(icon_path):
        return QIcon(icon_path)
    return QIcon()


class DeskFlowApp:
    """Main application managing tray, hotkey, capsule, and overlays"""

    def __init__(self):
        # Set the AppUserModelID before any window / QApplication exists so
        # the taskbar shows the DeskFlow icon. Mirrors icon_set.md.
        set_app_user_model_id()

        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        self.app.setApplicationName("DeskFlow")
        self.app.setWindowIcon(load_app_icon())

        Config()
        I18n.get_language()

        # Apply auto-start setting from config to registry
        apply_autostart(Config().get("autostart", False))

        self.capsule = CapsuleBar()
        self.active_overlay = None

        # Clipboard manager wires itself to the capsule + panel and restores
        # persisted state (enabled / expanded / room / position) from config.
        # Created before connect_signals so the button wiring can reference it.
        self.clipboard_mgr = ClipboardManager(self.capsule)

        self.setup_tray()
        self.setup_hotkey()
        self.connect_signals()

    def setup_tray(self):
        self.tray_icon = QSystemTrayIcon()
        self.tray_icon.setIcon(load_app_icon())
        self.tray_icon.setToolTip("DeskFlow")

        menu = QMenu()
        show_action = menu.addAction(I18n.tr("show"))
        show_action.triggered.connect(self.toggle_capsule)
        menu.addSeparator()
        exit_action = menu.addAction(I18n.tr("exit"))
        exit_action.triggered.connect(self.exit_app)

        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def setup_hotkey(self):
        self.hotkey_filter = WinHotkeyFilter(self.toggle_capsule)
        self.app.installNativeEventFilter(self.hotkey_filter)
        if not register_hotkey():
            print("Warning: Failed to register hotkey (maybe already in use)")

    def connect_signals(self):
        self.capsule.btn_screenshot.clicked.connect(self._on_screenshot)
        self.capsule.btn_annotation.clicked.connect(self._on_annotation)
        self.capsule.btn_translate.clicked.connect(self._on_translate)
        self.capsule.btn_settings.clicked.connect(self._on_settings)
        self.capsule.btn_close.clicked.connect(self._on_close)
        # Clipboard: left-click toggles on/off, right-click opens room config.
        self.capsule.btn_clipboard.clicked.connect(self.clipboard_mgr.on_button_left)
        self.capsule.btn_clipboard.rightClicked.connect(self.clipboard_mgr.on_button_right)

    def toggle_capsule(self):
        if self.active_overlay is not None:
            return
        # When the LAN clipboard is enabled, Ctrl+` surfaces the capsule and
        # (if the user last left it expanded) the clipboard card together;
        # toggling again hides both. Family visibility is checked as a whole
        # (capsule OR panel) so a stray panel doesn't strand the family.
        if self.clipboard_mgr.is_enabled():
            if self.capsule.isVisible() or self.clipboard_mgr.is_panel_visible():
                self.clipboard_mgr.hide_family()
            else:
                self.capsule.show_capsule()
                if self.clipboard_mgr.is_expanded():
                    self.clipboard_mgr.show_card()
        else:
            self.capsule.toggle_visibility()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.toggle_capsule()
            
    def _on_screenshot(self):
        # Hide the whole family (capsule + clipboard panel) instantly so
        # neither is captured in the screenshot. hide_family_immediately
        # doesn't touch _expanded or save_state — the panel preference is
        # preserved and the family resurfaces on the next Ctrl+`.
        self.clipboard_mgr.hide_family_immediately()
        overlay = ScreenshotOverlay()
        self.active_overlay = overlay
        overlay.closed.connect(lambda o=overlay: self._on_overlay_closed(o))

    def _on_annotation(self):
        # Same rationale as _on_screenshot: don't let family UI leak into
        # the annotation overlay's screen capture.
        self.clipboard_mgr.hide_family_immediately()
        overlay = AnnotationOverlay()
        self.active_overlay = overlay
        overlay.closed.connect(lambda o=overlay: self._on_overlay_closed(o))
        overlay.finished.connect(lambda o=overlay: self._on_overlay_closed(o))

    def _on_translate(self):
        # Same rationale as _on_screenshot: hide the whole family instantly
        # so neither the capsule nor the clipboard card leaks into the
        # translated selection.
        self.clipboard_mgr.hide_family_immediately()
        overlay = TranslateOverlay()
        self.active_overlay = overlay
        overlay.closed.connect(lambda o=overlay: self._on_overlay_closed(o))

    def _on_settings(self):
        dialog = SettingsDialog()
        dialog.exec()

    def _on_overlay_closed(self, overlay):
        if self.active_overlay is overlay:
            self.active_overlay = None

    def _on_close(self):
        # Close dismisses the whole family (capsule + clipboard card).
        # force_family_hide bypasses the show-debounce — user-explicit action.
        self.capsule.force_family_hide()

    def exit_app(self):
        # Uninstall the mouse hook first so no callback fires while we
        # tear down the rest of the family.
        self.capsule.shutdown()
        self.clipboard_mgr.shutdown()
        unregister_hotkey()
        self.app.quit()

    def run(self):
        return self.app.exec()


if __name__ == "__main__":
    app = DeskFlowApp()
    sys.exit(app.run())