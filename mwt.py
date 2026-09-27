#!/usr/bin/env python3
"""Unlock game window sizing via Win32 APIs.

Provides borderless/windowed fullscreen, free resize, aspect-ratio lock,
auto-maintain, and system-tray background mode. No game files are patched
and no DLL injection is used.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
from dataclasses import dataclass, replace
import sys
from pathlib import Path
from queue import Empty, SimpleQueue
from threading import Thread
import tkinter as tk
from tkinter import messagebox

from profiles import WindowProfile, default_profile_path, load_profiles, profile_key, save_profiles
from startup import StartupState, configure_startup
from ui import WindowUI

APP_NAME = "MWT遊戲視窗調整工具"
APP_VERSION = "1.5"

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
gdi32 = ctypes.windll.gdi32
shell32 = ctypes.windll.shell32
dwmapi = ctypes.windll.dwmapi

# Win32 constants
GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_FRAME_EDGES = 0x00000001 | 0x00000100 | 0x00000200 | 0x00020000
DWMWA_NCRENDERING_ENABLED = 1
DWMWA_NCRENDERING_POLICY = 2
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMNCRP_DISABLED = 1
DWMNCRP_ENABLED = 2
DWMWCP_DONOTROUND = 1
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_MAXIMIZEBOX = 0x00010000
WS_MINIMIZEBOX = 0x00020000
WS_SYSMENU = 0x00080000
WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000
WS_BORDER = 0x00800000
WS_DLGFRAME = 0x00400000
WS_MAXIMIZE = 0x01000000
SW_RESTORE = 9
SW_MAXIMIZE = 3
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040
HWND_TOP = 0
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
MONITOR_DEFAULTTONEAREST = 2
LOGPIXELSX = 88
DEFAULT_DPI = 96

# NotifyIcon (system tray) constants
NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NIF_TIP = 0x04
WM_APP = 0x8000
WM_TRAY = WM_APP + 1
WM_TRAY_SHOWMENU = WM_APP + 2
WM_NULL = 0x0000
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B
WM_POWERBROADCAST = 0x0218
PBT_APMRESUMEAUTOMATIC = 0x0012
PBT_APMRESUMESUSPEND = 0x0007
PM_REMOVE = 0x0001
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100
MF_STRING = 0x0000
WS_EX_TOOLWINDOW = 0x00000080
GWLP_WNDPROC = -4
IDI_APPLICATION = 32512
WM_SETICON = 0x0080
ICON_SMALL = 0
ICON_BIG = 1
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
TRAY_CMD_SHOW = 1
TRAY_CMD_QUIT = 2
TRAY_ACTION_SHOW = "show"
TRAY_ACTION_MENU = "menu"
APP_USER_MODEL_ID = "MWT.GameWindowTool.App"


def tray_refresh_operations(modify_succeeds: bool) -> tuple[int, ...]:
    """Return shell operations used to recover a notification icon."""
    return (NIM_MODIFY,) if modify_succeeds else (NIM_MODIFY, NIM_ADD)

# Poll interval (ms): responsive enough for aspect correction, light on the game
TICK_MS = 200
# Auto-discover new windows ~once per second (5 × TICK_MS)
DISCOVER_EVERY_N_TICKS = 5
# Size match slack so 1–2 px frame rounding does not trigger endless re-apply
TOLERANCE = 2

# Launcher / patcher / helper names that must not be treated as game clients
EXE_EXCLUDE_FRAGMENTS = (
    "launcher", "patch", "update", "setup", "install", "helper", "tool",
)
# Common non-game apps excluded from the default (priority) list
NON_GAME_EXES = frozenset({
    "chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "brave.exe",
    "iexplore.exe", "explorer.exe", "applicationframehost.exe",
    "systemsettings.exe", "searchhost.exe", "textinputhost.exe",
    "shellexperiencehost.exe", "startmenuexperiencehost.exe",
    "code.exe", "devenv.exe", "notepad.exe", "notepad++.exe",
    "discord.exe", "slack.exe", "telegram.exe", "line.exe",
    "winword.exe", "excel.exe", "powerpnt.exe", "outlook.exe",
    "spotify.exe", "windowsterminal.exe", "cmd.exe", "powershell.exe",
    "pwsh.exe", "python.exe", "pythonw.exe", "cursor.exe",
})
# Window class substrings commonly used by game engines / clients
GAME_CLASS_HINTS = (
    "unitywndclass", "unrealwindow", "sdl_app", "sdl_window",
    "valve001", "riotwindowclass", "cryengine", "tgame",
)
# Minimum client size to treat an unknown app as a likely game window
MIN_GAME_CLIENT = (640, 480)
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
# Drop tiny windows when listing "all windows" for manual pick
MIN_LISTED_SIZE = (200, 150)
MODE_LABELS = {
    None: "尚未套用",
    "borderless_fs": "無邊框全螢幕",
    "windowed_fs": "有邊框全螢幕",
    "free": "自由縮放",
}

WNDENUMPROC = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)


def resolve_icon_path() -> Path | None:
    """Locate mushroom.ico (dev assets, PyInstaller _MEIPASS, or beside the exe)."""
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "assets" / "mushroom.ico")
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "assets" / "mushroom.ico")
    else:
        candidates.append(Path(__file__).resolve().parent / "assets" / "mushroom.ico")
    for path in candidates:
        if path.is_file():
            return path
    return None


def load_hicon(path: Path | None, size: int) -> int:
    """Load an HICON of the given pixel size from an .ico file; 0 on failure."""
    if path is None:
        return 0
    try:
        handle = user32.LoadImageW(
            None, str(path), IMAGE_ICON, size, size, LR_LOADFROMFILE,
        )
        return int(handle or 0)
    except OSError:
        return 0


def apply_hwnd_icons(hwnd: int, h_big: int, h_small: int) -> None:
    """Set icons on the Tk HWND and its outer frame so the taskbar updates."""
    if not hwnd:
        return
    targets = [hwnd]
    try:
        parent = int(user32.GetParent(hwnd) or 0)
        if parent and parent not in targets:
            targets.append(parent)
    except OSError:
        pass
    for target in targets:
        if h_big:
            user32.SendMessageW(target, WM_SETICON, ICON_BIG, h_big)
        if h_small:
            user32.SendMessageW(target, WM_SETICON, ICON_SMALL, h_small)


if ctypes.sizeof(ctypes.c_void_p) == 8:
    _LONG_PTR = ctypes.c_longlong
else:
    _LONG_PTR = ctypes.c_long
try:
    SetWindowLongPtr = user32.SetWindowLongPtrW
    GetWindowLongPtr = user32.GetWindowLongPtrW
except AttributeError:
    SetWindowLongPtr = user32.SetWindowLongW
    GetWindowLongPtr = user32.GetWindowLongW

GetWindowLongPtr.argtypes = [w.HWND, ctypes.c_int]
GetWindowLongPtr.restype = _LONG_PTR
SetWindowLongPtr.argtypes = [w.HWND, ctypes.c_int, _LONG_PTR]
SetWindowLongPtr.restype = _LONG_PTR
user32.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
user32.GetWindowTextLengthW.argtypes = [w.HWND]
user32.IsWindowVisible.argtypes = [w.HWND]
user32.IsWindow.argtypes = [w.HWND]
user32.IsIconic.argtypes = [w.HWND]
user32.IsZoomed.argtypes = [w.HWND]
user32.ShowWindow.argtypes = [w.HWND, ctypes.c_int]
user32.DestroyIcon.argtypes = [w.HICON]
user32.GetClientRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
user32.ClientToScreen.argtypes = [w.HWND, ctypes.POINTER(w.POINT)]
user32.ClientToScreen.restype = w.BOOL
user32.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
user32.SetWindowPos.argtypes = [
    w.HWND, w.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, ctypes.c_uint,
]
user32.MoveWindow.argtypes = [
    w.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.BOOL,
]
user32.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
user32.GetParent.argtypes = [w.HWND]
user32.GetParent.restype = w.HWND
kernel32.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
kernel32.OpenProcess.restype = w.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD),
]
kernel32.CloseHandle.argtypes = [w.HANDLE]
kernel32.SetLastError.argtypes = [w.DWORD]
kernel32.GetLastError.restype = w.DWORD
user32.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
user32.GetWindowRect.restype = w.BOOL
dwmapi.DwmGetWindowAttribute.argtypes = [w.HWND, w.DWORD, ctypes.c_void_p, w.DWORD]
dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
dwmapi.DwmSetWindowAttribute.argtypes = [w.HWND, w.DWORD, ctypes.c_void_p, w.DWORD]
dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
user32.GetClientRect.restype = w.BOOL
user32.SetWindowPos.restype = w.BOOL
user32.MoveWindow.restype = w.BOOL
user32.MonitorFromWindow.argtypes = [w.HWND, ctypes.c_uint]
user32.MonitorFromWindow.restype = w.HMONITOR
user32.MonitorFromRect.argtypes = [ctypes.POINTER(w.RECT), ctypes.c_uint]
user32.MonitorFromRect.restype = w.HMONITOR
user32.GetForegroundWindow.restype = w.HWND
user32.GetDC.argtypes = [w.HWND]
user32.GetDC.restype = w.HDC
user32.ReleaseDC.argtypes = [w.HWND, w.HDC]
gdi32.GetDeviceCaps.argtypes = [w.HDC, ctypes.c_int]
try:
    user32.GetDpiForWindow.argtypes = [w.HWND]
    user32.GetDpiForWindow.restype = ctypes.c_uint
except AttributeError:  # GetDpiForWindow needs Windows 10 1607+
    pass


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", w.DWORD),
        ("rcMonitor", w.RECT),
        ("rcWork", w.RECT),
        ("dwFlags", w.DWORD),
    ]


user32.GetMonitorInfoW.argtypes = [w.HMONITOR, ctypes.POINTER(MONITORINFO)]


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [
        ("length", w.UINT), ("flags", w.UINT), ("showCmd", w.UINT),
        ("ptMinPosition", w.POINT), ("ptMaxPosition", w.POINT),
        ("rcNormalPosition", w.RECT),
    ]


user32.GetWindowPlacement.argtypes = [w.HWND, ctypes.POINTER(WINDOWPLACEMENT)]
user32.GetWindowPlacement.restype = w.BOOL
user32.SetWindowPlacement.argtypes = [w.HWND, ctypes.POINTER(WINDOWPLACEMENT)]
user32.SetWindowPlacement.restype = w.BOOL


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("flags", w.DWORD),
                ("hwndActive", w.HWND), ("hwndFocus", w.HWND),
                ("hwndCapture", w.HWND), ("hwndMenuOwner", w.HWND),
                ("hwndMoveSize", w.HWND), ("hwndCaret", w.HWND), ("rcCaret", w.RECT)]


user32.GetGUIThreadInfo.argtypes = [w.DWORD, ctypes.POINTER(GUITHREADINFO)]
user32.GetGUIThreadInfo.restype = w.BOOL


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", w.DWORD),
        ("hWnd", w.HWND),
        ("uID", w.UINT),
        ("uFlags", w.UINT),
        ("uCallbackMessage", w.UINT),
        ("hIcon", w.HICON),
        ("szTip", w.WCHAR * 128),
        ("dwState", w.DWORD),
        ("dwStateMask", w.DWORD),
        ("szInfo", w.WCHAR * 256),
        ("uVersion", w.UINT),
        ("szInfoTitle", w.WCHAR * 64),
        ("dwInfoFlags", w.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", w.HICON),
    ]


shell32.Shell_NotifyIconW.argtypes = [w.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.ExtractIconW.argtypes = [w.HINSTANCE, w.LPCWSTR, w.UINT]
shell32.ExtractIconW.restype = w.HICON
user32.LoadImageW.argtypes = [
    w.HINSTANCE, w.LPCWSTR, w.UINT, ctypes.c_int, ctypes.c_int, w.UINT,
]
user32.LoadImageW.restype = w.HANDLE
user32.CreateWindowExW.argtypes = [
    w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID,
]
user32.CreateWindowExW.restype = w.HWND
user32.DestroyWindow.argtypes = [w.HWND]
user32.PeekMessageW.argtypes = [
    ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT,
]
user32.LoadIconW.argtypes = [w.HINSTANCE, ctypes.c_void_p]
user32.LoadIconW.restype = w.HICON
user32.CreatePopupMenu.restype = w.HMENU
user32.AppendMenuW.argtypes = [w.HMENU, w.UINT, ctypes.c_size_t, w.LPCWSTR]
user32.TrackPopupMenu.argtypes = [
    w.HMENU, w.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    w.HWND, ctypes.c_void_p,
]
user32.TrackPopupMenu.restype = ctypes.c_uint
user32.DestroyMenu.argtypes = [w.HMENU]
user32.GetCursorPos.argtypes = [ctypes.POINTER(w.POINT)]
user32.SetForegroundWindow.argtypes = [w.HWND]
user32.SetForegroundWindow.restype = w.BOOL
user32.BringWindowToTop.argtypes = [w.HWND]
user32.BringWindowToTop.restype = w.BOOL
user32.AttachThreadInput.argtypes = [w.DWORD, w.DWORD, w.BOOL]
user32.AttachThreadInput.restype = w.BOOL
user32.GetWindowThreadProcessId.restype = w.DWORD
kernel32.GetCurrentThreadId.restype = w.DWORD
user32.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.PostMessageW.restype = w.BOOL
user32.RegisterWindowMessageW.argtypes = [w.LPCWSTR]
user32.RegisterWindowMessageW.restype = w.UINT
# LRESULT is 64-bit on x64; keep WNDPROC refs on the instance to avoid GC
_LRESULT = ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long
WNDPROC = ctypes.WINFUNCTYPE(_LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
user32.CallWindowProcW.argtypes = [
    ctypes.c_void_p, w.HWND, w.UINT, w.WPARAM, w.LPARAM,
]
user32.CallWindowProcW.restype = _LRESULT


def _require_bool(ok: object) -> None:
    """Raise WinError when a BOOL Win32 call reports failure."""
    if not ok:
        raise ctypes.WinError()


def set_window_long(hwnd: int, index: int, value: int) -> int:
    """SetWindowLongPtr with correct failure detection (previous value may be 0)."""
    kernel32.SetLastError(0)
    prev = int(SetWindowLongPtr(hwnd, index, value))
    err = int(kernel32.GetLastError())
    if prev == 0 and err != 0:
        raise ctypes.WinError(err)
    return prev


def get_window_title(hwnd: int) -> str:
    """Return the window title text, or empty string if none."""
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def get_class_name(hwnd: int) -> str:
    """Return the Win32 window class name."""
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def get_process_id(hwnd: int) -> int:
    """Return the process ID that owns the window."""
    pid = w.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def get_process_path(pid: int) -> str:
    """Return the full executable path, or empty if inaccessible."""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = w.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return buf.value
    finally:
        kernel32.CloseHandle(handle)


def get_process_name(pid: int) -> str:
    return get_process_path(pid).rsplit("\\", 1)[-1]


def get_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """Return outer window rect as (x, y, width, height) in screen coordinates."""
    rect = w.RECT()
    _require_bool(user32.GetWindowRect(hwnd, ctypes.byref(rect)))
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def get_client_size(hwnd: int) -> tuple[int, int]:
    """Return the client-area (game view) size as (width, height)."""
    rect = w.RECT()
    _require_bool(user32.GetClientRect(hwnd, ctypes.byref(rect)))
    return rect.right - rect.left, rect.bottom - rect.top


def looks_like_game(
    title: str,
    exe: str,
    class_name: str = "",
    *,
    client_w: int = 0,
    client_h: int = 0,
) -> bool:
    """Heuristic for likely game clients (used to prioritize the window list).

    Prefer engine window classes and sizable unknown executables. Exclude
    browsers, launchers, and common desktop apps. Any window can still be
    chosen manually via「顯示所有視窗」.
    """
    _ = title  # title reserved for future heuristics
    name = (exe or "").lower().rsplit("\\", 1)[-1]
    cls = (class_name or "").lower()
    if name:
        if any(frag in name for frag in EXE_EXCLUDE_FRAGMENTS):
            return False
        if name in NON_GAME_EXES:
            return False
    if any(hint in cls for hint in GAME_CLASS_HINTS):
        return True
    if (
        client_w >= MIN_GAME_CLIENT[0]
        and client_h >= MIN_GAME_CLIENT[1]
        and (not name or name not in NON_GAME_EXES)
        and not any(frag in name for frag in EXE_EXCLUDE_FRAGMENTS)
    ):
        return True
    return False


def _collect_candidate_windows(show_all: bool = False, *, include_hwnds: set[int] | None = None) -> list[dict]:
    """Enumerate visible candidate windows (no snapshot side effects).

    By default only likely game windows are returned (prioritized). When
    show_all is True, other visible windows above MIN_LISTED_SIZE are included.
    """
    found: list[dict] = []
    own_pid = kernel32.GetCurrentProcessId()
    process_paths: dict[int, str] = {}
    include_hwnds = include_hwnds or set()

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = get_window_title(hwnd)
        tracked = int(hwnd) in include_hwnds
        if not title and not tracked:
            return True
        pid = get_process_id(hwnd)
        if pid == own_pid:
            return True
        if pid not in process_paths:
            process_paths[pid] = get_process_path(pid)
        exe_path = process_paths[pid]
        exe = exe_path.rsplit("\\", 1)[-1]
        class_name = get_class_name(hwnd)
        try:
            x, y, width, height = get_window_rect(hwnd)
            cw, ch = get_client_size(hwnd)
        except OSError:
            return True
        game = looks_like_game(
            title, exe, class_name, client_w=cw, client_h=ch,
        )
        if not game and not tracked:
            if not show_all:
                return True
            if width < MIN_LISTED_SIZE[0] or height < MIN_LISTED_SIZE[1]:
                return True
        found.append(
            {
                "hwnd": int(hwnd),
                "title": title or exe or "未命名視窗",
                "class": class_name,
                "pid": pid,
                "exe": exe,
                "exe_path": exe_path,
                "game": game,
                "x": x,
                "y": y,
                "w": width,
                "h": height,
                "cw": cw,
                "ch": ch,
            }
        )
        return True

    # Keep the ctypes callback alive for the duration of EnumWindows
    enum_proc = WNDENUMPROC(callback)
    user32.EnumWindows(enum_proc, 0)
    # Prefer game-like windows so the default selection is usually correct
    found.sort(key=lambda item: not item["game"])
    return found


def enum_top_level_windows(show_all: bool = False) -> list[dict]:
    """Enumerate top-level windows for the target combo and snapshot each."""
    found = _collect_candidate_windows(show_all)
    for item in found:
        capture_native_snapshot(item["hwnd"])
    return found


def discover_and_snapshot_windows(show_all: bool = False) -> list[dict]:
    """Snapshot candidates that have no valid first-seen state yet.

    Returns only windows that newly received a snapshot (for quiet UI updates).
    Does not overwrite existing same-HWND+PID snapshots.
    """
    newly: list[dict] = []
    for item in _collect_candidate_windows(show_all):
        hwnd = item["hwnd"]
        if peek_native_snapshot(hwnd) is not None:
            continue
        if capture_native_snapshot(hwnd) is not None:
            newly.append(item)
    return newly


def direct_children(parent: int) -> list[int]:
    """List direct child HWNDs only (not all descendants).

    EnumChildWindows walks the whole tree; resizing every descendant would
    break nested UI layout.
    """
    children: list[int] = []

    def callback(hwnd, _lparam):
        if int(user32.GetParent(hwnd) or 0) == parent:
            children.append(int(hwnd))
        return True

    # Keep the ctypes callback alive for the duration of EnumChildWindows
    enum_proc = WNDENUMPROC(callback)
    user32.EnumChildWindows(w.HWND(parent), enum_proc, 0)
    return children


def window_dpi(hwnd: int) -> int:
    """DPI of the monitor hosting hwnd.

    This process is per-monitor DPI aware, so Windows will not auto-scale
    the Tk UI; we scale widgets ourselves from this value.
    """
    try:
        dpi = int(user32.GetDpiForWindow(hwnd))
        if dpi > 0:
            return dpi
    except (AttributeError, OSError):
        pass
    hdc = user32.GetDC(0)
    if not hdc:
        return DEFAULT_DPI
    try:
        return int(gdi32.GetDeviceCaps(hdc, LOGPIXELSX)) or DEFAULT_DPI
    finally:
        user32.ReleaseDC(0, hdc)


def fit_aspect(avail_w: int, avail_h: int, aspect: float) -> tuple[int, int]:
    """Largest size that fits in avail_w×avail_h while keeping aspect (letterbox)."""
    height = min(avail_h, round(avail_w / aspect))
    width = round(height * aspect)
    if width > avail_w:
        width = avail_w
        height = round(width / aspect)
    return max(1, width), max(1, height)


def size_for_aspect(
    width: int,
    height: int,
    aspect: float,
    *,
    prev_w: int | None = None,
    prev_h: int | None = None,
) -> tuple[int, int]:
    """Adjust width/height to the given aspect ratio.

    When a previous size is provided, drive the axis that changed more
    (bottom-edge drag → width follows height; side drag → height follows
    width). Otherwise prefer width as the driver.
    """
    if aspect <= 0:
        return max(1, width), max(1, height)
    drive_height = False
    if prev_w is not None and prev_h is not None:
        drive_height = abs(height - prev_h) > abs(width - prev_w)
    if drive_height:
        new_h = max(1, height)
        new_w = max(1, round(new_h * aspect))
    else:
        new_w = max(1, width)
        new_h = max(1, round(new_w / aspect))
    return new_w, new_h


def frame_extra(hwnd: int) -> tuple[int, int]:
    """Non-client chrome size: outer size minus client size (caption/borders)."""
    _, _, out_w, out_h = get_window_rect(hwnd)
    cw, ch = get_client_size(hwnd)
    return out_w - cw, out_h - ch


# First-seen native game state; never overwritten by later resizes
# hwnd -> (style, exstyle, x, y, outer_w, outer_h, client_w, client_h, pid)
_native_snapshot: dict[int, tuple[int, int, int, int, int, int, int, int, int]] = {}


@dataclass
class WindowDecorations:
    pid: int
    frame_edges: int
    nc_enabled: int | None
    corner: int | None


# Only capture windows actually entering borderless mode. Keep the original
# values across repeated applications; discard them when the HWND is reused.
_borderless_decorations: dict[int, WindowDecorations] = {}


def get_dwm_attribute(hwnd: int, attribute: int) -> int | None:
    value = w.DWORD()
    result = dwmapi.DwmGetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value))
    if result < 0:
        return None  # Older Windows versions do not expose corner preferences.
    return value.value


def set_dwm_attribute(hwnd: int, attribute: int, value: int) -> None:
    data = w.DWORD(value)
    result = dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(data), ctypes.sizeof(data))
    if result < 0:
        raise OSError(f"無法更新視窗外觀（DWM 0x{result & 0xFFFFFFFF:08X}）")


def remember_decorations(hwnd: int) -> WindowDecorations:
    pid = get_process_id(hwnd)
    saved = _borderless_decorations.get(hwnd)
    if saved is None or saved.pid != pid:
        saved = WindowDecorations(
            pid, int(GetWindowLongPtr(hwnd, GWL_EXSTYLE)) & WS_EX_FRAME_EDGES,
            get_dwm_attribute(hwnd, DWMWA_NCRENDERING_ENABLED),
            get_dwm_attribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE),
        )
        _borderless_decorations[hwnd] = saved
    return saved


def restore_decorations(hwnd: int) -> None:
    saved = _borderless_decorations.get(hwnd)
    if saved is None:
        return
    if not user32.IsWindow(hwnd) or saved.pid != get_process_id(hwnd):
        _borderless_decorations.pop(hwnd, None)
        return
    exstyle = int(GetWindowLongPtr(hwnd, GWL_EXSTYLE))
    set_window_long(hwnd, GWL_EXSTYLE, (exstyle & ~WS_EX_FRAME_EDGES) | saved.frame_edges)
    # NC policy itself is write-only. Restore the previously observable enabled
    # state; do not touch write-only border colors or extended glass margins.
    if saved.nc_enabled is not None:
        set_dwm_attribute(hwnd, DWMWA_NCRENDERING_POLICY,
                          DWMNCRP_ENABLED if saved.nc_enabled else DWMNCRP_DISABLED)
    if saved.corner is not None:
        set_dwm_attribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, saved.corner)
    _borderless_decorations.pop(hwnd, None)


def borderless_decorations_match(hwnd: int) -> bool:
    saved = _borderless_decorations.get(hwnd)
    if saved is None:
        return True
    if saved.pid != get_process_id(hwnd):
        return False
    if saved.nc_enabled is not None and get_dwm_attribute(hwnd, DWMWA_NCRENDERING_ENABLED) not in (None, 0):
        return False
    return (saved.corner is None or get_dwm_attribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE)
            in (None, DWMWCP_DONOTROUND))


def peek_native_snapshot(
    hwnd: int,
) -> tuple[int, int, int, int, int, int, int, int, int] | None:
    """Return an existing snapshot only if HWND still belongs to the same PID.

    On HWND reuse, drop the stale entry and return None without rebinding.
    """
    existing = _native_snapshot.get(hwnd)
    if existing is None:
        return None
    if not user32.IsWindow(hwnd) or get_process_id(hwnd) != existing[-1]:
        _native_snapshot.pop(hwnd, None)
        _borderless_decorations.pop(hwnd, None)
        return None
    return existing


def capture_native_snapshot(
    hwnd: int,
) -> tuple[int, int, int, int, int, int, int, int, int] | None:
    """Record native state on intentional sighting (enum / discover / select / mutate).

    Routine UI polling must use peek_native_snapshot only; discovery may call
    this for HWNDs that still lack a valid snapshot. If a stale HWND entry
    belongs to another PID, drop it and record the current window instead.
    """
    existing = _native_snapshot.get(hwnd)
    if existing is not None:
        if user32.IsWindow(hwnd) and get_process_id(hwnd) == existing[-1]:
            return existing
        _native_snapshot.pop(hwnd, None)
        _borderless_decorations.pop(hwnd, None)
    if not user32.IsWindow(hwnd):
        return None
    pid = get_process_id(hwnd)
    cw, ch = get_client_size(hwnd)
    if cw <= 0 or ch <= 0:
        return None
    style = int(GetWindowLongPtr(hwnd, GWL_STYLE)) & 0xFFFFFFFF
    exstyle = int(GetWindowLongPtr(hwnd, GWL_EXSTYLE)) & 0xFFFFFFFF
    x, y, outer_w, outer_h = get_window_rect(hwnd)
    snap = (style, exstyle, x, y, outer_w, outer_h, cw, ch, pid)
    _native_snapshot[hwnd] = snap
    return snap


def capture_native_client(hwnd: int) -> tuple[int, int] | None:
    """Return native client (w, h) from an existing snapshot, if available."""
    snap = peek_native_snapshot(hwnd)
    if snap is None:
        return None
    return snap[6], snap[7]


def native_client_size(hwnd: int) -> tuple[int, int] | None:
    """Alias for capture_native_client."""
    return capture_native_client(hwnd)


def remember_original(hwnd: int) -> None:
    """Ensure a native snapshot exists before mutating the window."""
    capture_native_snapshot(hwnd)


def original_aspect(hwnd: int) -> float | None:
    """Native client aspect from first sighting (not the current dragged size)."""
    native = native_client_size(hwnd)
    if native is not None:
        cw, ch = native
        return (cw / ch) if ch else None
    cw, ch = get_client_size(hwnd)
    return (cw / ch) if ch else None


def format_aspect(aspect: float) -> str:
    """Pretty-print a ratio as 4:3 / 16:9 / … when close enough."""
    for label, value in (("4:3", 4 / 3), ("16:9", 16 / 9), ("16:10", 16 / 10), ("5:4", 5 / 4)):
        if abs(aspect - value) < 0.02:
            return label
    return f"{aspect:.3f}"


def _apply_style(hwnd: int, style: int) -> None:
    """Write GWL_STYLE and force a non-client frame refresh."""
    set_window_long(hwnd, GWL_STYLE, style & 0xFFFFFFFF)
    _require_bool(
        user32.SetWindowPos(
            hwnd, HWND_TOP, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED | SWP_SHOWWINDOW,
        )
    )


def enable_resizable(hwnd: int) -> None:
    """Restore caption chrome and thick-frame so the user can drag edges."""
    remember_original(hwnd)
    restore_decorations(hwnd)
    style = int(GetWindowLongPtr(hwnd, GWL_STYLE))
    style |= WS_THICKFRAME | WS_MAXIMIZEBOX | WS_MINIMIZEBOX | WS_SYSMENU | WS_CAPTION
    style &= ~WS_POPUP
    _apply_style(hwnd, style)


def set_borderless(hwnd: int) -> None:
    """Strip caption/borders for borderless fullscreen (popup + visible)."""
    remember_original(hwnd)
    decorations = remember_decorations(hwnd)
    exstyle = int(GetWindowLongPtr(hwnd, GWL_EXSTYLE))
    set_window_long(hwnd, GWL_EXSTYLE, exstyle & ~WS_EX_FRAME_EDGES)
    style = int(GetWindowLongPtr(hwnd, GWL_STYLE))
    style &= ~(
        WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX
        | WS_SYSMENU | WS_BORDER | WS_DLGFRAME
    )
    style |= WS_POPUP | WS_VISIBLE
    _apply_style(hwnd, style)
    # Removing WS_CAPTION alone leaves custom-frame DWM shadows and explicit
    # Windows 11 rounding in place (e.g. Electron windows).
    if decorations.nc_enabled is not None:
        set_dwm_attribute(hwnd, DWMWA_NCRENDERING_POLICY, DWMNCRP_DISABLED)
    if decorations.corner is not None:
        set_dwm_attribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_DONOTROUND)


def is_topmost(hwnd: int) -> bool:
    """True if the window has WS_EX_TOPMOST."""
    return bool(int(GetWindowLongPtr(hwnd, GWL_EXSTYLE)) & WS_EX_TOPMOST)


def set_topmost(hwnd: int, enable: bool) -> None:
    """Toggle topmost. Needed to cover the taskbar (itself a topmost window)."""
    _require_bool(
        user32.SetWindowPos(
            hwnd, HWND_TOPMOST if enable else HWND_NOTOPMOST, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )
    )


def restore_original(hwnd: int) -> bool:
    """Restore the first-seen native state only for the original process."""
    snap = peek_native_snapshot(hwnd)
    if snap is None:
        return False
    style, exstyle, x, y, outer_w, outer_h, _cw, _ch, _pid = snap
    if user32.IsZoomed(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    restore_decorations(hwnd)
    set_topmost(hwnd, False)
    set_window_long(hwnd, GWL_STYLE, style & ~WS_MAXIMIZE)
    set_window_long(hwnd, GWL_EXSTYLE, exstyle & ~WS_EX_TOPMOST)
    _require_bool(
        user32.SetWindowPos(
            hwnd, HWND_NOTOPMOST, x, y, outer_w, outer_h,
            SWP_FRAMECHANGED | SWP_SHOWWINDOW,
        )
    )
    if style & WS_MAXIMIZE:
        maximize_window(hwnd)
    _stretch_single_child(hwnd)
    return True


def monitor_area(hwnd: int, work_area: bool = True) -> tuple[int, int, int, int]:
    """Monitor rect for hwnd as (x, y, w, h); work area excludes the taskbar."""
    hmon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
        return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    r = info.rcWork if work_area else info.rcMonitor
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def resize_client(hwnd: int, width: int, height: int) -> None:
    """Resize so the client area becomes width×height; keep top-left fixed."""
    x, y, _, _ = get_window_rect(hwnd)
    extra_w, extra_h = frame_extra(hwnd)
    _require_bool(user32.MoveWindow(hwnd, x, y, width + extra_w, height + extra_h, True))
    _stretch_single_child(hwnd)


def _stretch_single_child(hwnd: int) -> None:
    """If the client has exactly one direct child, stretch it to fill the client.

    Some clients draw the game into that child; only safe when there is one.
    """
    children = direct_children(hwnd)
    if len(children) != 1:
        return
    cw, ch = get_client_size(hwnd)
    # Best-effort: some clients reject child moves; parent resize still stands
    user32.MoveWindow(children[0], 0, 0, cw, ch, True)


def maximize_window(hwnd: int) -> None:
    """Use native maximization, preserving the normal restore placement.

    GetWindowRect includes invisible resize borders. Stretching that rectangle
    to rcWork leaves visible gaps; Windows maximization puts those borders
    outside the work area and handles custom frames/DPI. SetWindowPlacement
    also avoids ShowWindow(SW_MAXIMIZE)'s foreground activation.
    """
    placement = WINDOWPLACEMENT()
    placement.length = ctypes.sizeof(placement)
    _require_bool(user32.GetWindowPlacement(hwnd, ctypes.byref(placement)))
    placement.showCmd = SW_MAXIMIZE
    _require_bool(user32.SetWindowPlacement(hwnd, ctypes.byref(placement)))
    if not user32.IsZoomed(hwnd):
        raise OSError("目標視窗未接受最大化，請重新套用")


def get_client_rect_screen(hwnd: int) -> tuple[int, int, int, int]:
    origin = w.POINT()
    _require_bool(user32.ClientToScreen(hwnd, ctypes.byref(origin)))
    width, height = get_client_size(hwnd)
    return origin.x, origin.y, width, height


def place_borderless_client(hwnd: int, x: int, y: int, width: int, height: int) -> None:
    """Fill with the actual client, compensating for custom non-client insets.

    Some custom window procedures retain resize margins even without the usual
    frame styles. Re-measure after moving (including a possible DPI change).
    """
    for _ in range(3):
        ox, oy, ow, oh = get_window_rect(hwnd)
        cx, cy, cw, ch = get_client_rect_screen(hwnd)
        if (cx, cy, cw, ch) == (x, y, width, height):
            break
        _require_bool(user32.MoveWindow(
            hwnd, ox + x - cx, oy + y - cy,
            max(1, ow + width - cw), max(1, oh + height - ch), True,
        ))
    _stretch_single_child(hwnd)
    actual = get_client_rect_screen(hwnd)
    if any(abs(value - wanted) > TOLERANCE
           for value, wanted in zip(actual, (x, y, width, height))):
        raise OSError("目標程式限制了無邊框尺寸，無法填滿指定範圍")


def fill_monitor(
    hwnd: int, *, borderless: bool, keep_ratio: bool, cover_taskbar: bool = True
) -> None:
    """Place the window in fullscreen on its monitor.

    Windowed mode always uses the work area. Borderless uses the full monitor
    when cover_taskbar is True. With keep_ratio, letterbox/pillarbox and center
    so the image is not stretched (desktop shows in the margins).
    """
    cover = borderless and cover_taskbar
    if borderless:
        set_borderless(hwnd)
    else:
        enable_resizable(hwnd)
    # Only topmost when covering the taskbar; otherwise clear any leftover topmost
    set_topmost(hwnd, cover)

    if not borderless and not keep_ratio:
        maximize_window(hwnd)
        _stretch_single_child(hwnd)
        return

    mx, my, mw, mh = monitor_area(hwnd, work_area=not cover)
    aspect = original_aspect(hwnd)
    if borderless:
        width, height = fit_aspect(mw, mh, aspect) if keep_ratio and aspect else (mw, mh)
        place_borderless_client(hwnd, mx + (mw - width) // 2, my + (mh - height) // 2, width, height)
        return
    if keep_ratio and aspect:
        extra_w, extra_h = frame_extra(hwnd)
        fit_w, fit_h = fit_aspect(max(1, mw - extra_w), max(1, mh - extra_h), aspect)
        target_w, target_h = fit_w + extra_w, fit_h + extra_h
    else:
        target_w, target_h = mw, mh

    x = mx + max(0, (mw - target_w) // 2)
    y = my + max(0, (mh - target_h) // 2)
    _require_bool(user32.MoveWindow(hwnd, x, y, target_w, target_h, True))
    _stretch_single_child(hwnd)


def restore_profile_geometry(hwnd: int, profile: WindowProfile) -> None:
    """Restore free sizing, keeping the frame on an available monitor."""
    width, height = profile.client_size
    if profile.keep_ratio:
        aspect = original_aspect(hwnd)
        if aspect:
            width, height = size_for_aspect(width, height, aspect)
    extra_w, extra_h = frame_extra(hwnd)
    x, y = profile.position
    rect = w.RECT(x, y, x + width + extra_w, y + height + extra_h)
    monitor = user32.MonitorFromRect(ctypes.byref(rect), MONITOR_DEFAULTTONEAREST)
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(info)
    _require_bool(user32.GetMonitorInfoW(monitor, ctypes.byref(info)))
    area = info.rcWork
    avail_w = max(1, area.right - area.left - extra_w)
    avail_h = max(1, area.bottom - area.top - extra_h)
    if width > avail_w or height > avail_h:
        if profile.keep_ratio:
            width, height = fit_aspect(avail_w, avail_h, width / height)
        else:
            width, height = min(width, avail_w), min(height, avail_h)
    outer_w, outer_h = width + extra_w, height + extra_h
    x = max(area.left, min(x, area.right - outer_w))
    y = max(area.top, min(y, area.bottom - outer_h))
    _require_bool(user32.MoveWindow(hwnd, x, y, outer_w, outer_h, True))
    _stretch_single_child(hwnd)


@dataclass
class WindowSession:
    """Independent live settings for one HWND/PID, including background windows."""

    hwnd: int
    pid: int
    mode: str | None = None
    keep_ratio: bool = False
    cover_taskbar: bool = False
    watch: bool = False
    target_client: tuple[int, int] | None = None
    applied_rect: tuple[int, int, int, int] | None = None
    dragging: bool = False
    drag_start: tuple[int, int] | None = None
    drag_last: tuple[int, int] | None = None
    monitor_bounds: tuple[int, int, int, int] | None = None

    def alive(self) -> bool:
        return bool(user32.IsWindow(self.hwnd)) and get_process_id(self.hwnd) == self.pid

    def clear(self) -> None:
        if self.alive():
            try:
                if is_topmost(self.hwnd):
                    set_topmost(self.hwnd, False)
            except OSError:
                pass
        self.mode = None
        self.watch = False
        self.target_client = None
        self.applied_rect = None
        self.monitor_bounds = None
        self.dragging = False
        self.drag_start = self.drag_last = None

    def apply(self, mode: str, profile: WindowProfile | None = None) -> None:
        if mode not in ("free", "borderless_fs", "windowed_fs"):
            raise ValueError("不支援的顯示模式")
        if not self.alive():
            raise OSError("目標視窗已關閉或已被替換")
        if user32.IsIconic(self.hwnd):
            raise OSError("請先還原最小化的目標視窗")
        if capture_native_snapshot(self.hwnd) is None:
            raise OSError("目標視窗尚未準備好")
        self.mode = None
        if profile is not None:
            self.keep_ratio = profile.keep_ratio
            self.cover_taskbar = profile.cover_taskbar
            self.watch = profile.watch
        # Keep an already-maximized window on its current monitor. Only leave
        # that state for modes which need an explicit client/outer size.
        if user32.IsZoomed(self.hwnd) and (mode != "windowed_fs" or self.keep_ratio):
            user32.ShowWindow(self.hwnd, SW_RESTORE)
        if mode == "free":
            enable_resizable(self.hwnd)
            set_topmost(self.hwnd, False)
            if profile is not None:
                restore_profile_geometry(self.hwnd, profile)
            elif self.keep_ratio:
                self.snap_aspect()
        else:
            fill_monitor(
                self.hwnd, borderless=mode == "borderless_fs",
                keep_ratio=self.keep_ratio, cover_taskbar=self.cover_taskbar,
            )
        self.target_client = get_client_size(self.hwnd)
        self.applied_rect = get_window_rect(self.hwnd) if mode != "free" else None
        self.monitor_bounds = monitor_area(
            self.hwnd, work_area=not (mode == "borderless_fs" and self.cover_taskbar),
        ) if mode != "free" else None
        self.dragging = False
        self.drag_start = self.drag_last = None
        self.mode = mode
        self.sync_topmost()

    def snap_aspect(self, prev: tuple[int, int] | None = None) -> bool:
        aspect = original_aspect(self.hwnd)
        if not aspect:
            return False
        cw, ch = get_client_size(self.hwnd)
        prev_w, prev_h = prev if prev is not None else (None, None)
        width, height = size_for_aspect(cw, ch, aspect, prev_w=prev_w, prev_h=prev_h)
        if abs(width - cw) <= TOLERANCE and abs(height - ch) <= TOLERANCE:
            self.target_client = (cw, ch)
            return False
        resize_client(self.hwnd, width, height)
        self.target_client = get_client_size(self.hwnd)
        return True

    def sync_topmost(self) -> None:
        if self.mode != "borderless_fs" or not self.cover_taskbar:
            return
        want = int(user32.GetForegroundWindow() or 0) == self.hwnd
        if is_topmost(self.hwnd) != want:
            set_topmost(self.hwnd, want)

    def tick(self) -> None:
        if self.mode is None or not self.alive() or user32.IsIconic(self.hwnd):
            return
        self.sync_topmost()
        if self.mode == "free":
            self.tick_free()
        elif self.watch and self.applied_rect is not None and not self.user_dragging():
            current = get_window_rect(self.hwnd)
            ax, ay, aw, ah = self.applied_rect
            cx, cy, cw, ch = current
            size_drift = abs(cw - aw) > TOLERANCE or abs(ch - ah) > TOLERANCE
            if self.mode == "borderless_fs" and self.target_client is not None:
                client = get_client_size(self.hwnd)
                size_drift |= any(abs(actual - target) > TOLERANCE
                                  for actual, target in zip(client, self.target_client))
            pos_drift = abs(cx - ax) > TOLERANCE or abs(cy - ay) > TOLERANCE
            monitor_changed = self.monitor_bounds != monitor_area(
                self.hwnd, work_area=not (self.mode == "borderless_fs" and self.cover_taskbar),
            )
            if (size_drift or (pos_drift and self.mode == "borderless_fs")
                    or monitor_changed or not self.style_matches()):
                self.apply(self.mode)
            elif pos_drift:
                self.applied_rect = current

    def tick_free(self) -> None:
        if self.user_dragging():
            if not self.dragging:
                self.dragging = True
                self.drag_start = get_client_size(self.hwnd)
                self.drag_last = self.drag_start
            elif self.keep_ratio:
                cw, ch = get_client_size(self.hwnd)
                last = self.drag_last or self.drag_start
                if last is not None and (
                    abs(cw - last[0]) > TOLERANCE or abs(ch - last[1]) > TOLERANCE
                ):
                    self.snap_aspect(last)
                    self.drag_last = get_client_size(self.hwnd)
            return
        if self.dragging:
            self.dragging = False
            start = self.drag_start
            self.drag_start = self.drag_last = None
            cw, ch = get_client_size(self.hwnd)
            if start is not None and (
                abs(cw - start[0]) > TOLERANCE or abs(ch - start[1]) > TOLERANCE
            ):
                if self.keep_ratio:
                    self.snap_aspect(start)
                else:
                    self.target_client = (cw, ch)
            return
        if not self.watch or self.target_client is None:
            return
        tw, th = self.target_client
        cw, ch = get_client_size(self.hwnd)
        if abs(cw - tw) > TOLERANCE or abs(ch - th) > TOLERANCE or not self.style_matches():
            enable_resizable(self.hwnd)
            resize_client(self.hwnd, tw, th)

    def user_dragging(self) -> bool:
        """Use Windows' move/size loop, not ordinary in-game mouse clicks.

        https://learn.microsoft.com/windows/win32/api/winuser/ns-winuser-guithreadinfo
        """
        thread = user32.GetWindowThreadProcessId(self.hwnd, None)
        if not thread:
            return False
        info = GUITHREADINFO()
        info.cbSize = ctypes.sizeof(info)
        return bool(user32.GetGUIThreadInfo(thread, ctypes.byref(info))
                    and info.flags & 0x00000002 and info.hwndMoveSize == self.hwnd)

    def style_matches(self) -> bool:
        style = int(GetWindowLongPtr(self.hwnd, GWL_STYLE))
        if self.mode == "borderless_fs":
            return (not bool(style & (WS_CAPTION | WS_THICKFRAME))
                    and not bool(int(GetWindowLongPtr(self.hwnd, GWL_EXSTYLE)) & WS_EX_FRAME_EDGES)
                    and borderless_decorations_match(self.hwnd))
        required = WS_CAPTION | WS_THICKFRAME | WS_MAXIMIZEBOX
        if self.mode == "windowed_fs" and not self.keep_ratio:
            required |= WS_MAXIMIZE
        return style & required == required


class TrayIcon:
    """System-tray icon with left-click restore and right-click menu.

    Tk's message loop dispatches messages for foreign HWNDs before we can
    Peek them, so we subclass the tray window's WndProc, stash click actions
    in _pending, and run them from pump() on the Tk main thread.
    """

    def __init__(self, tip: str, on_show, on_quit) -> None:
        self._tip = tip
        self._on_show = on_show
        self._on_quit = on_quit
        self._hwnd: int | None = None
        self._icon: int = 0
        self._owns_icon = False
        self._data: NOTIFYICONDATAW | None = None
        self._pending: str | None = None
        self._wndproc = None
        self._old_wndproc = 0
        self._taskbar_created = int(
            user32.RegisterWindowMessageW("TaskbarCreated") or 0
        )
        self._registered = False

    @property
    def alive(self) -> bool:
        """True while the hidden tray owner window exists."""
        return self._hwnd is not None

    def create(self) -> bool:
        """Add the tray icon; on failure the caller keeps the main UI visible."""
        if self.alive:
            return True
        try:
            # Real hidden window (not HWND_MESSAGE): TrackPopupMenu needs an owner
            hwnd = user32.CreateWindowExW(
                WS_EX_TOOLWINDOW, "STATIC", "MWTTray", WS_POPUP,
                0, 0, 0, 0, None, None, None, None,
            )
            if not hwnd:
                return False
            self._hwnd = int(hwnd)
            # Keep the WNDPROC callable on self or ctypes GC crashes on click
            self._wndproc = WNDPROC(self._window_proc)
            self._old_wndproc = int(
                set_window_long(
                    self._hwnd, GWLP_WNDPROC,
                    ctypes.cast(self._wndproc, ctypes.c_void_p).value,
                )
            )
            self._icon = self._load_icon()
            if not self._register_icon(NIM_ADD):
                self.destroy()
                return False
            return True
        except OSError:
            self.destroy()
            return False

    def _window_proc(self, hwnd, msg, wparam, lparam):
        """Subclassed WndProc: record tray clicks, forward everything else."""
        if msg == self._taskbar_created:
            self._registered = False
            self._register_icon(NIM_ADD)
            return 0
        if msg == WM_POWERBROADCAST and wparam in (
            PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND,
        ):
            self._refresh_icon()
            return 1
        if msg == WM_TRAY:
            self._note_tray_event(int(lparam) & 0xFFFF)
            return 0
        if msg == WM_TRAY_SHOWMENU:
            self._pending = TRAY_ACTION_MENU
            return 0
        if self._old_wndproc:
            return user32.CallWindowProcW(self._old_wndproc, hwnd, msg, wparam, lparam)
        return 0

    def _register_icon(self, operation: int) -> bool:
        """Add or refresh the shell icon after Explorer/display recovery."""
        if self._hwnd is None:
            return False
        data = self._data or NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = self._hwnd
        data.uID = 1
        data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        data.uCallbackMessage = WM_TRAY
        data.hIcon = self._icon
        data.szTip = self._tip[:127]
        try:
            ok = bool(shell32.Shell_NotifyIconW(operation, ctypes.byref(data)))
        except OSError:
            ok = False
        if ok:
            self._data = data
            self._registered = True
        return ok

    def _refresh_icon(self) -> bool:
        """Refresh an existing icon, falling back to add after shell recovery."""
        if self._register_icon(NIM_MODIFY):
            return True
        self._registered = False
        return self._register_icon(NIM_ADD)

    def _load_icon(self) -> int:
        """Prefer mushroom.ico; fall back to the exe-embedded or default icon."""
        path = resolve_icon_path()
        hicon = load_hicon(path, 16) or load_hicon(path, 32)
        if hicon:
            self._owns_icon = True
            return hicon
        try:
            icon = shell32.ExtractIconW(None, sys.executable, 0)
            if icon and icon != 1:
                self._owns_icon = True
                return int(icon)
        except OSError:
            pass
        return int(user32.LoadIconW(None, IDI_APPLICATION) or 0)

    def _note_tray_event(self, event: int) -> None:
        """Map WM_* click codes to a pending action for the Tk thread."""
        if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
            self._pending = TRAY_ACTION_SHOW
        elif event in (WM_RBUTTONUP, WM_CONTEXTMENU):
            self._pending = TRAY_ACTION_MENU

    @staticmethod
    def _force_foreground(hwnd: int) -> None:
        """Attach to the foreground thread so TrackPopupMenu can take focus."""
        fg = int(user32.GetForegroundWindow() or 0)
        cur_thread = int(kernel32.GetCurrentThreadId())
        fg_thread = int(user32.GetWindowThreadProcessId(fg, None)) if fg else 0
        attached = False
        if fg_thread and fg_thread != cur_thread:
            attached = bool(user32.AttachThreadInput(cur_thread, fg_thread, True))
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetWindowPos(
                hwnd, HWND_TOP, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW,
            )
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(cur_thread, fg_thread, False)

    def pump(self) -> None:
        """Drain tray messages and run any pending show/menu action.

        Explorer usually PostMessages; Tk does not dispatch foreign HWNDs, so
        we Peek ourselves. Cross-process SendMessage hits WndProc during Peek.
        Both paths converge on _pending. A queued right-click is handled on
        the next pump, without reposting messages into the queue being drained.
        """
        if self._hwnd is None:
            return
        msg = w.MSG()
        # Never re-post into the queue we are draining: PeekMessage could
        # consume that same message forever and freeze the Tk event loop.
        defer_menu = False
        try:
            for _ in range(64):
                if not user32.PeekMessageW(ctypes.byref(msg), w.HWND(self._hwnd), 0, 0, PM_REMOVE):
                    break
                if msg.message == WM_TRAY:
                    event = int(msg.lParam) & 0xFFFF
                    if event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                        defer_menu = True
                    self._note_tray_event(event)
                elif msg.message == WM_TRAY_SHOWMENU:
                    self._pending = TRAY_ACTION_MENU
                else:
                    self._window_proc(msg.hWnd, msg.message, msg.wParam, msg.lParam)
        except OSError:
            # A display/Explorer transition can invalidate one shell callback;
            # keep the Tk pump alive and let TaskbarCreated re-register it.
            self._registered = False
            self._refresh_icon()
        action = self._pending
        if action == TRAY_ACTION_MENU and defer_menu:
            return  # The next scheduled pump opens the menu once.
        self._pending = None
        if action == TRAY_ACTION_SHOW:
            self._on_show()
        elif action == TRAY_ACTION_MENU:
            self._show_menu()

    def _show_menu(self) -> None:
        """Popup context menu at the cursor (Show / Quit)."""
        if self._hwnd is None:
            return
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        choice = 0
        try:
            user32.AppendMenuW(menu, MF_STRING, TRAY_CMD_SHOW, "開啟視窗")
            user32.AppendMenuW(menu, MF_STRING, TRAY_CMD_QUIT, "結束")
            pos = w.POINT()
            user32.GetCursorPos(ctypes.byref(pos))
            # Hidden owners must take foreground or the menu will not dismiss
            self._force_foreground(self._hwnd)
            choice = int(
                user32.TrackPopupMenu(
                    menu, TPM_RIGHTBUTTON | TPM_RETURNCMD, pos.x, pos.y, 0,
                    self._hwnd, None,
                )
            )
            user32.PostMessageW(self._hwnd, WM_NULL, 0, 0)
        finally:
            user32.DestroyMenu(menu)
        if choice == TRAY_CMD_SHOW:
            self._on_show()
        elif choice == TRAY_CMD_QUIT:
            self._on_quit()

    def destroy(self) -> None:
        """Remove the tray icon and destroy the owner window."""
        if self._data is not None:
            try:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._data))
            except OSError:
                pass
            self._data = None
            self._registered = False
        if self._hwnd is not None:
            if self._old_wndproc:
                try:
                    SetWindowLongPtr(self._hwnd, GWLP_WNDPROC, self._old_wndproc)
                except OSError:
                    pass
            try:
                user32.DestroyWindow(self._hwnd)
            except OSError:
                pass
            self._hwnd = None
        self._wndproc = None
        self._old_wndproc = 0
        self._pending = None
        if self._icon and self._owns_icon:
            user32.DestroyIcon(self._icon)
        self._icon = 0
        self._owns_icon = False


class App(WindowUI, tk.Tk):
    """Main Tk UI: pick a game window, apply display modes, and poll state."""

    def __init__(self, profile_path: Path | None = None, *, startup_backend=None) -> None:
        super().__init__()
        self.title(f"{APP_NAME} v{APP_VERSION}")
        # Apply the default after the UI creates its option; hide/minimize
        # temporarily clears topmost until the tool becomes visible again.
        self.attributes("-topmost", False)
        self.bind("<Unmap>", self._on_ui_unmap, add="+")
        self.bind("<Map>", self._on_ui_map, add="+")
        self._hicon_big = 0
        self._hicon_small = 0
        self._apply_app_icon()

        self.scale = self._setup_scaling()

        self.windows: list[dict] = []
        self.sessions: dict[tuple[int, int], WindowSession] = {}
        self.profile_path = profile_path if profile_path is not None else default_profile_path()
        self.profile_error = ""
        try:
            self.profiles = load_profiles(self.profile_path)
        except (OSError, ValueError) as exc:
            self.profiles = {}
            self.profile_error = f"無法讀取設定檔：{exc}"
        self._profile_keys: list[tuple[str, str]] = []
        self._deleted_profile: WindowProfile | None = None
        self._auto_pending: dict[tuple[int, int], int] = {}
        self._auto_done: set[tuple[int, int]] = set()
        self._auto_attempts: dict[tuple[int, int], int] = {}
        self._tick_job: str | None = None
        self._discover_tick = 0
        self._tray: TrayIcon | None = None
        self._pump_job: str | None = None
        self._closing = False
        self._startup_backend = startup_backend or configure_startup
        self._startup_results: SimpleQueue = SimpleQueue()
        self._startup_state: StartupState | None = None
        self._startup_busy = False

        self._build_ui()
        self.attributes("-topmost", self.pin_window.get())
        self._refresh_profiles()
        self.refresh_windows()
        self._sync_option_states()
        self._fit_to_content()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._tick()
        self._request_startup("ensure")

    def _on_ui_unmap(self, _event=None) -> None:
        """Stop forcing the tool above other windows while minimized/hidden."""
        if _event is not None and _event.widget is not self:
            return
        try:
            self.attributes("-topmost", False)
        except tk.TclError:
            pass

    def _on_ui_map(self, _event=None) -> None:
        """Restore tool topmost state when it becomes visible again."""
        if _event is not None and _event.widget is not self:
            return
        try:
            if self.state() == "normal":
                self.attributes("-topmost", bool(self.__dict__.get("pin_window") and self.pin_window.get()))
        except tk.TclError:
            pass

    def _apply_app_icon(self) -> None:
        """Set title-bar/taskbar icons; Tk's default feather overrides the exe icon."""
        path = resolve_icon_path()
        if path is not None:
            try:
                self.iconbitmap(default=str(path))
            except tk.TclError:
                try:
                    self.iconbitmap(str(path))
                except tk.TclError:
                    pass
        self.update_idletasks()
        self._hicon_big = load_hicon(path, 32)
        self._hicon_small = load_hicon(path, 16)
        if not self._hicon_big and getattr(sys, "frozen", False):
            try:
                extracted = shell32.ExtractIconW(None, sys.executable, 0)
                if extracted and extracted != 1:
                    self._hicon_big = int(extracted)
                    self._hicon_small = self._hicon_big
            except OSError:
                pass
        apply_hwnd_icons(int(self.winfo_id()), self._hicon_big, self._hicon_small)
        # Geometry/DPI setup may reset icons; re-apply once idle
        self.after_idle(
            lambda: apply_hwnd_icons(
                int(self.winfo_id()), self._hicon_big, self._hicon_small
            )
        )

    def _setup_scaling(self) -> float:
        """Scale Tk UI from monitor DPI (no automatic scaling when DPI-aware)."""
        dpi = window_dpi(self.winfo_id())
        self.tk.call("tk", "scaling", dpi / 72.0)
        return dpi / DEFAULT_DPI

    def _px(self, value: int) -> int:
        """Convert a design pixel at 96 DPI to the current UI scale."""
        return int(round(value * self.scale))

    def _fit_to_content(self) -> None:
        """Fit the content within the work area; scroll on shorter displays."""
        self.update_idletasks()
        mx, my, work_w, work_h = monitor_area(self.winfo_id())
        width = min(self._px(640), work_w - self._px(24))
        height = min(self._px(650), work_h - self._px(56))
        self.geometry(f"{width}x{height}")
        self.minsize(min(width, self._px(600)), min(height, self._px(460)))
        self.update_idletasks()
        hwnd = int(user32.GetParent(self.winfo_id()) or self.winfo_id())
        x, y, outer_w, outer_h = get_window_rect(hwnd)
        x = max(mx, min(x, mx + work_w - outer_w))
        y = max(my, min(y, my + work_h - outer_h))
        user32.SetWindowPos(hwnd, HWND_TOP, x, y, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)

    def selected_hwnd(self) -> int | None:
        """HWND of the combo selection, or None if invalid/closed/replaced."""
        idx = self.win_combo.current()
        if idx < 0 or idx >= len(self.windows):
            return None
        item = self.windows[idx]
        hwnd = item["hwnd"]
        expected_pid = item["pid"]
        if not user32.IsWindow(hwnd):
            return None
        if get_process_id(hwnd) != expected_pid:
            return None
        return hwnd

    def _window_labels(self) -> list[str]:
        """Combo labels for the current windows list."""
        return [
            f'{item["title"][:32]} · {item["exe"] or "?"} · {item["cw"]}x{item["ch"]}'
            for item in self.windows
        ]

    def _selected_session(self) -> WindowSession | None:
        hwnd = self.selected_hwnd()
        if hwnd is None:
            return None
        return self.sessions.get((hwnd, get_process_id(hwnd)))

    def _load_session_controls(self) -> None:
        session = self._selected_session()
        self.keep_ratio.set(session.keep_ratio if session else False)
        self.cover_taskbar.set(session.cover_taskbar if session else False)
        self.watch_var.set(session.watch if session else False)
        self._sync_option_states()
        self._update_info()

    def _update_window_list(self, found: list[dict]) -> None:
        popup = self.tk.call("ttk::combobox::PopdownWindow", str(self.win_combo))
        if self.tk.getboolean(self.tk.call("winfo", "ismapped", popup)):
            return  # Keep the open menu stable until the user finishes choosing.
        old_hwnd = self.selected_hwnd()
        old_key = (old_hwnd, get_process_id(old_hwnd)) if old_hwnd is not None else None
        self.windows = [item for item in found if (
            self.show_all.get() or item["game"]
            or profile_key(item["exe_path"], item["class"]) in self.profiles
            or (item["hwnd"], item["pid"]) == old_key
            or (item["hwnd"], item["pid"]) in self.sessions
        )]
        self.win_combo["values"] = self._window_labels()
        self._target_count.set(f"偵測到 {len(self.windows)} 個視窗" if self.windows else "等待遊戲開啟")
        if self.windows:
            index = next((i for i, item in enumerate(self.windows)
                          if (item["hwnd"], item["pid"]) == old_key), 0)
            self.win_combo.current(index)
        else:
            self.win_var.set("")
        new_hwnd = self.selected_hwnd()
        new_key = (new_hwnd, get_process_id(new_hwnd)) if new_hwnd is not None else None
        if old_key != new_key or self._selected_session() is not None:
            self._load_session_controls()
        else:
            # Preserve options chosen before applying the first mode.
            self._sync_option_states()
            self._update_info()

    def refresh_windows(self) -> None:
        self._discover_windows()
        if not self.windows:
            self.status_var.set("等待遊戲視窗開啟；也可勾選「顯示所有視窗」")

    def _discover_windows(self) -> None:
        # Keep handled identities across temporary hiding/minimization. Remove
        # only dead HWND/PID pairs, so showing a window is not another launch.
        known = set(self.sessions) | set(self._auto_pending) | self._auto_done | set(self._auto_attempts)
        for key in known:
            hwnd, pid = key
            if not user32.IsWindow(hwnd) or get_process_id(hwnd) != pid:
                self.sessions.pop(key, None)
                self._auto_pending.pop(key, None)
                self._auto_attempts.pop(key, None)
                self._auto_done.discard(key)
                peek_native_snapshot(hwnd)
        for hwnd in list(_native_snapshot):
            peek_native_snapshot(hwnd)
        tracked = {hwnd for hwnd, _pid in self.sessions}
        tracked.update(item["hwnd"] for item in self.windows
                       if user32.IsWindow(item["hwnd"]) and get_process_id(item["hwnd"]) == item["pid"])
        found = _collect_candidate_windows(show_all=True, include_hwnds=tracked)
        for item in found:
            if not user32.IsIconic(item["hwnd"]):
                try:
                    capture_native_snapshot(item["hwnd"])
                except OSError:
                    pass  # A window may disappear while it is being enumerated.
        self._auto_apply_profiles(found)
        self._update_window_list(found)

    def _auto_apply_profiles(self, found: list[dict]) -> None:
        ready = set()
        for item in found:
            key = (item["hwnd"], item["pid"])
            profile = self.profiles.get(profile_key(item["exe_path"], item["class"]))
            if (profile is None or not profile.auto_apply or key in self._auto_done
                    or item["cw"] <= 0 or item["ch"] <= 0
                    or user32.IsIconic(item["hwnd"])):
                continue
            ready.add(key)
            self._auto_pending[key] = self._auto_pending.get(key, 0) + 1
            # Wait for two sightings to avoid mutating a still-starting window.
            if self._auto_pending[key] < 2:
                continue
            try:
                self._apply_profile_to_window(profile, item)
            except OSError as exc:
                attempts = self._auto_attempts.get(key, 0) + 1
                self._auto_attempts[key] = attempts
                if attempts >= 3:
                    self._auto_done.add(key)
                suffix = "請確認權限後按「立即套用」重試" if attempts >= 3 else "稍後重試"
                self.status_var.set(f"自動套用「{profile.name}」失敗：{exc}（{suffix}）")
            else:
                self._auto_done.add(key)
                self._auto_attempts.pop(key, None)
                self.status_var.set(f"已自動套用：{profile.name}／{MODE_LABELS[profile.mode]}")
        self._auto_pending = {key: count for key, count in self._auto_pending.items()
                              if key in ready and key not in self._auto_done}

    def _update_info(self) -> None:
        session = self._selected_session()
        self.mode_var.set(MODE_LABELS[session.mode if session else None])
        hwnd = self.selected_hwnd()
        if hwnd is None:
            self.info_var.set("等待遊戲視窗開啟，或勾選「顯示所有視窗」")
            self._save_hint_var.set("先選取遊戲視窗，再選擇顯示模式。")
            self._setting_size_fields = True
            self.width_var.set("")
            self.height_var.set("")
            self._setting_size_fields = False
            self._size_target_key = None
            self._size_dirty = False
            self._sync_profile_target()
            return
        if user32.IsIconic(hwnd):
            self.info_var.set("視窗已最小化 · 還原遊戲視窗後即可繼續調整")
            self._sync_profile_target()
            return
        try:
            native = native_client_size(hwnd)
            cw, ch = get_client_size(hwnd)
            x, y, _, _ = get_window_rect(hwnd)
        except OSError:
            self.info_var.set("目標視窗暫時無法讀取，請重新整理")
            return
        suffix = "尚未記錄原始狀態"
        if native is not None:
            nw, nh = native
            suffix = f"原始 {nw}×{nh}（{format_aspect(nw / nh)}）"
        self.info_var.set(f"畫面 {cw} × {ch}　位置 ({x}, {y})　{suffix}")
        key = (hwnd, get_process_id(hwnd))
        if key != self._size_target_key or not self._size_dirty:
            self._setting_size_fields = True
            self.width_var.set(str(cw))
            self.height_var.set(str(ch))
            self._setting_size_fields = False
            self._size_dirty = False
            self._size_target_key = key
        item = self.windows[self.win_combo.current()]
        profile = self.profiles.get(profile_key(item["exe_path"], item["class"]))
        if not session or not session.mode:
            hint = "選擇顯示模式，完成後可儲存給下次使用。"
        elif profile is None:
            hint = "這個遊戲尚未儲存設定。按右下角儲存，下次開啟自動套用。"
        elif ((session.mode, session.keep_ratio, session.cover_taskbar, session.watch)
              != (profile.mode, profile.keep_ratio, profile.cover_taskbar, profile.watch)
              or (session.mode == "free" and ((cw, ch) != profile.client_size or (x, y) != profile.position))):
            hint = "有未儲存的變更 · 按右下角「儲存目前設定」更新。"
        else:
            hint = "設定已儲存 · 下次開啟自動套用" if profile.auto_apply else "設定已儲存 · 自動套用目前暫停"
        self._save_hint_var.set(hint)
        self._sync_profile_target()

    def _sync_option_states(self) -> None:
        session = self._selected_session()
        hwnd = self.selected_hwnd()
        ready = hwnd is not None and not user32.IsIconic(hwnd)
        available = ready and session is not None and session.mode == "borderless_fs"
        self._cover_check.state(["!disabled"] if available else ["disabled"])
        self._cover_hint.configure(
            text="使用完整螢幕範圍" if available else "無邊框全螢幕可用"
        )
        for mode, button in self._mode_buttons.items():
            button.state(["!disabled"] if ready else ["disabled"])
            button.configure(style="Selected.Mode.TButton" if session and session.mode == mode else "Mode.TButton")
        for widget in (self._ratio_check, self._size_apply_button, self._width_entry,
                       self._height_entry, self._size_presets):
            widget.state(["!disabled"] if ready else ["disabled"])
        applied = bool(ready and session and session.mode)
        self._watch_check.state(["!disabled"] if applied else ["disabled"])
        self._save_button.state(["!disabled"] if applied and not self.profile_error else ["disabled"])
        self._restore_button.state(["!disabled"] if ready and peek_native_snapshot(hwnd) else ["disabled"])

    def on_target_changed(self) -> None:
        self._load_session_controls()
        hwnd = self.selected_hwnd()
        if hwnd is not None:
            item = self.windows[self.win_combo.current()]
            key = profile_key(item["exe_path"], item["class"])
            if key in self._profile_keys:
                self.profile_combo.current(self._profile_keys.index(key))
                self._show_profile()
        self.status_var.set("已切換目標視窗；其他視窗繼續維持各自設定")

    def _require_target(self) -> int | None:
        hwnd = self.selected_hwnd()
        if hwnd is None:
            messagebox.showwarning("提示", "請先選取要調整的遊戲視窗", parent=self)
        elif user32.IsIconic(hwnd):
            messagebox.showwarning("視窗已最小化", "請先還原遊戲視窗，再進行調整。", parent=self)
            return None
        return hwnd

    def _manual_apply(self, mode: str) -> bool:
        hwnd = self._require_target()
        if hwnd is None:
            return False
        key = (hwnd, get_process_id(hwnd))
        # An explicit action wins over any pending automatic application.
        self._auto_done.add(key)
        session = self.sessions.setdefault(key, WindowSession(*key))
        session.keep_ratio = self.keep_ratio.get()
        session.cover_taskbar = self.cover_taskbar.get()
        session.watch = self.watch_var.get()
        try:
            session.apply(mode)
            self.status_var.set(f"已套用{MODE_LABELS[mode]}；可儲存供下次開啟使用")
        except OSError as exc:
            session.clear()
            messagebox.showerror("失敗", str(exc), parent=self)
            self._load_session_controls()
            return False
        self._size_dirty = False
        self._load_session_controls()
        return True

    def _on_size_edit(self, *_args) -> None:
        if not self._setting_size_fields:
            self._size_dirty = True

    def on_size_preset(self) -> None:
        width, height = self._size_presets.get().split(" × ")
        self.width_var.set(width)
        self.height_var.set(height)

    def on_apply_size(self) -> None:
        try:
            width, height = int(self.width_var.get()), int(self.height_var.get())
            if not (1 <= width <= 32768 and 1 <= height <= 32768):
                raise ValueError
        except ValueError:
            messagebox.showwarning("尺寸無效", "請輸入 1 到 32768 之間的整數尺寸。", parent=self)
            return
        hwnd = self._require_target()
        if hwnd is None:
            return
        if self.keep_ratio.get():
            aspect = original_aspect(hwnd)
            if aspect:
                width, height = size_for_aspect(width, height, aspect)
        if width > 32768 or height > 32768:
            messagebox.showwarning("尺寸無效", "保持比例後的尺寸超過 32768，請降低寬度。", parent=self)
            return
        if not self._manual_apply("free"):
            return
        session = self._selected_session()
        if session is None:
            return
        try:
            resize_client(session.hwnd, width, height)
            session.target_client = get_client_size(session.hwnd)
            self._size_dirty = False
            self._update_info()
            cw, ch = session.target_client
            self.status_var.set(f"已調整為 {cw} × {ch}；需要下次沿用時請儲存。")
        except OSError as exc:
            session.clear()
            self._load_session_controls()
            messagebox.showerror("調整失敗", str(exc), parent=self)

    def on_borderless_fullscreen(self) -> None:
        self._manual_apply("borderless_fs")

    def on_windowed_fullscreen(self) -> None:
        self._manual_apply("windowed_fs")

    def on_enable_resize(self) -> None:
        self._manual_apply("free")

    def on_restore(self) -> None:
        hwnd = self._require_target()
        if hwnd is None:
            return
        key = (hwnd, get_process_id(hwnd))
        self._auto_done.add(key)
        session = self.sessions.pop(key, None)
        if session is not None:
            session.clear()
        try:
            restored = restore_original(hwnd)
            self.status_var.set("已還原原狀；儲存的設定將於下次開啟時套用" if restored
                                else "沒有原始狀態紀錄，未做變更")
        except OSError as exc:
            messagebox.showerror("失敗", str(exc), parent=self)
        self._load_session_controls()

    def on_ratio_toggle(self) -> None:
        session = self._selected_session()
        if session is not None:
            session.keep_ratio = self.keep_ratio.get()
            try:
                if session.mode in ("borderless_fs", "windowed_fs"):
                    session.apply(session.mode)
                elif session.mode == "free" and session.keep_ratio:
                    session.snap_aspect()
            except OSError as exc:
                session.clear()
                messagebox.showerror("失敗", str(exc), parent=self)
                self._load_session_controls()
                return
        self._update_info()
        self.status_var.set("長寬比選項已更新；若要記住變更，請重新儲存設定")

    def on_cover_toggle(self) -> None:
        session = self._selected_session()
        if session is not None and session.mode == "borderless_fs":
            session.cover_taskbar = self.cover_taskbar.get()
            try:
                session.apply(session.mode)
            except OSError as exc:
                session.clear()
                messagebox.showerror("失敗", str(exc), parent=self)
                self._load_session_controls()
                return
        self.status_var.set("工作列選項已更新；若要記住變更，請重新儲存設定")

    def on_watch_toggle(self) -> None:
        session = self._selected_session()
        if session is None or session.mode is None:
            self.watch_var.set(False)
            messagebox.showwarning("提示", "請先套用一種模式（全螢幕或自由縮放）", parent=self)
            return
        session.watch = self.watch_var.get()
        self.status_var.set("自動維持已開啟" if session.watch else "自動維持已關閉")

    def _selected_profile(self) -> WindowProfile | None:
        index = self.profile_combo.current()
        if 0 <= index < len(self._profile_keys):
            return self.profiles.get(self._profile_keys[index])
        return None

    def _refresh_profiles(self, selected_key: tuple[str, str] | None = None) -> None:
        selected = self._selected_profile()
        if selected_key is None and selected is not None:
            selected_key = selected.key
        self._profile_keys = list(self.profiles)
        self._profiles_nav.configure(text=f"已儲存設定 {len(self.profiles)}" if self.profiles else "已儲存設定")
        self.profile_combo["values"] = [
            f'{p.name[:32]} · {p.exe_path.rsplit(chr(92), 1)[-1]} · '
            f'{"自動" if p.auto_apply else "暫停"}' for p in self.profiles.values()
        ]
        if self._profile_keys:
            self.profile_combo.current(self._profile_keys.index(selected_key)
                                       if selected_key in self._profile_keys else 0)
        else:
            self.profile_combo.set("")
        self._show_profile()

    def _show_profile(self) -> None:
        profile = self._selected_profile()
        self.auto_profile_var.set(profile.auto_apply if profile else False)
        for widget in (self._auto_check, self._delete_profile_button, self._rename_profile_button):
            widget.state(["!disabled"] if profile is not None else ["disabled"])
        self._profile_name_var.set(profile.name if profile else "")
        if self.profile_error:
            self.profile_info.set(f"{self.profile_error}。原檔保留，請修復後重啟。位置：{self.profile_path}")
        elif profile is None:
            self.profile_info.set("目前沒有儲存的設定。\n返回「視窗調整」選擇模式，再按「儲存目前設定」。")
        else:
            size = f" · {profile.client_size[0]}×{profile.client_size[1]} · 位置 {profile.position}" if profile.mode == "free" else ""
            self.profile_info.set(
                f'{MODE_LABELS[profile.mode]}{size} · 比例{"鎖定" if profile.keep_ratio else "自由"}'
                f' · 工作列{"蓋住" if profile.cover_taskbar and profile.mode == "borderless_fs" else "保留"}'
                f' · 維持{"開啟" if profile.watch else "關閉"}\n'
                f'\n程式：{profile.exe_path}'
            )
        self._sync_profile_target()
        self._sync_option_states()

    def _profile_matches(self, profile: WindowProfile) -> list[int]:
        return [i for i, item in enumerate(self.windows)
                if profile_key(item["exe_path"], item["class"]) == profile.key
                and user32.IsWindow(item["hwnd"]) and get_process_id(item["hwnd"]) == item["pid"]]

    def _sync_profile_target(self) -> None:
        profile = self._selected_profile()
        matches = self._profile_matches(profile) if profile else []
        current = self.win_combo.current()
        chosen = current if current in matches else matches[0] if len(matches) == 1 else None
        ready = chosen is not None and not user32.IsIconic(self.windows[chosen]["hwnd"])
        self._apply_profile_button.state(["!disabled"] if ready else ["disabled"])
        if not profile:
            hint = "儲存設定後，可在這裡管理。"
        elif chosen is not None:
            hint = "可套用至：" + self.windows[chosen]["title"][:45] if ready else "請先還原對應的遊戲視窗。"
        elif matches:
            hint = "有多個符合的視窗，請先返回「視窗調整」選取目標。"
        else:
            hint = "對應遊戲尚未開啟；自動套用開啟後，遊戲啟動時會自動處理。"
        self._profile_target_hint.set(hint)

    def _commit_profiles(self, updated: dict) -> bool:
        if self.profile_error:
            messagebox.showerror("無法儲存", self.profile_error, parent=self)
            return False
        try:
            save_profiles(self.profile_path, updated)
        except (OSError, ValueError) as exc:
            messagebox.showerror("無法儲存", f"{exc}\n設定檔：{self.profile_path}", parent=self)
            return False
        self.profiles = updated
        return True

    def on_save_profile(self) -> None:
        hwnd = self._require_target()
        if hwnd is None:
            return
        session = self._selected_session()
        if session is None or session.mode is None:
            messagebox.showwarning("提示", "請先套用顯示模式並調整好視窗，再儲存設定。", parent=self)
            return
        exe_path = get_process_path(session.pid)
        class_name = get_class_name(hwnd)
        if not exe_path or not class_name:
            messagebox.showerror("無法辨識程式", "讀不到程式完整路徑或視窗類別，請以管理員身分執行本工具。", parent=self)
            return
        try:
            if user32.IsIconic(hwnd):
                raise OSError("請先還原最小化的目標視窗再儲存")
            size = get_client_size(hwnd)
            x, y, _, _ = get_window_rect(hwnd)
            old = self.profiles.get(profile_key(exe_path, class_name))
            profile = WindowProfile(
                name=old.name if old else get_window_title(hwnd) or Path(exe_path).stem,
                exe_path=exe_path, class_name=class_name, mode=session.mode,
                keep_ratio=session.keep_ratio, cover_taskbar=session.cover_taskbar,
                watch=session.watch, auto_apply=old.auto_apply if old else True,
                client_size=size, position=(x, y),
            )
            if not self._commit_profiles({**self.profiles, profile.key: profile}):
                return
        except OSError as exc:
            messagebox.showerror("無法儲存", str(exc), parent=self)
            return
        self._auto_done.add((hwnd, session.pid))
        self._refresh_profiles(profile.key)
        self.status_var.set(f"已儲存：{profile.name}；" + ("下次開啟會自動套用" if profile.auto_apply else "自動套用目前暫停"))

    def on_profile_auto_toggle(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            return
        updated = replace(profile, auto_apply=self.auto_profile_var.get())
        if self._commit_profiles({**self.profiles, profile.key: updated}):
            self._refresh_profiles(profile.key)
            self.status_var.set("已啟用下次開啟自動套用" if updated.auto_apply else "已暫停自動套用；目前視窗維持不變")
        else:
            self.auto_profile_var.set(profile.auto_apply)

    def on_delete_profile(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            return
        updated = dict(self.profiles)
        del updated[profile.key]
        if self._commit_profiles(updated):
            self._deleted_profile = profile
            self._undo_profile_button.state(["!disabled"])
            # The old selection may no longer exist in the updated dictionary.
            self.profile_combo.set("")
            self._refresh_profiles()
            self.status_var.set(f"已刪除：{profile.name}；目前視窗維持不變")

    def on_undo_delete(self) -> None:
        profile = self._deleted_profile
        if profile is None:
            return
        if profile.key in self.profiles:
            self.status_var.set("此程式已有新的設定，未覆蓋；舊設定仍可稍後復原。")
            return
        if self._commit_profiles({**self.profiles, profile.key: profile}):
            self._deleted_profile = None
            self._undo_profile_button.state(["disabled"])
            self._refresh_profiles(profile.key)
            self.status_var.set(f"已復原設定：{profile.name}")

    def on_rename_profile(self) -> None:
        profile = self._selected_profile()
        name = self._profile_name_var.get().strip()
        if profile is None:
            return
        if not name or len(name) > 80:
            messagebox.showwarning("名稱無效", "請輸入 1 到 80 個字的設定名稱。", parent=self)
            return
        if self._commit_profiles({**self.profiles, profile.key: replace(profile, name=name)}):
            self._refresh_profiles(profile.key)
            self.status_var.set(f"已重新命名：{name}")

    def _apply_profile_to_window(self, profile: WindowProfile, item: dict) -> None:
        hwnd, pid = item["hwnd"], item["pid"]
        if (not user32.IsWindow(hwnd) or get_process_id(hwnd) != pid
                or profile_key(get_process_path(pid), get_class_name(hwnd)) != profile.key):
            raise OSError("目標視窗已關閉或程式身分已改變")
        key = (hwnd, pid)
        session = self.sessions.setdefault(key, WindowSession(*key))
        try:
            session.apply(profile.mode, profile)
        except OSError:
            session.clear()
            raise

    def on_apply_profile(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            return
        matches = self._profile_matches(profile)
        if self.win_combo.current() not in matches and len(matches) == 1:
            self.win_combo.current(matches[0])
            self._load_session_controls()
        if self._require_target() is None:
            return
        item = self.windows[self.win_combo.current()]
        if profile_key(item["exe_path"], item["class"]) != profile.key:
            messagebox.showwarning("程式不符", "請返回「視窗調整」，選取此設定對應的程式視窗。", parent=self)
            return
        key = (item["hwnd"], item["pid"])
        self._auto_done.add(key)
        try:
            self._apply_profile_to_window(profile, item)
            self.status_var.set(f"已套用儲存設定：{profile.name}")
        except OSError as exc:
            messagebox.showerror("套用失敗", str(exc), parent=self)
        self._load_session_controls()

    def _tick(self) -> None:
        if self._closing:
            return
        try:
            self._tick_body()
        except OSError as exc:
            self.status_var.set(f"操作失敗：{exc}")
        if not self._closing:
            self._tick_job = self.after(TICK_MS, self._tick)

    def _tick_body(self) -> None:
        self._finish_startup_requests()
        self._discover_tick += 1
        if self._discover_tick >= DISCOVER_EVERY_N_TICKS:
            self._discover_tick = 0
            self._discover_windows()
        for session in list(self.sessions.values()):
            try:
                session.tick()
            except OSError as exc:
                session.clear()
                self.status_var.set(f"視窗操作失敗，已停止維持：{exc}")
                if session is self._selected_session():
                    self._load_session_controls()
        self._update_info()
        self._sync_option_states()

    def _request_startup(self, operation: str) -> None:
        """Keep scheduler I/O off the Tk thread; only the poll loop updates UI."""
        if self._startup_busy:
            return
        self._startup_busy = True
        self._startup_check.state(["disabled"])
        self._startup_retry.pack_forget()
        self._startup_hint.configure(text="正在讀取…" if operation == "query" else "正在更新開機啟動設定…")
        backend, results = self._startup_backend, self._startup_results

        def work() -> None:
            state, error = None, None
            try:
                state = backend(operation)
            except Exception as exc:
                error = exc
                # A timed-out write might have completed. Read actual OS state
                # instead of pretending the previous checkbox value is correct.
                if operation != "query":
                    try:
                        state = backend("query")
                        if ((operation in ("enable", "ensure") and state.enabled and state.matches_current)
                                or (operation == "disable" and not state.enabled)):
                            error = None  # A verified read-back confirms the requested result.
                    except Exception:
                        pass
            results.put((operation, state, error))

        Thread(target=work, name="MWT-startup-settings", daemon=True).start()

    def _finish_startup_requests(self) -> None:
        try:
            operation, state, error = self._startup_results.get_nowait()
        except Empty:
            return
        self._startup_busy = False
        self._startup_state = state
        self._startup_retry_operation = "ensure" if operation == "ensure" and error else "query"
        self.startup_var.set(state.enabled if state is not None else False)
        self._startup_check.state(["!disabled"] if state is not None else ["disabled"])
        if error:
            permission_denied = isinstance(error, PermissionError)
            hint = ("權限不足，請以管理員身分執行並重試" if permission_denied
                    else "無法完成預設開機啟動設定，請重試" if operation == "ensure"
                    else "無法確認開機啟動設定，請重新讀取狀態" if operation != "query"
                    else "無法讀取開機啟動狀態，請重試")
            self._startup_hint.configure(text=hint)
            self._startup_retry.configure(text="重試")
            self._startup_retry.pack(side=tk.RIGHT, padx=(self._px(4), 0))
            self.status_var.set(f"開機啟動設定失敗：{error}")
            if operation not in ("query", "ensure"):
                guidance = ("請以管理員身分執行 MWT 後再試。" if permission_denied
                            else "請按「重試」重新讀取目前狀態。")
                messagebox.showerror(
                    "開機啟動設定失敗", f"{error}\n\n{guidance}", parent=self,
                )
        elif state is not None and state.enabled and not state.matches_current:
            self._startup_hint.configure(text="已啟用，但啟動位置或設定不同；可更新為目前程式")
            self._startup_retry.configure(text="更新位置")
            self._startup_retry.pack(side=tk.RIGHT, padx=(self._px(4), 0))
        else:
            self._startup_hint.configure(text="登入 Windows 後自動開啟本程式（需管理員權限）")
            if operation != "query":
                self.status_var.set("已啟用開機自動啟動，下次登入 Windows 生效" if state and state.enabled
                                    else "已關閉開機自動啟動")

    def on_startup_toggle(self) -> None:
        self._request_startup("enable" if self.startup_var.get() else "disable")

    def on_startup_retry(self) -> None:
        operation = ("enable" if self._startup_retry.cget("text") == "更新位置"
                     else getattr(self, "_startup_retry_operation", "query"))
        self._request_startup(operation)

    def on_tray_toggle(self) -> None:
        """Status feedback when minimize-to-tray is toggled."""
        state = "縮到系統匣" if self.minimize_to_tray.get() else "直接結束程式"
        self.status_var.set(f"按關閉時{state}")

    def on_pin_toggle(self) -> None:
        self.attributes("-topmost", self.pin_window.get())
        self.status_var.set("工具視窗保持置頂" if self.pin_window.get() else "工具視窗不再置頂")

    def on_close(self) -> None:
        """Hide to tray (after() ticks keep running) or quit if tray is off."""
        if not self.minimize_to_tray.get():
            self.quit_app()
            return
        self.hide_to_tray()

    def hide_to_tray(self) -> None:
        if self._tray is None:
            self._tray = TrayIcon(
                f"{APP_NAME} v{APP_VERSION}", self.show_from_tray, self.quit_app
            )
        if not self._tray.create():
            self._tray = None
            messagebox.showerror("無法縮到系統匣", "通知區圖示建立失敗，工具會保持開啟。", parent=self)
            return
        self.withdraw()
        if self._pump_job is None:
            self._pump()

    def show_from_tray(self) -> None:
        """Restore the main window from the tray icon."""
        if self._pump_job is not None:
            self.after_cancel(self._pump_job)
            self._pump_job = None
        if self._tray is not None:
            self._tray.destroy()
        self.deiconify()
        self.lift()
        self.focus_force()

    def _pump(self) -> None:
        """Tray message pump; denser than TICK_MS so clicks feel responsive."""
        if self._tray is not None and self._tray.alive:
            self._tray.pump()
            if not self._closing and self._tray is not None and self._tray.alive:
                self._pump_job = self.after(50, self._pump)
                return
        self._pump_job = None

    def quit_app(self) -> None:
        """Tear down timers/tray and clear managed topmost before destroy."""
        if self._closing:
            return
        self._closing = True
        for job in (self._tick_job, self._pump_job):
            if job is not None:
                self.after_cancel(job)
        self._tick_job = None
        self._pump_job = None
        if self._tray is not None:
            self._tray.destroy()
            self._tray = None
        for session in self.sessions.values():
            session.clear()
        self.destroy()
        for icon in {self._hicon_big, self._hicon_small} - {0}:
            user32.DestroyIcon(icon)
        self._hicon_big = self._hicon_small = 0


if __name__ == "__main__":
    # Keep the taskbar from adopting python.exe / Tk feather branding
    try:
        shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass
    # Per-monitor DPI awareness for accurate screen coordinates
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass
    App().mainloop()
