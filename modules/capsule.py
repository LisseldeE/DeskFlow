import time
import ctypes
from ctypes import wintypes
from PySide6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QGraphicsDropShadowEffect, QApplication
from PySide6.QtCore import Qt, QPoint, QSize, QRect, QPropertyAnimation, QEasingCurve, QEvent, QByteArray, QAbstractNativeEventFilter, QTimer
from PySide6.QtGui import QPainter, QColor, QPen, QPixmap, QIcon, QGuiApplication, QCursor, QPalette, QKeyEvent
from PySide6.QtSvg import QSvgRenderer
from modules.icons import ICON_SCREENSHOT, ICON_ANNOTATION, ICON_SETTINGS, ICON_CLOSE
from modules.i18n import I18n

# Windows constants
WM_KEYDOWN = 0x0100
VK_ESCAPE = 0x1B
WM_ACTIVATE = 0x0006
WA_INACTIVE = 0


def _system_color(role):
    """Get a system palette color as hex string"""
    c = QApplication.palette().color(role)
    return f"#{c.red():02x}{c.green():02x}{c.blue():02x}"


def _make_icon(svg_content, color="#555555", size=22):
    """Create QIcon from SVG string with color substitution"""
    colored = svg_content.replace('stroke="currentColor"', f'stroke="{color}"')
    renderer = QSvgRenderer(QByteArray(colored.encode()))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


class CapsuleNativeFilter(QAbstractNativeEventFilter):
    """Native event filter to catch global ESC key (WM_KEYDOWN VK_ESCAPE)"""

    def __init__(self, capsule):
        super().__init__()
        self.capsule = capsule

    def nativeEventFilter(self, eventType, message):
        if eventType == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message.__int__()))
            if msg.message == WM_KEYDOWN and msg.wParam == VK_ESCAPE:
                if self.capsule.isVisible() and not self.capsule._animating:
                    self.capsule.hide_capsule()
                    return True, 0
        return False, 0


