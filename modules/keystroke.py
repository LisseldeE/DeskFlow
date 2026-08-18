"""Send keystrokes to the foreground window via Windows SendInput.

Used by the clipboard panel's copy-on-click: since the panel never steals
focus (WS_EX_NOACTIVATE), the foreground stays on the user's original input
window, so a synthesized Ctrl+V pastes at their caret.

Why SendInput (not keybd_event / PostMessage):
  - SendInput is the modern, reliable API; keybd_event is deprecated.
  - PostMessage(WM_PASTE) only works for EDIT/RICHEDIT controls and silently
    fails in modern apps (browsers, Electron, Office ribbon, …).
  - SendInput drives the real input stream — works everywhere a physical
    Ctrl+V would.

Layout of INPUT/KEYBDINPUT matches the Windows SDK exactly; ctypes unions
must mirror the C union size so the array stride is correct (4 events * 40
bytes on x64).
"""
import ctypes
from ctypes import wintypes


INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_V = 0x56


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", _MOUSEINPUT),
        ("ki", _KEYBDINPUT),
        ("hi", _HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _anonymous_ = ("_input",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("_input", _INPUT_UNION),
    ]


_user32 = ctypes.windll.user32
_user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
_user32.SendInput.restype = wintypes.UINT


def send_ctrl_v():
    """Synthesize a Ctrl+V keystroke to the current foreground window.

    The foreground window is whatever the user was typing in BEFORE they
    clicked our panel — the panel is WS_EX_NOACTIVATE so it never steals
    focus. The paste therefore lands at the user's caret.

    Qt's QClipboard.setText() is synchronous on Windows (OpenClipboard /
    SetClipboardData / CloseClipboard), but some apps read the clipboard
    asynchronously on WM_PASTE; callers should ensure setText has run
    BEFORE this — typically via a short QTimer.singleShot to let the
    clipboard settle.
    """
    # Order: Ctrl down, V down, V up, Ctrl up.
    inputs = (INPUT * 4)()
    inputs[0].type = INPUT_KEYBOARD
    inputs[0].ki.wVk = VK_CONTROL
    inputs[1].type = INPUT_KEYBOARD
    inputs[1].ki.wVk = VK_V
    inputs[2].type = INPUT_KEYBOARD
    inputs[2].ki.wVk = VK_V
    inputs[2].ki.dwFlags = KEYEVENTF_KEYUP
    inputs[3].type = INPUT_KEYBOARD
    inputs[3].ki.wVk = VK_CONTROL
    inputs[3].ki.dwFlags = KEYEVENTF_KEYUP
    # Pass the array directly: ctypes auto-converts an (INPUT * N) array to
    # POINTER(INPUT) for the declared argtype. ctypes.byref(inputs) would
    # yield a pointer-to-array, which ctypes refuses to coerce and raises
    # "expected LP_INPUT instance instead of pointer to INPUT_Array_4".
    _user32.SendInput(4, inputs, ctypes.sizeof(INPUT))
