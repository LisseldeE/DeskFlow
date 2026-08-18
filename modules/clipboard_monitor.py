"""System clipboard monitor with echo-guard against sync loops.

Local copy  -> emits `copied(text)` so the manager can sync to peers.
Remote write -> manager calls `set_clipboard(text)` which sets the system
                clipboard WITHOUT re-emitting (so we don't echo our own
                just-received text back to the network).

QClipboard.dataChanged is emitted synchronously from setText() on Windows
(Qt auto-connection same-thread = direct call), so a simple boolean guard
cleared inside the handler is sufficient. We emit IMMEDIATELY on dataChanged
(no debounce timer) so the panel updates the instant the user copies; burst
writes (text/html/inline variants of the same copy) are deduped by the
manager via a last-text check.
"""
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QObject, Signal


class ClipboardMonitor(QObject):
    """Monitors the system clipboard; emits only on genuine local copies."""

    copied = Signal(str)  # text the user copied locally (echo-guarded)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._clip = QApplication.clipboard()
        self._suppress = False   # True while we are writing the clipboard ourselves
        self._enabled = False
        # Last text we emitted (or set remotely). Burst writes (some apps fire
        # dataChanged several times for text/html/inline variants of one copy)
        # are collapsed by comparing against this — instant, no timer latency.
        self._last_text = None

    def enable(self):
        if self._enabled:
            return
        self._enabled = True
        self._clip.dataChanged.connect(self._on_changed)

    def disable(self):
        if not self._enabled:
            return
        self._enabled = False
        try:
            self._clip.dataChanged.disconnect(self._on_changed)
        except (TypeError, RuntimeError):
            pass

    def set_clipboard(self, text):
        """Write text to the system clipboard WITHOUT re-emitting it as local.
        Also records it as the last text so a subsequent identical local copy
        (echo) doesn't re-emit / re-broadcast."""
        if not isinstance(text, str) or text == "":
            return
        self._suppress = True
        self._last_text = text
        self._clip.setText(text)
        # If setText fired dataChanged synchronously, the handler already
        # cleared _suppress. If the content was identical (no signal fired),
        # clear it manually so a subsequent real copy isn't swallowed.
        if self._suppress:
            self._suppress = False

    def _on_changed(self):
        if self._suppress:
            self._suppress = False
            return
        if not self._enabled:
            return
        text = self._clip.text()
        if not text or text == self._last_text:
            return
        self._last_text = text
        self.copied.emit(text)
