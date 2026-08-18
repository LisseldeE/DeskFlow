"""Clipboard feature coordinator.

Ties together discovery, network (host/client), the system-clipboard
monitor, the SQLite history, and the floating panel. Owns the lifecycle:
enable/disable, room config, host-vs-join auto-selection, and state
persistence (enabled / expanded / room / panel position) across restarts.

Role selection (per PRD): after a room code is entered, scan the LAN via UDP.
If a host for that room responds -> join as a TCP client. Otherwise -> become
the host (start a TCP server + UDP discovery responder).
"""
import socket
import uuid

from PySide6.QtCore import QObject, Signal, QPoint

from modules.config import Config
from modules.i18n import I18n
from modules.clipboard_history import ClipboardHistory
from modules.clipboard_monitor import ClipboardMonitor
from modules.clipboard_network import (
    RoomDiscovery, RoomResponder, ClipboardHost, ClipboardClient
)
from modules.clipboard_panel import ClipboardPanel
from modules.room_config import RoomConfigDialog


class ClipboardManager(QObject):
    """Coordinates the LAN clipboard feature end-to-end."""

    def __init__(self, capsule):
        super().__init__()
        self.capsule = capsule

        self._history = ClipboardHistory()
        self._monitor = ClipboardMonitor(self)
        self._panel = ClipboardPanel()

        # Network components (created on enable)
        self._discovery = None
        self._responder = None
        self._host = None
        self._client = None
        self._role = None  # "host" | "client" | None

        self._peer_id = uuid.uuid4().hex[:12]
        self._peer_name = (socket.gethostname() or "DeskFlow")[:24]
        self._room_code = ""
        self._enabled = False
        # expanded = panel card visible. Independent of _enabled so the user can
        # have the feature running (network on) with the card collapsed.
        self._expanded = False
        self._connecting = False
        self._peer_count = 0
        self._status = ""
        # Last text emitted by the local monitor — collapses burst writes (some
        # apps set the clipboard text/html/inline several times in a row).
        self._last_local_text = None

        # --- Wire panel ---
        self._panel.copy_requested.connect(self._on_panel_copy)
        self._panel.delete_requested.connect(self._on_panel_delete)
        self._panel.clear_requested.connect(self._on_panel_clear)
        self._panel.position_changed.connect(self._on_panel_moved)
        self._panel.collapse_requested.connect(self.hide_card)

        # --- Wire monitor ---
        self._monitor.copied.connect(self._on_local_copy)

        # --- Family hide coordination ---
        # Both capsule and panel emit this when focus leaves the family; the
        # manager hides whichever is still visible.
        self.capsule.hide_family_requested.connect(self._hide_family)
        self._panel.hide_family_requested.connect(self._hide_family)

        self._refresh_panel()
        self._restore_from_config()

    # ------------------------------------------------------------------ state

    def is_enabled(self) -> bool:
        return self._enabled

    def is_expanded(self) -> bool:
        return self._expanded

    def is_panel_visible(self) -> bool:
        """True if the panel card is currently on screen (mid-animation or
        fully shown). Used by DeskFlow.toggle_capsule to decide whether the
        family as a whole is visible — capsule OR panel counts."""
        return self._panel.isVisible()

    def _restore_from_config(self):
        cfg = Config()
        room = cfg.get("clipboard_room", "") or ""
        enabled = bool(cfg.get("clipboard_enabled", False))
        expanded = bool(cfg.get("clipboard_expanded", False))
        pos = cfg.get("clipboard_pos", None)
        if isinstance(pos, list) and len(pos) == 2:
            self._panel.set_initial_pos(QPoint(int(pos[0]), int(pos[1])))

        if enabled and self._is_valid_room(room):
            self._enable(room)  # network + monitor on, no UI
            # Persist the user's panel preference but DON'T auto-pop the card
            # on startup. Restarting with a floating panel and no capsule (the
            # previous behavior) felt broken — and the panel couldn't detect
            # focus loss without the capsule's poll running. Instead, the
            # panel surfaces together with the capsule when the user presses
            # Ctrl+`, gated by _expanded.
            self._expanded = bool(expanded)
        self._update_button_tooltip()
        self._refresh_status_text()

    def _save_state(self):
        cfg = Config()
        cfg.set("clipboard_enabled", self._enabled)
        cfg.set("clipboard_expanded", self._expanded)
        if self._room_code:
            cfg.set("clipboard_room", self._room_code)

    # ------------------------------------------------------------------ button

    def on_button_left(self):
        """Three-state cycle (per PRD):
            A. disabled           -> enable + expand panel   (needs a room)
            B. enabled + expanded -> collapse panel (stay enabled)
            C. enabled + collapsed-> disable
        Left-click NEVER opens the room config when a room is already
        configured — config is right-click only. First-time setup (no room)
        still opens the config since there's nothing to enable."""
        if not self._enabled:
            # A -> B
            if not self._is_valid_room(self._room_code):
                # No room yet: must configure once before the feature can run.
                self._open_room_config(initial=self._room_code)
                return
            self._enable(self._room_code)
            self.show_card()
            return
        # enabled: toggle on the logical _expanded flag (NOT panel.isVisible(),
        # which stays True during the 300ms collapse animation and would send
        # a rapid second click back into the collapse branch instead of disable).
        if self._expanded:
            # B -> C: collapse the card, keep the network running
            self.hide_card()
        else:
            # C -> A: turn the feature off
            self.disable()

    def on_button_right(self):
        """Right-click: change (or set) the room code. The only path that
        opens the room-config dialog."""
        self._open_room_config(initial=self._room_code)

    def _open_room_config(self, initial=""):
        dialog = RoomConfigDialog(initial_code=initial)
        if dialog.exec() == RoomConfigDialog.Accepted:
            code = dialog.get_room_code()
            if self._is_valid_room(code):
                self._enable(code)
                # If the capsule is visible, surface the card as feedback.
                if self.capsule.isVisible():
                    self.show_card()

    # ------------------------------------------------------------------ enable

    def _enable(self, room_code):
        """Turn the feature on (network + monitor + active button) without
        touching the panel. Safe to call repeatedly; re-points to a new room."""
        self._stop_network()
        self._room_code = room_code
        Config().set("clipboard_room", room_code)
        was_enabled = self._enabled
        self._enabled = True
        self.capsule.set_clipboard_active(True)
        if not was_enabled:
            self._monitor.enable()
        self._save_state()
        self._start_connection(room_code)
        self._update_button_tooltip()

    def disable(self):
        if not self._enabled:
            return
        self._enabled = False
        self._expanded = False
        self._stop_network()
        self._monitor.disable()
        self.capsule.set_clipboard_active(False)
        if self._panel.isVisible():
            self._panel.hide_panel()
        self._save_state()
        self._set_status(I18n.tr("clipboard_status_disconnected"))
        self._update_button_tooltip()

    # ------------------------------------------------------------------ network

    def _start_connection(self, room_code):
        self._connecting = True
        self._set_status(I18n.tr("clipboard_status_scanning"))
        self._discovery = RoomDiscovery(self)
        self._discovery.discovery_finished.connect(self._on_discovery_finished)
        self._discovery.error.connect(self._on_discovery_error)
        self._discovery.discover(room_code)

    def _on_discovery_finished(self, rooms):
        self._connecting = False
        if not self._enabled:
            return
        match = None
        for ip, rc, port in rooms:
            if rc == self._room_code:
                match = (ip, port)
                break
        if match:
            self._become_client(match[0], match[1])
        else:
            self._become_host()

    def _on_discovery_error(self, msg):
        self._set_status(I18n.tr("clipboard_status_failed"))

    def _become_host(self):
        self._host = ClipboardHost(self)
        self._host.clipboard_received.connect(self._on_remote_clipboard)
        self._host.peer_count_changed.connect(self._on_peer_count)
        self._host.error.connect(self._on_network_error)
        if not self._host.start():
            self._set_status(I18n.tr("clipboard_status_failed"))
            return
        self._responder = RoomResponder(self)
        self._responder.start(self._room_code, self._host.port)
        self._role = "host"
        self._refresh_status_text()

    def _become_client(self, host_ip, port):
        self._client = ClipboardClient(self)
        # Connect to bound methods (not lambdas) so Qt auto-queues the
        # cross-thread call onto the main thread — network threads must not
        # touch QClipboard / widgets directly.
        self._client.clipboard_received.connect(self._on_client_clipboard)
        self._client.peer_count_changed.connect(self._on_peer_count)
        self._client.connected.connect(self._on_client_connected)
        self._client.disconnected.connect(self._on_client_disconnected)
        self._client.error.connect(self._on_network_error)
        self._client.connect_to_host(
            host_ip, port, self._room_code, self._peer_id, self._peer_name
        )
        self._role = "client"
        self._set_status(I18n.tr("clipboard_status_connecting"))

    # ----- cross-thread slots (run on main thread via Qt auto-queue) -----

    def _on_client_clipboard(self, text):
        self._on_remote_clipboard(text, "")

    def _on_client_connected(self):
        self._set_status(I18n.tr("clipboard_status_joined"))

    def _on_client_disconnected(self):
        if not self._enabled:
            return
        self._set_status(I18n.tr("clipboard_status_disconnected"))

    def _on_network_error(self, msg):
        self._set_status(I18n.tr("clipboard_status_failed"))

    def _on_peer_count(self, count):
        self._peer_count = count
        self._refresh_status_text()

    def _stop_network(self):
        if self._discovery:
            self._discovery.stop()
            self._discovery = None
        if self._responder:
            self._responder.stop()
            self._responder = None
        if self._host:
            self._host.stop()
            self._host = None
        if self._client:
            self._client.disconnect()
            self._client = None
        self._role = None
        self._peer_count = 0

    # ------------------------------------------------------------------ clipboard flows

    def _on_local_copy(self, text):
        """User copied locally -> store + broadcast to peers.
        Dedupes burst writes (some apps fire dataChanged several times for the
        same text: text/html/inline variants) so history + network aren't
        hammered with duplicates."""
        if not text or text == self._last_local_text:
            return
        self._last_local_text = text
        self._history.add(text, source="local", origin_peer=self._peer_id)
        self._refresh_panel()
        self._broadcast(text)

    def _on_remote_clipboard(self, text, origin_peer=""):
        """Received text from a peer -> write to local clipboard (echo-guarded)
        and add to history. Echo guard prevents re-broadcasting it."""
        self._monitor.set_clipboard(text)
        self._history.add(text, source="remote", origin_peer=origin_peer)
        self._refresh_panel()

    def _on_panel_copy(self, content):
        """User clicked a history item -> make it the active clipboard locally
        and on every peer, bumping it to the top of history. The panel does
        NOT steal focus (WS_EX_NOACTIVATE), so the user's caret stays in their
        input field and Ctrl+V pastes at the cursor. The panel stays open so
        the user can copy several items; it collapses when focus moves to a
        non-anchor window (detected by the capsule's _poll_check)."""
        self._monitor.set_clipboard(content)
        self._history.add(content, source="local", origin_peer=self._peer_id)
        self._refresh_panel()
        self._broadcast(content)

    def _on_panel_delete(self, item_id):
        self._history.delete(item_id)
        self._refresh_panel()

    def _on_panel_clear(self):
        self._history.clear()
        self._refresh_panel()

    def _on_panel_moved(self, x, y):
        Config().set("clipboard_pos", [int(x), int(y)])

    def _broadcast(self, text):
        if not self._enabled or not text:
            return
        if self._role == "host" and self._host:
            self._host.broadcast_clipboard(text, origin_peer_id=self._peer_id)
        elif self._role == "client" and self._client and self._client.is_connected:
            self._client.send_clipboard(text)

    # ------------------------------------------------------------------ panel / status

    def show_card(self):
        if not self._enabled:
            return
        self._refresh_panel()
        self._panel.show_panel()
        self._expanded = True
        self._save_state()

    def hide_card(self):
        """Collapse just the panel (B -> C). Feature stays enabled."""
        if self._panel.isVisible():
            self._panel.hide_panel()
        self._expanded = False
        self._save_state()

    def hide_family(self):
        """Hide the whole family (capsule + panel). Animation-aware: doesn't
        gate on _can_hide / _animating — the show/hide methods themselves
        handle reversal cleanly. Does NOT change _expanded — that is a user
        preference and is only flipped by an explicit collapse (hide_card)
        or expand (show_card). Keeping it stable means the next Ctrl+`
        surfaces the family exactly as the user last had it."""
        if self.capsule.isVisible():
            self.capsule.hide_capsule()
        if self._panel.isVisible():
            self._panel.hide_panel()

    def _hide_family(self):
        # Called when focus leaves the family (capsule or panel detected it).
        self.hide_family()

    def _refresh_panel(self):
        self._panel.set_items(self._history.get_all())

    def _set_status(self, text):
        self._status = text
        self._panel.set_status(text)

    def _refresh_status_text(self):
        if not self._enabled:
            self._set_status(I18n.tr("clipboard_status_disconnected"))
            return
        if self._connecting:
            self._set_status(I18n.tr("clipboard_status_scanning"))
            return
        total = self._peer_count + 1
        if self._role == "host":
            self._set_status(f"{I18n.tr('clipboard_status_hosting')} · {total}")
        elif self._role == "client":
            self._set_status(f"{I18n.tr('clipboard_status_joined')} · {total}")
        else:
            self._set_status(I18n.tr("clipboard_status_disconnected"))

    def _update_button_tooltip(self):
        if self._enabled and self._room_code:
            tip = f"{I18n.tr('clipboard')} · {self._room_code}"
        else:
            tip = I18n.tr("clipboard_no_room")
        self.capsule.btn_clipboard.setToolTip(tip)

    @staticmethod
    def _is_valid_room(code):
        return isinstance(code, str) and len(code) == 6 and code.isdigit()

    # ------------------------------------------------------------------ shutdown

    def shutdown(self):
        """Persist state and tear down. Does NOT reset enabled/expanded
        (so they survive restart) — per the hard constraint."""
        self._save_state()
        self._stop_network()
        if self._panel.isVisible():
            self._panel.hide_panel()
        self._monitor.disable()
        self._history.close()
