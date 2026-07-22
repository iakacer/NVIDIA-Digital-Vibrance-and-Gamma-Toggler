# NVIDIA Vibrance & Gamma Toggler

A tiny Windows tray app that switches between **colour profiles** — each a
combination of **Digital Vibrance** and **Gamma** from the NVIDIA Control Panel's
*Adjust desktop colour settings → Apply colour enhancements* section — with a
**global keyboard shortcut** per profile.

- 🎨 Digital Vibrance `0–100 %` (matches NVIDIA: 50 % = neutral default, below = desaturated, above = oversaturated)
- 🔆 Gamma `0.30–2.80` (1.00 = neutral)
- ⌨️ Per-profile global hotkeys (work from anywhere, even in-game)
- 🖱️ System tray menu — one click to switch
- 🪶 Modern dark GUI, no admin required

## Quick start

```powershell
pip install -r requirements.txt
pythonw app.py          # or double-click run.bat
```

The app lives in the system tray (look for the colour-wheel icon).
On first launch the settings window opens automatically.

## Usage

- **Left-click / open the tray menu** → pick a profile to apply it.
- **Global shortcut** → applies that profile from anywhere.
- **Settings…** → add/rename profiles, set vibrance & gamma sliders, record a
  shortcut (click the shortcut button, then press your key combo, e.g.
  `Ctrl + Alt + 1`), then **Save**.
- **Live readout (read)** → the top of the Settings window and the top line of
  the tray menu show the *actual current* Digital Vibrance and Gamma read back
  from the GPU. These reflect changes you make in the NVIDIA Control Panel too.
  They update when you open Settings / the tray menu and via **Refresh** /
  **Refresh values** — no background polling. (Gamma is derived from the display
  LUT, so heavy Control-Panel brightness/contrast tweaks can skew it slightly.)
- **Re-apply current profile (write)** → re-asserts the active profile onto the
  GPU. Gamma lives in the GPU LUT, so a fullscreen game, sleep/wake, or opening
  the NVIDIA Control Panel can reset it — this pushes it back. Also runs on
  startup (see Options).
- **Reset to NVIDIA default** → vibrance 50 %, gamma 1.00 (clears the active
  profile so no profile is marked in the tray).

Profiles are stored in `%APPDATA%\NvColorToggler\config.json`.

### Options
- **Apply last profile on startup** — re-applies your last profile when the app
  starts (gamma resets on reboot/logon, so this restores it).
- **Run at Windows startup** — adds the app to your user startup (HKCU Run key).

## Build a standalone .exe (optional)

```powershell
build_exe.bat
```

Produces `dist\NvColorToggler.exe` — a single windowed executable you can put
in your Startup folder. Python is not needed to run it.

## How it works

- **Digital Vibrance** is set through NVIDIA's driver API (`nvapi64.dll`,
  extended Digital Vibrance Control) via `ctypes` — the same 0–100 scale the
  Control Panel uses (50 = neutral). Requires an NVIDIA GPU + driver.
- **Gamma** is applied through the Windows GDI gamma ramp
  (`SetDeviceGammaRamp`), which writes the same GPU LUT the Control Panel's Gamma
  slider drives. This works even without NVAPI.
- **Shortcuts** use Win32 `RegisterHotKey` — true system-wide hotkeys, no
  keyboard hooking.

### Why NVCP's gamma slider doesn't move when we change gamma

The Control Panel mirrors **Digital Vibrance** because that's a real driver
setting it reads back. **Gamma** is different: NVCP shows its own stored number
and never reverse-reads the LUT, and it re-writes its own gamma (1.0) into the
LUT whenever its colour page opens — so the slider won't reflect our value (and
opening NVCP will reset our gamma). Every gamma tool (f.lux, ReShade, calibrators)
has this same limitation. That's why this app instead **reads the live values
back** so you can always see the true current state, and offers **Re-apply** to
restore gamma after something resets it.

## Notes & limitations

- Vibrance is applied to **all** NVIDIA displays; gamma is applied to the
  **primary** display.
- Windows may clamp very aggressive gamma ramps. If extreme gamma values look
  limited, set the registry DWORD
  `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ICM\GdiIcmGammaRange` to
  `256` (decimal) and reboot. Moderate values work out of the box.
- No NVIDIA GPU? Gamma still works; vibrance is skipped with a console warning.
