"""Settings dialog for CapRise.

Split-pane layout: a left navigation column lets the user switch between the
General / Translate / System / About sub-cards, and the right pane shows the
settings for the selected section (task 4). The About page also carries the
check-for-update logic (task 5)."""
import sys
import os
import winreg
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QCheckBox,
    QListWidget, QListWidgetItem, QStackedWidget, QWidget, QFrame,
    QApplication, QGraphicsOpacityEffect, QAbstractItemView
)
from PySide6.QtCore import (
    Qt, QSize, QByteArray, QPropertyAnimation, QEasingCurve, Signal,
    QMimeData, QPoint
)
from PySide6.QtGui import (
    QGuiApplication, QPalette, QPixmap, QPainter, QColor, QDrag
)
from PySide6.QtSvg import QSvgRenderer
from modules.config import Config
from modules.i18n import I18n
from modules.about import AboutPage

# Dotted grip glyph used as the drag handle on each reorder row.
GRIP_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
  <circle cx="9"  cy="6"  r="1.7" fill="currentColor"/>
  <circle cx="15" cy="6"  r="1.7" fill="currentColor"/>
  <circle cx="9"  cy="12" r="1.7" fill="currentColor"/>
  <circle cx="15" cy="12" r="1.7" fill="currentColor"/>
  <circle cx="9"  cy="18" r="1.7" fill="currentColor"/>
  <circle cx="15" cy="18" r="1.7" fill="currentColor"/>
</svg>"""


def _get_app_cmd():
    """Command registered to HKCU Run.

    For a source launch, prefer pythonw.exe (windowless) when available so no
    black console flashes at logon alongside the app; a frozen build registers
    only the exe itself (sys.executable is already the entry point)."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    exe = sys.executable
    if os.path.basename(exe).lower() == "python.exe":
        alt = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(alt):
            exe = alt
    return f'"{exe}" "{os.path.abspath(sys.argv[0])}"'


def apply_autostart(enabled):
    """Set or clear the auto-start registry entry for the current user.

    Returns True on success, False on failure (logged to stderr) so callers
    can surface the problem instead of it being silently swallowed."""
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE
            )
        except OSError:
            # Run key missing (rare) — create it so the entry can be written.
            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path)
        try:
            if enabled:
                winreg.SetValueEx(key, "CapRise", 0, winreg.REG_SZ, _get_app_cmd())
            else:
                try:
                    winreg.DeleteValue(key, "CapRise")
                except FileNotFoundError:
                    pass
        finally:
            winreg.CloseKey(key)
        return True
    except Exception as e:
        print(f"Failed to set autostart: {e}")
        return False


def _accent_hex():
    c = QApplication.palette().color(QPalette.Highlight)
    return f"#{c.red():02x}{c.green():02x}{c.blue():02x}"


def _text_hex():
    c = QApplication.palette().color(QPalette.WindowText)
    return f"#{c.red():02x}{c.green():02x}{c.blue():02x}"


def _make_grip_pixmap(size=14):
    """Rasterise the dotted grip SVG as a DPR-aware QPixmap.

    The grip is a subtle grey derived from the window-text colour blended
    against the window background (SVG only takes RGB, so transparency is
    baked in that way) — it reads well on both light and dark themes."""
    base = QColor(_text_hex())
    bg = QApplication.palette().color(QPalette.Window)
    alpha = 170
    r = int((base.red() * alpha + bg.red() * (255 - alpha)) / 255)
    g = int((base.green() * alpha + bg.green() * (255 - alpha)) / 255)
    b = int((base.blue() * alpha + bg.blue() * (255 - alpha)) / 255)
    hex_color = f"#{r:02x}{g:02x}{b:02x}"
    colored = GRIP_SVG.replace('fill="currentColor"', f'fill="{hex_color}"')
    renderer = QSvgRenderer(QByteArray(colored.encode()))
    dpr = QGuiApplication.primaryScreen().devicePixelRatio() or 1.0
    pix = QPixmap(int(size * dpr), int(size * dpr))
    pix.setDevicePixelRatio(dpr)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    renderer.render(painter)
    painter.end()
    return pix


