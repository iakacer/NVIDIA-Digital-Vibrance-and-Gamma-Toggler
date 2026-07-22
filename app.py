"""
NVIDIA Vibrance & Gamma Toggler
--------------------------------
A tiny tray app to switch between colour profiles (Digital Vibrance + Gamma)
from the NVIDIA "Adjust desktop colour settings" page, each with its own
global keyboard shortcut.

Run:  pythonw app.py      (or:  python app.py)
"""

import os
import sys
import ctypes
import json
import copy
import queue
import threading
import tkinter as tk
import winreg

import customtkinter as ctk
from PIL import Image, ImageDraw
import pystray

import nvcolor
from hotkeys import HotkeyManager, parse_hotkey

APP_NAME = "NVIDIA Vibrance & Gamma Toggler"
RUN_NAME = "NvColorToggler"
APPDIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "NvColorToggler")
CONFIG_PATH = os.path.join(APPDIR, "config.json")

GAMMA_INFO = ("Gamma is written to the GPU's colour LUT, so it changes the "
              "picture but will NOT show up on the NVIDIA Control Panel's gamma "
              "slider — the Control Panel keeps its own stored number and never "
              "reads the LUT back. Digital Vibrance is different: it's a real "
              "driver setting the Control Panel shares, so that one does match. "
              "Use the LIVE readout above to see the true current gamma.")


