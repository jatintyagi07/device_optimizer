# Memory Optimizer Pro

A polished Windows RAM optimizer and game/emulator performance booster, built
with pure Python + Tkinter. Animated glass-style gauge, live history graph,
sortable process manager, a dedicated Game Boost tab for emulators like
BlueStacks/MSI App Player, ambient particle background effects, and a fully
scrollable, resizable interface.

© jatintyagi07 — All rights reserved.

---

## Table of contents

- [What this app does](#what-this-app-does)
- [Features](#features)
- [Requirements](#requirements)
- [Setup — running from source](#setup--running-from-source)
- [How to use it — one by one](#how-to-use-it--one-by-one)
  - [1. Dashboard tab](#1-dashboard-tab)
  - [2. Processes tab](#2-processes-tab)
  - [3. Game Boost tab](#3-game-boost-tab)
  - [4. Settings tab](#4-settings-tab)
  - [System tray](#system-tray)
- [Packaging into a standalone .exe](#packaging-into-a-standalone-exe)
- [How the core mechanisms work](#how-the-core-mechanisms-work)
- [Where your settings are stored](#where-your-settings-are-stored)
- [Known limitations](#known-limitations)
- [Files in this project](#files-in-this-project)
- [License / credit](#license--credit)

---

## What this app does

Memory Optimizer Pro helps a Windows PC feel snappier in two ways:

1. **Frees up RAM** that's sitting idle in background processes, so more is
   available to whatever you're actually using.
2. **Boosts a specific game or Android emulator** (BlueStacks, MSI App
   Player, NoxPlayer, LDPlayer, MEmu, or any custom `.exe`) by raising its
   priority, freeing RAM around it, switching Windows to a high-performance
   power plan, and optionally disabling fullscreen-optimization throttling.

Everything is one-click from a clean, modern-looking dashboard — no command
line needed once it's running.

## Features

**Dashboard**
- Animated circular gauge showing current RAM usage, with a color shift
  (green → amber → red) as usage climbs
- Live history sparkline of usage over the last few minutes
- One-click **Optimize Now** (trims idle memory from every process)
- One-click **Clear Standby List** (admin-only, frees the Windows standby
  cache)
- Auto-Optimize status indicator (configured from Settings)
- An ambient animated particle/constellation background in the header for
  a premium feel

**Processes**
- Live table of the top RAM-consuming processes, with **CPU %** shown too
- Click any column header to sort by it (name, PID, memory, CPU)
- **Close Selected** — end a process directly from the list
- **Refresh** to re-scan on demand

**Game Boost** — the FPS/performance tab for games and Android emulators
- Pick a target from BlueStacks, MSI App Player, NoxPlayer, LDPlayer, MEmu,
  or type a custom `.exe` name
- **Detect** shows whether it's currently running and its PID
- **Boost Now** applies whichever of these you've checked:
  - Raise the target's process priority
  - Free RAM from every other running app (never touches the target itself)
  - Switch Windows to the High Performance power plan
  - Disable Fullscreen Optimizations for the target (a known DWM overhead
    reducer)
- **Revert to Normal** — instantly undoes every change Boost Now made
- **Close background apps first** — an opt-in checklist of common
  resource-hogging apps (Discord, Chrome, Steam, OneDrive, etc.) that are
  currently running; nothing closes unless you check it yourself

**Settings**
- Auto-Optimize toggle: automatically runs Optimize Now when available
  memory drops below a threshold you set
- Optional "only run when CPU is idle" guard, so it never kicks in mid-task
- Light/Dark theme toggle, applied instantly across the whole app
- Launch at Windows startup toggle

**Polish**
- Sidebar navigation with a smooth animated active-tab indicator
- Every tab is independently **scrollable** — resize the window as small
  as you like, nothing gets clipped
- Toast notifications (slide + fade) for every action's result
- A small celebratory particle burst plays when Optimize Now / Boost Now
  succeeds
- Smooth fade-in when the app launches
- System tray icon with Show / Optimize Now / Quit (optional, needs
  `pystray` + `Pillow`)

## Requirements

- **Windows 10/11** for full functionality (RAM trimming, standby-list
  clearing, Game Boost, startup registration, tray icon)
- Python 3.9+ if running from source
- On macOS/Linux the app still opens and shows live stats and the process
  list — Windows-only actions are disabled or hidden automatically

## Setup — running from source

```bash
pip install -r requirements.txt
python memory_optimizer.py
```

That's it — the window opens straight into the Dashboard tab.

## How to use it — one by one

### 1. Dashboard tab
This is what you see on launch.

1. The big circular **gauge** in the top card shows your current RAM usage
   percentage, live, updating every couple of seconds.
2. To the right, you'll see exact numbers (Used / Available / Total) and a
   small graph of usage over the last few minutes.
3. Click **⚡ Optimize Now** to immediately trim idle memory from every
   running process. A toast will tell you how much was freed.
4. Click **Clear Standby List** to flush the Windows standby memory cache
   (you may be prompted for admin rights — this is expected, since it's a
   system-wide operation).
5. Below that, the **Auto-Optimize** status line tells you at a glance
   whether automatic optimization is on — head to Settings to configure it.

### 2. Processes tab
1. You'll see a live table of the top 20 memory-consuming processes:
   name, PID, memory (MB), and CPU %.
2. **Click any column header** (Process, PID, Memory, CPU %) to sort by
   that column — click again to reverse the order.
3. Select a row and click **Close Selected** to end that process. Use this
   carefully — closing a system process can cause instability.
4. Click **Refresh** any time to re-scan the current process list.

### 3. Game Boost tab
This is the tab for squeezing more performance out of an emulator or game.

1. **Pick a target** from the dropdown — BlueStacks, MSI App Player,
   NoxPlayer, LDPlayer, MEmu — or choose **Custom...** and type the exact
   `.exe` name (e.g. `mygame.exe`).
2. Click **Detect** to confirm it's actually running. If it's not, start
   it first — Boost Now only affects apps that are already running.
3. Choose which **Boost actions** you want (all four are on by default
   except "Disable Fullscreen Optimizations"):
   - *Raise the app's process priority* — gives it more CPU scheduling
     priority over background tasks.
   - *Free RAM from other apps* — trims idle memory everywhere except the
     target, so it has more headroom.
   - *Switch to High Performance power plan* — stops Windows from
     throttling for battery savings.
   - *Disable Fullscreen Optimizations* — can reduce frame-pacing overhead
     for some full-screen apps; try it and see if it helps your setup.
4. Click **⚡ Boost Now**. You'll get a toast summarizing what was changed,
   plus a small particle-burst celebration if it worked.
5. When you're done gaming, click **Revert to Normal** to instantly put
   priority, power plan, and fullscreen settings back the way they were.
6. Optionally, in **Close background apps first**, check off any running
   app you want closed before boosting (Discord, Chrome, Steam, etc.) and
   click **Close Checked Apps**. Nothing is closed automatically — you
   choose exactly what gets shut down.
7. Read the tip at the bottom: BlueStacks/MSI App Player's own FPS cap and
   CPU/RAM allocation live inside *that app's* Settings → Performance
   panel. This tab optimizes Windows around the emulator — for the biggest
   gains, raise the emulator's own allocation too.

### 4. Settings tab
1. Toggle **Auto-Optimize: Enabled** to have the app automatically run
   Optimize Now whenever available memory drops below a threshold.
2. Drag the **threshold slider** to set that percentage (5%–50%).
3. Check **"Only run when CPU is idle"** if you don't want auto-optimize
   kicking in while you're actively using the CPU for something else.
4. Click the theme button (**☀ Light** / **☾ Dark**) to switch the whole
   app's appearance instantly.
5. Check **"Launch at Windows startup"** to have the app open automatically
   when you log into Windows (adds a standard registry entry — no admin
   tools needed to remove it, just uncheck the box).

### System tray
If `pystray` and `Pillow` are installed, closing the window minimizes the
app to the system tray instead of quitting. Right-click the tray icon for
**Show**, **Optimize Now**, and **Quit**.

## Packaging into a standalone .exe

PyInstaller can't cross-compile — you need to build **on a Windows machine**:

1. Copy this whole folder to the Windows PC.
2. Double-click `build_exe.bat` (or run it from Command Prompt).
3. Find the finished app at `dist\MemoryOptimizerPro.exe`.

For "Clear Standby List", "Game Boost", and "Launch at startup" to work
once packaged, run the `.exe` as Administrator (or add `--uac-admin` to the
PyInstaller command inside `build_exe.bat` so it always prompts for
elevation).

## How the core mechanisms work

- **Optimize Now**: loops through processes and calls `EmptyWorkingSet`,
  asking Windows to release each process's idle memory pages. Windows
  reloads pages the moment a process actually needs them — nothing breaks,
  you just reclaim currently-unused RAM.
- **Clear Standby List**: calls the internal Windows API
  (`NtSetSystemInformation` / `MemoryPurgeStandbyList`) that dedicated
  standby-clearing tools use — a system-wide operation, hence the admin
  requirement.
- **Game Boost — priority**: uses `psutil`'s `Process.nice()` to set the
  target process to `HIGH_PRIORITY_CLASS`.
- **Game Boost — power plan**: shells out to `powercfg /s SCHEME_MIN`
  (the documented alias for the built-in High Performance plan) and
  restores the previous plan on revert.
- **Game Boost — fullscreen optimizations**: writes/removes the same
  per-exe registry flag
  (`HKCU\Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers`)
  that the Windows Properties → Compatibility tab checkbox writes.
- **Launch at startup**: writes/removes a value under
  `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` — the same
  mechanism most lightweight Windows utilities use for auto-start.

## Where your settings are stored

Config (auto-optimize threshold, theme, startup preference, last Game
Boost target, etc.) is saved to:

- **Windows**: `%APPDATA%\MemoryOptimizerPro\config.json`
- **macOS/Linux**: `~/.memory_optimizer_pro/config.json`

Delete that file any time to reset the app to defaults.

## Known limitations

- Effectiveness varies by machine — most noticeable on RAM-constrained
  systems or after long sessions with many open apps/tabs.
- Some protected/system processes will always be skipped when trimming —
  expected, not a bug.
- Game Boost's actual FPS impact depends heavily on the emulator's *own*
  settings (CPU/RAM allocation, graphics engine) — this tab optimizes
  Windows around it, it can't override the emulator's internal cap.
- Tray icon requires `pystray`/`Pillow`; standby-clearing, Game Boost, and
  startup registration require Windows. The app detects and degrades
  gracefully when these aren't available.
- Windows-only APIs (working-set trimming, standby clearing, priority
  boosting, power-plan switching, registry edits) were verified by code
  review and structural testing, not by running on a live Windows machine.
  Test those specific actions on your machine before relying on them.

## Files in this project

- `memory_optimizer.py` — the app
- `requirements.txt` — dependencies
- `build_exe.bat` — one-click Windows packaging script
- `app_icon.ico` — app/exe icon
- `.gitignore` — keeps build artifacts and caches out of version control
- `README.md` — this file

## License / credit

© jatintyagi07 — All rights reserved.
