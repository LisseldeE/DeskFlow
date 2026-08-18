"""LAN clipboard networking: framing protocol + UDP discovery + TCP relay.

Topology (matches LANSyncBox model): one peer acts as HOST — it runs a TCP
server and a UDP discovery responder. Other peers JOIN — they discover the
host via UDP broadcast and connect to it as TCP clients. The host relays
clipboard updates between all clients (star topology), so a joiner only
talks to the host, which forwards to every other joiner.

All socket I/O runs in daemon threads. Results are delivered to the Qt main
thread via Qt signals (auto-queued across threads).

Message framing: 4-byte big-endian length prefix + UTF-8 JSON payload.
Message types:
  hello       joiner -> host   {type, room_code, peer_id, peer_name}
  welcome     host  -> joiner  {type, peer_id, peer_count}
  clipboard   either -> host / host -> all   {type, text, origin_peer_id}
  peer_update host  -> all     {type, peer_count}
  bye         joiner -> host   {type}
"""
import json
import socket
import struct
import threading

from PySide6.QtCore import QObject, Signal


# --- Ports (DeskFlow-specific; separate from LANSyncBox to allow co-existence)
DISCOVERY_PORT_START = 9548
DISCOVERY_PORT_END = 9557            # inclusive (10 ports, allows multi-instance)
TCP_PORT_DEFAULT = 9547
TCP_PORT_RANGE = 11                  # try 9547..9557
DISCOVERY_TIMEOUT = 3.0              # seconds
MAX_MESSAGE_SIZE = 10 * 1024 * 1024  # 10 MB sanity cap
RECV_CHUNK = 65536


# ---------------------------------------------------------------------------
# Framing helpers
# ---------------------------------------------------------------------------

def pack_message(msg: dict) -> bytes:
    payload = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    return struct.pack(">I", len(payload)) + payload


class MessageReader:
    """Accumulates bytes from a TCP stream and yields complete JSON messages."""

    def __init__(self):
        self._buf = bytearray()

    def feed(self, data: bytes):
        self._buf.extend(data)
        out = []
        while len(self._buf) >= 4:
            (length,) = struct.unpack(">I", self._buf[:4])
            if length > MAX_MESSAGE_SIZE:
                raise ValueError("message too large")
            if len(self._buf) < 4 + length:
                break
            payload = bytes(self._buf[4:4 + length])
            del self._buf[:4 + length]
            out.append(json.loads(payload.decode("utf-8")))
        return out


def _get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _safe_emit(signal, *args):
    """Emit a Qt signal, swallowing RuntimeError if the QObject was deleted."""
    try:
        signal.emit(*args)
    except RuntimeError:
        pass


# ---------------------------------------------------------------------------
# Host-side per-client connection
# ---------------------------------------------------------------------------

class _ClientConn:
    """A connected joiner on the host side."""

    def __init__(self, sock, addr):
        self.sock = sock
        self.addr = addr
        self.peer_id = None
        self.peer_name = ""
        self.send_lock = threading.Lock()
        self.reader = MessageReader()
        self.alive = True

    def send(self, msg) -> bool:
        with self.send_lock:
            if not self.alive:
                return False
            try:
                self.sock.sendall(pack_message(msg))
                return True
            except Exception:
                self.alive = False
                return False


# ---------------------------------------------------------------------------
# UDP discovery (joiner side) + responder (host side)
# ---------------------------------------------------------------------------

class RoomDiscovery(QObject):
    """Joiner-side UDP discovery. Broadcasts a request and collects responses."""

    room_found = Signal(str, str, int)   # ip, room_code, tcp_port
    discovery_finished = Signal(list)    # [(ip, room_code, tcp_port), ...]
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sock = None
        self._running = False
        self._found = {}
        self._lock = threading.Lock()

    def discover(self, room_code: str, timeout: float = DISCOVERY_TIMEOUT) -> bool:
        try:
            self._found.clear()
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(("0.0.0.0", 0))
            self._sock.settimeout(0.5)
            self._running = True

            threading.Thread(target=self._receive_loop, daemon=True).start()

            msg = json.dumps(
                {"type": "discovery_request", "room_code": room_code}
            ).encode("utf-8")
            local_ip = _get_local_ip()
            for port in range(DISCOVERY_PORT_START, DISCOVERY_PORT_END + 1):
                try:
                    self._sock.sendto(msg, ("<broadcast>", port))
                except OSError:
                    pass
                try:
                    self._sock.sendto(msg, ("127.0.0.1", port))
                except OSError:
                    pass
                if local_ip != "127.0.0.1":
                    try:
                        self._sock.sendto(msg, (local_ip, port))
                    except OSError:
                        pass

            timer = threading.Timer(timeout, self._finish)
            timer.daemon = True
            timer.start()
            return True
        except Exception as e:
            _safe_emit(self.error, f"discovery start failed: {e}")
            return False

    def _receive_loop(self):
        while self._running:
            try:
                data, addr = self._sock.recvfrom(2048)
                resp = json.loads(data.decode("utf-8"))
                if resp.get("type") == "discovery_response":
                    ip = addr[0]
                    rc = resp.get("room_code", "")
                    port = resp.get("port", TCP_PORT_DEFAULT)
                    with self._lock:
                        self._found[ip] = (ip, rc, port)
                    _safe_emit(self.room_found, ip, rc, port)
            except socket.timeout:
                continue
            except OSError:
                break
            except Exception:
                continue

    def _finish(self):
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        with self._lock:
            rooms = list(self._found.values())
        _safe_emit(self.discovery_finished, rooms)

    def stop(self):
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None


