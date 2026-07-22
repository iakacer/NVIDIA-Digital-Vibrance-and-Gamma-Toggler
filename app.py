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
import json
import copy
import queue
import threading
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

DEFAULT_CONFIG = {
    "profiles": [
        {"name": "Default", "vibrance": 50,  "gamma": 1.00, "hotkey": "ctrl+alt+0"},
        {"name": "Gaming",  "vibrance": 100, "gamma": 1.00, "hotkey": "ctrl+alt+1"},
        {"name": "Movie",   "vibrance": 65,  "gamma": 1.15, "hotkey": "ctrl+alt+2"},
    ],
    "settings": {"apply_on_startup": True, "last_profile": "Default"},
}

# --------------------------------------------------------------------------- #
#  Config
# --------------------------------------------------------------------------- #

def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("profiles", [])
        data.setdefault("settings", {})
        data["settings"].setdefault("apply_on_startup", True)
        data["settings"].setdefault("last_profile", None)
        return data
    except Exception:
        return copy.deepcopy(DEFAULT_CONFIG)


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


def reset_default():
    apply_values(50, 1.0)
    CONFIG["settings"]["last_profile"] = None      # no profile is active now
    save_config(CONFIG)
    refresh_tray()                                 # clears the radio dot
    if icon is not None:
        try:
            icon.notify("Vibrance 50% · Gamma 1.00", "Reset to NVIDIA default")
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
        pystray.MenuItem("Reset to NVIDIA default", lambda i, item: reset_default()),
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

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(12, 2))
        name = ctk.StringVar(value=p["name"])
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
        glbl = ctk.CTkLabel(grow, text=f"Gamma     {p['gamma']:.2f}", width=140, anchor="w")
        glbl.pack(side="left")
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
        if CONFIG["settings"].get("last_profile") not in names:
            CONFIG["settings"]["last_profile"] = names[0] if names else None
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
    root.lift()
    root.focus_force()


def main():
    global root, ui, hotkeys

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
