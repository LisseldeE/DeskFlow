from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, Signal, QRect
from PySide6.QtGui import QPainter, QColor, QGuiApplication


def pixel_source(pixmap, dip_rect):
    """Map a Qt device-independent (logical) rect into a screen-grab pixmap's
    PIXEL coordinates.

    `QPixmap.copy()` and `QPainter.drawPixmap` interpret their source rect in
    the pixmap's device (pixel) coordinates. But all mouse coordinates and the
    overlay geometry are in Qt logical units. On HiDPI displays the grab from
    `QScreen.grabWindow` has `devicePixelRatio() > 1` (e.g. 1.25 at 125%
    scaling), so a logical rect must be scaled up by the DPR — otherwise the
    boxed content is read from a too-small, shifted pixel region and renders
    enlarged and offset."""
    dpr = pixmap.devicePixelRatio() or 1.0
    return QRect(
        int(dip_rect.x() * dpr), int(dip_rect.y() * dpr),
        int(dip_rect.width() * dpr), int(dip_rect.height() * dpr),
    )


class BaseOverlay(QWidget):
    """Full-screen dark overlay base class"""
    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)

        # Cover all monitors
        virtual_geo = QGuiApplication.primaryScreen().virtualGeometry()
        self.setGeometry(virtual_geo)
        self.show()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 180))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close_overlay()
        super().keyPressEvent(event)

    def close_overlay(self):
        self.closed.emit()
        self.close()
        self.deleteLater()