def resource_path(name):
    """Path to a bundled asset, whether running from source or a PyInstaller exe."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


ICON_PNG = resource_path("icon.png")
ICON_ICO = resource_path("icon.ico")

# Win32 for a crisp window/taskbar icon (no Pillow/ImageTk, which is flaky in
# a frozen exe). restype/argtypes matter on 64-bit so handles aren't truncated.
_user32 = ctypes.windll.user32
_user32.LoadImageW.restype = ctypes.c_void_p
_user32.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint,
                               ctypes.c_int, ctypes.c_int, ctypes.c_uint]
_user32.SendMessageW.restype = ctypes.c_void_p
_user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                 ctypes.c_void_p, ctypes.c_void_p]
_user32.GetParent.restype = ctypes.c_void_p
_user32.GetParent.argtypes = [ctypes.c_void_p]
_user32.GetSystemMetrics.restype = ctypes.c_int
_user32.GetSystemMetrics.argtypes = [ctypes.c_int]

DEFAULT_NAME = "User Default"      # the pinned, undeletable default profile

DEFAULT_CONFIG = {
    "profiles": [
        {"name": DEFAULT_NAME, "vibrance": 50, "gamma": 1.00, "hotkey": "ctrl+alt+0", "locked": True},
        {"name": "Gaming",     "vibrance": 65, "gamma": 1.20, "hotkey": "ctrl+alt+1"},
        {"name": "Movie",      "vibrance": 58, "gamma": 1.00, "hotkey": "ctrl+alt+2"},
    ],
    "settings": {"apply_on_startup": True, "last_profile": DEFAULT_NAME},
}

# --------------------------------------------------------------------------- #
#  Config
# --------------------------------------------------------------------------- #

def _ensure_default_profile(cfg):
    """Guarantee exactly one locked 'User Default' profile, pinned first.
    Seeds it from NVIDIA defaults (50 / 1.0); migrates an old 'Default' profile."""
    profiles = cfg.setdefault("profiles", [])
    locked = [p for p in profiles if p.get("locked")]
    if locked:
        default = locked[0]
        for extra in locked[1:]:            # collapse any accidental duplicates
            extra["locked"] = False
    else:
        default = next((p for p in profiles if p.get("name") == "Default"), None)
        if default is None:                 # create a fresh one
            default = {"name": DEFAULT_NAME, "vibrance": 50, "gamma": 1.00,
                       "hotkey": "", "locked": True}
            profiles.insert(0, default)
        else:                               # migrate legacy "Default"
            default["locked"] = True
        default["name"] = DEFAULT_NAME
    if profiles and profiles[0] is not default:   # keep it pinned first
        profiles.remove(default)
        profiles.insert(0, default)

    settings = cfg.setdefault("settings", {})
    settings.setdefault("apply_on_startup", True)
    names = [p["name"] for p in profiles]
    if settings.get("last_profile") not in names:   # heal renamed/removed ref
        settings["last_profile"] = DEFAULT_NAME
    return cfg


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = copy.deepcopy(DEFAULT_CONFIG)
    return _ensure_default_profile(data)


def save_config(cfg):
    os.makedirs(APPDIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


CONFIG = load_config()

# --------------------------------------------------------------------------- #
#  Run-at-startup (registry HKCU\...\Run)
# --------------------------------------------------------------------------- #

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _startup_command():
    if getattr(sys, "frozen", False):          # PyInstaller .exe
        return f'"{sys.executable}"'
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pyw if os.path.exists(pyw) else sys.executable
    return f'"{exe}" "{os.path.abspath(__file__)}"'


def is_run_at_startup():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
            return True
    except OSError:
        return False


def set_run_at_startup(enable):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enable:
                winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, _startup_command())
            else:
                try:
                    winreg.DeleteValue(k, RUN_NAME)
                except FileNotFoundError:
                    pass
    except OSError as e:
        print("startup registry error:", e)

# --------------------------------------------------------------------------- #
#  Global state / helpers
# --------------------------------------------------------------------------- #

root = None
ui = None
icon = None
hotkeys = None
gui_queue = queue.Queue()


def find_profile(name):
    for p in CONFIG["profiles"]:
        if p["name"] == name:
            return p
    return None


def current_bindings():
    return [(p["name"], p.get("hotkey", "")) for p in CONFIG["profiles"] if p.get("hotkey")]


def apply_values(vibrance, gamma):
    try:
        nvcolor.set_vibrance(vibrance)
    except Exception as e:
        print("vibrance error:", e)
    try:
        nvcolor.set_gamma(gamma)
    except Exception as e:
        print("gamma error:", e)


def apply_profile(name, notify=True):
    p = find_profile(name)
    if not p:
        return
    apply_values(p.get("vibrance", 0), p.get("gamma", 1.0))
    CONFIG["settings"]["last_profile"] = name
    save_config(CONFIG)
    refresh_tray()
    if notify and icon is not None:
        try:
            icon.notify(f'{name}  ·  vibrance {int(p["vibrance"])}%  gamma {p["gamma"]:.2f}',
                        "Profile applied")
        except Exception:
            pass


def reapply_current(notify=True):
    """Re-assert the active profile (mainly to restore gamma after a game,
    sleep/wake, or the NVIDIA Control Panel reset the GPU LUT)."""
    name = CONFIG["settings"].get("last_profile")
    p = find_profile(name) if name else None
    if p:
        apply_values(p.get("vibrance", 50), p.get("gamma", 1.0))
        label = f'Re-applied "{name}"'
    else:
        apply_values(50, 1.0)
        label = "Re-applied NVIDIA default"
    if notify and icon is not None:
        try:
            icon.notify(label, APP_NAME)
        except Exception:
            pass


def queue_gui(fn):
    gui_queue.put(fn)


def poll_queue():
    try:
        while True:
            fn = gui_queue.get_nowait()
            try:
                fn()
            except Exception as e:
                print("gui task error:", e)
    except queue.Empty:
        pass
    root.after(80, poll_queue)

# --------------------------------------------------------------------------- #
#  Tray icon
# --------------------------------------------------------------------------- #

def make_icon():
    try:
        return Image.open(ICON_PNG).convert("RGBA").resize((64, 64), Image.LANCZOS)
    except Exception:
        pass
    # Fallback if icon.png is missing: a simple colour wheel.
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    box = (8, 8, 56, 56)
    d.pieslice(box, 0, 120, fill=(255, 78, 96))
    d.pieslice(box, 120, 240, fill=(70, 205, 120))
    d.pieslice(box, 240, 360, fill=(90, 130, 255))
    d.ellipse((23, 23, 41, 41), fill=(22, 22, 26, 255))
    return img


def _apply_action(name):
    return lambda icon, item: apply_profile(name)


def _is_current(name):
    return lambda item: CONFIG["settings"].get("last_profile") == name


def _live_text(item=None):
    v = nvcolor.get_vibrance()
    g = nvcolor.get_gamma()
    vs = f"{v}%" if v is not None else "n/a"
    gs = f"{g:.2f}" if g is not None else "n/a"
    return f"Now:  Vibrance {vs}   ·   Gamma {gs}"


def build_menu():
    items = [
        pystray.MenuItem(_live_text, None, enabled=False),   # live readout
        pystray.MenuItem("Refresh values", lambda i, item: refresh_tray()),
        pystray.Menu.SEPARATOR,
    ]
    for p in CONFIG["profiles"]:
        items.append(pystray.MenuItem(
            p["name"],
            _apply_action(p["name"]),
            checked=_is_current(p["name"]),
            radio=True,
        ))
    items += [
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Re-apply current profile", lambda i, item: reapply_current()),
        pystray.MenuItem("Settings…", lambda i, item: queue_gui(show_settings), default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", lambda i, item: quit_app()),
    ]
    return pystray.Menu(*items)


def refresh_tray():
    if icon is not None:
        try:
            icon.menu = build_menu()
            icon.update_menu()
        except Exception:
            pass


def run_tray():
    global icon
    icon = pystray.Icon("nvcolor", make_icon(), APP_NAME, menu=build_menu())
    icon.run()


def quit_app(*_):
    try:
        hotkeys.stop()
    except Exception:
        pass
    try:
        if icon:
            icon.stop()
    except Exception:
        pass
    queue_gui(root.destroy)

# --------------------------------------------------------------------------- #
#  Settings window (customtkinter)
# --------------------------------------------------------------------------- #

_MOD_KEYSYMS = {
    "Control_L": "ctrl", "Control_R": "ctrl",
    "Alt_L": "alt", "Alt_R": "alt", "ISO_Level3_Shift": "alt",
    "Shift_L": "shift", "Shift_R": "shift",
    "Super_L": "win", "Super_R": "win", "Win_L": "win", "Win_R": "win",
}

_KEYSYM_TOKENS = {
    "space": "space", "return": "enter", "tab": "tab", "escape": "esc",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "prior": "pageup", "next": "pagedown", "home": "home", "end": "end",
    "insert": "insert", "delete": "delete", "backspace": "backspace",
    "minus": "-", "equal": "=", "bracketleft": "[", "bracketright": "]",
    "semicolon": ";", "comma": ",", "period": ".", "slash": "/",
    "grave": "`", "backslash": "\\", "apostrophe": "'",
}


class Tooltip:
    """Lightweight hover tooltip for any tk/ctk widget."""

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, _=None):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 22
        y = self.widget.winfo_rooty() + 22
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=self.text, justify="left", bg="#202024",
                 fg="#e6e6e6", relief="solid", borderwidth=1, wraplength=300,
                 padx=10, pady=8, font=("Segoe UI", 9)).pack()

    def _hide(self, _=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


class SettingsUI:
    def __init__(self, master):
        self.root = master
        self.profiles = []
        self.rows = []
        self.capture_row = None
        self.capture_held = []
        self._build()

    def _build(self):
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.root.title(APP_NAME)
        self.root.geometry("580x640")
        self.root.minsize(540, 500)
        self._apply_window_icon()
        # customtkinter re-stamps the window icon shortly after init; re-apply
        # so ours wins and the taskbar icon stays crisp.
        self.root.after(300, self._apply_window_icon)

        ctk.CTkLabel(self.root, text="Colour Profiles",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(padx=22, pady=(18, 2), anchor="w")
        ctk.CTkLabel(self.root, text="Digital Vibrance & Gamma  ·  NVIDIA desktop colour",
                     text_color="gray55").pack(padx=22, pady=(0, 10), anchor="w")

        # Live readout of the actual current values (reflects the NVIDIA
        # Control Panel). Read on open and via Refresh — no background polling.
        live = ctk.CTkFrame(self.root)
        live.pack(fill="x", padx=16, pady=(0, 8))
        ctk.CTkLabel(live, text="LIVE", text_color="gray55",
                     font=ctk.CTkFont(size=11, weight="bold")).pack(side="left", padx=(12, 10), pady=10)
        self.live_lbl = ctk.CTkLabel(live, text="Digital Vibrance  —      Gamma  —",
                                     font=ctk.CTkFont(size=14))
        self.live_lbl.pack(side="left", pady=10)
        ctk.CTkButton(live, text="Refresh", width=80, fg_color="gray25",
                      hover_color="gray35", command=self.refresh_live).pack(side="right", padx=10)

        self.list_frame = ctk.CTkScrollableFrame(self.root)
        self.list_frame.pack(fill="both", expand=True, padx=16, pady=4)

        opt = ctk.CTkFrame(self.root, fg_color="transparent")
        opt.pack(fill="x", padx=20, pady=(6, 0))
        self.var_apply_startup = ctk.BooleanVar(value=CONFIG["settings"].get("apply_on_startup", True))
        ctk.CTkCheckBox(opt, text="Apply last profile on startup",
                        variable=self.var_apply_startup).pack(side="left")
        self.var_run_startup = ctk.BooleanVar(value=is_run_at_startup())
        ctk.CTkCheckBox(opt, text="Run at Windows startup",
                        variable=self.var_run_startup).pack(side="left", padx=18)

        bar = ctk.CTkFrame(self.root, fg_color="transparent")
        bar.pack(fill="x", padx=20, pady=12)
        ctk.CTkButton(bar, text="+  Add profile", width=130, command=self.add_profile).pack(side="left")
        self.status = ctk.CTkLabel(bar, text="", text_color="gray55")
        self.status.pack(side="left", padx=12)
        ctk.CTkButton(bar, text="Save", width=90, command=self.save).pack(side="right")

        self.root.bind("<KeyPress>", self._on_key_press)
        self.root.bind("<KeyRelease>", self._on_key_release)
        self.root.protocol("WM_DELETE_WINDOW", self.hide)

    def _apply_window_icon(self):
        """Set the titlebar + taskbar icon to icon.ico.

        iconbitmap() flips customtkinter's internal flag so it stops stamping its
        own default icon over ours. WM_SETICON then sets a correctly sized large
        icon straight from the .ico for a crisp (non-blurry) taskbar button."""
        try:
            self.root.iconbitmap(ICON_ICO)
        except Exception:
            pass
        try:
            IMAGE_ICON, LR_LOADFROMFILE = 1, 0x0010
            WM_SETICON, ICON_SMALL, ICON_BIG = 0x0080, 0, 1
            cxs, cys = _user32.GetSystemMetrics(49), _user32.GetSystemMetrics(50)   # small icon
            cxb, cyb = _user32.GetSystemMetrics(11), _user32.GetSystemMetrics(12)   # large icon
            h_small = _user32.LoadImageW(None, ICON_ICO, IMAGE_ICON, cxs, cys, LR_LOADFROMFILE)
            h_big = _user32.LoadImageW(None, ICON_ICO, IMAGE_ICON, cxb, cyb, LR_LOADFROMFILE)
            wid = self.root.winfo_id()
            for hwnd in (wid, _user32.GetParent(wid)):
                if not hwnd:
                    continue
                if h_small:
                    _user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, h_small)
                if h_big:
                    _user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, h_big)
        except Exception:
            pass

    # -- data <-> widgets ------------------------------------------------- #
    def load(self):
        self.profiles = copy.deepcopy(CONFIG["profiles"])
        self.var_apply_startup.set(CONFIG["settings"].get("apply_on_startup", True))
        self.var_run_startup.set(is_run_at_startup())
        self.refresh_live()
        self._render()

    def refresh_live(self):
        v = nvcolor.get_vibrance()
        g = nvcolor.get_gamma()
        vs = f"{v}%" if v is not None else "n/a"
        gs = f"{g:.2f}" if g is not None else "n/a"
        self.live_lbl.configure(text=f"Digital Vibrance  {vs}       Gamma  {gs}")

    def _commit_widgets(self):
        for i, row in enumerate(self.rows):
            self.profiles[i]["name"] = row["name"].get().strip()
            self.profiles[i]["vibrance"] = int(round(row["vib"].get()))
            self.profiles[i]["gamma"] = round(float(row["gam"].get()), 2)

    def _render(self):
        for w in self.list_frame.winfo_children():
            w.destroy()
        self.rows = []
        for i, p in enumerate(self.profiles):
            self._card(i, p)

    def _card(self, i, p):
        card = ctk.CTkFrame(self.list_frame)
        card.pack(fill="x", pady=6, padx=4)

        locked = p.get("locked", False)
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(12, 2))
        name = ctk.StringVar(value=p["name"])
        if locked:
            ctk.CTkEntry(top, textvariable=name, width=210,
                         state="disabled").pack(side="left")
            ctk.CTkLabel(top, text="your default · can't be deleted",
                         text_color="gray55").pack(side="left", padx=10)
        else:
            ctk.CTkEntry(top, textvariable=name, width=210,
                         placeholder_text="Profile name").pack(side="left")
            ctk.CTkButton(top, text="Delete", width=64, fg_color="#933", hover_color="#b44",
                          command=lambda idx=i: self.delete(idx)).pack(side="right")
        ctk.CTkButton(top, text="Apply", width=64,
                      command=lambda idx=i: self.apply_now(idx)).pack(side="right", padx=(0, 6))

        vrow = ctk.CTkFrame(card, fg_color="transparent")
        vrow.pack(fill="x", padx=12, pady=2)
        vlbl = ctk.CTkLabel(vrow, text=f"Vibrance   {int(p['vibrance'])}%", width=140, anchor="w")
        vlbl.pack(side="left")
        vib = ctk.CTkSlider(vrow, from_=0, to=100, number_of_steps=100,
                            command=lambda v, l=vlbl: l.configure(text=f"Vibrance   {int(round(float(v)))}%"))
        vib.set(p["vibrance"])
        vib.pack(side="left", fill="x", expand=True, padx=8)

        grow = ctk.CTkFrame(card, fg_color="transparent")
        grow.pack(fill="x", padx=12, pady=2)
        glbl = ctk.CTkLabel(grow, text=f"Gamma     {p['gamma']:.2f}", width=118, anchor="w")
        glbl.pack(side="left")
        ginfo = ctk.CTkLabel(grow, text="ⓘ", width=22, text_color="#6ea8ff",
                             cursor="hand2", font=ctk.CTkFont(size=15))
        ginfo.pack(side="left")
        Tooltip(ginfo, GAMMA_INFO)
        gam = ctk.CTkSlider(grow, from_=0.30, to=2.80, number_of_steps=250,
                            command=lambda v, l=glbl: l.configure(text=f"Gamma     {float(v):.2f}"))
        gam.set(p["gamma"])
        gam.pack(side="left", fill="x", expand=True, padx=8)

        hrow = ctk.CTkFrame(card, fg_color="transparent")
        hrow.pack(fill="x", padx=12, pady=(2, 12))
        ctk.CTkLabel(hrow, text="Shortcut", width=140, anchor="w").pack(side="left")
        hk_btn = ctk.CTkButton(hrow, text=p.get("hotkey") or "Click to set", width=170,
                               fg_color="gray25", hover_color="gray35",
                               command=lambda idx=i: self.begin_capture(idx))
        hk_btn.pack(side="left", padx=8)
        ctk.CTkButton(hrow, text="Clear", width=56, fg_color="gray25", hover_color="gray35",
                      command=lambda idx=i: self.clear_hotkey(idx)).pack(side="left")

        self.rows.append({"name": name, "vib": vib, "gam": gam, "hk": hk_btn})

    # -- actions ---------------------------------------------------------- #
    def add_profile(self):
        self._commit_widgets()
        self.profiles.append({"name": f"Profile {len(self.profiles) + 1}",
                              "vibrance": 50, "gamma": 1.00, "hotkey": ""})
        self._render()

    def delete(self, idx):
        if self.profiles[idx].get("locked"):     # User Default can't be removed
            return
        self._commit_widgets()
        del self.profiles[idx]
        self._render()

    def apply_now(self, idx):
        self._commit_widgets()
        p = self.profiles[idx]
        apply_values(p["vibrance"], p["gamma"])
        self._flash(f'Applied “{p["name"]}”')

    def clear_hotkey(self, idx):
        self.profiles[idx]["hotkey"] = ""
        self.rows[idx]["hk"].configure(text="Click to set")

    def save(self):
        self._commit_widgets()
        names = [p["name"] for p in self.profiles]
        if any(not n for n in names):
            self._flash("Every profile needs a name", err=True)
            return
        if len(set(names)) != len(names):
            self._flash("Profile names must be unique", err=True)
            return
        seen = {}
        for p in self.profiles:
            hk = p.get("hotkey", "")
            if not hk:
                continue
            if parse_hotkey(hk) is None:
                self._flash(f"Invalid shortcut: {hk}", err=True)
                return
            if hk in seen:
                self._flash(f"Shortcut {hk} used twice", err=True)
                return
            seen[hk] = p["name"]

        CONFIG["profiles"] = copy.deepcopy(self.profiles)
        CONFIG["settings"]["apply_on_startup"] = bool(self.var_apply_startup.get())
        _ensure_default_profile(CONFIG)          # keep User Default pinned & valid
        save_config(CONFIG)
        set_run_at_startup(bool(self.var_run_startup.get()))
        hotkeys.set_bindings(current_bindings())
        refresh_tray()
        self._flash("Saved")

    # -- hotkey capture --------------------------------------------------- #
    def begin_capture(self, idx):
        if self.capture_row is not None:
            self._end_capture(cancel=True)
        self.capture_row = idx
        self.capture_held = []
        self.rows[idx]["hk"].configure(text="Press keys…  (Esc to cancel)")
        hotkeys.set_enabled(False)      # let key events reach this window

    def _on_key_press(self, e):
        if self.capture_row is None:
            return
        ks = e.keysym
        if ks == "Escape":
            self._end_capture(cancel=True)
            return
        if ks in _MOD_KEYSYMS:
            m = _MOD_KEYSYMS[ks]
            if m not in self.capture_held:
                self.capture_held.append(m)
            self.rows[self.capture_row]["hk"].configure(
                text="+".join(self._ordered()) + "+…")
            return
        token = self._token(ks)
        if token is None:
            return
        combo = self._ordered() + [token]
        hk = "+".join(combo)
        if parse_hotkey(hk) is None:
            return
        self.profiles[self.capture_row]["hotkey"] = hk
        self.rows[self.capture_row]["hk"].configure(text=hk)
        self._end_capture()

    def _on_key_release(self, e):
        if self.capture_row is None:
            return
        m = _MOD_KEYSYMS.get(e.keysym)
        if m in self.capture_held:
            self.capture_held.remove(m)

    def _end_capture(self, cancel=False):
        idx = self.capture_row
        self.capture_row = None
        self.capture_held = []
        hotkeys.set_enabled(True)
        if cancel and idx is not None:
            self.rows[idx]["hk"].configure(text=self.profiles[idx].get("hotkey") or "Click to set")

    def _ordered(self):
        return [m for m in ("ctrl", "alt", "shift", "win") if m in self.capture_held]

    @staticmethod
    def _token(ks):
        if len(ks) == 1 and ks.isalnum():
            return ks.lower()
        kl = ks.lower()
        if kl.startswith("f") and kl[1:].isdigit():
            return kl
        return _KEYSYM_TOKENS.get(kl)

    # -- window ----------------------------------------------------------- #
    def _flash(self, text, err=False):
        self.status.configure(text=text, text_color="#e66" if err else "gray60")
        self.root.after(2500, lambda: self.status.configure(text=""))

    def hide(self):
        if self.capture_row is not None:
            self._end_capture(cancel=True)
        self.root.withdraw()

# --------------------------------------------------------------------------- #
#  Entry point
# --------------------------------------------------------------------------- #

def show_settings():
    ui.load()                          # reads live values into the readout
    root.deiconify()
    ui._apply_window_icon()            # taskbar button appears on show; set its icon
    root.lift()
    root.focus_force()


def main():
    global root, ui, hotkeys

    # Give the process its own taskbar identity so Windows shows our icon
    # (crisp) instead of the pythonw.exe host icon.
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NvColorToggler.App")
    except Exception:
        pass

    first_run = not os.path.exists(CONFIG_PATH)
    if first_run:
        save_config(CONFIG)

    root = ctk.CTk()
    root.withdraw()
    ui = SettingsUI(root)

    hotkeys = HotkeyManager(apply_profile)
    hotkeys.set_bindings(current_bindings())
    hotkeys.start()

    threading.Thread(target=run_tray, name="tray", daemon=True).start()

    if not nvcolor.available():
        print("Warning: NVIDIA / NVAPI not detected — vibrance will be skipped, "
              "gamma still works.")

    if CONFIG["settings"].get("apply_on_startup") and CONFIG["settings"].get("last_profile"):
        apply_profile(CONFIG["settings"]["last_profile"], notify=False)

    if first_run:
        root.after(400, lambda: queue_gui(show_settings))

    root.after(200, poll_queue)
    root.mainloop()


if __name__ == "__main__":
    main()
