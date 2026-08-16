import ctypes
from ctypes import wintypes
from PySide6.QtCore import QAbstractNativeEventFilter

user32 = ctypes.windll.user32

# Windows constants
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
VK_OEM_3 = 0xC0   # ` key (also used for · on Chinese keyboards)
WM_HOTKEY = 0x0312
HOTKEY_ID = 1


class WinHotkeyFilter(QAbstractNativeEventFilter):
    """Native event filter to catch WM_HOTKEY messages"""

    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def nativeEventFilter(self, eventType, message):
        if eventType == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message.__int__()))
            if msg.message == WM_HOTKEY:
                self.callback()
                return True, 0
        return False, 0


def register_hotkey(hotkey_id=HOTKEY_ID, mod=MOD_CONTROL, vk=VK_OEM_3):
    """Register global hotkey via Win32 API"""
    return user32.RegisterHotKey(None, hotkey_id, mod | MOD_NOREPEAT, vk)


def unregister_hotkey(hotkey_id=HOTKEY_ID):
    """Unregister global hotkey"""
    return user32.UnregisterHotKey(None, hotkey_id)