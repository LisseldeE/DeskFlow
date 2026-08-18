"""Floating clipboard history card (glassmorphism).

A frameless Qt.Tool window that floats above other windows. Shows the
clipboard history list with per-item copy/delete, a clear-all button, a
connection-status line, and is draggable by its header. It is registered
as a "family window" so interacting with it does NOT make the capsule收起.

Position: appears near the cursor on first show (clamped to screen), then
persists across drags (emitted via `position_changed`).
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QApplication, QSizePolicy
)
from PySide6.QtCore import (
    Qt, Signal, QPoint, QSize, QPropertyAnimation, QEasingCurve, QTimer
)
from PySide6.QtGui import (
    QPainter, QColor, QPen, QPalette, QCursor, QGuiApplication, QMouseEvent
)

from modules.icons import ICON_CLIPBOARD, ICON_COPY, ICON_TRASH, ICON_CLOSE
from modules.i18n import I18n
from modules.family import FamilyWindowRegistry


def _make_text_icon(svg, color, size=16):
    """Tiny helper kept local — the capsule has its own _make_icon."""
    from PySide6.QtCore import QByteArray
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtGui import QPixmap, QIcon
    colored = svg.replace('stroke="currentColor"', f'stroke="{color}"')
    renderer = QSvgRenderer(QByteArray(colored.encode()))
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    renderer.render(p)
    p.end()
    return QIcon(pm)


def _system_color(role):
    c = QApplication.palette().color(role)
    return f"#{c.red():02x}{c.green():02x}{c.blue():02x}"


class ClipboardItemWidget(QWidget):
    """A single history row: click to copy, hover-delete button."""

    copy_requested = Signal(str)       # content
    delete_requested = Signal(int)     # item id

    def __init__(self, item, parent=None):
        super().__init__(parent)
        self._content = item["content"]
        self._id = item["id"]
        self.setFixedHeight(56)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        is_local = item.get("source", "local") == "local"
        dot_color = "#339af0" if is_local else "#37b24d"

        # Elided preview (single line) + full content on tooltip
        preview = self._content.replace("\n", " ").replace("\r", " ").strip()
        if len(preview) > 90:
            preview = preview[:89] + "…"

        self.setStyleSheet(f"""
            ClipboardItemWidget {{
                background-color: transparent;
                border: none;
                border-radius: 8px;
            }}
            ClipboardItemWidget:hover {{
                background-color: rgba(51, 154, 240, 38);
            }}
        """)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 8, 8)
        lay.setSpacing(10)

        # Source dot
        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(
            f"background-color: {dot_color}; border-radius: 4px; border: none;"
        )
        lay.addWidget(dot)

        text = QLabel(preview)
        text.setToolTip(self._content)
        text.setStyleSheet("color: palette(text); border: none; background: transparent; font-size: 13px;")
        text.setWordWrap(False)
        text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        lay.addWidget(text, 1)

        del_btn = QPushButton()
        del_btn.setFixedSize(26, 26)
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.setIcon(_make_text_icon(ICON_TRASH, _system_color(QPalette.WindowText), 14))
        del_btn.setIconSize(QSize(14, 14))
        del_btn.setStyleSheet("""
            QPushButton {
                background: transparent; border: none; border-radius: 6px;
            }
            QPushButton:hover { background-color: rgba(224, 49, 49, 60); }
        """)
        del_btn.clicked.connect(lambda: self.delete_requested.emit(self._id))
        lay.addWidget(del_btn)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.copy_requested.emit(self._content)
        super().mousePressEvent(event)


class PanelHeader(QWidget):
    """Draggable header: title + status + clear + close."""

    moved = Signal(QPoint)  # new global pos

    def __init__(self, parent_panel):
        super().__init__(parent_panel)
        self._panel = parent_panel
        self._drag_offset = None
        self.setFixedHeight(40)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("""
            PanelHeader { background: transparent; border: none; }
        """)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 6, 8, 6)
        lay.setSpacing(8)

        icon_lbl = QLabel()
        ic = _make_text_icon(ICON_CLIPBOARD, _system_color(QPalette.WindowText), 16)
        icon_lbl.setPixmap(ic.pixmap(16, 16))
        icon_lbl.setStyleSheet("background: transparent; border: none;")
        lay.addWidget(icon_lbl)

        title = QLabel(I18n.tr("clipboard"))
        title.setStyleSheet(
            "color: palette(text); background: transparent; border: none;"
            " font-size: 13px; font-weight: 600;"
        )
        lay.addWidget(title)

        self.status_label = QLabel(I18n.tr("clipboard_status_disconnected"))
        self.status_label.setStyleSheet(
            "color: palette(placeholder-text); background: transparent;"
            " border: none; font-size: 11px;"
        )
        lay.addWidget(self.status_label, 1)

        clear_btn = QPushButton()
        clear_btn.setFixedSize(26, 26)
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.setIcon(_make_text_icon(ICON_TRASH, _system_color(QPalette.WindowText), 14))
        clear_btn.setIconSize(QSize(14, 14))
        clear_btn.setStyleSheet("""
            QPushButton { background: transparent; border: none; border-radius: 6px; }
            QPushButton:hover { background-color: rgba(255,255,255,30); }
        """)
        clear_btn.setToolTip(I18n.tr("clipboard_clear_all"))
        clear_btn.clicked.connect(parent_panel.clear_requested)
        lay.addWidget(clear_btn)

        close_btn = QPushButton()
        close_btn.setFixedSize(26, 26)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setIcon(_make_text_icon(ICON_CLOSE, _system_color(QPalette.WindowText), 14))
        close_btn.setIconSize(QSize(14, 14))
        close_btn.setStyleSheet("""
            QPushButton { background: transparent; border: none; border-radius: 6px; }
            QPushButton:hover { background-color: rgba(224, 49, 49, 60); }
        """)
        close_btn.clicked.connect(parent_panel.collapse_requested)
        lay.addWidget(close_btn)

    def set_status(self, text):
        self.status_label.setText(text)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self._panel.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            new_pos = event.globalPosition().toPoint() - self._drag_offset
            # Clamp to the current screen's available geometry
            screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
            geo = screen.availableGeometry()
            new_pos.setX(max(geo.x(), min(new_pos.x(), geo.right() - self._panel.width())))
            new_pos.setY(max(geo.y(), min(new_pos.y(), geo.bottom() - self._panel.height())))
            self._panel.move(new_pos)
            self.moved.emit(new_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class ClipboardPanel(QWidget):
    """Floating clipboard history card."""

    copy_requested = Signal(str)
    delete_requested = Signal(int)
    clear_requested = Signal()
    position_changed = Signal(int, int)  # x, y
    hide_family_requested = Signal()      # focus left family -> hide capsule + card
    collapse_requested = Signal()         # X button -> collapse card only

    WIDTH = 340
    MAX_HEIGHT = 460

    def __init__(self, initial_pos=None, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        # Never steal focus (on show OR on click) — the user's caret stays in
        # their input field so clicking a history item copies and Ctrl+V
        # pastes at the original cursor. WS_EX_NOACTIVATE is applied in
        # showEvent once the HWND exists.
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedWidth(self.WIDTH)

        self._animating = False
        self._pending_hide = False  # True when hide_panel was triggered mid-show
        self._initial_pos = initial_pos

        self._init_ui()
        self._init_anim()
        self.hide()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(0)

        # Card container (rounded background painted in paintEvent)
        self.header = PanelHeader(self)
        self.header.moved.connect(self._on_header_moved)
        root.addWidget(self.header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical {
                background: transparent; width: 8px; margin: 4px 2px;
            }
            QScrollBar::handle:vertical {
                background: palette(mid); border-radius: 4px; min-height: 24px;
            }
            QScrollBar::handle:vertical:hover { background: palette(dark); }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)

        self.list_container = QWidget()
        self.list_container.setStyleSheet("background: transparent;")
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 4, 0, 4)
        self.list_layout.setSpacing(4)
        self.list_layout.addStretch()
        self.scroll.setWidget(self.list_container)
        root.addWidget(self.scroll, 1)

        # Empty-state label (centered)
        self.empty_label = QLabel(I18n.tr("clipboard_empty"))
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet(
            "color: palette(placeholder-text); background: transparent; border: none; padding: 24px;"
        )
        self.empty_label.setFixedHeight(120)
        root.addWidget(self.empty_label)

    def _init_anim(self):
        self.opacity_anim = QPropertyAnimation(self, b"windowOpacity")
        self.opacity_anim.setDuration(300)
        self.opacity_anim.setEasingCurve(QEasingCurve.OutCubic)
        # A single finished-handler resets _animating for both show & hide.
        self.opacity_anim.finished.connect(self._on_opacity_anim_finished)

    def paintEvent(self, event):
        # Paint a feathered shadow + card entirely within the window bounds.
        # (QGraphicsDropShadowEffect made the dirty region exceed the window
        # on a translucent Qt.Tool window, triggering UpdateLayeredWindowIndirect
        # "参数错误". Painting in-bounds avoids the out-of-bounds dirty rect.)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        shadow = QColor(0, 0, 0, 90)
        # Concentric rounded rects with rising alpha -> soft feathered edge
        # in the 2..8 px ring around the card. All in-bounds -> no error.
        for inset, alpha in ((2, 18), (4, 28), (6, 42)):
            c = QColor(shadow)
            c.setAlpha(alpha)
            painter.setBrush(c)
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(self.rect().adjusted(inset, inset, -inset, -inset), 16, 16)
        bg = self.palette().color(QPalette.Window)
        bg.setAlpha(238)
        painter.setBrush(bg)
        border = self.palette().color(QPalette.Mid)
        border.setAlpha(70)
        painter.setPen(QPen(border, 1))
        painter.drawRoundedRect(self.rect().adjusted(8, 8, -8, -8), 14, 14)

    def showEvent(self, event):
        # Apply WS_EX_NOACTIVATE once the HWND exists so mouse clicks on the
        # panel never steal focus from the user's input field.
        FamilyWindowRegistry.refresh_hwnd(self)
        FamilyWindowRegistry.set_no_activate(self)
        super().showEvent(event)

    def _on_header_moved(self, pos):
        # Remember the dragged position so the next show respects it, and
        # notify the manager to persist it.
        self._initial_pos = pos
        self.position_changed.emit(int(pos.x()), int(pos.y()))

    # ----- public API -----

    def set_items(self, items):
        # Clear existing item widgets (keep the trailing stretch)
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        for it in items:
            row = ClipboardItemWidget(it)
            row.copy_requested.connect(self.copy_requested)
            row.delete_requested.connect(self.delete_requested)
            self.list_layout.insertWidget(self.list_layout.count() - 1, row)
        self.empty_label.setVisible(not items)
        self.scroll.setVisible(bool(items))
        # Resize height to fit content (clamped)
        self._adjust_height(len(items))

    def set_status(self, text, peer_count=0):
        self.header.set_status(text)

    def _adjust_height(self, n):
        header_h = 40 + 24  # header + margins
        item_h = 56 + 4     # item + spacing
        content_h = header_h + min(n, 6) * item_h + 24
        self.setFixedHeight(min(self.MAX_HEIGHT, max(220, content_h)))

    def show_panel(self):
        """Show the panel, interrupting any in-progress hide animation by
        reversing from the current opacity. Safe to call when already fully
        shown (no-op) or while hiding (reverses)."""
        # Already fully shown and not hiding → nothing to do.
        if self.isVisible() and not self._pending_hide:
            return

        first_show = not self.isVisible()
        if first_show:
            if self._initial_pos is not None:
                self.move(self._initial_pos)
            else:
                self._place_near_cursor()
            FamilyWindowRegistry.add(self)
            self.setWindowOpacity(0.0)
            self.show()
            FamilyWindowRegistry.refresh_hwnd(self)
            self.raise_()
            start_opacity = 0.0
        else:
            # Reverse from wherever the hide animation currently is.
            start_opacity = self.windowOpacity()

        self.opacity_anim.stop()
        self.opacity_anim.setStartValue(start_opacity)
        self.opacity_anim.setEndValue(1.0)
        self._animating = True
        self._pending_hide = False
        self.opacity_anim.start()

    def hide_panel(self):
        """Hide the panel, interrupting any in-progress show animation by
        reversing from the current opacity. Safe to call when already hidden
        (no-op) or while showing (reverses).

        Qt's QAbstractAnimation.stop() does NOT emit finished, so reversing
        mid-show is safe — the unified _on_opacity_anim_finished handler
        only fires when this new hide animation completes."""
        if not self.isVisible():
            # Already hidden; ensure registry state is consistent.
            FamilyWindowRegistry.remove(self)
            return
        self.opacity_anim.stop()
        self.opacity_anim.setStartValue(self.windowOpacity())
        self.opacity_anim.setEndValue(0.0)
        self._animating = True
        self._pending_hide = True
        self.opacity_anim.start()

    def _on_opacity_anim_finished(self):
        """Unified handler for both show & hide animation completion."""
        self._animating = False
        if self._pending_hide:
            self._pending_hide = False
            self.hide()
            FamilyWindowRegistry.remove(self)

    def _place_near_cursor(self):
        cursor = QCursor.pos()
        screen = QGuiApplication.screenAt(cursor) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        x = cursor.x() + 12
        y = cursor.y() + 12
        if x + self.WIDTH > geo.right():
            x = cursor.x() - self.WIDTH - 12
        if y + self.height() > geo.bottom():
            y = geo.bottom() - self.height() - 8
        x = max(geo.x(), x)
        y = max(geo.y(), y)
        self.move(x, y)

    def set_initial_pos(self, pos):
        self._initial_pos = pos
