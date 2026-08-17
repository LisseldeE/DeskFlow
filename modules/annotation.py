from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton, QTextEdit, QApplication
)
from PySide6.QtCore import Qt, QRect, QPoint, QSize, Signal, QByteArray
from PySide6.QtGui import (
    QPainter, QColor, QPen, QFont, QGuiApplication, QFontMetrics,
    QPainterPath, QPalette, QIcon, QPixmap, QBrush, QKeyEvent
)
from PySide6.QtSvg import QSvgRenderer
from modules.overlay import BaseOverlay
from modules.icons import ICON_RECTANGLE, ICON_FREEFORM, ICON_TEXT, ICON_CLOSE
from modules.i18n import I18n


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


def _system_color(mode):
    """Get system color by mode: 'normal' -> WindowText, 'accent' -> Highlight"""
    p = QApplication.palette()
    if mode == "normal":
        c = p.color(QPalette.WindowText)
        return f"#{c.red():02x}{c.green():02x}{c.blue():02x}"
    else:
        c = p.color(QPalette.Highlight)
        return f"#{c.red():02x}{c.green():02x}{c.blue():02x}"


def _annotation_border_color():
    """Return border color for annotation shapes based on theme.
    Dark mode -> light gray, Light mode -> dark gray."""
    bg = QApplication.palette().color(QPalette.Window)
    is_dark = bg.lightness() < 128
    return QColor(200, 200, 200) if is_dark else QColor(80, 80, 80)


class TextEditWidget(QTextEdit):
    """Inline text editor for annotation text.
    Created after dragging a rectangle in text mode.
    Press Enter to finish, double-click existing text to re-edit."""

    def __init__(self, rect, text="", parent=None):
        super().__init__(parent)
        self.setGeometry(rect)
        self.setPlainText(text)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet("""
            QTextEdit {
                background-color: rgba(0, 0, 0, 140);
                color: #ffffff;
                border: 2px solid #c8c8c8;
                border-radius: 2px;
                padding: 6px;
                font-size: 16px;
                font-family: 'Segoe UI';
            }
        """)
        self.setFocus()
        self.selectAll()

    def keyPressEvent(self, event: QKeyEvent):
        # Ctrl+Enter to finish editing
        if event.key() == Qt.Key_Return and event.modifiers() & Qt.ControlModifier:
            self.parent()._finish_text_edit()
            return
        # ESC to cancel
        if event.key() == Qt.Key_Escape:
            self.parent()._cancel_text_edit()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        # Finish editing when clicking outside
        self.parent()._finish_text_edit()
        super().focusOutEvent(event)


