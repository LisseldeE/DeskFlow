"""Global search window (spotlight-style).

A floating search card with an input box, two scope toggles (全局文件 /
安装软件) and a results card. Safe calculation results and matches against
installed apps (read from the registry Uninstall keys in the background)
are both inline; the "全局文件" scope is backed by the Everything (ET)
software through its bundled es.exe command-line tool.

The window is a "family window" (registered with FamilyWindowRegistry) so
clicking inside it never collapses the family. ESC / outside-click / Enter
all funnel through CapRiseApp which hides the family, so closing the search
card also brings the capsule back.
"""
import ast
import math
import os
import shutil
import subprocess
import sys
import threading
import time
import winreg
import zipfile
from collections import deque
from pathlib import Path

from PySide6.QtCore import (
    Qt, Signal, QSize, QPoint, QPointF, QRectF, QPropertyAnimation,
    QVariantAnimation, QEasingCurve, QTimer, QEvent, QFileInfo, QUrl
)
from PySide6.QtGui import (
    QPainter, QColor, QPen, QPalette, QGuiApplication, QCursor, QKeyEvent,
    QIcon, QDesktopServices
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QScrollArea,
    QApplication, QAbstractButton, QFileIconProvider, QMessageBox
)

from modules.icons import ICON_SEARCH, ICON_APP, ICON_CALC, ICON_FILE
from modules.i18n import I18n
from modules.family import FamilyWindowRegistry
from modules.config import Config
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
# Everything (ET) file-search integration.
#
# CapRise drives Everything through its bundled command-line tool es.exe
# (installed alongside Everything by default). Everything itself must be
# running for es.exe to answer, so before each query the SearchWindow makes
# sure the Everything.exe process is up (launched silently to the tray when
# needed). The user-facing dependency flow (download / pick location) lives
# in SearchWindow._ensure_et_ready.
# --------------------------------------------------------------------------

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

_ET_DOWNLOAD_URL = "https://www.voidtools.com/downloads/"


def find_et_es():
    """Path to es.exe in the project config dir (~/CapRise/everything), or
    None. Only this dir is used: system-wide Everything installs and PATH
    entries are deliberately ignored."""
    es = os.path.join(_ET_RUN_DIR, "es.exe")
    if os.path.isfile(es):
        return es
    return None


def find_everything_exe():
    """Path to Everything.exe in the project config dir, or None."""
    run_exe = os.path.join(_ET_RUN_DIR, "Everything.exe")
    if os.path.isfile(run_exe):
        return run_exe
    return None


# --- Bundled Everything (portable Everything.exe + es.exe) ----------------
#
# CapRise ships an `everything.zip` next to the app so file search works out
# of the box. Both files are MIT licensed and redistributable (with
# attribution). File search ONLY reads ~/CapRise/everything: when the switch
# is first turned on, the zip is extracted there (Everything keeps its index
# next to its own exe, so it must never run from a temp extraction dir like
# _MEIPASS); a copy the user placed there is used as-is.

_ET_RUN_DIR = os.path.join(str(Path.home()), "CapRise", "everything")
_ET_ZIP_NAME = "everything.zip"
# Dedicated Everything instance name. Everything only answers es.exe from a
# same-session instance, and a system-wide install running as a service
# (Session 0) is invisible to es.exe — so CapRise always launches its own
# portable instance under this name and es.exe always connects to it. A
# distinct name also lets both coexist (Everything is single-instance per
# name, not per machine).
_ET_INSTANCE = "caprise"


