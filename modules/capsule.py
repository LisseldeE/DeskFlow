from ctypes import wintypes
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QGraphicsDropShadowEffect, QApplication
)
from PySide6.QtCore import (
    Qt, QPoint, QPropertyAnimation, QEasingCurve, QEvent,
    QAbstractNativeEventFilter, Signal
)
from PySide6.QtGui import QPainter, QColor, QGuiApplication, QKeyEvent, QCursor
from modules.icons import (
    ICON_SCREENSHOT, ICON_ANNOTATION, ICON_TRANSLATE, ICON_SETTINGS,
    ICON_CLOSE, ICON_CLIPBOARD, ICON_SEARCH
)
from modules.i18n import I18n
from modules.family import FamilyWindowRegistry
from modules.global_mouse_hook import GlobalMouseHook
from modules.widgets import GlassIconButton, paint_pill
from modules.config import Config

# Windows constants
WM_KEYDOWN = 0x0100
VK_ESCAPE = 0x1B


class CapsuleNativeFilter(QAbstractNativeEventFilter):
    """Native event filter to catch global ESC key (WM_KEYDOWN VK_ESCAPE).

    Triggers a family-wide hide as long as ANY family window is visible
    (capsule OR panel) — not just the capsule. ESC is a user-explicit intent
    so it bypasses the show-debounce via force_family_hide()."""

    def __init__(self, capsule):
        super().__init__()
        self.capsule = capsule

    def nativeEventFilter(self, eventType, message):
        if eventType == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message.__int__()))
            if msg.message == WM_KEYDOWN and msg.wParam == VK_ESCAPE:
                if FamilyWindowRegistry.any_visible() and not self.capsule._animating:
                    self.capsule.force_family_hide()
                    return True, 0
        return False, 0


