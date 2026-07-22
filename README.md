# NVIDIA Vibrance & Gamma Toggler

A small Windows tray app that switches your NVIDIA **Digital Vibrance** and **Gamma**
between saved profiles with a keyboard shortcut. It touches exactly the two
controls from the NVIDIA Control Panel's *Adjust desktop colour settings* page and
nothing else, so it stays tiny and starts instantly.

If you've ever cranked Digital Vibrance to 100% before a Counter-Strike match and
then dropped it back to watch a video, this does that for you. One hotkey. If
you've used VibranceGUI before, think of this as the same idea with gamma added
and a proper profile list on top.

## Who it's for

- **Competitive FPS players** who raise Digital Vibrance so enemy models pop out
  of busy backgrounds. That's the number one reason anyone touches this setting.
- People who want a single hotkey to flip between a saturated "gaming" look and a
  normal desktop look, without opening the Control Panel every time.
- Streamers and creators who need the same colour setup every time they hit record.
- Photo and video editors who keep a neutral, colour-accurate profile for work and
  a punchier one for everything else.
- Watching movies or playing dark, moody games where a small gamma bump lifts the
  shadows enough to actually see what's happening.
- Late nights, when a lower gamma takes some of the glare off the screen.
- Getting straight back to plain NVIDIA defaults after a match.

## Games people use this with

Digital Vibrance is big in competitive shooters because more saturation makes
enemies stand out from the environment. It's useful in games like:

- **Shooters:** Counter-Strike 2 / CS:GO, Valorant, Apex Legends, Overwatch 2,
  Rainbow Six Siege, PUBG, Call of Duty (Warzone / Modern Warfare), The Finals,
  Marvel Rivals, Escape from Tarkov, Fortnite, Battlefield, Quake Champions,
  Splitgate, Deadlock, Halo Infinite.
- **MOBAs, racing and party games:** League of Legends, Dota 2, Rocket League,
  Fall Guys, Dead by Daylight.
- **Darker, atmospheric games** where gamma matters more than vibrance: horror and
  survival titles, Elden Ring, Hunt: Showdown, Tarkov's night maps.

None of this is a fixed list. Vibrance and gamma apply to your whole desktop, so
every game and app is covered. These are just where it tends to matter most.

## What it does

- Digital Vibrance from 0 to 100% (matches NVIDIA: 50% is neutral, below that
  desaturates, above that oversaturates).
- Gamma from 0.30 to 2.80, with 1.00 as neutral.
- A global keyboard shortcut per profile that works even inside a fullscreen game.
- A tray menu with a live readout of the current values.
- A modern dark settings window. No admin rights needed.

## Quick start

```powershell
pip install -r requirements.txt
pythonw app.py          # or double-click run.bat
```

It runs in the system tray (the colour-wheel dial icon). The settings window opens
by itself the first time.

Prefer a single file with no Python install? See [Download and releases](#download-and-releases).

## How to use it

- Open the tray menu and click a profile to apply it, or press its shortcut.
- **User Default** is the first profile. You can't delete it. It starts at the
  NVIDIA defaults (vibrance 50%, gamma 1.00), but you can change its values and
  give it a shortcut. Applying it is your "reset to normal". It sits with the other
  profiles in the menu, so you can bind it to a key like anything else.
- **Settings** lets you add and rename profiles, drag the vibrance and gamma
  sliders, and record a shortcut (click the shortcut button, then press your combo,
  for example `Ctrl + Alt + 1`). Hit Save.
- **Live readout:** the top of the settings window and the top line of the tray
  menu show the real current vibrance and gamma read back from the GPU. These pick
  up changes you make in the NVIDIA Control Panel too. They refresh when you open
  the window or the menu, and with the Refresh buttons. Nothing polls in the
  background.
- **Re-apply current profile** pushes the active profile back onto the GPU. Gamma
  lives in the GPU's colour table, so a fullscreen game, sleep and wake, or opening
  the NVIDIA Control Panel can wipe it. One click puts it back. It also runs on
  startup if you leave that option on.

Profiles live in `%APPDATA%\NvColorToggler\config.json`.

### Options

- **Apply last profile on startup** re-applies whatever profile you used last when
  the app starts. Gamma resets on reboot, so this restores it for you.
- **Run at Windows startup** adds the app to your user startup entry (the HKCU Run
  key) so it's always there when you log in.

## Download and releases

You have a few options for shipping this on GitHub:

1. **A single `.exe` (recommended for most people).** Run `build_exe.bat` and
   PyInstaller produces `dist\NvColorToggler.exe`, a self-contained windowed app
   with the icon baked in. Attach it to a GitHub Release. No Python required to run
   it. This is the easiest thing for a non-technical user to download and launch.
2. **A zip of the source.** Smaller download, no antivirus false positives (bundled
   Python exes sometimes trip heuristic scanners), but the user needs Python and has
   to `pip install -r requirements.txt`.
3. **An installer** (Inno Setup or similar) if you want a Start-menu shortcut and a
   proper uninstaller. Overkill for a tool this size, but it's an option later.

For a first release, ship the `.exe` and keep the source zip as a fallback.

```powershell
build_exe.bat
```

## How it works

- **Digital Vibrance** goes through NVIDIA's driver API (`nvapi64.dll`, the extended
  Digital Vibrance Control interface) with `ctypes`. That's a real driver setting on
  the same 0 to 100 scale the Control Panel uses, so the Control Panel mirrors it.
- **Gamma** goes through the Windows gamma table (`SetDeviceGammaRamp`), which writes
  the same GPU colour table the Control Panel's gamma slider drives. This part works
  even without an NVIDIA card.
- **Shortcuts** use the Win32 `RegisterHotKey` API. They're system-wide and don't
  hook your keyboard, so antivirus has no reason to flag them.

### Why the Control Panel's gamma slider doesn't move

The Control Panel mirrors Digital Vibrance because that's a real driver setting it
reads back. Gamma is different. The Control Panel shows its own stored number and
never reads the GPU table back, and it re-writes its own gamma (1.0) into that table
whenever its colour page opens. So the slider won't reflect our value, and opening
the Control Panel will reset our gamma. Every gamma tool has this same limitation,
including f.lux, ReShade and monitor calibrators. That's why the app reads the live
values back instead, so you can always see the true current state, and why there's a
Re-apply button to restore gamma after something resets it. There's a small info icon
next to each gamma slider that explains this in the app.

## Notes and limits

- Vibrance is applied to every NVIDIA display. Gamma is applied to the primary
  display.
- Windows can clamp very aggressive gamma ramps. If extreme values look capped, set
  the DWORD `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ICM\GdiIcmGammaRange`
  to `256` (decimal) and reboot. Normal values work without touching anything.
- No NVIDIA GPU? Gamma still works. Vibrance is skipped with a note in the console.