def _make_tool_row_widget(label_text, grip_pix):
    """Per-item widget for the reorder list: label on the left, drag-handle
    grip pinned to the right.

    The whole widget is mouse-transparent so every press/drag event falls
    through to the QListWidget — the grip is purely visual and dragging can
    start from anywhere on the row. The background stays transparent so the
    stylesheet-driven hover/selection highlight shows through."""
    w = QWidget()
    w.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    lay = QHBoxLayout(w)
    lay.setContentsMargins(10, 0, 10, 0)
    lay.setSpacing(6)
    text = QLabel(label_text)
    lay.addWidget(text)
    lay.addStretch()
    grip = QLabel()
    grip.setPixmap(grip_pix)
    grip.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    lay.addWidget(grip)
    return w


class _ReorderToolList(QListWidget):
    """Tool list that can be reordered by dragging, used in the General page.

    Drag-and-drop is driven entirely by hand so the source item can never be
    lost or duplicated by Qt's internal machinery:

    - startDrag() builds a plain QDrag carrying only the source item's key
      (private MIME type) and calls drag.exec() WITHOUT super().startDrag(),
      which bypasses QAbstractItemView::clearOrRemove() — the source item
      never leaves the model mid-drag.
    - dropEvent() computes the destination from the drop indicator, then
      does exactly one takeItem()+insertItem(). Same-slot drops are no-ops.
      The very same QListWidgetItem is reused.
    - setItemWidget is backed by setIndexWidget (persistent-index bound),
      which Qt destroys on take/insert, so attach_row_widgets() rebuilds the
      row widgets (label + right grip) after every move.

    During a drag the `dragging` dynamic property suppresses hover/selection
    backgrounds, leaving only the drop-indicator line as the positional cue.
    """
    order_changed = Signal()
    MIME = "application/x-caprise-tool-key"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setFocusPolicy(Qt.NoFocus)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setDropIndicatorShown(True)
        self._tool_labels = {}
        self._grip_pix = None

    # ---------- row widgets (label left, grip right) ----------
    def set_tool_labels(self, labels, grip_pix):
        """Store the key->label map and grip pixmap used to build row widgets."""
        self._tool_labels = dict(labels)
        self._grip_pix = grip_pix

    def attach_row_widgets(self):
        """(Re)build the per-row widget for every item.

        Must be re-run after a move: takeItem()/insertItem() trigger Qt's
        index-widget cleanup, which destroys the row widgets."""
        if self._grip_pix is None:
            return
        for i in range(self.count()):
            item = self.item(i)
            key = item.data(Qt.UserRole)
            label = self._tool_labels.get(key, key)
            self.setItemWidget(item, _make_tool_row_widget(label, self._grip_pix))

    # ---------- drag-state styling ----------
    def _set_dragging_style(self, dragging):
        """Toggle the `dragging` dynamic property to suppress hover/selection
        backgrounds while a drag is in progress."""
        self.setProperty("dragging", "true" if dragging else "")
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    # ---------- drag entry points (source side) ----------
    def mimeTypes(self):
        return [self.MIME]

    def mimeData(self, items):
        md = QMimeData()
        if items:
            key = items[0].data(Qt.UserRole) or ""
            md.setData(self.MIME, key.encode("utf-8"))
        return md

    def supportedDropActions(self):
        return Qt.MoveAction

    # ---------- drag entry points (target side) ----------
    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(self.MIME):
            event.acceptProposedAction()
            self._set_dragging_style(True)
        else:
            super().dragEnterEvent(event)

    def dragLeaveEvent(self, event):
        super().dragLeaveEvent(event)
        self.clearSelection()
        self.setCurrentItem(None)
        self._set_dragging_style(False)

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(self.MIME):
            # The default handler updates the drop-indicator line; our
            # mimeTypes()/supportedDropActions() overrides tell it our MIME
            # is acceptable for a MoveAction here.
            super().dragMoveEvent(event)
            event.acceptProposedAction()
            # Suppress the transient row highlight so only the line shows.
            self.clearSelection()
            self.setCurrentItem(None)
        else:
            super().dragMoveEvent(event)

    def dropMimeData(self, index, data, action):
        # Never used in our manual-drag flow; the model stays untouched.
        return False

    def dropEvent(self, event):
        """Manual reorder using the source key carried in our private MIME.

        The source item has never left the model, so it is always found. One
        takeItem()+insertItem() moves it; same-slot drops are accepted as
        no-ops. Row widgets are rebuilt afterwards."""
        md = event.mimeData()
        if not md.hasFormat(self.MIME):
            super().dropEvent(event)
            return

        raw = bytes(md.data(self.MIME)).decode("utf-8")
        if not raw:
            self._set_dragging_style(False)
            event.ignore()
            return

        src_row = -1
        for i in range(self.count()):
            if self.item(i).data(Qt.UserRole) == raw:
                src_row = i
                break
        if src_row < 0:
            self._set_dragging_style(False)
            event.ignore()
            return

        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        target = self.indexAt(pos)
        indicator = self.dropIndicatorPosition()
        if not target.isValid():
            dst_row = self.count()      # empty space below the last item
        elif indicator == QAbstractItemView.AboveItem:
            dst_row = target.row()
        else:                           # BelowItem / OnItem -> insert after
            dst_row = target.row() + 1

        # Compensate for the source removal when it sits above the target.
        if src_row < dst_row:
            dst_row -= 1
        n = self.count()
        dst_row = max(0, min(dst_row, n - 1))

        if dst_row == src_row:
            # Same effective slot -> no-op, never produce a duplicate.
            self.clearSelection()
            self.setCurrentItem(None)
            event.acceptProposedAction()
            self._set_dragging_style(False)
            return

        moved = self.takeItem(src_row)
        self.insertItem(dst_row, moved)
        self.attach_row_widgets()

        self.clearSelection()
        self.setCurrentItem(None)

        event.acceptProposedAction()
        self.order_changed.emit()
        self._set_dragging_style(False)

    # ---------- drag source: start ----------
    def startDrag(self, supportedActions):
        """Start a manual drag; deliberately NOT super().startDrag().

        Never calling the base class means QAbstractItemView's trailing
        clearOrRemove() (which deletes still-selected source rows) never
        runs. The item stays put until our dropEvent() moves it."""
        item = self.currentItem()
        if item is None:
            return
        key = item.data(Qt.UserRole)
        if not key:
            return

        mime = QMimeData()
        mime.setData(self.MIME, key.encode("utf-8"))

        drag = QDrag(self)
        drag.setMimeData(mime)
        # Snapshot the row as the drag preview.
        rect = self.visualItemRect(item)
        if rect.isValid() and rect.width() > 0 and rect.height() > 0:
            pm = self.viewport().grab(rect)
            drag.setPixmap(pm)
            drag.setHotSpot(QPoint(pm.width() // 2, pm.height() // 2))

        self._set_dragging_style(True)
        try:
            drag.exec(supportedActions, Qt.MoveAction)
        finally:
            # Runs whether dropped, cancelled, or aborted with ESC.
            self._set_dragging_style(False)


class _Sidebar(QListWidget):
    """Compact navigation column for the settings dialog."""

    def __init__(self, entries, parent=None):
        super().__init__(parent)
        self.setFixedWidth(132)
        self.setFocusPolicy(Qt.NoFocus)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSelectionMode(QListWidget.SingleSelection)
        for key, label in entries:
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, key)
            item.setSizeHint(QSize(0, 40))
            self.addItem(item)
        self.setStyleSheet(f"""
            QListWidget {{
                background: transparent;
                border: none;
                padding: 10px 6px;
                font-size: 13px;
                outline: none;
            }}
            QListWidget::item {{
                color: {self._fg()};
                border-radius: 8px;
                padding-left: 4px;
            }}
            QListWidget::item:hover {{ background: rgba(128,128,128,45); }}
            QListWidget::item:selected {{
                background: {_accent_hex()};
                color: #ffffff;
            }}
        """)
        if self.count() > 0:
            self.setCurrentRow(0)

    def _fg(self):
        c = QApplication.palette().color(QPalette.WindowText)
        return f"#{c.red():02x}{c.green():02x}{c.blue():02x}"


class SettingsDialog(QDialog):
    """Settings dialog split into a left navigation sidebar and a right pane."""

    def __init__(self, capsule=None, parent=None):
        super().__init__(parent)
        self._capsule = capsule
        self.setWindowTitle(I18n.tr("settings_title"))
        self.setFixedSize(520, 400)
        self._page_anim = None
        self.setWindowFlags(
            Qt.Dialog | Qt.WindowCloseButtonHint | Qt.WindowStaysOnTopHint
        )
        self.setup_ui()
        self.load_settings()
        self._connect_signals()
        self.center_on_screen()

    def _connect_signals(self):
        # Changes apply immediately as the user edits each control, rather than
        # waiting for the dialog to close. done() still re-persists as a safety
        # net. Load-order note: signals are wired after load_settings() so the
        # initial population does not trigger redundant saves.
        self.lang_combo.currentIndexChanged.connect(self._persist)
        self.autostart_check.toggled.connect(self._persist)
        self.translate_lang_combo.currentIndexChanged.connect(self._persist)
        self.tool_order_list.order_changed.connect(self._on_tool_order_changed)

    def _on_tool_order_changed(self, *_):
        """Drag reorder landed: persist the new order and refresh the capsule."""
        order = []
        for i in range(self.tool_order_list.count()):
            key = self.tool_order_list.item(i).data(Qt.UserRole)
            if key:
                order.append(key)
        Config().set("tool_order", order)
        if self._capsule is not None:
            self._capsule.reorder_tools(order)

    def setup_ui(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # --- left navigation ---
        self.sidebar = _Sidebar([
            ("general", I18n.tr("settings_general")),
            ("translate", I18n.tr("settings_translate")),
            ("system", I18n.tr("settings_system")),
            ("about", I18n.tr("settings_about")),
        ])
        outer.addWidget(self.sidebar)

        # --- right content + a menu divider ---
        divider = QFrame(self)
        divider.setFixedWidth(1)
        divider.setStyleSheet("background: rgba(120,120,120,80);")
        outer.addWidget(divider)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_general_page())
        self.stack.addWidget(self._build_translate_page())
        self.stack.addWidget(self._build_system_page())
        self.stack.addWidget(AboutPage())

        # Right pane = content stack only. Settings are persisted automatically
        # whenever the dialog closes (see done()), so no Save button is needed.
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addWidget(self.stack, 1)

        outer.addLayout(right, 1)

        self.sidebar.currentRowChanged.connect(self._switch_page)

    # ----- pages -----

    def _row(self, label_text, widget):
        row = QHBoxLayout()
        row.setSpacing(12)
        label = QLabel(label_text)
        label.setFixedWidth(90)
        row.addWidget(label)
        row.addWidget(widget, 1)
        row.addStretch()
        return row

    def _build_general_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(18)

        self.lang_combo = QComboBox()
        self.lang_combo.addItem("中文", "zh_CN")
        self.lang_combo.addItem("English", "en")
        self.lang_combo.setFixedWidth(180)
        layout.addLayout(self._row(I18n.tr("language"), self.lang_combo))

        hotkey_value = QLabel("Ctrl + `")
        layout.addLayout(self._row(I18n.tr("hotkey"), hotkey_value))

        # --- tool order: drag to reorder the capsule buttons ---
        order_title = QLabel(I18n.tr("tool_order"))
        order_title.setStyleSheet("font-size: 13px; font-weight: 600;")
        layout.addWidget(order_title)

        self.tool_order_list = _ReorderToolList()
        self.tool_order_list.setFixedHeight(132)
        grip_pix = _make_grip_pixmap(14)
        tool_labels = {
            "screenshot": I18n.tr("screenshot"),
            "annotation": I18n.tr("annotation"),
            "translate": I18n.tr("translate"),
            "clipboard": I18n.tr("clipboard"),
            "search": I18n.tr("search"),
        }
        self.tool_order_list.set_tool_labels(tool_labels, grip_pix)

        def _add_tool_item(key):
            item = QListWidgetItem()
            item.setData(Qt.UserRole, key)
            item.setSizeHint(QSize(0, 30))
            self.tool_order_list.addItem(item)

        order = Config().get(
            "tool_order",
            ["screenshot", "annotation", "translate", "clipboard", "search"])
        for key in order:
            if key not in tool_labels:
                continue
            _add_tool_item(key)
        # Append any tool missing from a stale saved order so the list always
        # shows every tool.
        for key in tool_labels:
            found = any(
                self.tool_order_list.item(i).data(Qt.UserRole) == key
                for i in range(self.tool_order_list.count())
            )
            if not found:
                _add_tool_item(key)
        # Mount the per-row widgets (label left, grip right).
        self.tool_order_list.attach_row_widgets()
        self.tool_order_list.setStyleSheet(f"""
            QListWidget {{
                background: transparent;
                border: 1px solid rgba(128,128,128,60);
                border-radius: 8px;
                padding: 4px;
                font-size: 13px;
                outline: none;
            }}
            QListWidget::item {{
                color: {_text_hex()};
                border-radius: 6px;
                padding: 2px 8px;
            }}
            QListWidget::item:hover {{ background: rgba(128,128,128,45); }}
            QListWidget::item:selected {{
                background: {_accent_hex()};
                color: #ffffff;
            }}
            /* While a drag is in progress, gate the hover/selected paints so
               the only positional cue is the drop-indicator line. */
            QListWidget[dragging="true"]::item:hover {{ background: transparent; }}
            QListWidget[dragging="true"]::item:selected {{
                background: transparent;
                color: {_text_hex()};
            }}
        """)
        layout.addWidget(self.tool_order_list)

        order_hint = QLabel(I18n.tr("tool_order_hint"))
        order_hint.setStyleSheet("font-size: 11px; color: #868e96;")
        order_hint.setWordWrap(True)
        layout.addWidget(order_hint)

        layout.addStretch()
        return page

    def _build_translate_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(18)

        self.translate_lang_combo = QComboBox()
        self.translate_lang_combo.addItem("简体中文", "zh-CN")
        self.translate_lang_combo.addItem("繁體中文", "zh-TW")
        self.translate_lang_combo.addItem("English", "en")
        self.translate_lang_combo.addItem("日本語", "ja")
        self.translate_lang_combo.addItem("한국어", "ko")
        self.translate_lang_combo.setFixedWidth(180)
        layout.addLayout(
            self._row(I18n.tr("translate_target_lang"), self.translate_lang_combo))

        hint = QLabel(I18n.tr("translate_source_hint"))
        hint.setStyleSheet("font-size: 11px; color: #868e96;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addStretch()
        return page

    def _build_system_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(18)

        row = QHBoxLayout()
        row.setSpacing(12)
        label = QLabel(I18n.tr("autostart"))
        label.setFixedWidth(90)
        row.addWidget(label)
        self.autostart_check = QCheckBox()
        row.addWidget(self.autostart_check)
        row.addStretch()
        layout.addLayout(row)

        layout.addStretch()
        return page

    # ----- load / save -----

    def load_settings(self):
        config = Config()
        lang = config.get("language", "zh_CN")
        index = self.lang_combo.findData(lang)
        if index >= 0:
            self.lang_combo.setCurrentIndex(index)

        autostart = config.get("autostart", False)
        self.autostart_check.setChecked(autostart)

        translate_lang = config.get("translate_target_lang", "zh-CN")
        index = self.translate_lang_combo.findData(translate_lang)
        if index >= 0:
            self.translate_lang_combo.setCurrentIndex(index)

    def _persist(self, *_):
        lang = self.lang_combo.currentData()
        I18n.set_language(lang)

        autostart = self.autostart_check.isChecked()
        Config().set("autostart", autostart)
        apply_autostart(autostart)

        Config().set("translate_target_lang", self.translate_lang_combo.currentData())

    def done(self, result):
        # Settings already apply instantly via _connect_signals(); ending here
        # re-persists once more as a safety net so nothing is ever lost.
        self._persist()
        super().done(result)

    # ----- page transition -----

    def _switch_page(self, index):
        """Switch the right-pane page with a short fade-in transition."""
        self.stack.setCurrentIndex(index)
        page = self.stack.currentWidget()
        if page is None:
            return
        # Stop any in-flight transition.
        if self._page_anim is not None:
            try:
                self._page_anim.stop()
                self._page_anim.deleteLater()
            except RuntimeError:
                pass
            self._page_anim = None
        effect = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, QByteArray(b"opacity"))
        anim.setDuration(200)
        anim.setEasingCurve(QEasingCurve.Linear)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.finished.connect(self._on_page_fade_finished)
        self._page_anim = anim
        anim.start()

    def _on_page_fade_finished(self):
        """Clear the temporary opacity effect once the fade completes."""
        if self._page_anim is not None:
            try:
                self._page_anim.deleteLater()
            except RuntimeError:
                pass
            self._page_anim = None
        page = self.stack.currentWidget()
        if page is not None and page.graphicsEffect() is not None:
            page.setGraphicsEffect(None)

    def center_on_screen(self):
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(
            screen.center().x() - self.width() // 2,
            screen.center().y() - self.height() // 2
        )