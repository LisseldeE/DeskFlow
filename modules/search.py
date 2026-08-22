"""Global search window (spotlight-style).

A floating search card with an input box, two scope toggles (全局文件 /
安装软件) and a results card. The Everything(ET) file-search backend is NOT
wired yet — the "全局文件" toggle is only a front-end placeholder; the card
currently returns safe calculation results and matches against installed
apps (read from the registry Uninstall keys in the background).

The window is a "family window" (registered with FamilyWindowRegistry) so
clicking inside it never collapses the family. ESC / outside-click / Enter
all funnel through CapRiseApp which hides the family, so closing the search
card also brings the capsule back.
"""
import ast
import math
import os
import threading
import winreg

from PySide6.QtCore import (
    Qt, Signal, QSize, QPoint, QPointF, QRectF, QPropertyAnimation,
    QVariantAnimation, QEasingCurve, QTimer, QEvent, QFileInfo
)
from PySide6.QtGui import (
    QPainter, QColor, QPen, QPalette, QGuiApplication, QCursor, QKeyEvent, QIcon
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QScrollArea,
    QApplication, QAbstractButton, QFileIconProvider
)

from modules.icons import ICON_SEARCH, ICON_APP, ICON_CALC
from modules.i18n import I18n
from modules.family import FamilyWindowRegistry
from modules.widgets import make_pixmap, system_color, screen_dpr


# --------------------------------------------------------------------------
# Calculation engine (safe AST evaluation, no eval on raw input)
# --------------------------------------------------------------------------

_MATH_FUNCS = {
    "sqrt": math.sqrt,
    "abs": abs,
    "floor": math.floor,
    "ceil": math.ceil,
    "round": round,
    "log": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "exp": math.exp,
    "pow": pow,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "sinh": math.sinh,
    "cosh": math.cosh,
    "tanh": math.tanh,
    "degrees": math.degrees,
    "radians": math.radians,
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
}

# Guard against runaway exponents (e.g. 9**9**9) that would hang the UI.
_MAX_POW_EXP = 10000


def _eval_node(node):
    """Recursively evaluate an AST node against a strict whitelist.

    Anything outside {numbers, + - * / % **, unary +/-, whitelisted math
    names/functions} raises, so only plain arithmetic can ever be computed.
    """
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("unsupported constant")
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.FloorDiv):
            return left // right
        if isinstance(node.op, ast.Mod):
            return left % right
        if isinstance(node.op, ast.Pow):
            if abs(right) > _MAX_POW_EXP:
                raise OverflowError("exponent too large")
            return left ** right
        raise ValueError("unsupported operator")
    if isinstance(node, ast.UnaryOp):
        val = _eval_node(node.operand)
        if isinstance(node.op, ast.UAdd):
            return +val
        if isinstance(node.op, ast.USub):
            return -val
        raise ValueError("unsupported operator")
    if isinstance(node, ast.Name):
        value = _MATH_FUNCS.get(node.id)
        if value is None or callable(value):
            raise ValueError("unknown name")
        return value
    if isinstance(node, ast.Call):
        func = _MATH_FUNCS.get(node.func.id)
        if func is None or not callable(func):
            raise ValueError("unknown function")
        if node.keywords:
            raise ValueError("keyword args not allowed")
        return func(*[_eval_node(a) for a in node.args])
    raise ValueError("unsupported expression")


def evaluate_expression(text):
    """Return the numeric result if `text` is a valid safe expression,
    else None (not a calculable expression)."""
    try:
        value = _eval_node(ast.parse(text, mode="eval"))
    except Exception:
        return None
    if isinstance(value, float):
        if math.isinf(value) or math.isnan(value):
            return None
        if value == int(value) and abs(value) < 1e15:
            value = int(value)
    return value