def _bundle_zip():
    """Path of the bundled everything.zip, or None.

    Covers dev (project_root/everything.zip), PyInstaller onefile
    (sys._MEIPASS/everything.zip) and onedir (exe_dir/everything.zip)."""
    candidates = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidates.append(os.path.join(sys._MEIPASS, _ET_ZIP_NAME))
    candidates.append(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", _ET_ZIP_NAME))
    candidates.append(os.path.join(
        os.path.dirname(sys.executable), _ET_ZIP_NAME))
    for cand in candidates:
        if os.path.isfile(cand):
            return cand
    return None


def _provision_bundled_et():
    """Ensure ~/CapRise/everything has Everything.exe + es.exe.

    On first use the bundled everything.zip is extracted there; afterwards
    existing files are kept as-is (an already-present / older version is
    enabled instead of being overwritten). Returns (Everything.exe, es.exe)
    when ready, else (None, None)."""
    dst_e = os.path.join(_ET_RUN_DIR, "Everything.exe")
    dst_es = os.path.join(_ET_RUN_DIR, "es.exe")
    if os.path.isfile(dst_e) and os.path.isfile(dst_es):
        return dst_e, dst_es
    zip_path = _bundle_zip()
    if not zip_path:
        return None, None
    try:
        os.makedirs(_ET_RUN_DIR, exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
            for name in zf.namelist():
                base = os.path.basename(name).lower()
                if base not in ("everything.exe", "everything64.exe",
                                "es.exe", "license.txt") \
                        and not base.endswith(".lng"):
                    continue
                target = os.path.join(_ET_RUN_DIR, os.path.basename(name))
                if os.path.isfile(target):
                    continue
                with zf.open(name) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        if os.path.isfile(dst_e) and os.path.isfile(dst_es):
            return dst_e, dst_es
    except OSError:
        pass
    return None, None


def _decode_es_output(data):
    """Decode es.exe output.

    The bundled es.exe (1.1.0.x) writes redirected output using the system
    ANSI codepage (GBK on a zh-CN Windows) with no BOM — decoding it as
    UTF-8 turns every Chinese path into mojibake. Newer builds may emit
    UTF-16 / UTF-8, so try those first (BOM / null-byte heuristics), then
    strict UTF-8, and only fall back to the system ANSI codec."""
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        # The "utf-16" codec consumes the BOM it detects.
        return data.decode("utf-16", errors="replace")
    if b"\x00" in data:
        return data.decode("utf-16-le", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        # `mbcs` is Python's name for the real Windows ANSI code page
        # (GetACP): cp936/GBK on zh-CN, cp1252 on en-US — not the process
        # locale, which is often UTF-8 regardless of system locale.
        return data.decode("mbcs", errors="replace")


def query_et_files(es_path, query, instance=_ET_INSTANCE):
    """Run es.exe and return the full list of matching file paths.

    Always targets the `caprise` Everything instance — a system-wide
    Everything running as a service (Session 0) is invisible to es.exe, so
    queries must go through the portable instance CapRise launches itself.
    Returns a list of absolute paths (possibly empty). Raises OSError on a
    failed invocation so the caller can surface a "search failed" row."""
    cmd = [es_path, "-instance", instance, query]
    proc = subprocess.run(
        cmd, capture_output=True, creationflags=_CREATE_NO_WINDOW, timeout=20)
    if proc.returncode != 0:
        raise OSError(proc.stderr.decode(errors="replace").strip()
                      or f"es.exe exited with code {proc.returncode}")
    text = _decode_es_output(proc.stdout)
    return [line.strip() for line in text.splitlines() if line.strip()]


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
        self._animate_to(checked)

    def nextCheckState(self):
        """Keep the knob in sync when QAbstractButton toggles the state on a
        real mouse click.

        QAbstractButton flips the check state through the internal C++
        nextCheckState() on mouse release, which BYPASSES the Python
        setChecked() override — so without this the state would change but
        _t (the knob animation) would never move, making the switch look
        completely unresponsive to clicks."""
        super().nextCheckState()
        self._animate_to(self.isChecked())

    def _animate_to(self, checked):
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
    # 2nd arg: list of matching file paths (success) or None (search failed).
    file_results_ready = Signal(int, object)

    WIDTH = 520
    MAX_RESULTS_H = 300
    # Result rows are rendered in small batches on a timer so a huge result
    # set (hundreds of apps / thousands of files) never freezes the UI
    # thread. Each batch also only extracts that many system icons, so a
    # tick stays cheap even while the shell is queried.
    RENDER_BATCH = 30
    RENDER_INTERVAL = 25
    # Debounce: typing only re-arms a short single-shot timer; the actual
    # rebuild (re-filter + re-render + new es.exe query) fires once input
    # pauses, so fast typing doesn't spam refreshes or spawn a query per key.
    DEBOUNCE_MS = 300

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
        # Chunked result rendering: queued ("app", app) / ("file", path)
        # entries are drained RENDER_BATCH at a time on a timer so a huge
        # match set can't block the UI thread. Sections and status/loading
        # rows are added immediately (they're cheap).
        self._render_timer = QTimer(self)
        self._render_timer.setInterval(self.RENDER_INTERVAL)
        self._render_timer.timeout.connect(self._render_batch)
        self._pending_rows = deque()
        # Debounce timer for the input box (see DEBOUNCE_MS).
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(self.DEBOUNCE_MS)
        self._debounce.timeout.connect(self._rebuild_results)
        # Per-section row counters: async-drained rows are inserted right
        # after their own section header (see _append_row) so app rows can
        # never fall under the file header that was laid out before them.
        self._section_rows = {}
        # Everything(ET) file-search session state. _et_es is the es.exe path
        # once detection/manual selection succeeds (None = not ready); the
        # async query posts results back via file_results_ready, and stale
        # queries are dropped by comparing against _file_query_gen.
        self._et_es = None
        self._et_exe = None
        self._file_query_gen = 0
        self._file_loading_row = None
        self._file_section_lbl = None

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
        # Restore the persisted file-search switch (default off). Only
        # re-enable when Everything is already provisioned; otherwise keep
        # it off — the dependency prompt shows the next time the user turns
        # the switch on (no silent half-enabled state, no startup dialog).
        if Config().get("search_files_enabled", False) \
                and find_et_es() and find_everything_exe():
            self.switch_files.setChecked(True)
        else:
            self.switch_files.setChecked(False)
        self.switch_apps = ToggleSwitch(I18n.tr("search_installed_apps"))
        self.switch_apps.setChecked(True)
        toggles.addWidget(self.switch_files)
        toggles.addWidget(self.switch_apps)
        toggles.addStretch()
        root.addLayout(toggles)

        # --- separator ---
        # Hidden together with the results area so it never draws as a
        # stray short line under the input box when there is no content.
        self._sep = QLabel()
        self._sep.setFixedHeight(1)
        self._sep.setStyleSheet("background: rgba(128,128,128,60);")
        root.addWidget(self._sep)

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
        self.switch_files.toggled.connect(self._on_files_toggle)
        self.switch_apps.toggled.connect(self._on_scope_changed)
        # Results from the ET worker thread arrive here (queued connection).
        self.file_results_ready.connect(self._on_file_results_ready)

        # If the persisted file-search switch was restored ON, provision the
        # ET backend now. The setChecked(True) above ran before the toggled
        # signal was connected, so _on_files_toggle / _ensure_et_ready never
        # fired and self._et_es is still None — without this, restarting with
        # the switch enabled would leave file search silently non-functional.
        # ET is guaranteed present by the restore condition, so no dialog.
        if self.switch_files.isChecked():
            self._ensure_et_ready()

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
        self._debounce.stop()
        self._render_timer.stop()
        self._pending_rows.clear()
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
        if not text.strip():
            # Empty input collapses immediately — no need to wait out the
            # debounce just to hide an already-empty results card.
            self._debounce.stop()
            self._rebuild_results()
        else:
            # Re-arm the debounce: the rebuild runs once typing pauses.
            self._debounce.start()

    def _on_scope_changed(self, _=None):
        self._rebuild_results()

    def _on_index_poll(self):
        if get_indexed_apps() is not None:
            self._index_poll.stop()
            self._rebuild_results()

    # ----------------------------------------------------- Everything (ET)

    def _on_files_toggle(self, checked):
        """Enable/disable the Everything-backed file search.

        Turning the toggle ON checks once whether Everything is installed;
        if not, a friendly dialog explains the dependency and offers to
        download it or let the user point at an existing install. A
        cancelled / unresolved dependency keeps the feature OFF (the toggle
        is un-checked) so no silent half-enabled state lingers."""
        if checked and not self._ensure_et_ready():
            self.switch_files.blockSignals(True)
            self.switch_files.setChecked(False)
            self.switch_files.blockSignals(False)
        # Persist the switch so it survives restarts (default off).
        Config().set("search_files_enabled", self.switch_files.isChecked())
        self._rebuild_results()

    def _ensure_et_ready(self):
        """Return True when Everything.exe + es.exe are usable.

        Resolution order:
        1. files already present in ~/CapRise/everything (user-placed or
           previously extracted);
        2. the bundled everything.zip, extracted to ~/CapRise/everything on
           first use (existing / older copies are kept);
        3. friendly download dialog pointing at the config dir.
        """
        if self._et_es:
            return True
        es = find_et_es()
        if es:
            everything = find_everything_exe()
            if everything:
                self._et_es = es
                self._et_exe = everything
                return True
        # Config dir empty: pull Everything + es.exe from the bundled zip.
        everything, es = _provision_bundled_et()
        if everything and es:
            self._et_es = es
            self._et_exe = everything
            return True
        return self._prompt_et_missing()

    def _prompt_et_missing(self):
        """Dialog when file search can't start: the config dir has no
        Everything. Offers the official download / skip for now."""
        box = QMessageBox(self)
        box.setWindowTitle(I18n.tr("search_et_title"))
        box.setIcon(QMessageBox.Warning)
        box.setText(I18n.tr("search_et_msg") + "\n\n" + _ET_RUN_DIR)
        btn_download = box.addButton(
            I18n.tr("search_et_download"), QMessageBox.AcceptRole)
        box.addButton(I18n.tr("search_et_later"), QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is btn_download:
            QDesktopServices.openUrl(QUrl(_ET_DOWNLOAD_URL))
        return False

    def _ensure_et_running(self):
        """Make sure the `caprise` Everything instance is running.

        Runs on the worker thread (pure subprocess, no Qt). A `tasklist` hit
        on "Everything.exe" is NOT enough: a system-wide Everything running
        as a service (Session 0) can't answer es.exe, so we probe the named
        instance directly and, when missing, launch the bundled portable
        Everything with a dedicated instance name (which coexists with any
        system install). Returns True when the instance was already up — a
        freshly launched instance needs a short grace period before es.exe
        can answer."""
        exe = self._et_exe
        es = self._et_es
        if not exe or not es:
            return True
        if self._et_instance_up(es):
            return True
        try:
            subprocess.Popen(
                [exe, "-instance", _ET_INSTANCE, "-startup"],
                creationflags=_CREATE_NO_WINDOW)
        except Exception:
            pass
        return False

    def _et_instance_up(self, es):
        """True when the `caprise` Everything instance is reachable via es.exe."""
        try:
            proc = subprocess.run(
                [es, "-instance", _ET_INSTANCE, "-get-window-handle"],
                capture_output=True, creationflags=_CREATE_NO_WINDOW, timeout=5)
            return proc.returncode == 0
        except Exception:
            return False

    def _run_file_query(self, text, gen):
        """Background worker: query es.exe and post the paths back.

        The result is matched against `gen` in _on_file_results_ready so a
        stale (superseded) query never overwrites newer results."""
        was_running = self._ensure_et_running()
        paths = None
        for attempt in range(1 if was_running else 3):
            try:
                paths = query_et_files(self._et_es, text, _ET_INSTANCE)
                break
            except Exception:
                paths = None
                if attempt < 2:
                    # A freshly launched instance needs a moment for its IPC
                    # to come up before es.exe can connect.
                    time.sleep(2.0 if not was_running else 1.0)
        try:
            self.file_results_ready.emit(gen, paths)
        except RuntimeError:
            pass  # the search card closed before the query finished

    def _on_file_results_ready(self, gen, paths):
        """File query finished (queued from the worker thread)."""
        if gen != self._file_query_gen:
            return  # superseded by a newer query
        if self._file_loading_row is not None:
            self.results_layout.removeWidget(self._file_loading_row)
            self._file_loading_row.deleteLater()
            self._file_loading_row = None
        if paths is None:
            self._add_status_row(I18n.tr("search_et_failed"))
        else:
            for path in paths:
                self._pending_rows.append(("file", path))
            if not paths and self._file_section_lbl is not None:
                # No matches -> drop the empty section header too.
                self.results_layout.removeWidget(self._file_section_lbl)
                self._file_section_lbl.deleteLater()
                self._file_section_lbl = None
            if self._pending_rows:
                self._render_timer.start()
        self._set_results_visible(self.results_layout.count() > 1 or
                                  bool(self._pending_rows))

    def _add_file_row(self, path):
        name = os.path.basename(path.rstrip("\\/")) or path
        parent = os.path.dirname(path.rstrip("\\/"))
        icon = _get_file_icon(path) or ICON_FILE
        row = ResultRow(icon, name, parent or path)
        self._append_row(row, {"kind": "file", "path": path},
                         tooltip=I18n.tr("search_file_open_tip"),
                         section=self._file_section_lbl)

    def _add_file_loading_row(self):
        return self._add_status_row(I18n.tr("search_et_searching"))

    def _add_status_row(self, text):
        lbl = QLabel(text)
        lbl.setFixedHeight(36)
        lbl.setStyleSheet(
            "color: palette(placeholder-text); background: transparent;"
            " border: none; padding: 0 10px; font-size: 12px;")
        self.results_layout.insertWidget(self.results_layout.count() - 1, lbl)
        return lbl

    def _rebuild_results(self):
        # Any rebuild supersedes a pending debounce (e.g. a scope toggle).
        self._debounce.stop()
        self._render_timer.stop()
        self._pending_rows.clear()
        # Invalidate any in-flight file query: stale results must never
        # repopulate a rebuilt list (e.g. after the file switch is toggled
        # off mid-search). A fresh query below re-reads the bumped gen.
        self._file_query_gen += 1
        # Fresh per-section row counters / header refs for this rebuild.
        self._section_rows = {}
        self._apps_section_lbl = None
        while self.results_layout.count() > 1:
            item = self.results_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._nav = []
        self._file_loading_row = None
        self._file_section_lbl = None

        text = self.search_input.text().strip()
        if not text:
            self._set_results_visible(False)
            return

        # 1) Calculation result (always active, independent of the toggles).
        value = evaluate_expression(text)
        if value is not None:
            self._add_section(I18n.tr("search_category_calc"))
            self._add_calc_row(text, value)

        # 2) Installed apps (gated by the 安装软件 toggle). Matching rows are
        #    queued and drained RENDER_BATCH at a time on a timer, so a big
        #    match set can't freeze the card. Every match is shown — never
        #    truncated, it just scrolls.
        if self.switch_apps.isChecked():
            apps = get_indexed_apps()
            if apps is None:
                self._add_section(I18n.tr("search_category_apps"))
                self._add_loading_row()
            else:
                matches = [a for a in apps
                           if text.lower() in a["name"].lower()]
                if matches:
                    self._apps_section_lbl = self._add_section(
                        I18n.tr("search_category_apps"))
                    for app in matches:
                        self._pending_rows.append(("app", app))

        # 3) Global files — Everything(ET) backend, queried on a background
        #    thread so typing stays responsive. The loading row shows until
        #    es.exe answers; the section is populated in
        #    _on_file_results_ready (stale queries are dropped by gen).
        if self.switch_files.isChecked() and self._et_es:
            self._file_section_lbl = self._add_section(
                I18n.tr("search_category_files"))
            self._file_loading_row = self._add_file_loading_row()
            gen = self._file_query_gen
            threading.Thread(
                target=self._run_file_query, args=(text, gen),
                name="caprise-et-search", daemon=True).start()

        if self._pending_rows:
            self._set_results_visible(True)
            self._render_timer.start()
        elif self.results_layout.count() > 1:
            self._set_results_visible(True)
        else:
            self._set_results_visible(False)

    def _render_batch(self):
        """Drain queued result rows in small batches (timer callback).

        Yields the event loop between batches, so typing / closing / the
        global mouse hook keep working even while hundreds of rows are
        still being added. The first row is auto-selected as soon as the
        first entries land."""
        made = 0
        while made < self.RENDER_BATCH and self._pending_rows:
            kind, data = self._pending_rows.popleft()
            if kind == "app":
                self._add_app_row(data)
            else:
                self._add_file_row(data)
            made += 1
        self._update_results_height()
        if not self._pending_rows:
            self._render_timer.stop()
        if self._selected < 0 and self._nav:
            self._select(0)

    def _add_section(self, text):
        label = SectionLabel(text)
        self.results_layout.insertWidget(
            self.results_layout.count() - 1, label)
        return label

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
                         tooltip=I18n.tr("search_app_open_tip"),
                         section=self._apps_section_lbl)

    def _add_loading_row(self):
        lbl = QLabel(I18n.tr("search_indexing"))
        lbl.setFixedHeight(36)
        lbl.setStyleSheet(
            "color: palette(placeholder-text); background: transparent;"
            " border: none; padding: 0 10px; font-size: 12px;")
        self.results_layout.insertWidget(self.results_layout.count() - 1, lbl)

    def _append_row(self, row, payload, tooltip="", section=None):
        """Insert `row` into the results list.

        Rows drain asynchronously from _pending_rows while section headers
        are laid out synchronously inside _rebuild_results, so inserting
        every row at the end (count()-1) would drop later-added sections'
        rows under the wrong header — e.g. app rows appearing below the
        file header that was already created. Rows therefore go immediately
        after their own section header, using a per-section counter to keep
        them in order; without a section they fall back to the end.
        """
        if tooltip:
            row.setToolTip(tooltip)
        row.clicked.connect(self._on_row_clicked)
        if section is not None:
            idx = self.results_layout.indexOf(section)
            if idx >= 0:
                n = self._section_rows.get(id(section), 0)
                self.results_layout.insertWidget(idx + 1 + n, row)
                self._section_rows[id(section)] = n + 1
            else:
                self.results_layout.insertWidget(
                    self.results_layout.count() - 1, row)
        else:
            self.results_layout.insertWidget(self.results_layout.count() - 1, row)
        self._nav.append((row, payload))

    def _set_results_visible(self, visible):
        self.scroll.setVisible(visible)
        # The separator belongs to the results area: hide it together so it
        # doesn't linger as a lone short line under the input when empty.
        self._sep.setVisible(visible)
        if visible:
            self._update_results_height()
        else:
            self.scroll.setFixedHeight(0)
            self.setFixedHeight(110)

    def _update_results_height(self):
        """Refit the scroll area (and the card) to the current content."""
        content = self._results_content_h()
        scroll_h = max(0, min(content, self.MAX_RESULTS_H))
        self.scroll.setFixedHeight(scroll_h)
        total = 12 + 40 + (10 + 24 + 6) + 1 + scroll_h + 12
        self.setFixedHeight(max(110, total))

    def _results_content_h(self):
        """Total content height, with an early-out once it passes
        MAX_RESULTS_H — the scroll view clamps there anyway, so this stays
        O(visible rows) instead of O(all rows) on huge result sets."""
        spacing = self.results_layout.spacing()
        h = 0
        for i in range(self.results_layout.count()):
            w = self.results_layout.itemAt(i).widget()
            if w is None:
                continue
            h += w.height() if w.height() > 0 else w.sizeHint().height()
            if h >= self.MAX_RESULTS_H:
                return self.MAX_RESULTS_H
            h += spacing
        m = self.results_layout.contentsMargins()
        return h + m.top() + m.bottom()

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
        elif kind in ("app", "file"):
            path = payload.get("path", "")
            if path:
                try:
                    os.startfile(path)
                except OSError:
                    pass
            self.close_search()