class RoomResponder(QObject):
    """Host-side UDP responder. Replies to discovery requests matching room."""

    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sock = None
        self._running = False
        self._room_code = ""
        self._tcp_port = TCP_PORT_DEFAULT
        self._discovery_port = None

    @property
    def discovery_port(self):
        return self._discovery_port

    def start(self, room_code: str, tcp_port: int) -> bool:
        self._room_code = room_code
        self._tcp_port = tcp_port
        for port in range(DISCOVERY_PORT_START, DISCOVERY_PORT_END + 1):
            try:
                self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self._sock.bind(("0.0.0.0", port))
                self._sock.settimeout(1.0)
                self._running = True
                self._discovery_port = port
                threading.Thread(target=self._loop, daemon=True).start()
                return True
            except OSError:
                if self._sock:
                    try:
                        self._sock.close()
                    except Exception:
                        pass
                self._sock = None
                continue
            except Exception as e:
                _safe_emit(self.error, f"responder start failed: {e}")
                return False
        _safe_emit(self.error, "all discovery ports occupied")
        return False

    def _loop(self):
        while self._running:
            try:
                data, addr = self._sock.recvfrom(2048)
                req = json.loads(data.decode("utf-8"))
                if req.get("type") != "discovery_request":
                    continue
                target = req.get("room_code", "")
                # Empty room_code = scan-all request; respond anyway.
                if target and target != self._room_code:
                    continue
                resp = json.dumps({
                    "type": "discovery_response",
                    "room_code": self._room_code,
                    "port": self._tcp_port,
                }).encode("utf-8")
                self._sock.sendto(resp, addr)
            except socket.timeout:
                continue
            except OSError:
                break
            except Exception:
                continue

    def stop(self):
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
        self._sock = None


# ---------------------------------------------------------------------------
# TCP host (server + relay)
# ---------------------------------------------------------------------------

class ClipboardHost(QObject):
    """Host: accepts TCP joiners, relays clipboard updates between them,
    and emits received updates to the local manager (host is also a peer)."""

    clipboard_received = Signal(str, str)   # text, origin_peer_id
    peer_count_changed = Signal(int)
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._listen_sock = None
        self._running = False
        self._clients = {}                   # peer_id -> _ClientConn
        self._clients_lock = threading.Lock()
        self._tcp_port = None

    @property
    def port(self):
        return self._tcp_port

    def start(self, preferred_port: int = TCP_PORT_DEFAULT) -> bool:
        for port in range(preferred_port, preferred_port + TCP_PORT_RANGE):
            try:
                self._listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self._listen_sock.bind(("0.0.0.0", port))
                self._listen_sock.listen(16)
                self._tcp_port = port
                self._running = True
                threading.Thread(target=self._accept_loop, daemon=True).start()
                return True
            except OSError:
                if self._listen_sock:
                    try:
                        self._listen_sock.close()
                    except Exception:
                        pass
                self._listen_sock = None
                continue
        _safe_emit(self.error, "no free TCP port")
        return False

    def _accept_loop(self):
        while self._running:
            try:
                sock, addr = self._listen_sock.accept()
            except OSError:
                break
            conn = _ClientConn(sock, addr)
            threading.Thread(
                target=self._handle_client, args=(conn,), daemon=True
            ).start()

    def _handle_client(self, conn):
        try:
            conn.sock.settimeout(None)
            while self._running and conn.alive:
                try:
                    data = conn.sock.recv(RECV_CHUNK)
                except OSError:
                    break
                if not data:
                    break
                try:
                    msgs = conn.reader.feed(data)
                except Exception:
                    break  # malformed frame — drop connection
                for msg in msgs:
                    self._on_message(conn, msg)
        except Exception:
            pass
        finally:
            self._remove_client(conn)

    def _on_message(self, conn, msg):
        t = msg.get("type")
        if t == "hello":
            conn.peer_id = msg.get("peer_id") or f"peer-{id(conn)}"
            conn.peer_name = msg.get("peer_name", "peer")
            with self._clients_lock:
                old = self._clients.get(conn.peer_id)
                self._clients[conn.peer_id] = conn
            if old is not None and old is not conn:
                old.alive = False
                try:
                    old.sock.close()
                except Exception:
                    pass
            conn.send({"type": "welcome", "peer_id": conn.peer_id})
            self._broadcast_peer_count()
        elif t == "clipboard":
            text = msg.get("text", "")
            origin = msg.get("origin_peer_id", conn.peer_id)
            # Relay to every other client (star relay). Originator excluded
            # so it never receives its own update back (no echo loop).
            self._relay(msg, except_peer=conn.peer_id)
            _safe_emit(self.clipboard_received, text, origin)
        elif t == "bye":
            self._remove_client(conn)

    def _relay(self, msg, except_peer):
        with self._clients_lock:
            targets = [c for pid, c in self._clients.items() if pid != except_peer]
        for c in targets:
            c.send(msg)

    def _broadcast_peer_count(self):
        with self._clients_lock:
            count = len(self._clients)
        self._relay({"type": "peer_update", "peer_count": count}, except_peer=None)
        _safe_emit(self.peer_count_changed, count)

    def _remove_client(self, conn):
        removed = False
        with self._clients_lock:
            if conn.peer_id and self._clients.get(conn.peer_id) is conn:
                self._clients.pop(conn.peer_id, None)
                removed = True
        conn.alive = False
        try:
            conn.sock.close()
        except Exception:
            pass
        if removed:
            self._broadcast_peer_count()

    def broadcast_clipboard(self, text: str, origin_peer_id: str = "host"):
        """Send a local clipboard update to every connected joiner."""
        msg = {"type": "clipboard", "text": text, "origin_peer_id": origin_peer_id}
        self._relay(msg, except_peer=None)

    def peer_count(self) -> int:
        with self._clients_lock:
            return len(self._clients)

    def stop(self):
        self._running = False
        if self._listen_sock:
            try:
                self._listen_sock.close()
            except Exception:
                pass
            self._listen_sock = None
        with self._clients_lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for c in clients:
            c.alive = False
            try:
                c.sock.close()
            except Exception:
                pass
        _safe_emit(self.peer_count_changed, 0)


