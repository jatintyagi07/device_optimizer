"""
Memory Optimizer Pro (v2)
--------------------------
A professional-grade Windows RAM optimizer with:

  Dashboard
    - Animated glass-style circular gauge (glow + eased transitions)
    - Live RAM history sparkline (last few minutes)
    - Optimize Now / Clear Standby List, with toast notifications
  Processes
    - Live top-memory-consumers table (refreshable)
  Settings
    - Auto-optimize threshold + idle-only guard
    - Light/Dark theme toggle
    - Launch at Windows startup toggle
    - Preferences persisted to disk between runs

Plus: sidebar navigation with sliding transitions, ripple-effect buttons,
and a toast notification system instead of a plain status line.

Requirements:
  pip install -r requirements.txt

Run:
  python memory_optimizer.py

Notes:
  - Process trimming + standby-list clearing require Windows.
  - Standby-list clearing additionally requires Administrator rights.
  - "Launch at startup" writes/removes a registry Run key (Windows only).
  - On non-Windows OSes the app still runs and shows live stats/process
    list, with Windows-only actions disabled.
"""

import os
import sys
import json
import math
import time
import ctypes
import random
import platform
import subprocess
import threading
from collections import deque
import tkinter as tk
from tkinter import ttk

import psutil

IS_WINDOWS = platform.system() == "Windows"

try:
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except Exception:
    # Covers ImportError as well as backend init failures (e.g. missing
    # Gtk/AppIndicator on some Linux setups) — tray support is optional.
    TRAY_AVAILABLE = False


# ------------------------------------------------------------------
# Config (persisted preferences)
# ------------------------------------------------------------------
def _config_path():
    if IS_WINDOWS:
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        folder = os.path.join(base, "MemoryOptimizerPro")
    else:
        folder = os.path.join(os.path.expanduser("~"), ".memory_optimizer_pro")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "config.json")


DEFAULT_CONFIG = {
    "auto_enabled": False,
    "idle_only": True,
    "threshold": 20,
    "theme": "dark",
    "start_with_windows": False,
    "boost_target": "BlueStacks",
    "boost_custom_exe": "",
}


def load_config():
    path = _config_path()
    cfg = dict(DEFAULT_CONFIG)
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


def save_config(cfg):
    try:
        with open(_config_path(), "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


def set_start_with_windows(enabled: bool):
    """Add/remove a HKCU Run key so the app can launch at login. Windows only."""
    if not IS_WINDOWS:
        return
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                exe = sys.executable
                script = os.path.abspath(__file__)
                cmd = f'"{exe}" "{script}"' if not getattr(sys, "frozen", False) else f'"{exe}"'
                winreg.SetValueEx(key, "MemoryOptimizerPro", 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, "MemoryOptimizerPro")
                except FileNotFoundError:
                    pass
    except Exception:
        pass


# ------------------------------------------------------------------
# Theme
# ------------------------------------------------------------------
class DarkTheme:
    name = "dark"
    bg = "#0b0d13"
    sidebar = "#0f1219"
    panel = "#171a23"
    panel_light = "#1e2230"
    accent = "#5b8cff"
    accent_glow = "#7fa8ff"
    good = "#3ddc97"
    warn = "#ffb454"
    danger = "#ff5d73"
    text = "#e8eaf0"
    subtext = "#8891a7"
    track = "#242838"
    toast_bg = "#1c2030"


class LightTheme:
    name = "light"
    bg = "#f4f6fb"
    sidebar = "#eaedf6"
    panel = "#ffffff"
    panel_light = "#f0f2f9"
    accent = "#3f6df0"
    accent_glow = "#5b8cff"
    good = "#22b573"
    warn = "#e08e2b"
    danger = "#e2495f"
    text = "#1c2030"
    subtext = "#6b7280"
    track = "#e2e6f0"
    toast_bg = "#ffffff"


def lerp(a, b, t):
    return a + (b - a) * t


def lerp_color(c1, c2, t):
    c1, c2 = c1.lstrip("#"), c2.lstrip("#")
    r1, g1, b1 = int(c1[0:2], 16), int(c1[2:4], 16), int(c1[4:6], 16)
    r2, g2, b2 = int(c2[0:2], 16), int(c2[2:4], 16), int(c2[4:6], 16)
    return f"#{int(lerp(r1,r2,t)):02x}{int(lerp(g1,g2,t)):02x}{int(lerp(b1,b2,t)):02x}"


def ease_out_cubic(t):
    return 1 - (1 - t) ** 3


def ease_in_out_quad(t):
    return 2 * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 2 / 2


# ------------------------------------------------------------------
# Native memory operations (Windows)
# ------------------------------------------------------------------
def trim_process_working_sets(exclude_pids=None):
    psapi = ctypes.WinDLL("psapi.dll")
    kernel32 = ctypes.WinDLL("kernel32.dll")
    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_SET_QUOTA = 0x0100
    exclude_pids = exclude_pids or set()

    ok, fail = 0, 0
    for proc in psutil.process_iter(["pid"]):
        pid = proc.info["pid"]
        if pid == 0 or pid in exclude_pids:
            continue
        handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_SET_QUOTA, False, pid)
        if not handle:
            fail += 1
            continue
        try:
            if psapi.EmptyWorkingSet(handle):
                ok += 1
            else:
                fail += 1
        except Exception:
            fail += 1
        finally:
            kernel32.CloseHandle(handle)
    return ok, fail


def _enable_privilege(name: str) -> bool:
    advapi32 = ctypes.WinDLL("advapi32.dll")
    kernel32 = ctypes.WinDLL("kernel32.dll")
    TOKEN_ADJUST_PRIVILEGES = 0x0020
    TOKEN_QUERY = 0x0008
    SE_PRIVILEGE_ENABLED = 0x0002

    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", ctypes.c_ulong), ("HighPart", ctypes.c_long)]

    class LUID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Luid", LUID), ("Attributes", ctypes.c_ulong)]

    class TOKEN_PRIVILEGES(ctypes.Structure):
        _fields_ = [("PrivilegeCount", ctypes.c_ulong), ("Privileges", LUID_AND_ATTRIBUTES * 1)]

    h_token = ctypes.c_void_p()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, ctypes.byref(h_token)
    ):
        return False
    luid = LUID()
    if not advapi32.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
        return False
    tp = TOKEN_PRIVILEGES()
    tp.PrivilegeCount = 1
    tp.Privileges[0] = LUID_AND_ATTRIBUTES(luid, SE_PRIVILEGE_ENABLED)
    return bool(advapi32.AdjustTokenPrivileges(h_token, False, ctypes.byref(tp), 0, None, None))


def empty_standby_list():
    if not IS_WINDOWS:
        return False, "Not supported on this OS."
    try:
        _enable_privilege("SeProfileSingleProcessPrivilege")
        ntdll = ctypes.WinDLL("ntdll.dll")
        SystemMemoryListInformation = 80
        MemoryPurgeStandbyList = 4
        cmd = ctypes.c_int(MemoryPurgeStandbyList)
        status = ntdll.NtSetSystemInformation(
            SystemMemoryListInformation, ctypes.byref(cmd), ctypes.sizeof(cmd)
        )
        if status == 0:
            return True, "Standby list cleared."
        return False, f"Requires Administrator (status={status})."
    except Exception as e:
        return False, f"Unavailable: {e}"


def get_top_processes(limit=12):
    procs = []
    for p in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            mi = p.info["memory_info"]
            if mi is None:
                continue
            cpu = p.cpu_percent(interval=None)
            procs.append((p.info["name"] or "Unknown", p.info["pid"], mi.rss / (1024 ** 2), cpu))
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    procs.sort(key=lambda x: -x[2])
    return procs[:limit]


def kill_process(pid):
    try:
        p = psutil.Process(pid)
        p.terminate()
        try:
            p.wait(timeout=3)
        except psutil.TimeoutExpired:
            p.kill()
        return True, f"Closed PID {pid}."
    except psutil.NoSuchProcess:
        return True, "Process already closed."
    except psutil.AccessDenied:
        return False, "Access denied (try running as Administrator)."
    except Exception as e:
        return False, f"Couldn't close process: {e}"


# ------------------------------------------------------------------
# Game / Emulator Boost
# ------------------------------------------------------------------
# Executable names (lowercase) for popular Android emulators. "MSI App
# Player" (sometimes typed "msi5"/"MSIAppPlayer") is built on the same
# engine as BlueStacks, so it shares the hd-player.exe process name.
KNOWN_EMULATORS = {
    "BlueStacks":     ["hd-player.exe", "bluestacks.exe", "bluestacksservices.exe"],
    "MSI App Player": ["msiappplayer.exe", "hd-player.exe"],
    "NoxPlayer":      ["nox.exe", "noxvmhandle.exe"],
    "LDPlayer":       ["dnplayer.exe", "ldplayer9.exe", "ldplayer.exe"],
    "MEmu":           ["memu.exe", "memuheadless.exe"],
}

# Common background apps that tend to eat CPU/RAM/GPU during gaming.
# Purely informational + opt-in — the app never closes these automatically.
COMMON_BACKGROUND_APPS = [
    "discord.exe", "spotify.exe", "onedrive.exe", "steam.exe",
    "skype.exe", "teams.exe", "chrome.exe", "msedge.exe",
    "epicgameslauncher.exe", "slack.exe", "dropbox.exe",
]