class AnimatedIconButton(QPushButton):
    """Button with SVG icon, hover effect, and press-down animation.
    Supports custom hover color for special buttons (e.g. close button red)."""

    def __init__(self, svg_content, tooltip="", hover_color=None, hover_bg_color=None, parent=None):
        super().__init__(parent)
        self._svg = svg_content
        self._normal_color = _system_color(QPalette.WindowText)
        self._hover_color = hover_color or _system_color(QPalette.Highlight)
        self._original_pos = None
        self._is_pressed = False

        self.setToolTip(tooltip)
        self.setFixedSize(44, 44)
        self.setCursor(Qt.PointingHandCursor)
        self.setIcon(_make_icon(svg_content, self._normal_color))
        self.setIconSize(QSize(22, 22))

        # Hover background color (custom or system highlight)
        if hover_bg_color:
            h = hover_bg_color
        else:
            h = QApplication.palette().color(QPalette.Highlight)
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                border: none;
                border-radius: 8px;
            }}
            QPushButton:hover {{
                background-color: rgba({h.red()}, {h.green()}, {h.blue()}, 50);
            }}
        """)

    def enterEvent(self, event):
        self.setIcon(_make_icon(self._svg, self._hover_color))
        super().enterEvent(event)

    def leaveEvent(self, event):
        if not self._is_pressed:
            self.setIcon(_make_icon(self._svg, self._normal_color))
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._original_pos = self.pos()
            self._is_pressed = True
            super().mousePressEvent(event)
            if self._original_pos:
                self.move(int(self._original_pos.x()),
                          int(self._original_pos.y() + 1))
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._is_pressed = False
            if self._original_pos is not None:
                self.move(self._original_pos)
            if self.underMouse():
                self.setIcon(_make_icon(self._svg, self._hover_color))
            else:
                self.setIcon(_make_icon(self._svg, self._normal_color))
            super().mouseReleaseEvent(event)
        else:
            super().mouseReleaseEvent(event)

    def event(self, event):
        if event.type() == QEvent.LayoutRequest:
            if not self._is_pressed:
                self._original_pos = None
        return super().event(event)


class CapsuleBar(QWidget):
    """Main floating capsule bar with tool buttons.
    Background follows system palette - no fixed colors.
    Click outside auto-hides with animation, close button has red hover."""

    def __init__(self, parent=None):
        super().__init__(parent)
        # Qt.Tool: no taskbar icon. WindowStaysOnTopHint: stays above all windows.
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(240, 56)

        self._animating = False
        self._pending_hide = False
        # Show debounce: ignore hide requests for 500ms after show
        self._ignore_hide_until = 0.0
        self.setup_ui()
        self.setup_animations()
        self.setup_shadow()
        self.hide()

        # Install native event filters
        # ESC: catches WM_KEYDOWN VK_ESCAPE at Windows message level
        self._esc_filter = CapsuleNativeFilter(self)
        QApplication.instance().installNativeEventFilter(self._esc_filter)

        # Global event filter: catches mouse press outside capsule (within Qt app)
        QApplication.instance().installEventFilter(self)

        # Polling timer: reliable fallback for click-outside detection
        # Checks mouse button state + focus window every 50ms when capsule is visible
        self._poll_timer = QTimer()
        self._poll_timer.setInterval(50)
        self._poll_timer.timeout.connect(self._poll_check)

    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(14, 6, 14, 6)

        self.btn_screenshot = AnimatedIconButton(
            ICON_SCREENSHOT, I18n.tr("screenshot"))
        layout.addWidget(self.btn_screenshot)

        self.btn_annotation = AnimatedIconButton(
            ICON_ANNOTATION, I18n.tr("annotation"))
        layout.addWidget(self.btn_annotation)

        self.btn_settings = AnimatedIconButton(
            ICON_SETTINGS, I18n.tr("settings"))
        layout.addWidget(self.btn_settings)

        self.btn_close = AnimatedIconButton(
            ICON_CLOSE, I18n.tr("close"),
            hover_color="#e03131",
            hover_bg_color=QColor(224, 49, 49)
        )
        layout.addWidget(self.btn_close)

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
        """Paint capsule background using system palette colors"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        bg = self.palette().color(QPalette.Window)
        bg.setAlpha(240)
        painter.setBrush(bg)
        border = self.palette().color(QPalette.Mid)
        border.setAlpha(40)
        painter.setPen(QPen(border, 1))
        painter.drawRoundedRect(
            self.rect().adjusted(1, 1, -1, -1), 28, 28)

    def nativeEvent(self, eventType, message):
        """Intercept WM_ACTIVATE to detect window deactivation (click outside)."""
        if eventType == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message.__int__()))
            if msg.message == WM_ACTIVATE:
                if msg.wParam & 0xFFFF == WA_INACTIVE:
                    if self.isVisible() and not self._animating:
                        self.hide_capsule()
                        return True, 0
        return super().nativeEvent(eventType, message)

    def eventFilter(self, obj, event):
        """Global event filter: detect mouse press outside capsule (within Qt app)."""
        if event.type() == QEvent.MouseButtonPress:
            if self.isVisible() and not self._animating:
                try:
                    click_pos = event.globalPosition().toPoint()
                except AttributeError:
                    click_pos = event.globalPos()
                capsule_rect = QRect(self.mapToGlobal(QPoint(0, 0)), self.size())
                if not capsule_rect.contains(click_pos):
                    self.hide_capsule()
                    return True
        return super().eventFilter(obj, event)

    def event(self, event):
        """Intercept ESC key press to trigger animated hide.
        Works when the capsule has keyboard focus (within Qt app)."""
        if event.type() == QEvent.KeyPress:
            if isinstance(event, QKeyEvent) and event.key() == Qt.Key_Escape:
                if self.isVisible() and not self._animating:
                    self.hide_capsule()
                    return True
        return super().event(event)

    def hideEvent(self, event):
        """Handle final hide after animation completes"""
        self.pos_anim.stop()
        self.opacity_anim.stop()
        self._animating = False
        self._pending_hide = False
        self._poll_timer.stop()
        super().hideEvent(event)

    def _poll_check(self):
        """Polling fallback for click-outside detection.
        Checks every 50ms: mouse button pressed outside capsule, or focus window changed."""
        if not self._can_hide():
            return
        # Check 1: mouse button pressed outside capsule geometry
        if QApplication.mouseButtons() != Qt.NoButton:
            cursor_pos = QCursor.pos()
            capsule_rect = QRect(self.mapToGlobal(QPoint(0, 0)), self.size())
            if not capsule_rect.contains(cursor_pos):
                self.hide_capsule()
                return
        # Check 2: focus window is not the capsule (or None, meaning desktop/other app)
        active = QGuiApplication.focusWindow()
        my_handle = self.windowHandle()
        if my_handle is not None:
            if active is None or active != my_handle:
                self.hide_capsule()

    def _get_screen_geo(self):
        cursor_pos = QCursor.pos()
        screen = QGuiApplication.screenAt(cursor_pos)
        if not screen:
            screen = QGuiApplication.primaryScreen()
        return screen.availableGeometry()

    def _can_hide(self):
        """Check if the capsule can be hidden (respecting show debounce)."""
        if self._animating:
            return False
        if not self.isVisible():
            return False
        if time.time() < self._ignore_hide_until:
            return False
        return True

    def _on_anim_finished(self):
        self._animating = False
        if self._pending_hide:
            self._pending_hide = False
            self.hide()

    def show_capsule(self):
        if self._animating:
            return
        if self.isVisible():
            return

        self._animating = True
        self._pending_hide = False
        # Debounce: ignore hide requests for 500ms after show
        # This prevents hide/show loop after overlay operations (screenshot, annotation)
        self._ignore_hide_until = time.time() + 0.5
        screen = self._get_screen_geo()
        target_x = (screen.width() - self.width()) // 2 + screen.x()
        target_y = screen.y() + 30  # Higher position

        self.setWindowOpacity(0.0)
        self.move(int(target_x), int(-self.height()))
        self.show()
        self.raise_()
        self.activateWindow()

        self.pos_anim.setStartValue(QPoint(int(target_x), int(-self.height())))
        self.pos_anim.setEndValue(QPoint(int(target_x), int(target_y)))
        self.opacity_anim.setStartValue(0.0)
        self.opacity_anim.setEndValue(1.0)
        self.pos_anim.start()
        self.opacity_anim.start()
        self._poll_timer.start()

    def hide_capsule(self):
        if not self._can_hide():
            return

        self._animating = True
        self._pending_hide = True
        current_pos = self.pos()

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
        if self._animating:
            return
        if self.isVisible():
            self.hide_capsule()
        else:
            self.show_capsule()