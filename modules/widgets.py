"""Shared visual helpers for the floating toolbars.

The capsule bar and the annotation sub-bar are siblings in the same window
"family", so they share one look: a rounded "pill" background with a subtle
vertical gradient (Qt decides the colours from the system palette), the
family hairline border (#505050, same as the clipboard panel), and smooth
animated icon buttons (translucent light-blue highlight on hover/press, a
persistent lit state for toggles, and a 1 px press-down from the reference
AnimatedButton pattern). Keeping this in one module guarantees the two bars
stay visually consistent.
"""
from PySide6.QtCore import (
    Qt, QByteArray, QEvent, QVariantAnimation, QEasingCurve, Signal
)
from PySide6.QtGui import (
    QPainter, QColor, QPixmap, QIcon, QPalette, QLinearGradient, QPen
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QPushButton, QApplication


def make_icon(svg_content, color, size=22):
    """Create a QIcon from an SVG string with a color substitution."""
    colored = svg_content.replace('stroke="currentColor"', f'stroke="{color}"')
    renderer = QSvgRenderer(QByteArray(colored.encode()))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


def system_color(role):
    """Hex string for a system palette color."""
    c = QApplication.palette().color(role)
    return f"#{c.red():02x}{c.green():02x}{c.blue():02x}"


def paint_pill(painter, rect, radius):
    """Paint a toolbar pill: gradient body + hairline border + glass edge.

    The body is a subtle top-to-bottom gradient derived from the system
    window color (no fixed colors — Qt decides), the hairline is the
    family's #505050 so every window of the app reads as one design, and a
    faint white catch-light runs along the flat top edge on dark themes.
    """
    painter.setRenderHint(QPainter.Antialiasing)
    bg = QApplication.palette().color(QPalette.Window)
    is_dark = bg.lightness() < 128
    top = bg.lighter(150) if is_dark else bg.lighter(105)
    grad = QLinearGradient(0, 0, 0, rect.height())
    grad.setColorAt(0.0, top)
    grad.setColorAt(1.0, bg)
    painter.setBrush(grad)
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(rect, radius, radius)

    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(QColor(80, 80, 80, 255), 1))
    painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), radius, radius)

    # Soft catch-light across the flat top edge. Only drawn on dark themes —
    # a white line is invisible on a light pill anyway.
    if is_dark:
        painter.setPen(QPen(QColor(255, 255, 255, 40), 1))
        painter.drawLine(rect.x() + radius - 2, rect.y() + 1,
                         rect.x() + rect.width() - radius + 2, rect.y() + 1)


def _blend(c1, c2, t):
    """Linearly interpolate two QColors by t in [0, 1]."""
    return QColor(
        int(c1.red() + (c2.red() - c1.red()) * t),
        int(c1.green() + (c2.green() - c1.green()) * t),
        int(c1.blue() + (c2.blue() - c1.blue()) * t),
    )


def _to_hex(c):
    return f"#{c.red():02x}{c.green():02x}{c.blue():02x}"