# Remembers what we changed, so "Revert to Normal" can undo it.
_boost_state = {"applied": False, "power_plan_before": None, "targets": []}


def find_target_processes(exe_names):
    """Return list of (pid, name, exe_path) for any running process whose
    name matches one of exe_names (case-insensitive)."""
    wanted = {n.lower() for n in exe_names}
    found = []
    for p in psutil.process_iter(["pid", "name", "exe"]):
        try:
            name = (p.info["name"] or "").lower()
            if name in wanted:
                found.append((p.info["pid"], p.info["name"], p.info.get("exe") or ""))
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return found


def _get_active_power_scheme():
    try:
        out = subprocess.check_output(["powercfg", "/getactivescheme"], text=True,
                                       stderr=subprocess.DEVNULL)
        # Example: "Power Scheme GUID: 381b4222-... (Balanced)"
        return out.split(":", 1)[1].strip() if ":" in out else out.strip()
    except Exception:
        return None


def _set_power_plan_high_performance():
    """Switch to the built-in High Performance plan. Returns (ok, message)."""
    if not IS_WINDOWS:
        return False, "Power plan switching requires Windows."
    try:
        before = _get_active_power_scheme()
        # SCHEME_MIN is the documented alias for the High Performance plan.
        subprocess.run(["powercfg", "/s", "SCHEME_MIN"], check=True,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True, before
    except Exception as e:
        return False, str(e)


def _restore_power_plan(previous_desc):
    if not IS_WINDOWS or not previous_desc:
        return
    try:
        # previous_desc looks like "... (Balanced)" — fall back to balanced alias.
        subprocess.run(["powercfg", "/s", "SCHEME_BALANCED"], check=True,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _boost_process_priority(pid):
    """Raise a process's scheduling priority. Windows-only, best-effort."""
    if not IS_WINDOWS:
        return False
    try:
        proc = psutil.Process(pid)
        original = proc.nice()
        proc.nice(psutil.HIGH_PRIORITY_CLASS)
        return original
    except Exception:
        return None


def _restore_process_priority(pid, original_nice):
    if not IS_WINDOWS or original_nice is None:
        return
    try:
        psutil.Process(pid).nice(original_nice)
    except Exception:
        pass


def _set_fullscreen_optimizations_disabled(exe_path, disabled=True):
    """Adds/removes the standard per-exe compatibility flag that disables
    'Fullscreen Optimizations' — the same flag the Windows 'Compatibility'
    tab checkbox writes. Reduces DWM-related frame-pacing overhead for
    some full-screen apps/emulators. Windows-only."""
    if not IS_WINDOWS or not exe_path:
        return False, "Requires Windows and a resolved executable path."
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS) as key:
            if disabled:
                winreg.SetValueEx(key, exe_path, 0, winreg.REG_SZ,
                                   "~ DISABLEDXMAXIMIZEDWINDOWEDMODE HIGHDPIAWARE")
            else:
                try:
                    winreg.DeleteValue(key, exe_path)
                except FileNotFoundError:
                    pass
        return True, None
    except Exception as e:
        return False, str(e)


def apply_game_boost(exe_names, do_priority=True, do_free_ram=True,
                      do_power_plan=True, do_disable_fso=False):
    """Runs the selected boost actions against any running process matching
    exe_names. Returns (summary_lines, matched_targets) where matched_targets
    is a list of dicts used later by revert_game_boost()."""
    matches = find_target_processes(exe_names)
    summary = []
    targets = []

    if not matches:
        summary.append("Target app isn't running — start it, then boost again.")
        return summary, targets

    for pid, name, exe_path in matches:
        entry = {"pid": pid, "name": name, "exe": exe_path,
                  "original_nice": None, "fso_disabled": False}
        if do_priority:
            entry["original_nice"] = _boost_process_priority(pid)
            if entry["original_nice"] is not None:
                summary.append(f"Raised priority for {name} (PID {pid}).")
        if do_disable_fso and exe_path:
            ok, err = _set_fullscreen_optimizations_disabled(exe_path, True)
            entry["fso_disabled"] = ok
            if ok:
                summary.append(f"Disabled fullscreen optimizations for {name}.")
        targets.append(entry)

    if do_free_ram and IS_WINDOWS:
        target_pids = {t["pid"] for t in targets}
        ok, fail = trim_process_working_sets(exclude_pids=target_pids)
        summary.append(f"Freed RAM from {ok} other processes ({fail} skipped).")

    if do_power_plan:
        ok, before_or_err = _set_power_plan_high_performance()
        if ok:
            summary.append("Switched Windows to the High Performance power plan.")
            _boost_state["power_plan_before"] = before_or_err
        else:
            summary.append(f"Power plan unchanged: {before_or_err}")

    _boost_state["applied"] = True
    _boost_state["targets"] = targets
    return summary, targets


def revert_game_boost():
    if not _boost_state["applied"]:
        return ["Nothing to revert — boost hasn't been applied yet."]
    summary = []
    for entry in _boost_state["targets"]:
        _restore_process_priority(entry["pid"], entry["original_nice"])
        if entry["fso_disabled"] and entry["exe"]:
            _set_fullscreen_optimizations_disabled(entry["exe"], False)
    _restore_power_plan(_boost_state["power_plan_before"])
    summary.append("Reverted priority, fullscreen-optimization, and power plan changes.")
    _boost_state["applied"] = False
    _boost_state["targets"] = []
    return summary


# ------------------------------------------------------------------
# Animated circular gauge (glass / pseudo-3D look)
# ------------------------------------------------------------------
class Gauge(tk.Canvas):
    def __init__(self, master, theme, size=200, **kwargs):
        super().__init__(master, width=size, height=size, bg=theme.panel,
                          highlightthickness=0, **kwargs)
        self.theme = theme
        self.size = size
        self.value = 0.0
        self.target = 0.0
        self._anim_start = 0.0
        self._anim_from = 0.0
        self._anim_running = False
        self._phase = 0.0
        self._sheen_angle = 0.0
        self.after(16, self._tick)

    def set_theme(self, theme):
        self.theme = theme
        self.configure(bg=theme.panel)

    def set_value(self, pct):
        pct = max(0.0, min(100.0, pct))
        if abs(pct - self.target) < 0.05:
            return
        self._anim_from = self.value
        self.target = pct
        self._anim_start = time.time()
        self._anim_running = True

    def _tick(self):
        self._phase = (self._phase + 0.025) % (2 * math.pi)
        self._sheen_angle = (self._sheen_angle + 0.6) % 360
        if self._anim_running:
            t = min(1.0, (time.time() - self._anim_start) / 0.7)
            self.value = lerp(self._anim_from, self.target, ease_out_cubic(t))
            if t >= 1.0:
                self._anim_running = False
        self._draw()
        self.after(16, self._tick)

    def _draw(self):
        th = self.theme
        self.delete("all")
        s = self.size
        cx, cy = s / 2, s / 2
        r_outer = s / 2 - 10
        r_inner = r_outer - 16

        if self.value < 60:
            color = lerp_color(th.good, th.warn, self.value / 60)
        elif self.value < 85:
            color = lerp_color(th.warn, th.danger, (self.value - 60) / 25)
        else:
            color = th.danger

        pulse = 1.0 + 0.035 * math.sin(self._phase)
        for i, base_r in enumerate([r_outer + 9, r_outer + 5, r_outer + 1]):
            shade = lerp_color(th.panel, color, 0.12 - i * 0.03)
            rr = base_r * pulse
            self.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, outline=shade, width=1)

        self.create_arc(cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer,
                         start=90, extent=-360, style="arc", outline=th.track, width=13)

        extent = -360 * (self.value / 100)
        self.create_arc(cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer,
                         start=90, extent=extent, style="arc", outline=color, width=13)

        # small rounded "cap" dot at the end of the value arc
        end_angle = math.radians(90 - (-extent))
        cap_x = cx + r_outer * math.cos(end_angle)
        cap_y = cy - r_outer * math.sin(end_angle)
        self.create_oval(cap_x - 6, cap_y - 6, cap_x + 6, cap_y + 6, fill=color, outline="")

        steps = 10
        for i in range(steps):
            t = i / steps
            shade = lerp_color(th.panel_light, th.panel, t)
            rr = r_inner * (1 - t * 0.02)
            self.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, fill=shade, outline="")

        # rotating specular sheen for extra "glass" depth
        sheen_rad = math.radians(self._sheen_angle)
        sx = cx + r_inner * 0.35 * math.cos(sheen_rad)
        sy = cy + r_inner * 0.35 * math.sin(sheen_rad)
        self.create_oval(sx - r_inner * 0.28, sy - r_inner * 0.18,
                          sx + r_inner * 0.28, sy + r_inner * 0.18,
                          fill="", outline=th.panel_light)

        big_font = ("Segoe UI", int(s * 0.155), "bold")
        small_font = ("Segoe UI", int(s * 0.05))
        self.create_text(cx, cy - 4, text=f"{self.value:.0f}%", fill=th.text, font=big_font)
        self.create_text(cx, cy + s * 0.155, text="MEMORY USED", fill=th.subtext, font=small_font)