# ---------------------------------------------------------------------------
# TCP client (joiner)
# ---------------------------------------------------------------------------

class ClipboardClient(QObject):
    """Joiner: connects to the host, sends local updates, receives relays."""

    clipboard_received = Signal(str)
    peer_count_changed = Signal(int)
    connected = Signal()
    disconnected = Signal()
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sock = None
        self._reader = MessageReader()
        self._running = False
        self._peer_id = ""
        self._send_lock = threading.Lock()
        self._host = None
        self._port = None

    @property
    def is_connected(self):
        return self._running and self._sock is not None

    def connect_to_host(self, host: str, port: int, room_code: str,
                        peer_id: str, peer_name: str):
        self._peer_id = peer_id
        self._host = host
        self._port = port
        self._running = True
        threading.Thread(
            target=self._connect_loop,
            args=(host, port, room_code, peer_id, peer_name),
            daemon=True,
        ).start()

    def _connect_loop(self, host, port, room_code, peer_id, peer_name):
        try:
            self._sock = socket.create_connection((host, port), timeout=5)
            self._sock.settimeout(None)
            self._send({"type": "hello", "room_code": room_code,
                        "peer_id": peer_id, "peer_name": peer_name})
            # `connected` is emitted on welcome receipt (see _on_message),
            # so the host has registered us by the time it fires.
            while self._running:
                try:
                    data = self._sock.recv(RECV_CHUNK)
                except OSError:
                    break
                if not data:
                    break
                try:
                    msgs = self._reader.feed(data)
                except Exception:
                    break
                for msg in msgs:
                    self._on_message(msg)
        except Exception as e:
            if self._running:
                _safe_emit(self.error, f"connect failed: {e}")
        finally:
            self._running = False
            if self._sock:
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None
            _safe_emit(self.disconnected)

    def _on_message(self, msg):
        t = msg.get("type")
        if t == "clipboard":
            _safe_emit(self.clipboard_received, msg.get("text", ""))
        elif t == "welcome":
            pid = msg.get("peer_id")
            if pid:
                self._peer_id = pid
            # Emitted here (not at hello-send) so the host has confirmed
            # registration before listeners act on "connected".
            _safe_emit(self.connected)
        elif t == "peer_update":
            _safe_emit(self.peer_count_changed, msg.get("peer_count", 0))

    def send_clipboard(self, text: str):
        self._send({"type": "clipboard", "text": text,
                    "origin_peer_id": self._peer_id})

    def _send(self, msg):
        with self._send_lock:
            if self._sock is None:
                return
            try:
                self._sock.sendall(pack_message(msg))
            except Exception:
                pass

    def disconnect(self):
        self._running = False
        if self._sock:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