class GlassIconButton(QPushButton):
    """Icon button with a smooth hover / press / toggle animation.

    Hover and press fade a translucent highlight (the system accent color —
    light blue on Windows) in over 180 ms, the icon color cross-fades to
    that accent, and a click nudges the button 1 px down (the reference
    AnimatedButton pattern). A persistent lit state marks toggles that are
    ON. Emits `rightClicked` on right-click.
    """

    rightClicked = Signal()

    def __init__(self, svg_content, tooltip="", size=44, icon_size=22,
                 hover_color=None, hover_bg_color=None, parent=None):
        super().__init__(parent)
        self._svg = svg_content
        self._size = size
        self._normal = QApplication.palette().color(QPalette.WindowText)
        self._hover = QColor(hover_color) if hover_color else \
            QApplication.palette().color(QPalette.Highlight)
        self._hover_bg = hover_bg_color or \
            QApplication.palette().color(QPalette.Highlight)
        self._icon_size = icon_size
        self._original_pos = None
        self._is_pressed = False
        self._active = False
        self._t = 0.0  # interaction intensity: 0 = idle, 1 = fully lit

        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setContextMenuPolicy(Qt.PreventContextMenu)
        self.setToolTip(tooltip)
        # Flat + no focus so Qt's native pressed/background drawing is fully
        # suppressed — the background is painted ourselves in paintEvent.
        # Using paintEvent instead of per-frame setStyleSheet avoids relayout
        # and the graphics-effect buffer flicker that caused a stray blue
        # plate to flash when a hover ended.
        self.setFlat(True)
        self.setFocusPolicy(Qt.NoFocus)
        self._pix_cache = {}  # (argb,size) -> QPixmap of the icon
        self._alpha = 0       # current plate alpha, resolved on each frame
        self._icon_color = self._normal

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._apply_visuals)
        self._apply_visuals()

    # ----- animation -----

    def _animate_to(self, target):
        self._anim.stop()
        self._anim.setStartValue(self._t)
        self._anim.setEndValue(target)
        self._anim.start()

    # Hover/press only ever drive the fade for *inactive* buttons. An active
    # button (clipboard on, annotation mode selected) keeps a fixed stable
    # base plate that must NOT react to hover — otherwise the hover animation
    # would wobble the "selected" indicator's opacity.
    ACTIVE_ALPHA = 150
    HOVER_ALPHA = 46

    def _apply_visuals(self, value=None):
        """Recompute the current plate alpha + icon color from state, then
        repaint. No stylesheet magic — just a repaint, so the fade is
        glitch-free."""
        if value is not None:
            self._t = float(value)
        if self._active:
            # Stable plate: fixed accent plate, but the icon keeps its
            # original "window text" color (white in dark / black in light).
            # A blue icon on the blue plate would be invisible, so selected
            # modes must keep high contrast against the lit background.
            self._alpha = self.ACTIVE_ALPHA
            self._icon_color = self._normal
        else:
            self._alpha = int(self.HOVER_ALPHA * self._t)
            self._icon_color = _blend(self._normal, self._hover, self._t)
        self.update()

    def set_active(self, active):
        """Persistent lit background to mark a toggle that is ON.

        Applied instantly (no shared fade): the active plate has a fixed
        opacity, so state changes can't collide with an in-flight hover
        animation and produce a blue flash."""
        self._active = bool(active)
        self._anim.stop()
        if self._active:
            self._t = 1.0
        else:
            self._t = 1.0 if self.underMouse() else 0.0
        self._apply_visuals()

    # ----- painting -----

    def _icon_pixmap(self, color, size):
        key = (color.rgba(), size)
        pm = self._pix_cache.get(key)
        if pm is None:
            pm = make_icon(self._svg, _to_hex(color), size).pixmap(size, size)
            self._pix_cache[key] = pm
        return pm

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        # Background plate (translucent accent) — only when lit.
        if self._alpha > 0:
            h = self._hover_bg
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(h.red(), h.green(), h.blue(), self._alpha))
            p.drawRoundedRect(rect, self._size // 3, self._size // 3)
        # Icon, centered via the cached pixmap at the resolved color.
        pm = self._icon_pixmap(self._icon_color, self._icon_size)
        x = max(0, (rect.width() - pm.width()) // 2)
        y = max(0, (rect.height() - pm.height()) // 2)
        p.drawPixmap(x, y, pm)
        p.end()

    # ----- events -----

    def enterEvent(self, event):
        if not self._active:
            self._animate_to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self._active:
            super().leaveEvent(event)
            return
        if not self._is_pressed:
            self._animate_to(0.0)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            self.rightClicked.emit()
            return
        if event.button() == Qt.LeftButton:
            self._original_pos = self.pos()
            self._is_pressed = True
            self._animate_to(1.0)
            super().mousePressEvent(event)
            if self._original_pos is not None:
                self.move(int(self._original_pos.x()),
                          int(self._original_pos.y() + 1))
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._is_pressed = False
            if self._original_pos is not None:
                self.move(self._original_pos)
            self._animate_to(1.0 if self.underMouse() else 0.0)
            super().mouseReleaseEvent(event)
        else:
            super().mouseReleaseEvent(event)

    def event(self, event):
        if event.type() == QEvent.LayoutRequest:
            if not self._is_pressed:
                self._original_pos = None
        return super().event(event)