class AnnotationOverlay(BaseOverlay):
    """Screen annotation overlay with rectangle, freeform, and text tools"""
    finished = Signal()

    def __init__(self, parent=None):
        # Capture desktop before overlay is shown
        self.desktop_pixmap = QGuiApplication.primaryScreen().grabWindow(0)
        super().__init__(parent)
        self.annotations = []
        self.current_mode = "rectangle"
        self.current_shape = None
        self.is_drawing = False
        self.text_editor = None  # active inline text editor
        self._text_edit_idx = None  # index of text annotation being edited, None for new
        self._drag_start = None  # start point of current drag (like screenshot approach)
        self.activateWindow()
        self.setFocus()
        self.setup_toolbar()

    def setup_toolbar(self):
        """Create floating annotation mode toolbar using system palette colors"""
        bg = QApplication.palette().color(QPalette.Window)
        hl = QApplication.palette().color(QPalette.Highlight)

        self.toolbar = QWidget(self)
        self.toolbar.setStyleSheet(f"""
            QWidget {{
                background-color: rgba({bg.red()}, {bg.green()}, {bg.blue()}, 230);
                border: 1px solid rgba({hl.red()}, {hl.green()}, {hl.blue()}, 60);
                border-radius: 24px;
            }}
            QPushButton {{
                background-color: transparent;
                border: none;
                border-radius: 8px;
                padding: 4px;
            }}
            QPushButton:hover {{
                background-color: rgba({hl.red()}, {hl.green()}, {hl.blue()}, 50);
            }}
            QPushButton:checked {{
                background-color: rgba({hl.red()}, {hl.green()}, {hl.blue()}, 150);
            }}
        """)

        layout = QHBoxLayout(self.toolbar)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 5, 12, 5)

        self.mode_buttons = {}
        for mode, icon_name, tip in [
            ("rectangle", "rectangle", I18n.tr("rectangle")),
            ("freeform", "freeform", I18n.tr("freeform")),
            ("text", "text", I18n.tr("text")),
        ]:
            btn = QPushButton()
            btn.setToolTip(tip)
            btn.setFixedSize(38, 38)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            svg = ICON_RECTANGLE if mode == "rectangle" else \
                  ICON_FREEFORM if mode == "freeform" else \
                  ICON_TEXT
            normal_color = _system_color("normal")
            accent_color = _system_color("accent")
            btn.setProperty("svg", svg)
            btn.setProperty("normal_color", normal_color)
            btn.setProperty("accent_color", accent_color)
            btn.setIcon(_make_icon(svg, normal_color, 20))
            btn.setIconSize(QSize(20, 20))
            btn.clicked.connect(lambda checked, m=mode: self._set_mode(m))
            self.mode_buttons[mode] = btn
            layout.addWidget(btn)

        self.mode_buttons["rectangle"].setChecked(True)

        # Close button (same style as capsule close button)
        self.btn_close = QPushButton()
        self.btn_close.setToolTip(I18n.tr("close"))
        self.btn_close.setFixedSize(38, 38)
        self.btn_close.setCursor(Qt.PointingHandCursor)
        self.btn_close.setIcon(_make_icon(ICON_CLOSE, _system_color("normal"), 20))
        self.btn_close.setIconSize(QSize(20, 20))
        self.btn_close.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                border: none;
                border-radius: 8px;
            }}
            QPushButton:hover {{
                background-color: rgba(224, 49, 49, 80);
            }}
        """)
        self.btn_close.enterEvent = lambda e: self._on_close_enter()
        self.btn_close.leaveEvent = lambda e: self._on_close_leave()
        self.btn_close.clicked.connect(self._on_close_clicked)
        layout.addWidget(self.btn_close)

        screen = QGuiApplication.primaryScreen().availableGeometry()
        tw = 12 + 38 + 8 + 38 + 8 + 38 + 8 + 38 + 12  # 200
        tx = (screen.width() - tw) // 2
        self.toolbar.setGeometry(int(tx), 60, int(tw), 48)
        self.toolbar.show()

    def _on_close_enter(self):
        self.btn_close.setIcon(_make_icon(ICON_CLOSE, "#e03131", 20))

    def _on_close_leave(self):
        self.btn_close.setIcon(_make_icon(ICON_CLOSE, _system_color("normal"), 20))

    def _on_close_clicked(self):
        self.finished.emit()
        self.close_overlay()

    def _set_mode(self, mode):
        self.current_mode = mode
        for m, btn in self.mode_buttons.items():
            btn.setChecked(m == mode)
            svg = btn.property("svg")
            if m == mode:
                btn.setIcon(_make_icon(svg, btn.property("accent_color"), 20))
            else:
                btn.setIcon(_make_icon(svg, btn.property("normal_color"), 20))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 180))
        for ann in self.annotations:
            self._draw_annotation(painter, ann, is_temp=False)
        if self.current_shape and self.is_drawing:
            # During drag, text shape has no text content — only draw the rect
            self._draw_annotation(painter, self.current_shape, is_temp=True)

    def _draw_annotation(self, painter, ann, is_temp=False):
        ann_type = ann[0]
        border_color = _annotation_border_color()
        painter.setPen(QPen(border_color, 2))

        if ann_type == "rectangle":
            rect = ann[1]
            # Cut out the overlay - show desktop content inside the rectangle
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            painter.drawPixmap(rect, self.desktop_pixmap, rect)
            # Draw border only (no fill)
            painter.setPen(QPen(border_color, 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect)
            if not is_temp:
                self._draw_delete_button(painter, rect.topRight(), rect)

        elif ann_type == "freeform":
            points = ann[1]
            if len(points) < 2:
                return
            path = QPainterPath()
            path.moveTo(points[0])
            for pt in points[1:]:
                path.lineTo(pt)
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)
            if not is_temp:
                bounds = path.boundingRect().toRect()
                self._draw_delete_button(painter, bounds.topRight(), bounds)

        elif ann_type == "text":
            rect = ann[1]
            # Draw border
            painter.setPen(QPen(border_color, 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect)
            if not is_temp and len(ann) > 2:
                # Draw text inside the rect (only for saved annotations)
                text = ann[2]
                painter.setFont(QFont("Segoe UI", 14))
                painter.setPen(QColor(255, 255, 255))
                painter.drawText(rect.adjusted(6, 6, -6, -6), Qt.AlignLeft | Qt.AlignTop, text)
                self._draw_delete_button(painter, rect.topRight(), rect)

    def _delete_button_rect(self, top_right, annotation_rect):
        """Compute delete button position, clamping to visible area.
        If too close to top edge, places button below the annotation instead."""
        btn_size = 16
        margin = 4
        x = top_right.x() + margin
        y = top_right.y() - btn_size - margin
        if y < 0:
            # Place below the annotation rect instead
            y = annotation_rect.bottom() + margin
        return QRect(int(x), int(y), btn_size, btn_size)

    def _draw_delete_button(self, painter, top_right, annotation_rect):
        btn_rect = self._delete_button_rect(top_right, annotation_rect)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(239, 68, 68, 200))
        painter.drawRoundedRect(btn_rect, 3, 3)
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.drawLine(btn_rect.topLeft() + QPoint(4, 4),
                         btn_rect.bottomRight() - QPoint(4, 4))
        painter.drawLine(btn_rect.topRight() + QPoint(-4, 4),
                         btn_rect.bottomLeft() + QPoint(4, -4))
        ann_key = id(annotation_rect)
        if not hasattr(self, '_delete_rects'):
            self._delete_rects = {}
        self._delete_rects[ann_key] = btn_rect

    def _get_delete_rects(self):
        if not hasattr(self, '_delete_rects'):
            self._delete_rects = {}
        result = {}
        for i, ann in enumerate(self.annotations):
            ann_type = ann[0]
            if ann_type == "rectangle":
                rect = ann[1]
                key = id(rect)
                if key in self._delete_rects:
                    result[i] = self._delete_rects[key]
            elif ann_type == "freeform":
                points = ann[1]
                if len(points) >= 2:
                    path = QPainterPath()
                    path.moveTo(points[0])
                    for pt in points[1:]:
                        path.lineTo(pt)
                    bounds = path.boundingRect().toRect()
                    result[i] = self._delete_button_rect(bounds.topRight(), bounds)
            elif ann_type == "text":
                rect = ann[1]
                result[i] = self._delete_button_rect(rect.topRight(), rect)
        return result

    def _is_text_double_click(self, pos):
        """Check if position is inside an existing text annotation (for double-click edit)"""
        for ann in self.annotations:
            if ann[0] == "text":
                rect = ann[1]
                if rect.contains(pos):
                    return ann
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pos = event.position().toPoint()

            # Check delete buttons first
            delete_rects = self._get_delete_rects()
            for idx, rect in delete_rects.items():
                if rect.contains(pos):
                    self._finish_text_edit()
                    self.annotations.pop(idx)
                    self._delete_rects = {}
                    self.update()
                    return

            # If text editor is active and user clicks outside it, finish editing
            if self.text_editor:
                if not self.text_editor.geometry().contains(pos):
                    self._finish_text_edit()
                else:
                    super().mousePressEvent(event)
                    return

            # Start drawing (same approach as screenshot: store start_point)
            self._drag_start = pos
            self.is_drawing = True
            if self.current_mode == "rectangle":
                self.current_shape = ("rectangle", QRect(pos, pos))
            elif self.current_mode == "freeform":
                self.current_shape = ("freeform", [pos])
            elif self.current_mode == "text":
                self.current_shape = ("text", QRect(pos, pos))
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        """Double-click on existing text annotation to edit"""
        if event.button() == Qt.LeftButton and self.current_mode == "text":
            pos = event.position().toPoint()
            existing = self._is_text_double_click(pos)
            if existing:
                # Cancel the drawing started by the first click
                self.is_drawing = False
                self.current_shape = None
                self._drag_start = None
                idx = self.annotations.index(existing)
                self._start_text_edit(existing[1], existing[2], idx)
                return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event):
        if self.is_drawing and self._drag_start and self.current_shape:
            pos = event.position().toPoint()
            if self.current_mode == "rectangle":
                # Same approach as screenshot: QRect(start, end).normalized()
                self.current_shape = ("rectangle", QRect(self._drag_start, pos).normalized())
            elif self.current_mode == "freeform":
                self.current_shape[1].append(pos)
            elif self.current_mode == "text":
                self.current_shape = ("text", QRect(self._drag_start, pos).normalized())
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.is_drawing:
            self.is_drawing = False
            if self.current_shape:
                mode = self.current_mode
                if mode == "rectangle":
                    rect = self.current_shape[1]
                    if rect.width() > 5 and rect.height() > 5:
                        self.annotations.append(self.current_shape)
                elif mode == "freeform":
                    if len(self.current_shape[1]) >= 3:
                        self.annotations.append(self.current_shape)
                elif mode == "text":
                    rect = self.current_shape[1]
                    if rect.width() > 5 and rect.height() > 5:
                        # Create inline text editor
                        self._start_text_edit(rect, "", None)
            self.current_shape = None
            self._drag_start = None
            self._delete_rects = {}
            self.update()

    def _start_text_edit(self, rect, text, edit_idx):
        """Create an inline text editor at the given rect"""
        self._finish_text_edit()  # finish any existing editor first
        self._text_edit_idx = edit_idx
        self.text_editor = TextEditWidget(rect, text, self)
        self.text_editor.show()
        self.text_editor.setFocus()

    def _finish_text_edit(self):
        """Save text from the active editor and destroy it"""
        if self.text_editor is None:
            return
        text = self.text_editor.toPlainText().strip()
        rect = self.text_editor.geometry()
        self.text_editor.deleteLater()
        self.text_editor = None
        if text:
            if self._text_edit_idx is not None:
                # Update existing annotation
                self.annotations[self._text_edit_idx] = ("text", rect, text)
            else:
                # Add new annotation
                self.annotations.append(("text", rect, text))
        self._text_edit_idx = None
        self.update()
        # Re-focus the overlay for keyboard events
        self.setFocus()

    def _cancel_text_edit(self):
        """Cancel text editing and destroy the editor"""
        if self.text_editor:
            self.text_editor.deleteLater()
            self.text_editor = None
        self._text_edit_idx = None
        self.setFocus()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            if self.is_drawing:
                self.is_drawing = False
                self.current_shape = None
                self._drag_start = None
                self.update()
            else:
                self.finished.emit()
                self.close_overlay()
        super().keyPressEvent(event)