# ------------------------------------------------------------------
# History sparkline chart
# ------------------------------------------------------------------
class HistoryGraph(tk.Canvas):
    def __init__(self, master, theme, width=340, height=90, max_points=90, **kwargs):
        super().__init__(master, width=width, height=height, bg=theme.panel,
                          highlightthickness=0, **kwargs)
        self.theme = theme
        self.w, self.h = width, height
        self.data = deque([0.0] * max_points, maxlen=max_points)
        self._draw()

    def set_theme(self, theme):
        self.theme = theme
        self.configure(bg=theme.panel)

    def push(self, value):
        self.data.append(value)
        self._draw()

    def _draw(self):
        th = self.theme
        self.delete("all")
        n = len(self.data)
        if n < 2:
            return
        step = self.w / (n - 1)

        # gridlines
        for frac in (0.25, 0.5, 0.75):
            y = self.h * (1 - frac)
            self.create_line(0, y, self.w, y, fill=th.track, dash=(2, 4))

        points = []
        for i, v in enumerate(self.data):
            x = i * step
            y = self.h - (v / 100.0) * (self.h - 6) - 3
            points.append((x, y))

        # filled area under the line
        poly = [(0, self.h)] + points + [(self.w, self.h)]
        flat = [c for p in poly for c in p]
        avg = sum(self.data) / n
        fill_color = lerp_color(th.good, th.danger, min(1.0, avg / 100))
        self.create_polygon(flat, fill=fill_color, outline="", stipple="gray25")

        flat_line = [c for p in points for c in p]
        self.create_line(*flat_line, fill=fill_color, width=2, smooth=True)

        last_x, last_y = points[-1]
        self.create_oval(last_x - 4, last_y - 4, last_x + 4, last_y + 4,
                          fill=fill_color, outline="")


# ------------------------------------------------------------------
# Rounded button with hover + ripple click animation
# ------------------------------------------------------------------
class RoundedButton(tk.Canvas):
    def __init__(self, master, text, command, theme, width=200, height=44,
                 kind="primary", **kwargs):
        super().__init__(master, width=width, height=height, bg=theme.panel,
                          highlightthickness=0, **kwargs)
        self.command = command
        self.theme = theme
        self.kind = kind
        self.width, self.height = width, height
        self.text = text
        self.enabled = True
        self._ripples = []
        self._refresh_colors()
        self.current_color = self.base_color
        self._draw()
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self.after(16, self._animate_ripples)

    def set_theme(self, theme):
        self.theme = theme
        self.configure(bg=theme.panel)
        self._refresh_colors()
        self.current_color = self.base_color if self.enabled else theme.track
        self._draw()

    def _refresh_colors(self):
        th = self.theme
        if self.kind == "primary":
            self.base_color, self.hover_color, self.text_color = th.accent, th.accent_glow, th.bg
        elif self.kind == "secondary":
            self.base_color, self.hover_color, self.text_color = th.panel_light, th.track, th.text
        else:  # danger
            self.base_color, self.hover_color, self.text_color = th.danger, "#ff7d90", th.bg

    def set_text(self, text):
        self.text = text
        self._draw()

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.current_color = self.base_color if enabled else self.theme.track
        self._draw()

    def _rounded_rect(self, x1, y1, x2, y2, r, **kw):
        pts = [x1+r,y1, x2-r,y1, x2,y1, x2,y1+r, x2,y2-r, x2,y2,
               x2-r,y2, x1+r,y2, x1,y2, x1,y2-r, x1,y1+r, x1,y1]
        return self.create_polygon(pts, smooth=True, **kw)

    def _draw(self):
        self.delete("all")
        pad = 2
        self._rounded_rect(pad, pad, self.width - pad, self.height - pad, 12,
                            fill=self.current_color, outline="")
        for (x, y, r, alpha_step) in self._ripples:
            shade = lerp_color(self.current_color, "#ffffff", 0.35)
            self.create_oval(x - r, y - r, x + r, y + r, outline=shade, width=2)
        self.create_text(self.width / 2, self.height / 2, text=self.text,
                          fill=self.text_color if self.enabled else self.theme.subtext,
                          font=("Segoe UI", 11, "bold"))

    def _on_enter(self, _):
        if self.enabled:
            self.current_color = self.hover_color
            self._draw()

    def _on_leave(self, _):
        if self.enabled:
            self.current_color = self.base_color
            self._draw()

    def _on_click(self, event):
        if not self.enabled:
            return
        self._ripples.append([event.x, event.y, 4, 1.0])
        if self.command:
            self.command()

    def _animate_ripples(self):
        alive = []
        for r in self._ripples:
            r[2] += 4
            r[3] -= 0.06
            if r[3] > 0 and r[2] < max(self.width, self.height):
                alive.append(r)
        self._ripples = alive
        self._draw()
        self.after(16, self._animate_ripples)


# ------------------------------------------------------------------
# Toast notifications
# ------------------------------------------------------------------
class ToastManager:
    def __init__(self, root, theme):
        self.root = root
        self.theme = theme
        self._active = []

    def set_theme(self, theme):
        self.theme = theme

    def show(self, message, kind="info"):
        th = self.theme
        color = {"info": th.accent, "success": th.good, "error": th.danger}.get(kind, th.accent)

        toast = tk.Toplevel(self.root)
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        try:
            toast.attributes("-alpha", 0.0)
        except tk.TclError:
            pass

        frame = tk.Frame(toast, bg=th.toast_bg, highlightbackground=color, highlightthickness=2)
        frame.pack(fill="both", expand=True)
        bar = tk.Frame(frame, bg=color, width=4)
        bar.pack(side="left", fill="y")
        tk.Label(frame, text=message, bg=th.toast_bg, fg=th.text, font=("Segoe UI", 10),
                 wraplength=260, justify="left", padx=12, pady=10).pack(side="left")

        toast.update_idletasks()
        w, h = toast.winfo_width(), toast.winfo_height()
        rx = self.root.winfo_rootx() + self.root.winfo_width() - w - 24
        stacked_offset = sum(t[1] for t in self._active) + 16 * len(self._active)
        ry = self.root.winfo_rooty() + self.root.winfo_height() - h - 24 - stacked_offset
        toast.geometry(f"{w}x{h}+{rx}+{ry + 30}")

        entry = [toast, h]
        self._active.append(entry)
        self._fade(toast, 0.0, 0.95, ry + 30, ry, 160, then=lambda: self._schedule_close(toast, entry, ry))

    def _schedule_close(self, toast, entry, ry):
        self.root.after(2600, lambda: self._close(toast, entry, ry))

    def _close(self, toast, entry, ry):
        if not toast.winfo_exists():
            return
        self._fade(toast, 0.95, 0.0, ry, ry + 20, 160, then=lambda: self._finalize(toast, entry))

    def _finalize(self, toast, entry):
        if entry in self._active:
            self._active.remove(entry)
        if toast.winfo_exists():
            toast.destroy()

    def _fade(self, toast, a_from, a_to, y_from, y_to, duration_ms, then=None):
        start = time.time()

        def step():
            if not toast.winfo_exists():
                return
            t = min(1.0, (time.time() - start) * 1000 / duration_ms)
            e = ease_in_out_quad(t)
            try:
                toast.attributes("-alpha", lerp(a_from, a_to, e))
            except tk.TclError:
                pass
            x = toast.winfo_x()
            toast.geometry(f"+{x}+{int(lerp(y_from, y_to, e))}")
            if t < 1.0:
                toast.after(16, step)
            elif then:
                then()

        step()


# ------------------------------------------------------------------
# Sidebar nav item
# ------------------------------------------------------------------
class NavItem(tk.Canvas):
    def __init__(self, master, icon, label, theme, on_click, width=190, height=44, **kwargs):
        super().__init__(master, width=width, height=height, bg=theme.sidebar,
                          highlightthickness=0, **kwargs)
        self.theme = theme
        self.icon, self.label = icon, label
        self.on_click = on_click
        self.active = False
        self.width, self.height = width, height
        self._bar_frac = 0.0
        self._bar_target = 0.0
        self._bar_anim_start = 0.0
        self._bar_anim_from = 0.0
        self._alive = True
        self._draw()
        self.bind("<Button-1>", lambda e: self.on_click())
        self.bind("<Enter>", lambda e: self._draw(hover=True))
        self.bind("<Leave>", lambda e: self._draw(hover=False))
        self.bind("<Destroy>", self._on_destroy)

    def _on_destroy(self, _):
        self._alive = False

    def set_theme(self, theme):
        self.theme = theme
        self.configure(bg=theme.sidebar)
        self._draw()

    def set_active(self, active):
        self.active = active
        self._bar_anim_from = self._bar_frac
        self._bar_target = 1.0 if active else 0.0
        self._bar_anim_start = time.time()
        self._animate_bar()

    def _animate_bar(self):
        if not self._alive:
            return
        t = min(1.0, (time.time() - self._bar_anim_start) / 0.22)
        self._bar_frac = lerp(self._bar_anim_from, self._bar_target, ease_out_cubic(t))
        self._draw()
        if t < 1.0:
            self.after(16, self._animate_bar)

    def _draw(self, hover=False):
        th = self.theme
        self.delete("all")
        bg = th.panel if (self.active or hover) else th.sidebar
        self.create_rectangle(0, 0, self.width, self.height, fill=bg, outline="")
        if self._bar_frac > 0.01:
            bar_h = (self.height - 12) * self._bar_frac
            cy = self.height / 2
            self.create_rectangle(0, cy - bar_h / 2, 4, cy + bar_h / 2, fill=th.accent, outline="")
        color = th.text if self.active else th.subtext
        self.create_text(24, self.height / 2, text=self.icon, font=("Segoe UI", 14), fill=color, anchor="w")
        self.create_text(54, self.height / 2, text=self.label, font=("Segoe UI", 11,
                          "bold" if self.active else "normal"), fill=color, anchor="w")