def format_number(value):
    """Human-friendly rendering, truncated for absurdly large integers."""
    if isinstance(value, int):
        s = str(value)
        return s if len(s) <= 2000 else s[:1997] + "..."
    s = f"{value:.10g}"
    return s if len(s) <= 2000 else s[:1997] + "..."


# --------------------------------------------------------------------------
# Installed-app index (registry Uninstall keys), loaded once in the
# background on a daemon thread so the UI never blocks.
# --------------------------------------------------------------------------

_REGISTRY_ROOTS = [
    (winreg.HKEY_LOCAL_MACHINE,
     r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_LOCAL_MACHINE,
     r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_CURRENT_USER,
     r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_CURRENT_USER,
     r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
]


def _reg_get(key, name):
    try:
        value, _ = winreg.QueryValueEx(key, name)
        if isinstance(value, str):
            return value.strip()
    except OSError:
        pass
    return ""


def _load_installed_apps():
    """Read installed programs from the registry Uninstall keys.

    Only entries with a DisplayName are kept. The same app can appear under
    several roots (HKLM/HKCU x 32/64-bit), so duplicates by name are dropped."""
    seen = set()
    apps = []
    for root, sub_path in _REGISTRY_ROOTS:
        try:
            with winreg.OpenKey(root, sub_path) as key:
                for i in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        sub_name = winreg.EnumKey(key, i)
                        with winreg.OpenKey(key, sub_name) as sub:
                            name = _reg_get(sub, "DisplayName")
                            if not name:
                                continue
                            key_l = name.lower()
                            if key_l in seen:
                                continue
                            seen.add(key_l)
                            apps.append({
                                "name": name,
                                "location": _reg_get(sub, "InstallLocation"),
                                "icon": _reg_get(sub, "DisplayIcon"),
                                "publisher": _reg_get(sub, "Publisher"),
                                "version": _reg_get(sub, "DisplayVersion"),
                            })
                    except OSError:
                        continue
        except OSError:
            continue
    apps.sort(key=lambda a: a["name"].lower())
    return apps


_INDEX_LOCK = threading.Lock()
_INDEX_THREAD = None
_INDEX_READY = False
_INDEX_APPS = []


def start_app_index():
    """Kick off the registry index once (idempotent)."""
    global _INDEX_THREAD
    with _INDEX_LOCK:
        if _INDEX_READY or _INDEX_THREAD is not None:
            return

        def _run():
            global _INDEX_READY, _INDEX_APPS
            try:
                _INDEX_APPS = _load_installed_apps()
            except Exception:
                _INDEX_APPS = []
            finally:
                _INDEX_READY = True

        _INDEX_THREAD = threading.Thread(
            target=_run, name="caprise-app-index", daemon=True)
        _INDEX_THREAD.start()


def get_indexed_apps():
    """Return the cached app list, or None while the index is still loading."""
    return _INDEX_APPS if _INDEX_READY else None


def _app_launch_path(app):
    """Best-effort launch path from registry data.

    DisplayIcon usually carries the main exe (sometimes with a ",0" suffix);
    InstallLocation + DisplayName is the fallback guess. Empty string means
    "cannot launch — open the install folder instead"."""
    icon = app.get("icon") or ""
    path = icon.split(",")[0].strip().strip('"')
    if path.lower().endswith(".exe") and os.path.isfile(path):
        return path
    loc = app.get("location") or ""
    if loc and os.path.isdir(loc):
        candidate = os.path.join(loc, (app.get("name") or "") + ".exe")
        if os.path.isfile(candidate):
            return candidate
    return ""


# --------------------------------------------------------------------------
# Real system icons (extracted from the exe / DisplayIcon), cached so
# re-searching doesn't hammer the shell. Falls back to None when a path
# has no resolvable icon, so the caller can show the generic SVG.
# --------------------------------------------------------------------------

_ICON_PROVIDER = QFileIconProvider()
_FILE_ICON_CACHE = {}


def _get_file_icon(path):
    """Return the real Windows icon for `path` (exe/ico/dll/lnk/...), or
    None if it can't be resolved. Results are cached per lowercased path."""
    if not path:
        return None
    key = path.lower()
    if key in _FILE_ICON_CACHE:
        return _FILE_ICON_CACHE[key]
    try:
        icon = _ICON_PROVIDER.icon(QFileInfo(path))
    except Exception:
        icon = QIcon()
    cached = icon if not icon.isNull() else None
    _FILE_ICON_CACHE[key] = cached
    return cached


def _app_icon(app, launch):
    """Best real icon for an app row: prefer the launch exe, then the raw
    DisplayIcon value (may be an .ico/.dll/.exe). None = generic fallback."""
    icon = _get_file_icon(launch)
    if icon:
        return icon
    display = (app.get("icon") or "").split(",")[0].strip().strip('"')
    return _get_file_icon(display)


def _render_real_icon(icon, slot):
    """Render a real system `QIcon` into a QPixmap of exactly `slot` logical px.

    QIcon.pixmap() is DPR-unpredictable: on some platforms it returns a
    DPR-1.0 pixmap of the requested pixel size, on others it already
    pre-scales by the screen DPR (treating the request as logical). Blindly
    tagging a DPR on either can make the icon render larger than the slot
    and get clipped at the bottom-right on scaled (125%/150%) displays.
    Normalize to DPR 1.0 and rescale to the exact physical size first, so
    the icon ALWAYS displays at exactly `slot` logical px, never larger."""
    dpr = screen_dpr()
    phys = max(1, int(slot * dpr))
    pm = icon.pixmap(QSize(phys, phys))
    pm.setDevicePixelRatio(1.0)
    if pm.size() != QSize(phys, phys):
        pm = pm.scaled(phys, phys, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    pm.setDevicePixelRatio(dpr)
    return pm


# --------------------------------------------------------------------------
# Small UI widgets
# --------------------------------------------------------------------------

def _blend(c1, c2, t):
    return QColor(
        int(c1.red() + (c2.red() - c1.red()) * t),
        int(c1.green() + (c2.green() - c1.green()) * t),
        int(c1.blue() + (c2.blue() - c1.blue()) * t),
    )


class ToggleSwitch(QAbstractButton):
    """Compact animated switch (track + knob) with a text label on the right."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self._text = text
        self._t = 1.0 if self.isChecked() else 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

    def _on_anim(self, value):
        self._t = float(value)
        self.update()

    def setChecked(self, checked):
        if bool(checked) == self.isChecked():
            super().setChecked(checked)
            return
        super().setChecked(checked)
        self._anim.stop()
        self._anim.setStartValue(self._t)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def sizeHint(self):
        fm = self.fontMetrics()
        w = 34 + 8
        if self._text:
            w += fm.horizontalAdvance(self._text)
        return QSize(int(w), 24)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        track_w, track_h = 34, 18
        track_y = (self.height() - track_h) / 2.0
        accent = QApplication.palette().color(QPalette.Highlight)
        off = QApplication.palette().color(QPalette.Mid)
        p.setPen(Qt.NoPen)
        p.setBrush(_blend(off, accent, self._t))
        p.drawRoundedRect(
            QRectF(0, track_y, track_w, track_h), track_h / 2.0, track_h / 2.0)
        knob_d = track_h - 6
        knob_x = 3 + (track_w - knob_d - 6) * self._t
        knob_y = track_y + 3
        knob = _blend(
            QApplication.palette().color(QPalette.WindowText),
            QColor(255, 255, 255), self._t * 0.85)
        p.setBrush(knob)
        p.drawEllipse(
            QPointF(knob_x + knob_d / 2.0, knob_y + knob_d / 2.0),
            knob_d / 2.0, knob_d / 2.0)
        if self._text:
            x = track_w + 8
            p.setPen(QApplication.palette().color(QPalette.WindowText))
            p.drawText(
                QRectF(x, 0, self.width() - x, self.height()),
                Qt.AlignVCenter | Qt.AlignLeft, self._text)
        p.end()


class ResultRow(QWidget):
    """A selectable result row: leading icon + title + secondary line."""

    clicked = Signal()

    def __init__(self, icon, title, subtitle, title_accent=False, parent=None):
        super().__init__(parent)
        self.setFixedHeight(44)
        self.setCursor(Qt.PointingHandCursor)
        self._selected = False
        self._title_accent = title_accent
        if isinstance(icon, str):
            # Generic SVG icon -> tinted with the window-text colour.
            self._icon_pix = make_pixmap(
                icon, system_color(QPalette.WindowText), 18)
        else:
            # Real system icon (QIcon) -> deterministic DPR-correct render
            # into the 26px slot (never larger, never clipped on scaling).
            self._icon_pix = _render_real_icon(icon, 26)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 10, 4)
        lay.setSpacing(10)

        icon_lbl = QLabel()
        icon_lbl.setFixedSize(26, 26)
        icon_lbl.setPixmap(self._icon_pix)
        lay.addWidget(icon_lbl)

        text_lay = QVBoxLayout()
        text_lay.setContentsMargins(0, 0, 0, 0)
        text_lay.setSpacing(0)
        self._title_lbl = QLabel(title)
        self._sub_lbl = QLabel(subtitle)
        self._title_lbl.setSizePolicy(
            self._title_lbl.sizePolicy().horizontalPolicy(), self._title_lbl.sizePolicy().verticalPolicy())
        text_lay.addWidget(self._title_lbl)
        text_lay.addWidget(self._sub_lbl)
        lay.addLayout(text_lay, 1)
        self._apply_style()

    def _apply_style(self):
        if self._selected:
            title = "#ffffff"
            sub = "rgba(255,255,255,190)"
        else:
            title = "palette(highlight)" if self._title_accent else "palette(text)"
            sub = "palette(placeholder-text)"
        self._title_lbl.setStyleSheet(
            f"color: {title}; background: transparent; border: none;"
            " font-size: 13px;")
        self._sub_lbl.setStyleSheet(
            f"color: {sub}; background: transparent; border: none;"
            " font-size: 11px;")
        self.update()

    def set_selected(self, selected):
        if self._selected == selected:
            return
        self._selected = selected
        self._apply_style()

    def paintEvent(self, event):
        if self._selected:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(QApplication.palette().color(QPalette.Highlight))
            p.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 8, 8)
        super().paintEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class SectionLabel(QLabel):
    """Non-selectable results section header."""

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setFixedHeight(22)
        self.setStyleSheet(
            "color: palette(placeholder-text); background: transparent;"
            " border: none; font-size: 11px; font-weight: 600;"
            " padding: 4px 10px 0 10px;")


# --------------------------------------------------------------------------
# Search window
# --------------------------------------------------------------------------

class SearchWindow(QWidget):
    """Floating search card: input + scope toggles + results list.

    Takes keyboard focus (unlike the capsule/panel which float passively),
    so activateWindow + a Win32 foreground nudge are applied on show.
    """

    closed = Signal()

    WIDTH = 520
    MAX_RESULTS_H = 300

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedWidth(self.WIDTH)

        self._animating = False
        self._pending_hide = False
        self._closing = False
        self._selected = -1
        self._nav = []          # list of (ResultRow, payload)
        self._closed_emitted = False

        self._build_ui()
        self._init_anim()

        FamilyWindowRegistry.add(self)
        start_app_index()
        if get_indexed_apps() is None:
            # Re-run the search once the background index becomes available.
            self._index_poll = QTimer(self)
            self._index_poll.setInterval(100)
            self._index_poll.timeout.connect(self._on_index_poll)
            self._index_poll.start()
        self.hide()

    @property
    def is_closing(self):
        """True while a close (fade-out) has been requested but not finished.

        Used by CapRiseApp to decide whether a re-entry must be queued
        (a card mid-close can't be re-shown without stacking a second card).
        """
        return self._closing

    # ---------------------------------------------------------------- UI

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(0)

        # --- input row ---
        self._input_wrap = QWidget()
        self._input_wrap.setObjectName("searchInputWrap")
        self._input_wrap.setFixedHeight(40)
        self._input_wrap.setStyleSheet("""
            QWidget#searchInputWrap {
                background: palette(base);
                border: 1px solid rgba(128,128,128,100);
                border-radius: 10px;
            }
        """)
        inp_lay = QHBoxLayout(self._input_wrap)
        inp_lay.setContentsMargins(12, 0, 8, 0)
        inp_lay.setSpacing(8)
        icon_lbl = QLabel()
        icon_lbl.setFixedSize(18, 18)
        icon_lbl.setPixmap(make_pixmap(
            ICON_SEARCH, system_color(QPalette.PlaceholderText), 18))
        inp_lay.addWidget(icon_lbl)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(I18n.tr("search_placeholder"))
        self.search_input.setFrame(False)
        self.search_input.setStyleSheet("""
            QLineEdit {
                background: transparent; border: none;
                color: palette(text); font-size: 14px;
                selection-background-color: palette(highlight);
                selection-color: #ffffff;
            }
        """)
        inp_lay.addWidget(self.search_input, 1)
        root.addWidget(self._input_wrap)

        # --- scope toggles ---
        toggles = QHBoxLayout()
        toggles.setContentsMargins(4, 10, 4, 6)
        toggles.setSpacing(18)
        self.switch_files = ToggleSwitch(I18n.tr("search_global_files"))
        self.switch_files.setChecked(False)
        self.switch_apps = ToggleSwitch(I18n.tr("search_installed_apps"))
        self.switch_apps.setChecked(True)
        toggles.addWidget(self.switch_files)
        toggles.addWidget(self.switch_apps)
        toggles.addStretch()
        root.addLayout(toggles)

        # --- separator ---
        sep = QLabel()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: rgba(128,128,128,60);")
        root.addWidget(sep)

        # --- results area ---
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.verticalScrollBar().setSingleStep(44)
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
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
        """)

        self.results_host = QWidget()
        self.results_host.setStyleSheet("background: transparent;")
        self.results_layout = QVBoxLayout(self.results_host)
        self.results_layout.setContentsMargins(2, 6, 2, 4)
        self.results_layout.setSpacing(2)
        self.results_layout.addStretch()
        self.scroll.setWidget(self.results_host)
        root.addWidget(self.scroll, 1)

        # --- wiring ---
        self.search_input.textChanged.connect(self._on_text_changed)
        self.search_input.installEventFilter(self)
        self.switch_files.toggled.connect(self._on_scope_changed)
        self.switch_apps.toggled.connect(self._on_scope_changed)

        # Collapse the results area to the compact 110px card on first show.
        # Without this the scroll area is visible at its default minimum
        # height (a blank box below the separator) and never collapses —
        # _set_results_visible is only reached from _rebuild_results (i.e.
        # once the user types). The card must only expand for real results.
        self._set_results_visible(False)

    def _init_anim(self):
        self.opacity_anim = QPropertyAnimation(self, b"windowOpacity")
        self.opacity_anim.setDuration(300)
        self.opacity_anim.setEasingCurve(QEasingCurve.OutCubic)
        self.pos_anim = QPropertyAnimation(self, b"pos")
        self.pos_anim.setDuration(300)
        self.pos_anim.setEasingCurve(QEasingCurve.OutCubic)
        self.opacity_anim.finished.connect(self._on_anim_finished)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        shadow = QColor(0, 0, 0, 90)
        for inset, alpha in ((2, 18), (4, 28), (6, 42)):
            c = QColor(shadow)
            c.setAlpha(alpha)
            painter.setBrush(c)
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(
                self.rect().adjusted(inset, inset, -inset, -inset), 18, 18)
        bg = self.palette().color(QPalette.Window)
        bg.setAlpha(255)
        painter.setBrush(bg)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(self.rect().adjusted(8, 8, -8, -8), 14, 14)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(80, 80, 80, 255), 1))
        painter.drawRoundedRect(self.rect().adjusted(8, 8, -9, -9), 14, 14)

    def showEvent(self, event):
        FamilyWindowRegistry.refresh_hwnd(self)
        super().showEvent(event)

    def hideEvent(self, event):
        self.opacity_anim.stop()
        self.pos_anim.stop()
        self._animating = False
        self._pending_hide = False
        super().hideEvent(event)
        # A hide that arrives while a close was requested (interrupted
        # fade-out, force-hide, ...) must still complete the close — this is
        # what guarantees the card can never linger on screen (expanded but
        # empty) and the `closed` signal is always eventually emitted.
        if self._closing:
            self._finish_close()

    def eventFilter(self, obj, event):
        if obj is self.search_input:
            if event.type() == QEvent.FocusIn:
                self._input_wrap.setStyleSheet("""
                    QWidget#searchInputWrap {
                        background: palette(base);
                        border: 1px solid palette(highlight);
                        border-radius: 10px;
                    }
                """)
            elif event.type() == QEvent.FocusOut:
                self._input_wrap.setStyleSheet("""
                    QWidget#searchInputWrap {
                        background: palette(base);
                        border: 1px solid rgba(128,128,128,100);
                        border-radius: 10px;
                    }
                """)
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------ show/hide

    def show_search(self):
        if self.isVisible() and not self._pending_hide:
            return
        first_show = not self.isVisible()
        if first_show:
            self._place()
            FamilyWindowRegistry.add(self)
            self.setWindowOpacity(0.0)
            self.show()
            FamilyWindowRegistry.refresh_hwnd(self)
            self.raise_()
            self.activateWindow()
            self.search_input.setFocus(Qt.OtherFocusReason)
            # On Windows the app is usually NOT the foreground process (the
            # capsule floats with WS_EX_NOACTIVATE), so a plain activateWindow
            # can be denied. Nudge the native window to the foreground once
            # its HWND exists.
            QTimer.singleShot(0, self._win32_foreground)
            start_pos = self.pos()
            start_opacity = 0.0
        else:
            start_pos = self.pos()
            start_opacity = self.windowOpacity()

        self.opacity_anim.stop()
        self.pos_anim.stop()
        self.opacity_anim.setStartValue(start_opacity)
        self.opacity_anim.setEndValue(1.0)
        self.pos_anim.setStartValue(
            QPoint(start_pos.x(), start_pos.y() - 8))
        self.pos_anim.setEndValue(start_pos)
        self._animating = True
        self._pending_hide = False
        self._closing = False
        self.opacity_anim.start()
        self.pos_anim.start()

    def close_search(self):
        """Animate out, then emit `closed` and schedule deletion.

        A safety-net timer guarantees the card collapses even if the opacity
        animation is interrupted (e.g. WM/focus churn while launching an app)
        or the platform ignores windowOpacity — the card must never linger
        on screen empty. `_closing` marks the close as requested so ANY
        subsequent hide (animation end, safety timer, external force-hide)
        funnels into _finish_close() exactly once — no stale window can
        survive repeated enter/exit cycles."""
        if self._closed_emitted or self._closing:
            return
        self._closing = True
        if not self.isVisible():
            self._finish_close()
            return
        self.opacity_anim.stop()
        self.pos_anim.stop()
        self.opacity_anim.setStartValue(self.windowOpacity())
        self.opacity_anim.setEndValue(0.0)
        self._animating = True
        self._pending_hide = True
        self.opacity_anim.start()
        QTimer.singleShot(400, self._finish_close_if_pending)

    def _finish_close_if_pending(self):
        """Safety net for close_search: if the fade-out was interrupted or
        never finished, force-hide now. hideEvent then completes the close
        via _finish_close(); if it already finished, this is a no-op."""
        if self._closed_emitted:
            return
        self.opacity_anim.stop()
        self.pos_anim.stop()
        self.hide()
        self._finish_close()

    def _on_anim_finished(self):
        self._animating = False
        if self._pending_hide:
            self.hide()      # hideEvent -> _finish_close (guarded by _closing)
            self._finish_close()

    def _finish_close(self):
        if self._closed_emitted:
            return
        self._closed_emitted = True
        self._closing = False
        FamilyWindowRegistry.remove(self)
        self.closed.emit()
        self.deleteLater()

    def _win32_foreground(self):
        """Allow the search window to take keyboard focus even though CapRise
        is normally a background app (capsule uses WS_EX_NOACTIVATE)."""
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = int(self.winId())
            if not hwnd:
                return
            fg = user32.GetForegroundWindow()
            fg_tid = user32.GetWindowThreadProcessId(fg, None)
            cur_tid = user32.GetCurrentThreadId()
            if cur_tid != fg_tid:
                user32.AttachThreadInput(cur_tid, fg_tid, True)
                try:
                    user32.SetForegroundWindow(hwnd)
                finally:
                    user32.AttachThreadInput(cur_tid, fg_tid, False)
            user32.SetFocus(hwnd)
        except Exception:
            pass

    def _place(self):
        cursor = QCursor.pos()
        screen = QGuiApplication.screenAt(cursor) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        x = geo.x() + (geo.width() - self.WIDTH) // 2
        y = geo.y() + 60
        self.move(x, y)

    # ------------------------------------------------------------- results

    def _on_text_changed(self, text):
        self._selected = -1
        self._rebuild_results()

    def _on_scope_changed(self, _=None):
        self._rebuild_results()

    def _on_index_poll(self):
        if get_indexed_apps() is not None:
            self._index_poll.stop()
            self._rebuild_results()

    def _rebuild_results(self):
        while self.results_layout.count() > 1:
            item = self.results_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._nav = []

        text = self.search_input.text().strip()
        if not text:
            self._set_results_visible(False)
            return

        # 1) Calculation result (always active, independent of the toggles).
        value = evaluate_expression(text)
        if value is not None:
            self._add_section(I18n.tr("search_category_calc"))
            self._add_calc_row(text, value)

        # 2) Installed apps (gated by the 安装软件 toggle).
        if self.switch_apps.isChecked():
            apps = get_indexed_apps()
            if apps is None:
                self._add_section(I18n.tr("search_category_apps"))
                self._add_loading_row()
            else:
                matches = [a for a in apps
                           if text.lower() in a["name"].lower()][:20]
                if matches:
                    self._add_section(I18n.tr("search_category_apps"))
                    for app in matches:
                        self._add_app_row(app)

        # 3) Global files — Everything(ET) backend is NOT wired yet; only the
        #    front-end toggle exists, so no file section is populated.

        has_widgets = self.results_layout.count() > 1
        self._set_results_visible(has_widgets)
        if self._nav:
            self._select(0)

    def _add_section(self, text):
        self.results_layout.insertWidget(
            self.results_layout.count() - 1, SectionLabel(text))

    def _add_calc_row(self, expr, value):
        result = format_number(value)
        row = ResultRow(ICON_CALC, f"= {result}", expr, title_accent=True)
        self._append_row(row, {"kind": "calc", "text": result},
                         tooltip=I18n.tr("search_calc_copy_tip"))

    def _add_app_row(self, app):
        launch = _app_launch_path(app)
        subtitle_parts = [p for p in (app.get("publisher"), app.get("version"))
                          if p]
        subtitle = " · ".join(subtitle_parts) if subtitle_parts else \
            (launch or app.get("location") or "")
        icon = _app_icon(app, launch) or ICON_APP
        row = ResultRow(icon, app["name"], subtitle)
        path = launch or (app.get("location") or "")
        self._append_row(row, {"kind": "app", "path": path},
                         tooltip=I18n.tr("search_app_open_tip"))

    def _add_loading_row(self):
        lbl = QLabel(I18n.tr("search_indexing"))
        lbl.setFixedHeight(36)
        lbl.setStyleSheet(
            "color: palette(placeholder-text); background: transparent;"
            " border: none; padding: 0 10px; font-size: 12px;")
        self.results_layout.insertWidget(self.results_layout.count() - 1, lbl)

    def _append_row(self, row, payload, tooltip=""):
        if tooltip:
            row.setToolTip(tooltip)
        row.clicked.connect(self._on_row_clicked)
        self.results_layout.insertWidget(self.results_layout.count() - 1, row)
        self._nav.append((row, payload))

    def _set_results_visible(self, visible):
        self.scroll.setVisible(visible)
        if visible:
            content = self._results_content_h()
            scroll_h = max(0, min(content, self.MAX_RESULTS_H))
            self.scroll.setFixedHeight(scroll_h)
            total = 12 + 40 + (10 + 24 + 6) + 1 + scroll_h + 12
        else:
            self.scroll.setFixedHeight(0)
            total = 110
        self.setFixedHeight(max(110, total))

    def _results_content_h(self):
        widgets = []
        for i in range(self.results_layout.count()):
            w = self.results_layout.itemAt(i).widget()
            if w is not None:
                widgets.append(w)
        if not widgets:
            return 0
        h = sum(w.height() if w.height() > 0 else w.sizeHint().height()
                for w in widgets)
        h += (len(widgets) - 1) * self.results_layout.spacing()
        m = self.results_layout.contentsMargins()
        h += m.top() + m.bottom()
        return h

    # --------------------------------------------------------- navigation

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key_Down, Qt.Key_Up):
            self._move_selection(1 if event.key() == Qt.Key_Down else -1)
            event.accept()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._activate_selection()
            event.accept()
            return
        if event.key() == Qt.Key_Escape:
            # Normally consumed by the native ESC filter; safe fallback.
            self.close_search()
            event.accept()
            return
        super().keyPressEvent(event)

    def _select(self, index):
        if index == self._selected:
            return
        if 0 <= self._selected < len(self._nav):
            self._nav[self._selected][0].set_selected(False)
        self._selected = index
        if 0 <= index < len(self._nav):
            row, _ = self._nav[index]
            row.set_selected(True)
            self._ensure_visible(row)

    def _move_selection(self, delta):
        if not self._nav:
            return
        if self._selected < 0:
            new = 0 if delta > 0 else len(self._nav) - 1
        else:
            new = self._selected + delta
        new = max(0, min(len(self._nav) - 1, new))
        self._select(new)

    def _ensure_visible(self, row):
        sb = self.scroll.verticalScrollBar()
        view_h = self.scroll.viewport().height()
        y = row.y()
        if y < sb.value():
            sb.setValue(y)
        elif y + row.height() > sb.value() + view_h:
            sb.setValue(y + row.height() - view_h)

    def _on_row_clicked(self, row):
        for i, (r, _) in enumerate(self._nav):
            if r is row:
                self._select(i)
                self._activate_selection()
                break

    def _activate_selection(self):
        if not (0 <= self._selected < len(self._nav)):
            return
        payload = self._nav[self._selected][1]
        if payload is None:
            return
        kind = payload.get("kind")
        if kind == "calc":
            QApplication.clipboard().setText(payload.get("text", ""))
            self.close_search()
        elif kind == "app":
            path = payload.get("path", "")
            if path:
                try:
                    os.startfile(path)
                except OSError:
                    pass
            self.close_search()
