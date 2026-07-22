"""
hotkeys.py — global shortcuts via Win32 RegisterHotKey.

These are true system-wide hotkeys (they work regardless of which window has
focus) and do NOT hook the keyboard, so they don't look like a keylogger to
antivirus. All registration happens on a dedicated thread that owns a Windows
message loop; the main app talks to it with PostThreadMessage.
"""

import ctypes
from ctypes import wintypes
import threading

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_HOTKEY = 0x0312
WM_USER = 0x0400
WM_APP = 0x8000
WM_RELOAD = WM_APP + 1
WM_STOP = WM_APP + 2
MOD_NOREPEAT = 0x4000

user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

_MODS = {
    "ctrl": 0x0002, "control": 0x0002,
    "alt": 0x0001,
    "shift": 0x0004,
    "win": 0x0008, "super": 0x0008, "meta": 0x0008,
}

_NAMED = {
    "space": 0x20, "esc": 0x1B, "escape": 0x1B, "tab": 0x09,
    "enter": 0x0D, "return": 0x0D, "backspace": 0x08,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "insert": 0x2D, "delete": 0x2E, "del": 0x2E,
    "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, "\\": 0xDC,
    ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF,
}


def _vk(token):
    t = token.lower()
    if len(t) == 1:
        c = t.upper()
        if "0" <= c <= "9" or "A" <= c <= "Z":
            return ord(c)
    if t.startswith("f") and t[1:].isdigit():
        n = int(t[1:])
        if 1 <= n <= 24:
            return 0x70 + (n - 1)
    return _NAMED.get(t)


def parse_hotkey(s):
    """'ctrl+alt+1' -> (modifiers, vk)  or None if invalid."""
    if not s:
        return None
    mods, vk = 0, None
    for part in (p.strip() for p in s.split("+") if p.strip()):
        pl = part.lower()
        if pl in _MODS:
            mods |= _MODS[pl]
        else:
            v = _vk(pl)
            if v is None:
                return None
            vk = v
    if vk is None:
        return None
    return mods, vk


class HotkeyManager:
    def __init__(self, on_trigger):
        self._on_trigger = on_trigger          # on_trigger(profile_name)
        self._lock = threading.Lock()
        self._bindings = []                    # [(name, hotkey_str), ...]
        self._enabled = True
        self._registered = {}                  # hotkey_id -> name
        self._tid = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="hotkeys", daemon=True)

    # -- public API ------------------------------------------------------- #
    def start(self):
        self._thread.start()
        self._ready.wait(3)

    def set_bindings(self, bindings):
        with self._lock:
            self._bindings = list(bindings)
        self._post(WM_RELOAD)

    def set_enabled(self, enabled):
        with self._lock:
            self._enabled = bool(enabled)
        self._post(WM_RELOAD)

    def stop(self):
        self._post(WM_STOP)

    # -- internals -------------------------------------------------------- #
    def _post(self, msg):
        if self._tid:
            user32.PostThreadMessageW(self._tid, msg, 0, 0)

    def _register_all(self):
        with self._lock:
            bindings = list(self._bindings)
            enabled = self._enabled
        self._registered.clear()
        if not enabled:
            return
        hid = 1
        for name, hk in bindings:
            parsed = parse_hotkey(hk)
            if not parsed:
                continue
            mods, vk = parsed
            if user32.RegisterHotKey(None, hid, mods | MOD_NOREPEAT, vk):
                self._registered[hid] = name
                hid += 1

    def _unregister_all(self):
        for hid in list(self._registered):
            user32.UnregisterHotKey(None, hid)
        self._registered.clear()

    def _run(self):
        self._tid = kernel32.GetCurrentThreadId()
        msg = wintypes.MSG()
        # Touch the queue so PostThreadMessage has somewhere to deliver.
        user32.PeekMessageW(ctypes.byref(msg), None, WM_USER, WM_USER, 0)
        self._register_all()
        self._ready.set()
        while True:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r == 0 or r == -1:
                break
            m = msg.message
            if m == WM_HOTKEY:
                name = self._registered.get(msg.wParam)
                if name is not None:
                    try:
                        self._on_trigger(name)
                    except Exception:
                        pass
            elif m == WM_RELOAD:
                self._unregister_all()
                self._register_all()
            elif m == WM_STOP:
                break
        self._unregister_all()