# ------------------------------------------------------------------
# Main Application
# ------------------------------------------------------------------
# ------------------------------------------------------------------
# Animated gradient accent bar (used at the top of cards)
# ------------------------------------------------------------------
class AccentStrip(tk.Canvas):
    def __init__(self, master, theme, height=3, **kwargs):
        super().__init__(master, height=height, bg=theme.panel, highlightthickness=0, **kwargs)
        self.theme = theme
        self.bar_height = height
        self._phase = random.random() * 10
        self._alive = True
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Destroy>", self._on_destroy)
        self.after(45, self._tick)

    def set_theme(self, theme):
        self.theme = theme
        self.configure(bg=theme.panel)
        self._draw()

    def _on_destroy(self, _):
        self._alive = False

    def _tick(self):
        if not self._alive:
            return
        self._phase += 0.02
        self._draw()
        self.after(45, self._tick)

    def _draw(self):
        w = self.winfo_width()
        if w < 4:
            return
        self.delete("all")
        th = self.theme
        segs = 36
        seg_w = w / segs
        center = (math.sin(self._phase) * 0.5 + 0.5) * segs
        for i in range(segs):
            d = abs(i - center) / (segs * 0.22)
            t = max(0.0, 1 - d)
            color = lerp_color(th.accent, th.accent_glow, min(1.0, t))
            self.create_rectangle(i * seg_w, 0, (i + 1) * seg_w + 1, self.bar_height,
                                   fill=color, outline="")


# ------------------------------------------------------------------
# Ambient particle field — a subtle animated "constellation" background
# used behind hero/header areas for a premium, alive feel.
# ------------------------------------------------------------------
class ParticleField(tk.Canvas):
    def __init__(self, master, theme, height=150, count=26, **kwargs):
        super().__init__(master, height=height, bg=theme.panel, highlightthickness=0, **kwargs)
        self.theme = theme
        self.field_height = height
        self.count = count
        self.particles = []
        self._alive = True
        self._running = True
        self._canvas_w = 400
        self.bind("<Configure>", self._on_resize)
        self.bind("<Destroy>", self._on_destroy)
        self.after(200, self._init_particles)

    def set_theme(self, theme):
        self.theme = theme
        self.configure(bg=theme.panel)

    def set_running(self, running):
        self._running = running

    def _on_destroy(self, _):
        self._alive = False

    def _on_resize(self, event):
        self._canvas_w = max(event.width, 50)

    def _init_particles(self):
        if not self._alive:
            return
        self._canvas_w = max(self.winfo_width(), 400)
        self.particles = []
        for _ in range(self.count):
            self.particles.append({
                "x": random.uniform(0, self._canvas_w),
                "y": random.uniform(0, self.field_height),
                "vx": random.uniform(-0.18, 0.18),
                "vy": random.uniform(-0.12, 0.12),
                "r": random.uniform(1.2, 2.6),
            })
        self._tick()

    def _tick(self):
        if not self._alive:
            return
        if self._running:
            self._step()
            self._draw()
        self.after(40, self._tick)

    def _step(self):
        w, h = self._canvas_w, self.field_height
        for p in self.particles:
            p["x"] += p["vx"]
            p["y"] += p["vy"]
            if p["x"] < 0 or p["x"] > w:
                p["vx"] *= -1
                p["x"] = max(0, min(w, p["x"]))
            if p["y"] < 0 or p["y"] > h:
                p["vy"] *= -1
                p["y"] = max(0, min(h, p["y"]))

    def _draw(self):
        self.delete("all")
        th = self.theme
        link_dist = 95
        dot_color = lerp_color(th.panel, th.accent_glow, 0.85)
        line_color = lerp_color(th.panel, th.accent, 0.35)
        n = len(self.particles)
        for i in range(n):
            a = self.particles[i]
            for j in range(i + 1, n):
                b = self.particles[j]
                dx, dy = a["x"] - b["x"], a["y"] - b["y"]
                dist = math.hypot(dx, dy)
                if dist < link_dist:
                    fade = 1 - dist / link_dist
                    if fade > 0.08:
                        self.create_line(a["x"], a["y"], b["x"], b["y"],
                                          fill=line_color, width=1)
        for p in self.particles:
            self.create_oval(p["x"] - p["r"], p["y"] - p["r"],
                              p["x"] + p["r"], p["y"] + p["r"],
                              fill=dot_color, outline="")