class CapsuleBar(QWidget):
    """Main floating capsule bar with tool buttons.

    The capsule is the anchor of a "family" of windows (itself + the
    clipboard panel + the room dialog). Focus moving to a family window
    does NOT trigger a hide; focus leaving the family hides everything.
    Background follows the system palette - no fixed colors.
    """

    hide_family_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        # Never steal focus on show — the user's caret stays in their input.
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(396, 56)

        self._animating = False
        self._pending_hide = False
        self.setup_ui()
        self.setup_animations()
        self.setup_shadow()
        self.hide()

        # The capsule is always a family window (the anchor).
        FamilyWindowRegistry.add(self)

        # ESC: catches WM_KEYDOWN VK_ESCAPE at Windows message level
        self._esc_filter = CapsuleNativeFilter(self)
        QApplication.instance().installNativeEventFilter(self._esc_filter)

        # Global low-level mouse hook: the OS delivers every mouse-down on
        # the screen to us before the target window sees it. When the click
        # is not inside any family window's HWND rect, we hide the family.
        # This is far more reliable than the foreground-window poll it
        # replaces — it works for clicks on other apps, the desktop, the
        # taskbar, the tray, etc., not just inside the Qt app.
        self._mouse_hook = GlobalMouseHook()
        self._mouse_hook.on_outside_click = self._on_outside_click
        self._mouse_hook.install()

    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(14, 6, 14, 6)

        # The five tool buttons are built in the user-defined order (stored
        # in config["tool_order"]); Settings and Close stay pinned at the end.
        tool_specs = {
            "screenshot": (ICON_SCREENSHOT, "screenshot"),
            "annotation": (ICON_ANNOTATION, "annotation"),
            "translate": (ICON_TRANSLATE, "translate"),
            "clipboard": (ICON_CLIPBOARD, "clipboard"),
            "search": (ICON_SEARCH, "search"),
        }
        order = Config().get(
            "tool_order",
            ["screenshot", "annotation", "translate", "clipboard", "search"])

        # All capsule icons must keep their original colour on hover (task 1):
        # only the translucent plate animates, never a colour tint on the SVG.
        self._tool_buttons = {}
        for key in order:
            if key not in tool_specs:
                continue
            svg, tooltip_key = tool_specs[key]
            btn = GlassIconButton(svg, I18n.tr(tooltip_key), colorize_icon=False)
            self._tool_buttons[key] = btn
            layout.addWidget(btn)
        # Fallback: if a stale saved order misses a tool, append it so the
        # capsule never loses a button.
        for key, (svg, tooltip_key) in tool_specs.items():
            if key in self._tool_buttons:
                continue
            btn = GlassIconButton(svg, I18n.tr(tooltip_key), colorize_icon=False)
            self._tool_buttons[key] = btn
            layout.addWidget(btn)

        self.btn_settings = GlassIconButton(
            ICON_SETTINGS, I18n.tr("settings"), colorize_icon=False)
        layout.addWidget(self.btn_settings)

        self.btn_close = GlassIconButton(
            ICON_CLOSE, I18n.tr("close"),
            hover_color="#e03131",
            hover_bg_color=QColor(224, 49, 49),
            colorize_icon=False
        )
        layout.addWidget(self.btn_close)

        # Stable references used by CapRiseApp.connect_signals().
        self.btn_screenshot = self._tool_buttons["screenshot"]
        self.btn_annotation = self._tool_buttons["annotation"]
        self.btn_translate = self._tool_buttons["translate"]
        self.btn_clipboard = self._tool_buttons["clipboard"]
        self.btn_search = self._tool_buttons["search"]

    def reorder_tools(self, order):
        """Reorder the tool buttons to match `order` (a list of tool keys).

        The existing button objects are reused and only their position in the
        layout changes, so the signal connections made in CapRiseApp stay
        valid. Settings and Close always remain pinned at the end.

        A stale saved order (e.g. from before a new tool was added) is
        tolerated: any tool missing from `order` is appended so the capsule
        never loses a button."""
        layout = self.layout()
        for btn in self._tool_buttons.values():
            layout.removeWidget(btn)
        anchor = self.btn_settings  # insert before Settings
        inserted = set()
        for key in order:
            btn = self._tool_buttons.get(key)
            if btn is not None:
                layout.insertWidget(layout.indexOf(anchor), btn)
                inserted.add(key)
        # Append tools missing from the saved order (new tools, stale config).
        for key, btn in self._tool_buttons.items():
            if key not in inserted:
                layout.insertWidget(layout.indexOf(anchor), btn)

    def setup_shadow(self):
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 60))
        shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)

    def setup_animations(self):
        self.pos_anim = QPropertyAnimation(self, b"pos")
        self.pos_anim.setDuration(300)
        self.pos_anim.setEasingCurve(QEasingCurve.OutCubic)
        self.pos_anim.finished.connect(self._on_anim_finished)

        self.opacity_anim = QPropertyAnimation(self, b"windowOpacity")
        self.opacity_anim.setDuration(300)
        self.opacity_anim.setEasingCurve(QEasingCurve.OutCubic)

    def paintEvent(self, event):
        # Shared pill look (gradient body + family hairline) so the capsule
        # and the annotation sub-bar read as one design family.
        painter = QPainter(self)
        paint_pill(painter, self.rect(), 28)

    def showEvent(self, event):
        # Native HWND may (re)create on show — refresh the registry and apply
        # WS_EX_NOACTIVATE so mouse clicks on the capsule don't steal focus
        # from the user's input field either.
        FamilyWindowRegistry.refresh_hwnd(self)
        FamilyWindowRegistry.set_no_activate(self)
        super().showEvent(event)

    def set_clipboard_active(self, active):
        self.btn_clipboard.set_active(active)

    # ----- family-aware hide -----

    def _on_outside_click(self):
        """Called synchronously by GlobalMouseHook when a mouse-down lands
        outside any family window's HWND rect.

        Synchronous is intentional: it lets the hook fire BEFORE Qt finishes
        processing the click that triggered show_capsule (e.g. a tray-icon
        click that toggles the capsule). At that moment the family is still
        invisible, so the any_visible() check no-ops and we don't dismiss
        the capsule we're about to show. An async QTimer.singleShot(0) here
        would race the show_capsule() call and dismiss it on the next loop
        iteration.
        """
        if FamilyWindowRegistry.any_visible():
            self.hide_family_requested.emit()

    def request_family_hide(self):
        """Outside-click / focus-loss path. No debounce is needed: a real
        click is unambiguous user intent (unlike transient foreground
        flicker that the old poll had to ride out). Emits and lets the
        ClipboardManager drive hide_family() so the whole family collapses
        together — never hide_capsule() alone, which would strand the panel.
        """
        self.hide_family_requested.emit()

    def force_family_hide(self):
        """User-explicit path (ESC, close button, Ctrl+` toggle-off).
        Same emit as request_family_hide — both names kept for clarity
        (callers signal intent: force = bypass any gate, request = reactive).
        """
        self.hide_family_requested.emit()

    def shutdown(self):
        """Release OS resources. Call from CapRise.exit_app before quit."""
        self._mouse_hook.uninstall()

    def event(self, event):
        """ESC key when the capsule itself has keyboard focus."""
        if event.type() == QEvent.KeyPress:
            if isinstance(event, QKeyEvent) and event.key() == Qt.Key_Escape:
                if FamilyWindowRegistry.any_visible() and not self._animating:
                    self.force_family_hide()
                    return True
        return super().event(event)

    def hideEvent(self, event):
        self.pos_anim.stop()
        self.opacity_anim.stop()
        self._animating = False
        self._pending_hide = False
        super().hideEvent(event)

    def _get_screen_geo(self):
        cursor_pos = QCursor.pos()
        screen = QGuiApplication.screenAt(cursor_pos)
        if not screen:
            screen = QGuiApplication.primaryScreen()
        return screen.availableGeometry()

    def _on_anim_finished(self):
        self._animating = False
        if self._pending_hide:
            self._pending_hide = False
            self.hide()

    def show_capsule(self):
        """Show the capsule, interrupting any in-progress hide animation by
        reversing from the current opacity / position. Safe to call when
        already fully shown (no-op) or while hiding (reverses)."""
        # Already fully shown and not hiding → nothing to do.
        if self.isVisible() and not self._pending_hide:
            return

        first_show = not self.isVisible()
        self._animating = True
        self._pending_hide = False
        screen = self._get_screen_geo()
        target_x = (screen.width() - self.width()) // 2 + screen.x()
        target_y = screen.y() + 30

        if first_show:
            # Boot from off-screen at zero opacity.
            self.setWindowOpacity(0.0)
            self.move(int(target_x), int(-self.height()))
            self.show()
            self.raise_()
            # NOTE: no activateWindow() — floats without taking focus.
            start_pos = QPoint(int(target_x), int(-self.height()))
            start_opacity = 0.0
        else:
            # Reverse from wherever the hide animation currently is.
            start_pos = self.pos()
            start_opacity = self.windowOpacity()

        self.pos_anim.stop()
        self.opacity_anim.stop()
        self.pos_anim.setStartValue(start_pos)
        self.pos_anim.setEndValue(QPoint(int(target_x), int(target_y)))
        self.opacity_anim.setStartValue(start_opacity)
        self.opacity_anim.setEndValue(1.0)
        self.pos_anim.start()
        self.opacity_anim.start()

    def hide_capsule(self):
        """Hide the capsule, interrupting any in-progress show animation by
        reversing from the current opacity / position. Safe to call when
        already hidden (no-op) or while showing (reverses)."""
        if not self.isVisible():
            return

        self._animating = True
        self._pending_hide = True
        current_pos = self.pos()

        self.pos_anim.stop()
        self.opacity_anim.stop()
        self.pos_anim.setStartValue(current_pos)
        self.pos_anim.setEndValue(
            QPoint(int(current_pos.x()), int(-self.height())))
        self.opacity_anim.setStartValue(self.windowOpacity())
        self.opacity_anim.setEndValue(0.0)
        self.pos_anim.start()
        self.opacity_anim.start()

    def hide_immediately(self):
        self._animating = False
        self._pending_hide = False
        self.pos_anim.stop()
        self.opacity_anim.stop()
        self.hide()

    def toggle_visibility(self):
        # User-explicit toggle: bypass the focus-loss debounce via
        # force_family_hide so a quick second press isn't swallowed by the
        # 500ms show-debounce. The animation itself is reversible, so we
        # no longer bail out when _animating is True.
        if self.isVisible() and not self._pending_hide:
            self.force_family_hide()
        else:
            self.show_capsule()
