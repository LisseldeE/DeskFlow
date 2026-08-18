from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QTextEdit, QApplication, QGraphicsDropShadowEffect
)
from PySide6.QtCore import (
    Qt, QRect, QPoint, QPointF, Signal, QVariantAnimation, QEasingCurve
)
from PySide6.QtGui import (
    QPainter, QColor, QPen, QFont, QGuiApplication, QFontMetrics,
    QPainterPath, QPalette, QBrush, QKeyEvent
)
from modules.overlay import BaseOverlay
from modules.icons import ICON_RECTANGLE, ICON_FREEFORM, ICON_TEXT, ICON_CLOSE
from modules.i18n import I18n
from modules.widgets import GlassIconButton, paint_pill


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


class AnnotationToolbar(QWidget):
    """Floating annotation sub-bar: mode buttons + close.

    Painted with the same pill look as the capsule bar (shared paint_pill),
    so both read as one design family; floats over the dark overlay with a
    soft drop shadow."""

    mode_changed = Signal(str)
    close_clicked = Signal()

    BTN = 40
    RADIUS = 26
    BAR_H = 52

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedHeight(self.BAR_H)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 90))
        shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)

        # Mode order = left-to-right button order. `_slide` is a float index
        # (0..2) that the selection plate glides across as it moves between
        # buttons — linear travel with slow-fast-slow easing (InOutCubic).
        self._modes = ["rectangle", "freeform", "text"]
        self._slide = 0.0
        self._slide_anim = QVariantAnimation(self)
        self._slide_anim.setDuration(280)
        self._slide_anim.setEasingCurve(QEasingCurve.InOutCubic)
        self._slide_anim.valueChanged.connect(self._on_slide)

        self.setup_ui()
        self.set_selected("rectangle")

    def set_selected(self, mode):
        """Glide the selection plate to the given mode's button position."""
        if mode not in self.mode_buttons:
            return
        target = float(self._modes.index(mode))
        self._slide_anim.stop()
        self._slide_anim.setStartValue(self._slide)
        self._slide_anim.setEndValue(target)
        self._slide_anim.start()

    def _on_slide(self, value):
        self._slide = float(value)
        self.update()

    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 6, 10, 6)

        self.mode_buttons = {}
        for mode, svg, tip in [
            ("rectangle", ICON_RECTANGLE, I18n.tr("rectangle")),
            ("freeform", ICON_FREEFORM, I18n.tr("freeform")),
            ("text", ICON_TEXT, I18n.tr("text")),
        ]:
            btn = GlassIconButton(svg, tip, size=self.BTN, icon_size=20)
            btn.clicked.connect(
                lambda checked, m=mode: self.mode_changed.emit(m))
            self.mode_buttons[mode] = btn
            layout.addWidget(btn)

        self.btn_close = GlassIconButton(
            ICON_CLOSE, I18n.tr("close"), size=self.BTN, icon_size=20,
            hover_color="#e03131", hover_bg_color=QColor(224, 49, 49))
        self.btn_close.clicked.connect(self.close_clicked.emit)
        layout.addWidget(self.btn_close)

        self.setFixedWidth(10 + self.BTN * 4 + 8 * 3 + 10)

    def paintEvent(self, event):
        painter = QPainter(self)
        paint_pill(painter, self.rect(), self.RADIUS)

        # Selection plate gliding behind the active mode button. The mode
        # buttons are transparent (never set_active), so this plate shows
        # through them and slides linearly with slow-fast-slow easing.
        margin = 10
        spacing = 8
        top = 6
        x = int(margin + self._slide * (self.BTN + spacing))
        slider = QRect(x, top, self.BTN, self.BTN)
        if slider.intersects(self.rect()):
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(Qt.NoPen)
            hl = QApplication.palette().color(QPalette.Highlight)
            painter.setBrush(
                QColor(hl.red(), hl.green(), hl.blue(), 150))
            painter.drawRoundedRect(slider, self.BTN // 3, self.BTN // 3)


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
        # Per-annotation corner controls: delete (red X) + drag handle (grip).
        self._delete_rects = {}
        self._drag_rects = {}
        self._drag_ann_idx = None  # annotation currently being moved
        self._drag_offset = None   # grab point offset from the annotation origin
        self._last_drag_pos = None
        self.activateWindow()
        self.setFocus()
        self.setup_toolbar()

    def setup_toolbar(self):
        """Create the floating annotation sub-bar (same pill style as capsule)."""
        self.toolbar = AnnotationToolbar(self)
        self.toolbar.mode_changed.connect(self._set_mode)
        self.toolbar.close_clicked.connect(self._on_close_clicked)

        screen = QGuiApplication.primaryScreen().availableGeometry()
        tw = self.toolbar.width()
        tx = (screen.width() - tw) // 2
        self.toolbar.setGeometry(int(tx), 60, tw, AnnotationToolbar.BAR_H)
        self.toolbar.show()

    def _on_close_clicked(self):
        self.finished.emit()
        self.close_overlay()

    def _set_mode(self, mode):
        self.current_mode = mode
        self.toolbar.set_selected(mode)

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
                self._draw_handles(painter, rect.topRight(), rect)

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
                self._draw_handles(painter, bounds.topRight(), bounds)

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
                self._draw_handles(painter, rect.topRight(), rect)

    def _handle_rects(self, top_right, annotation_rect):
        """Layout the two corner controls of an annotation, top-right:
           [drag grip] [delete]. Clamped to screen — if the corner is too
           close to the top edge, both controls drop below the annotation."""
        btn_size = 18
        spacing = 4
        margin = 4
        x = top_right.x() + margin
        y = top_right.y() - btn_size - margin
        if y < 0:
            y = annotation_rect.bottom() + margin
        delete_rect = QRect(int(x), int(y), btn_size, btn_size)
        drag_rect = QRect(int(x - btn_size - spacing), int(y), btn_size, btn_size)
        return drag_rect, delete_rect

    def _draw_handles(self, painter, top_right, annotation_rect):
        """Draw a delete button (red circle + white X) and a drag handle
        (translucent white circle + gray grip dots) at the annotation's
        top-right corner. Records both hit rects for mouse handling."""
        drag_rect, delete_rect = self._handle_rects(top_right, annotation_rect)
        ann_key = id(annotation_rect)
        self._delete_rects[ann_key] = delete_rect
        self._drag_rects[ann_key] = drag_rect

        painter.setRenderHint(QPainter.Antialiasing)
        # --- delete: red circle + white X, subtle white ring ---
        painter.setPen(QPen(QColor(255, 255, 255, 120), 1))
        painter.setBrush(QColor(239, 68, 68, 235))
        painter.drawEllipse(delete_rect)
        xpen = QPen(QColor(255, 255, 255), 2)
        xpen.setCapStyle(Qt.RoundCap)
        painter.setPen(xpen)
        ins = 5
        painter.drawLine(delete_rect.topLeft() + QPoint(ins, ins),
                         delete_rect.bottomRight() - QPoint(ins, ins))
        painter.drawLine(delete_rect.topRight() + QPoint(-ins, ins),
                         delete_rect.bottomLeft() + QPoint(ins, -ins))
        # --- drag: translucent white circle + gray grip grid ---
        painter.setPen(QPen(QColor(255, 255, 255, 120), 1))
        painter.setBrush(QColor(255, 255, 255, 190))
        painter.drawEllipse(drag_rect)
        gpen = QPen(QColor(90, 90, 90, 230), 1)
        gpen.setCapStyle(Qt.FlatCap)
        painter.setPen(gpen)
        painter.setBrush(Qt.NoBrush)
        cx, cy = drag_rect.center().x(), drag_rect.center().y()
        radius = 2.2
        for dx in (-3, 0, 3):
            for dy in (-3, 3):
                painter.drawEllipse(QPointF(cx + dx, cy + dy), radius, radius)

    def _get_delete_rects(self):
        result = {}
        for i, ann in enumerate(self.annotations):
            corner = self._annotation_corner(ann)
            if corner is not None:
                result[i] = self._handle_rects(*corner)[1]
        return result

    def _get_drag_rects(self):
        result = {}
        for i, ann in enumerate(self.annotations):
            corner = self._annotation_corner(ann)
            if corner is not None:
                result[i] = self._handle_rects(*corner)[0]
        return result

    def _annotation_corner(self, ann):
        """Return (top_right, annotation_rect) for a stored annotation,
        or None if it has no usable geometry."""
        ann_type = ann[0]
        if ann_type in ("rectangle", "text"):
            rect = ann[1]
            return (rect.topRight(), rect)
        if ann_type == "freeform":
            points = ann[1]
            if len(points) >= 2:
                path = QPainterPath()
                path.moveTo(points[0])
                for pt in points[1:]:
                    path.lineTo(pt)
                bounds = path.boundingRect().toRect()
                return (bounds.topRight(), bounds)
        return None

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
                    self._drag_rects = {}
                    self.update()
                    return

            # Drag handle: grab an existing annotation to move it.
            if self._drag_ann_idx is None:
                for idx, rect in self._get_drag_rects().items():
                    if rect.contains(pos):
                        self._finish_text_edit()
                        ann = self.annotations[idx]
                        ref = (ann[1][0] if ann[0] == "freeform"
                               else ann[1].topLeft())
                        self._drag_ann_idx = idx
                        self._drag_offset = pos - ref
                        self._last_drag_pos = pos
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
        # Dragging an existing annotation through its grip handle.
        if self._drag_ann_idx is not None and self._last_drag_pos is not None:
            pos = event.position().toPoint()
            ann = self.annotations[self._drag_ann_idx]
            if ann[0] == "freeform":
                delta = pos - self._last_drag_pos
                pts = ann[1]  # translate the shared list in place (tuple-immutable)
                pts[:] = [pt + delta for pt in pts]
                self._last_drag_pos = pos
            else:
                ann[1].moveTopLeft(pos - self._drag_offset)
            self._drag_rects = {}
            self._delete_rects = {}
            self.update()
            return

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
        # Finish an annotation move started from the grip handle.
        if event.button() == Qt.LeftButton and self._drag_ann_idx is not None:
            self._drag_ann_idx = None
            self._drag_offset = None
            self._last_drag_pos = None
            self._drag_rects = {}
            self._delete_rects = {}
            self.update()
            return

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