# ------------------------------------------------------------------
# One-shot particle burst overlay (celebratory effect on success)
# ------------------------------------------------------------------
class ParticleBurst:
    def __init__(self, root, theme):
        self.root = root
        self.theme = theme

    def play(self, cx=None, cy=None, count=26):
        th = self.theme
        rw, rh = self.root.winfo_width(), self.root.winfo_height()
        if cx is None:
            cx = rw / 2
        if cy is None:
            cy = rh / 2

        overlay = tk.Toplevel(self.root)
        overlay.overrideredirect(True)
        overlay.attributes("-topmost", True)
        try:
            overlay.attributes("-alpha", 0.92)
        except tk.TclError:
            pass
        overlay.geometry(f"{rw}x{rh}+{self.root.winfo_rootx()}+{self.root.winfo_rooty()}")
        canvas = tk.Canvas(overlay, width=rw, height=rh, bg=th.bg, highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        try:
            overlay.wm_attributes("-transparentcolor", th.bg)
        except tk.TclError:
            pass

        colors = [th.accent, th.accent_glow, th.good, th.warn]
        particles = []
        for _ in range(count):
            angle = random.uniform(0, 2 * math.pi)
            speed = random.uniform(2.2, 6.0)
            particles.append({
                "x": cx, "y": cy,
                "vx": math.cos(angle) * speed,
                "vy": math.sin(angle) * speed - 1.5,
                "r": random.uniform(2.5, 5.0),
                "color": random.choice(colors),
                "life": 1.0,
            })

        start = time.time()

        def step():
            if not overlay.winfo_exists():
                return
            elapsed = time.time() - start
            canvas.delete("all")
            alive_any = False
            for p in particles:
                p["x"] += p["vx"]
                p["y"] += p["vy"]
                p["vy"] += 0.18
                p["life"] -= 0.018
                if p["life"] > 0:
                    alive_any = True
                    r = p["r"] * max(0.15, p["life"])
                    canvas.create_oval(p["x"] - r, p["y"] - r, p["x"] + r, p["y"] + r,
                                        fill=p["color"], outline="")
            if alive_any and elapsed < 2.2:
                overlay.after(16, step)
            else:
                overlay.destroy()

        step()


class MemoryOptimizerApp(tk.Tk):
    TABS = ["dashboard", "processes", "gameboost", "settings"]

    def __init__(self):
        super().__init__()
        self.cfg = load_config()
        self.theme = LightTheme if self.cfg["theme"] == "light" else DarkTheme

        self.title("Memory Optimizer Pro")
        self.geometry("760x620")
        self.minsize(720, 580)
        self.configure(bg=self.theme.bg)

        self.auto_enabled = tk.BooleanVar(value=self.cfg["auto_enabled"])
        self.idle_only = tk.BooleanVar(value=self.cfg["idle_only"])
        self.threshold = tk.IntVar(value=self.cfg["threshold"])
        self.start_with_windows = tk.BooleanVar(value=self.cfg["start_with_windows"])

        self._last_auto_run = 0
        self._tray_icon = None
        self.current_tab = "dashboard"
        self._themed_widgets = []

        # Game Boost state
        self.boost_target = tk.StringVar(value=self.cfg.get("boost_target", "BlueStacks"))
        self.boost_custom_exe = tk.StringVar(value=self.cfg.get("boost_custom_exe", ""))
        self.boost_priority = tk.BooleanVar(value=True)
        self.boost_free_ram = tk.BooleanVar(value=True)
        self.boost_power_plan = tk.BooleanVar(value=True)
        self.boost_disable_fso = tk.BooleanVar(value=False)
        self.bg_app_vars = {}
        self._proc_sort_col = "mem"
        self._proc_sort_reverse = True

        self.toasts = ToastManager(self, self.theme)
        self.bursts = ParticleBurst(self, self.theme)

        self._build_layout()
        self._show_tab("dashboard", animate=False)
        self._start_background_threads()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._fade_in_window()

    def _fade_in_window(self):
        try:
            self.attributes("-alpha", 0.0)
        except tk.TclError:
            return
        start = time.time()
        duration = 0.35

        def step():
            t = min(1.0, (time.time() - start) / duration)
            try:
                self.attributes("-alpha", ease_out_cubic(t))
            except tk.TclError:
                return
            if t < 1.0:
                self.after(16, step)

        step()

    # ---------------- Layout scaffold ----------------
    def _build_layout(self):
        self.sidebar = tk.Frame(self, bg=self.theme.sidebar, width=210)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        logo = tk.Frame(self.sidebar, bg=self.theme.sidebar)
        logo.pack(fill="x", pady=(26, 20), padx=20)
        tk.Label(logo, text="\u25C9 Memory", font=("Segoe UI", 15, "bold"),
                 fg=self.theme.text, bg=self.theme.sidebar).pack(anchor="w")
        tk.Label(logo, text="Optimizer Pro", font=("Segoe UI", 10),
                 fg=self.theme.subtext, bg=self.theme.sidebar).pack(anchor="w")

        self.nav_items = {}
        nav_defs = [("dashboard", "\u25A6", "Dashboard"),
                    ("processes", "\u2699", "Processes"),
                    ("gameboost", "\u26A1", "Game Boost"),
                    ("settings", "\u2699\uFE0E", "Settings")]
        for key, icon, label in nav_defs:
            item = NavItem(self.sidebar, icon, label, self.theme,
                            on_click=lambda k=key: self._show_tab(k), width=210)
            item.pack(fill="x")
            self.nav_items[key] = item

        self.sidebar_status = tk.Label(self.sidebar, text="", font=("Segoe UI", 8),
                                        fg=self.theme.subtext, bg=self.theme.sidebar,
                                        wraplength=180, justify="left")
        self.sidebar_status.pack(side="bottom", pady=16, padx=20, anchor="w")
        self._update_sidebar_status()

        self.content_container = tk.Frame(self, bg=self.theme.bg)
        self.content_container.pack(side="left", fill="both", expand=True)

        self.tab_frames = {
            "dashboard": self._build_dashboard_tab(),
            "processes": self._build_processes_tab(),
            "gameboost": self._build_game_boost_tab(),
            "settings": self._build_settings_tab(),
        }
        for frame in self.tab_frames.values():
            frame.place(in_=self.content_container, relx=1, rely=0, relwidth=1, relheight=1)

    def _update_sidebar_status(self):
        txt = "Windows" if IS_WINDOWS else f"{platform.system()} (limited mode)"
        tray = "Tray: on" if TRAY_AVAILABLE else "Tray: unavailable"
        self.sidebar_status.config(text=f"{txt}\n{tray}\n\n\u00A9 jatintyagi07\nAll rights reserved.")

    def _make_scrollable(self):
        """Wraps a tab's content in a vertically scrollable canvas so the
        window can be resized smaller than the content without clipping it.
        Returns (outer_frame_to_register_as_the_tab, inner_frame_to_build_into)."""
        th = self.theme
        outer = tk.Frame(self.content_container, bg=th.bg)
        canvas = tk.Canvas(outer, bg=th.bg, highlightthickness=0)
        vscroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=th.bg)
        inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=vscroll.set)

        def on_inner_configure(_event):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def on_canvas_configure(event):
            canvas.itemconfig(inner_id, width=event.width)

        inner.bind("<Configure>", on_inner_configure)
        canvas.bind("<Configure>", on_canvas_configure)

        def on_mousewheel(event):
            if getattr(event, "num", None) == 4:
                canvas.yview_scroll(-1, "units")
            elif getattr(event, "num", None) == 5:
                canvas.yview_scroll(1, "units")
            elif getattr(event, "delta", 0):
                canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

        def bind_wheel(_event):
            canvas.bind_all("<MouseWheel>", on_mousewheel)
            canvas.bind_all("<Button-4>", on_mousewheel)
            canvas.bind_all("<Button-5>", on_mousewheel)

        def unbind_wheel(_event):
            canvas.unbind_all("<MouseWheel>")
            canvas.unbind_all("<Button-4>")
            canvas.unbind_all("<Button-5>")

        canvas.bind("<Enter>", bind_wheel)
        canvas.bind("<Leave>", unbind_wheel)

        canvas.pack(side="left", fill="both", expand=True)
        vscroll.pack(side="right", fill="y")
        return outer, inner

    def _accent_strip(self, card, height=3):
        """Adds an animated gradient bar across the top of a card for a
        premium, 'alive' feel."""
        strip = AccentStrip(card, self.theme, height=height)
        strip.pack(fill="x", side="top")
        return strip

    def _add_card_hover(self, card, base_border=None):
        """Gives a card a soft glow border on hover — a small but very
        noticeable 'this app is polished' touch."""
        th = self.theme
        base = base_border or lerp_color(th.track, th.accent, 0.4)
        glow = lerp_color(th.track, th.accent, 0.9)

        def on_enter(_):
            try:
                card.configure(highlightbackground=glow, highlightthickness=2)
            except tk.TclError:
                pass

        def on_leave(_):
            try:
                card.configure(highlightbackground=base, highlightthickness=1)
            except tk.TclError:
                pass

        card.bind("<Enter>", on_enter)
        card.bind("<Leave>", on_leave)

    # ---------------- Dashboard tab ----------------
    def _build_dashboard_tab(self):
        outer, f = self._make_scrollable()

        hero = tk.Frame(f, bg=self.theme.panel, highlightthickness=0)
        hero.pack(fill="x", padx=28, pady=(20, 0))
        self.dashboard_particles = ParticleField(hero, self.theme, height=104, count=22)
        self.dashboard_particles.pack(fill="x")
        hero_text = tk.Frame(hero, bg=self.theme.panel)
        hero_text.place(x=20, y=18)
        tk.Label(hero_text, text="Dashboard", font=("Segoe UI", 18, "bold"),
                 fg=self.theme.text, bg=self.theme.panel).pack(anchor="w")
        tk.Label(hero_text, text="Live memory status and one-click actions.",
                 font=("Segoe UI", 10), fg=self.theme.subtext, bg=self.theme.panel).pack(anchor="w")

        card = tk.Frame(f, bg=self.theme.panel, highlightbackground=lerp_color(self.theme.track, self.theme.accent, 0.4), highlightthickness=1)
        card.pack(padx=28, pady=14, fill="x")
        self._accent_strip(card)
        self._add_card_hover(card)
        top_row = tk.Frame(card, bg=self.theme.panel)
        top_row.pack(fill="x", padx=20, pady=20)

        gauge_col = tk.Frame(top_row, bg=self.theme.panel)
        gauge_col.pack(side="left")
        self.gauge = Gauge(gauge_col, self.theme, size=190)
        self.gauge.pack()

        info_col = tk.Frame(top_row, bg=self.theme.panel)
        info_col.pack(side="left", fill="both", expand=True, padx=(24, 0))

        self.detail_label = tk.Label(info_col, text="", font=("Segoe UI", 11),
                                      fg=self.theme.text, bg=self.theme.panel, justify="left")
        self.detail_label.pack(anchor="w", pady=(4, 10))

        tk.Label(info_col, text="Usage — last few minutes", font=("Segoe UI", 9),
                 fg=self.theme.subtext, bg=self.theme.panel).pack(anchor="w")
        self.history_graph = HistoryGraph(info_col, self.theme, width=320, height=80)
        self.history_graph.pack(anchor="w", pady=(4, 0))

        btn_row = tk.Frame(card, bg=self.theme.panel)
        btn_row.pack(pady=(0, 20))
        self.optimize_btn = RoundedButton(btn_row, "\u26A1  Optimize Now", self._on_optimize_click,
                                           self.theme, width=200, height=46, kind="primary")
        self.optimize_btn.grid(row=0, column=0, padx=6)
        self.standby_btn = RoundedButton(btn_row, "Clear Standby List", self._on_standby_click,
                                          self.theme, width=200, height=46, kind="secondary")
        self.standby_btn.grid(row=0, column=1, padx=6)
        if not IS_WINDOWS:
            self.optimize_btn.set_enabled(False)
            self.standby_btn.set_enabled(False)

        auto_card = tk.Frame(f, bg=self.theme.panel, highlightbackground=lerp_color(self.theme.track, self.theme.accent, 0.4), highlightthickness=1)
        auto_card.pack(padx=28, pady=10, fill="x")
        self._accent_strip(auto_card)
        self._add_card_hover(auto_card)
        acp = tk.Frame(auto_card, bg=self.theme.panel)
        acp.pack(fill="x", padx=20, pady=16)
        tk.Label(acp, text="Auto-Optimize is " , font=("Segoe UI", 10),
                 fg=self.theme.subtext, bg=self.theme.panel).pack(side="left")
        self.auto_status_label = tk.Label(acp, text="OFF", font=("Segoe UI", 10, "bold"),
                                           fg=self.theme.danger, bg=self.theme.panel)
        self.auto_status_label.pack(side="left")
        tk.Label(acp, text="  — configure it in Settings.", font=("Segoe UI", 10),
                 fg=self.theme.subtext, bg=self.theme.panel).pack(side="left")
        self._refresh_auto_status_label()

        return outer

    # ---------------- Processes tab ----------------
    def _build_processes_tab(self):
        outer, f = self._make_scrollable()
        header = tk.Frame(f, bg=self.theme.bg)
        header.pack(fill="x", padx=28, pady=(26, 6))
        tk.Label(header, text="Processes", font=("Segoe UI", 18, "bold"),
                 fg=self.theme.text, bg=self.theme.bg).pack(anchor="w")
        tk.Label(header, text="Biggest memory consumers right now.",
                 font=("Segoe UI", 10), fg=self.theme.subtext, bg=self.theme.bg).pack(anchor="w")

        card = tk.Frame(f, bg=self.theme.panel, highlightbackground=lerp_color(self.theme.track, self.theme.accent, 0.4), highlightthickness=1)
        card.pack(padx=28, pady=14, fill="both", expand=True)
        self._accent_strip(card)
        self._add_card_hover(card)

        style = ttk.Style(self)
        style.theme_use("clam")
        self._style_treeview(style)

        columns = ("name", "pid", "mem", "cpu")
        self.proc_tree = ttk.Treeview(card, columns=columns, show="headings", height=14)
        headings = {"name": "Process", "pid": "PID", "mem": "Memory (MB)", "cpu": "CPU %"}
        for col, text in headings.items():
            self.proc_tree.heading(col, text=text, command=lambda c=col: self._sort_process_list(c))
        self.proc_tree.column("name", width=230, anchor="w")
        self.proc_tree.column("pid", width=80, anchor="center")
        self.proc_tree.column("mem", width=120, anchor="e")
        self.proc_tree.column("cpu", width=80, anchor="e")
        self.proc_tree.pack(fill="both", expand=True, padx=16, pady=(16, 8))

        refresh_row = tk.Frame(card, bg=self.theme.panel)
        refresh_row.pack(pady=(0, 16))
        self.refresh_btn = RoundedButton(refresh_row, "Refresh", self._refresh_process_list,
                                          self.theme, width=140, height=38, kind="secondary")
        self.refresh_btn.grid(row=0, column=0, padx=6)
        self.kill_btn = RoundedButton(refresh_row, "Close Selected", self._on_kill_click,
                                       self.theme, width=160, height=38, kind="danger")
        self.kill_btn.grid(row=0, column=1, padx=6)

        self._process_cache = []
        self._refresh_process_list()
        return outer

    def _style_treeview(self, style):
        th = self.theme
        style.configure("Treeview", background=th.panel, fieldbackground=th.panel,
                         foreground=th.text, rowheight=26, borderwidth=0, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", background=th.panel_light, foreground=th.subtext,
                         font=("Segoe UI", 9, "bold"), borderwidth=0)
        style.map("Treeview", background=[("selected", th.accent)], foreground=[("selected", th.bg)])

    def _refresh_process_list(self):
        def work():
            top = get_top_processes(20)
            def apply():
                if not hasattr(self, "proc_tree"):
                    return
                self._process_cache = top
                self._render_process_list()
            self.after(0, apply)
        threading.Thread(target=work, daemon=True).start()

    def _render_process_list(self):
        col_idx = {"name": 0, "pid": 1, "mem": 2, "cpu": 3}
        idx = col_idx[self._proc_sort_col]
        rows = sorted(self._process_cache, key=lambda r: r[idx], reverse=self._proc_sort_reverse)
        self.proc_tree.delete(*self.proc_tree.get_children())
        for name, pid, mem, cpu in rows:
            self.proc_tree.insert("", "end", values=(name, pid, f"{mem:,.1f}", f"{cpu:.1f}"))

    def _sort_process_list(self, col):
        if self._proc_sort_col == col:
            self._proc_sort_reverse = not self._proc_sort_reverse
        else:
            self._proc_sort_col = col
            self._proc_sort_reverse = col in ("mem", "cpu")
        self._render_process_list()

    def _on_kill_click(self):
        sel = self.proc_tree.selection()
        if not sel:
            self.toasts.show("Select a process in the list first.", "info")
            return
        values = self.proc_tree.item(sel[0], "values")
        name, pid = values[0], int(values[1])
        ok, msg = kill_process(pid)
        self.toasts.show(msg, "success" if ok else "error")
        self._refresh_process_list()

    # ---------------- Game Boost tab ----------------
    def _build_game_boost_tab(self):
        outer, f = self._make_scrollable()
        header = tk.Frame(f, bg=self.theme.bg)
        header.pack(fill="x", padx=28, pady=(26, 6))
        tk.Label(header, text="Game Boost", font=("Segoe UI", 18, "bold"),
                 fg=self.theme.text, bg=self.theme.bg).pack(anchor="w")
        tk.Label(header, text="Tune Windows for smoother FPS while an emulator or game is running.",
                 font=("Segoe UI", 10), fg=self.theme.subtext, bg=self.theme.bg).pack(anchor="w")

        # --- Target picker ---
        card = tk.Frame(f, bg=self.theme.panel, highlightbackground=lerp_color(self.theme.track, self.theme.accent, 0.4), highlightthickness=1)
        card.pack(padx=28, pady=12, fill="x")
        self._accent_strip(card)
        self._add_card_hover(card)
        pad = tk.Frame(card, bg=self.theme.panel)
        pad.pack(fill="x", padx=20, pady=18)

        tk.Label(pad, text="Target", font=("Segoe UI", 12, "bold"),
                 fg=self.theme.text, bg=self.theme.panel).grid(row=0, column=0, sticky="w", columnspan=2)

        self.boost_target_menu = ttk.Combobox(
            pad, textvariable=self.boost_target, state="readonly",
            values=list(KNOWN_EMULATORS.keys()) + ["Custom..."], width=28)
        self.boost_target_menu.grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.boost_target_menu.bind("<<ComboboxSelected>>", lambda e: self._on_boost_target_changed())

        self.boost_custom_entry = tk.Entry(pad, textvariable=self.boost_custom_exe, width=26,
                                            bg=self.theme.panel_light, fg=self.theme.text,
                                            insertbackground=self.theme.text, relief="flat")
        self.boost_custom_entry.grid(row=1, column=1, sticky="w", padx=(10, 0), pady=(10, 0))

        self.boost_status_label = tk.Label(pad, text="", font=("Segoe UI", 9),
                                            fg=self.theme.subtext, bg=self.theme.panel)
        self.boost_status_label.grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))

        self.detect_btn = RoundedButton(pad, "Detect", self._on_detect_click,
                                         self.theme, width=110, height=34, kind="secondary")
        self.detect_btn.grid(row=1, column=2, sticky="w", padx=(10, 0), pady=(10, 0))

        self._on_boost_target_changed(save=False)

        # --- Actions ---
        card2 = tk.Frame(f, bg=self.theme.panel, highlightbackground=lerp_color(self.theme.track, self.theme.accent, 0.4), highlightthickness=1)
        card2.pack(padx=28, pady=10, fill="x")
        self._accent_strip(card2)
        self._add_card_hover(card2)
        pad2 = tk.Frame(card2, bg=self.theme.panel)
        pad2.pack(fill="x", padx=20, pady=18)

        tk.Label(pad2, text="Boost actions", font=("Segoe UI", 12, "bold"),
                 fg=self.theme.text, bg=self.theme.panel).grid(row=0, column=0, sticky="w", columnspan=2)

        checks = [
            (self.boost_priority, "Raise the app's process priority"),
            (self.boost_free_ram, "Free RAM from other apps (skips the target)"),
            (self.boost_power_plan, "Switch Windows to High Performance power plan"),
            (self.boost_disable_fso, "Disable Fullscreen Optimizations for the target"),
        ]
        for i, (var, label) in enumerate(checks):
            tk.Checkbutton(pad2, variable=var, text=label, bg=self.theme.panel,
                            activebackground=self.theme.panel, selectcolor=self.theme.track,
                            fg=self.theme.text, font=("Segoe UI", 10)).grid(
                row=1 + i, column=0, columnspan=2, sticky="w", pady=(8 if i == 0 else 2, 0))

        btn_row2 = tk.Frame(card2, bg=self.theme.panel)
        btn_row2.pack(anchor="w", pady=(16, 0))
        self.boost_btn = RoundedButton(btn_row2, "\u26A1  Boost Now", self._on_boost_click,
                                        self.theme, width=170, height=42, kind="primary")
        self.boost_btn.grid(row=0, column=0, padx=(0, 8))
        self.boost_revert_btn = RoundedButton(btn_row2, "Revert to Normal", self._on_revert_click,
                                               self.theme, width=170, height=42, kind="secondary")
        self.boost_revert_btn.grid(row=0, column=1)
        if not IS_WINDOWS:
            self.boost_btn.set_enabled(False)
            self.boost_revert_btn.set_enabled(False)

        # --- Background apps ---
        card3 = tk.Frame(f, bg=self.theme.panel, highlightbackground=lerp_color(self.theme.track, self.theme.accent, 0.4), highlightthickness=1)
        card3.pack(padx=28, pady=10, fill="both", expand=True)
        self._accent_strip(card3)
        self._add_card_hover(card3)
        pad3 = tk.Frame(card3, bg=self.theme.panel)
        pad3.pack(fill="both", expand=True, padx=20, pady=18)

        tk.Label(pad3, text="Close background apps first (optional)", font=("Segoe UI", 12, "bold"),
                 fg=self.theme.text, bg=self.theme.panel).pack(anchor="w")
        tk.Label(pad3, text="Only apps you check below are closed — nothing happens automatically.",
                 font=("Segoe UI", 9), fg=self.theme.subtext, bg=self.theme.panel).pack(anchor="w", pady=(2, 8))

        self.bg_apps_frame = tk.Frame(pad3, bg=self.theme.panel)
        self.bg_apps_frame.pack(fill="x")
        self._refresh_bg_apps_list()

        self.close_bg_btn = RoundedButton(pad3, "Close Checked Apps", self._on_close_bg_apps,
                                           self.theme, width=180, height=36, kind="danger")
        self.close_bg_btn.pack(anchor="w", pady=(12, 0))

        note = ("Tip: BlueStacks / MSI App Player's own FPS cap and CPU/RAM allocation live in "
                "its Settings > Performance panel — raise those there for the biggest gains; "
                "this tab optimizes Windows around it.")
        tk.Label(f, text=note, font=("Segoe UI", 9), fg=self.theme.subtext, bg=self.theme.bg,
                 wraplength=560, justify="left").pack(anchor="w", padx=28, pady=(10, 20))

        return outer

    def _current_target_exes(self):
        choice = self.boost_target.get()
        if choice == "Custom...":
            custom = self.boost_custom_exe.get().strip()
            return [custom] if custom else []
        return KNOWN_EMULATORS.get(choice, [])

    def _on_boost_target_changed(self, save=True):
        is_custom = self.boost_target.get() == "Custom..."
        self.boost_custom_entry.config(state="normal" if is_custom else "disabled")
        if save:
            self.cfg["boost_target"] = self.boost_target.get()
            save_config(self.cfg)
        self.boost_status_label.config(text="Click Detect to check if it's running.")

    def _on_detect_click(self):
        exes = self._current_target_exes()
        if not exes:
            self.boost_status_label.config(text="Enter an .exe name first.")
            return
        matches = find_target_processes(exes)
        if matches:
            names = ", ".join(f"{n} (PID {p})" for p, n, _ in matches)
            self.boost_status_label.config(text=f"Running: {names}", fg=self.theme.good)
        else:
            self.boost_status_label.config(text="Not detected — make sure it's running.",
                                             fg=self.theme.subtext)

    def _on_boost_click(self):
        exes = self._current_target_exes()
        if not exes:
            self.toasts.show("Enter or pick a target app first.", "info")
            return
        if self.boost_target.get() == "Custom...":
            self.cfg["boost_custom_exe"] = self.boost_custom_exe.get().strip()
            save_config(self.cfg)
        self.boost_btn.set_enabled(False)
        self.boost_btn.set_text("Boosting...")
        threading.Thread(target=self._run_boost, args=(exes,), daemon=True).start()

    def _run_boost(self, exes):
        summary, targets = apply_game_boost(
            exes,
            do_priority=self.boost_priority.get(),
            do_free_ram=self.boost_free_ram.get(),
            do_power_plan=self.boost_power_plan.get(),
            do_disable_fso=self.boost_disable_fso.get(),
        )
        def finish():
            self.boost_btn.set_enabled(True)
            self.boost_btn.set_text("\u26A1  Boost Now")
            kind = "success" if targets else "info"
            self.toasts.show(" ".join(summary), kind)
            if targets:
                self._burst_from_widget(self.boost_btn)
        self.after(0, finish)

    def _on_revert_click(self):
        self.boost_revert_btn.set_enabled(False)
        threading.Thread(target=self._run_revert, daemon=True).start()

    def _run_revert(self):
        summary = revert_game_boost()
        def finish():
            self.boost_revert_btn.set_enabled(True)
            self.toasts.show(" ".join(summary), "info")
        self.after(0, finish)

    def _refresh_bg_apps_list(self):
        for w in self.bg_apps_frame.winfo_children():
            w.destroy()
        self.bg_app_vars = {}
        running = {p.info["name"].lower() for p in psutil.process_iter(["name"]) if p.info["name"]}
        cols = 3
        row = col = 0
        for exe in COMMON_BACKGROUND_APPS:
            if exe not in running:
                continue
            var = tk.BooleanVar(value=False)
            self.bg_app_vars[exe] = var
            tk.Checkbutton(self.bg_apps_frame, variable=var, text=exe.replace(".exe", "").title(),
                           bg=self.theme.panel, activebackground=self.theme.panel,
                           selectcolor=self.theme.track, fg=self.theme.text,
                           font=("Segoe UI", 10)).grid(row=row, column=col, sticky="w", padx=(0, 16))
            col += 1
            if col >= cols:
                col = 0
                row += 1
        if not self.bg_app_vars:
            tk.Label(self.bg_apps_frame, text="None of the common background apps are running right now.",
                      font=("Segoe UI", 9), fg=self.theme.subtext, bg=self.theme.panel).grid(row=0, column=0, sticky="w")

    def _on_close_bg_apps(self):
        to_close = [exe for exe, var in self.bg_app_vars.items() if var.get()]
        if not to_close:
            self.toasts.show("Check at least one app to close.", "info")
            return
        def work():
            closed = 0
            for exe in to_close:
                for p in psutil.process_iter(["pid", "name"]):
                    if (p.info["name"] or "").lower() == exe:
                        ok, _ = kill_process(p.info["pid"])
                        closed += 1 if ok else 0
            def finish():
                self.toasts.show(f"Closed {closed} app(s).", "success")
                self._refresh_bg_apps_list()
            self.after(0, finish)
        threading.Thread(target=work, daemon=True).start()

    # ---------------- Settings tab ----------------
    def _build_settings_tab(self):
        outer, f = self._make_scrollable()
        header = tk.Frame(f, bg=self.theme.bg)
        header.pack(fill="x", padx=28, pady=(26, 6))
        tk.Label(header, text="Settings", font=("Segoe UI", 18, "bold"),
                 fg=self.theme.text, bg=self.theme.bg).pack(anchor="w")
        tk.Label(header, text="Tune automatic behavior and appearance.",
                 font=("Segoe UI", 10), fg=self.theme.subtext, bg=self.theme.bg).pack(anchor="w")

        card = tk.Frame(f, bg=self.theme.panel, highlightbackground=lerp_color(self.theme.track, self.theme.accent, 0.4), highlightthickness=1)
        card.pack(padx=28, pady=14, fill="x")
        self._accent_strip(card)
        self._add_card_hover(card)
        pad = tk.Frame(card, bg=self.theme.panel)
        pad.pack(fill="x", padx=20, pady=18)

        tk.Label(pad, text="Auto-Optimize", font=("Segoe UI", 12, "bold"),
                 fg=self.theme.text, bg=self.theme.panel).grid(row=0, column=0, sticky="w")
        self.auto_switch = tk.Checkbutton(
            pad, variable=self.auto_enabled, bg=self.theme.panel, activebackground=self.theme.panel,
            selectcolor=self.theme.track, fg=self.theme.text, text="Enabled", font=("Segoe UI", 10),
            command=self._on_setting_changed)
        self.auto_switch.grid(row=0, column=1, sticky="e", padx=(40, 0))

        tk.Label(pad, text="Trigger when available memory drops below:",
                 font=("Segoe UI", 9), fg=self.theme.subtext, bg=self.theme.panel).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(16, 0))

        slider_row = tk.Frame(pad, bg=self.theme.panel)
        slider_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self.threshold_label = tk.Label(slider_row, text=f"{self.threshold.get()}%", font=("Segoe UI", 10, "bold"),
                                         fg=self.theme.accent, bg=self.theme.panel, width=5)
        self.threshold_label.pack(side="right")
        self.threshold_slider = tk.Scale(
            slider_row, from_=5, to=50, orient="horizontal", variable=self.threshold,
            showvalue=False, bg=self.theme.panel, fg=self.theme.text, troughcolor=self.theme.track,
            highlightthickness=0, sliderrelief="flat", command=self._on_threshold_change)
        self.threshold_slider.pack(side="left", fill="x", expand=True)

        self.idle_check = tk.Checkbutton(
            pad, variable=self.idle_only, text="Only run when CPU is idle (< 15% usage)",
            bg=self.theme.panel, activebackground=self.theme.panel, selectcolor=self.theme.track,
            fg=self.theme.subtext, font=("Segoe UI", 9), command=self._on_setting_changed)
        self.idle_check.grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))

        card2 = tk.Frame(f, bg=self.theme.panel, highlightbackground=lerp_color(self.theme.track, self.theme.accent, 0.4), highlightthickness=1)
        card2.pack(padx=28, pady=10, fill="x")
        self._accent_strip(card2)
        self._add_card_hover(card2)
        pad2 = tk.Frame(card2, bg=self.theme.panel)
        pad2.pack(fill="x", padx=20, pady=18)

        tk.Label(pad2, text="Appearance & Startup", font=("Segoe UI", 12, "bold"),
                 fg=self.theme.text, bg=self.theme.panel).grid(row=0, column=0, columnspan=2, sticky="w")

        tk.Label(pad2, text="Theme", font=("Segoe UI", 10), fg=self.theme.subtext,
                 bg=self.theme.panel).grid(row=1, column=0, sticky="w", pady=(14, 0))
        theme_row = tk.Frame(pad2, bg=self.theme.panel)
        theme_row.grid(row=1, column=1, sticky="e", pady=(14, 0))
        self.theme_toggle_btn = RoundedButton(
            theme_row, "\u2600 Light" if self.theme.name == "dark" else "\u263D Dark",
            self._toggle_theme, self.theme, width=110, height=32, kind="secondary")
        self.theme_toggle_btn.pack()

        self.startup_check = tk.Checkbutton(
            pad2, variable=self.start_with_windows, text="Launch automatically at Windows startup",
            bg=self.theme.panel, activebackground=self.theme.panel, selectcolor=self.theme.track,
            fg=self.theme.text if IS_WINDOWS else self.theme.subtext, font=("Segoe UI", 10),
            command=self._on_startup_toggle, state="normal" if IS_WINDOWS else "disabled")
        self.startup_check.grid(row=2, column=0, columnspan=2, sticky="w", pady=(16, 0))

        return outer

    # ---------------- Tab switching (animated) ----------------
    def _show_tab(self, key, animate=True):
        if key == self.current_tab and animate:
            return
        for k, item in self.nav_items.items():
            item.set_active(k == key)

        outgoing = self.tab_frames[self.current_tab]
        incoming = self.tab_frames[key]
        direction = 1 if self.TABS.index(key) > self.TABS.index(self.current_tab) else -1
        self.current_tab = key

        if hasattr(self, "dashboard_particles"):
            self.dashboard_particles.set_running(key == "dashboard")

        if not animate:
            incoming.place(relx=0, rely=0, relwidth=1, relheight=1)
            outgoing.place_forget() if outgoing is not incoming else None
            return

        incoming.place(relx=direction, rely=0, relwidth=1, relheight=1)
        incoming.lift()
        start = time.time()
        duration = 0.28

        def step():
            t = min(1.0, (time.time() - start) / duration)
            e = ease_in_out_quad(t)
            outgoing.place_configure(relx=-direction * e)
            incoming.place_configure(relx=direction * (1 - e))
            if t < 1.0:
                self.after(12, step)
            else:
                outgoing.place_forget()
                if key == "processes":
                    self._refresh_process_list()

        step()

    # ---------------- Stats / background loops ----------------
    def _start_background_threads(self):
        self._refresh_stats()
        threading.Thread(target=self._auto_optimize_loop, daemon=True).start()
        if TRAY_AVAILABLE:
            self._setup_tray()

    def _refresh_stats(self):
        vm = psutil.virtual_memory()
        used_gb = (vm.total - vm.available) / (1024 ** 3)
        total_gb = vm.total / (1024 ** 3)
        avail_gb = vm.available / (1024 ** 3)

        self.gauge.set_value(vm.percent)
        self.history_graph.push(vm.percent)
        self.detail_label.config(
            text=f"Used:        {used_gb:.1f} GB\nAvailable:  {avail_gb:.1f} GB\nTotal:        {total_gb:.1f} GB"
        )
        self.after(2000, self._refresh_stats)

    def _refresh_auto_status_label(self):
        on = self.auto_enabled.get()
        self.auto_status_label.config(text="ON" if on else "OFF",
                                       fg=self.theme.good if on else self.theme.danger)

    def _on_threshold_change(self, val):
        self.threshold_label.config(text=f"{int(float(val))}%")
        self._on_setting_changed()

    def _on_setting_changed(self):
        self.cfg.update({
            "auto_enabled": self.auto_enabled.get(),
            "idle_only": self.idle_only.get(),
            "threshold": self.threshold.get(),
        })
        save_config(self.cfg)
        self._refresh_auto_status_label()

    def _on_startup_toggle(self):
        enabled = self.start_with_windows.get()
        set_start_with_windows(enabled)
        self.cfg["start_with_windows"] = enabled
        save_config(self.cfg)
        self.toasts.show("Startup setting updated." if IS_WINDOWS else
                          "Startup toggle only works on Windows.", "info")

    # ---------------- Theme toggle ----------------
    def _toggle_theme(self):
        self.theme = LightTheme if self.theme.name == "dark" else DarkTheme
        self.cfg["theme"] = self.theme.name
        save_config(self.cfg)
        self._apply_theme_everywhere()

    def _apply_theme_everywhere(self):
        th = self.theme
        self.configure(bg=th.bg)
        self.sidebar.configure(bg=th.sidebar)
        self.content_container.configure(bg=th.bg)
        self.toasts.set_theme(th)

        # simplest reliable approach: rebuild the tabs with new theme
        old_tab = self.current_tab
        for frame in self.tab_frames.values():
            frame.destroy()
        for item in self.nav_items.values():
            item.destroy()
        for w in self.sidebar.winfo_children():
            w.destroy()

        logo = tk.Frame(self.sidebar, bg=th.sidebar)
        logo.pack(fill="x", pady=(26, 20), padx=20)
        tk.Label(logo, text="\u25C9 Memory", font=("Segoe UI", 15, "bold"),
                 fg=th.text, bg=th.sidebar).pack(anchor="w")
        tk.Label(logo, text="Optimizer Pro", font=("Segoe UI", 10),
                 fg=th.subtext, bg=th.sidebar).pack(anchor="w")

        self.nav_items = {}
        nav_defs = [("dashboard", "\u25A6", "Dashboard"),
                    ("processes", "\u2699", "Processes"),
                    ("gameboost", "\u26A1", "Game Boost"),
                    ("settings", "\u2699\uFE0E", "Settings")]
        for key, icon, label in nav_defs:
            item = NavItem(self.sidebar, icon, label, th,
                            on_click=lambda k=key: self._show_tab(k), width=210)
            item.pack(fill="x")
            self.nav_items[key] = item

        self.sidebar_status = tk.Label(self.sidebar, text="", font=("Segoe UI", 8),
                                        fg=th.subtext, bg=th.sidebar, wraplength=180, justify="left")
        self.sidebar_status.pack(side="bottom", pady=16, padx=20, anchor="w")
        self._update_sidebar_status()

        self.tab_frames = {
            "dashboard": self._build_dashboard_tab(),
            "processes": self._build_processes_tab(),
            "gameboost": self._build_game_boost_tab(),
            "settings": self._build_settings_tab(),
        }
        for frame in self.tab_frames.values():
            frame.place(in_=self.content_container, relx=1, rely=0, relwidth=1, relheight=1)
        self.current_tab = old_tab
        self._show_tab(old_tab, animate=False)
        self.toasts.show("Theme updated.", "info")

    # ---------------- Actions ----------------
    def _on_optimize_click(self):
        self.optimize_btn.set_enabled(False)
        self.optimize_btn.set_text("Optimizing...")
        threading.Thread(target=self._run_optimize, daemon=True).start()

    def _run_optimize(self, silent=False):
        before = psutil.virtual_memory().available
        if IS_WINDOWS:
            try:
                ok, fail = trim_process_working_sets()
                msg = f"Trimmed {ok} processes ({fail} skipped)."
                kind = "success"
            except Exception as e:
                msg = f"Error: {e}"
                kind = "error"
        else:
            msg = "Trimming requires Windows."
            kind = "error"
        after = psutil.virtual_memory().available
        freed_gb = max(0, after - before) / (1024 ** 3)

        def finish():
            self.optimize_btn.set_enabled(True)
            self.optimize_btn.set_text("\u26A1  Optimize Now")
            full_msg = f"{msg} Freed ~{freed_gb:.2f} GB." if IS_WINDOWS else msg
            self.toasts.show(full_msg, kind)
            if kind == "success":
                self._burst_from_widget(self.optimize_btn)
            if self._tray_icon:
                self._tray_icon.title = f"Memory Optimizer — {psutil.virtual_memory().percent:.0f}% used"

        self.after(0, finish)

    def _on_standby_click(self):
        self.standby_btn.set_enabled(False)
        self.standby_btn.set_text("Clearing...")
        threading.Thread(target=self._run_standby_clear, daemon=True).start()

    def _run_standby_clear(self):
        ok, msg = empty_standby_list()

        def finish():
            self.standby_btn.set_enabled(True)
            self.standby_btn.set_text("Clear Standby List")
            self.toasts.show(msg, "success" if ok else "error")
            if ok:
                self._burst_from_widget(self.standby_btn)

        self.after(0, finish)

    def _burst_from_widget(self, widget):
        """Plays a small celebratory particle burst centered on a widget,
        in window-local coordinates (ParticleBurst expects screen-relative
        offsets from this window's top-left)."""
        try:
            wx = widget.winfo_rootx() - self.winfo_rootx() + widget.winfo_width() / 2
            wy = widget.winfo_rooty() - self.winfo_rooty() + widget.winfo_height() / 2
            self.bursts.play(cx=wx, cy=wy)
        except tk.TclError:
            pass

    # ---------------- Auto-optimize loop ----------------
    def _auto_optimize_loop(self):
        while True:
            time.sleep(5)
            if not self.auto_enabled.get() or not IS_WINDOWS:
                continue
            vm = psutil.virtual_memory()
            avail_pct = 100 - vm.percent
            if avail_pct >= self.threshold.get():
                continue
            if self.idle_only.get() and psutil.cpu_percent(interval=1) > 15:
                continue
            if time.time() - self._last_auto_run < 30:
                continue
            self._last_auto_run = time.time()
            self._run_optimize(silent=False)

    # ---------------- System tray ----------------
    def _make_tray_image(self):
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((4, 4, 60, 60), fill=(91, 140, 255, 255))
        d.ellipse((16, 16, 48, 48), fill=(15, 17, 23, 255))
        return img

    def _setup_tray(self):
        image = self._make_tray_image()
        menu = pystray.Menu(
            pystray.MenuItem("Show", self._show_from_tray, default=True),
            pystray.MenuItem("Optimize Now", lambda: self._run_optimize(silent=False)),
            pystray.MenuItem("Quit", self._quit_app),
        )
        self._tray_icon = pystray.Icon("memory_optimizer", image, "Memory Optimizer", menu)
        threading.Thread(target=self._tray_icon.run, daemon=True).start()

    def _show_from_tray(self, icon=None, item=None):
        self.after(0, self.deiconify)

    def _on_close(self):
        if TRAY_AVAILABLE and self._tray_icon:
            self.withdraw()
        else:
            self._quit_app()

    def _quit_app(self, icon=None, item=None):
        if self._tray_icon:
            self._tray_icon.stop()
        self.after(0, self.destroy)
        sys.exit(0)


def main():
    if not IS_WINDOWS:
        print("Note: full memory trimming, standby-list clearing, and startup "
              "registration require Windows. Running in limited mode.")
    app = MemoryOptimizerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
