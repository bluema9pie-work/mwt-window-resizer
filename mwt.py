#!/usr/bin/env python3
"""Unlock game window sizing via Win32 APIs.

Provides borderless/windowed fullscreen, free resize, aspect-ratio lock,
auto-maintain, and system-tray background mode. No game files are patched
and no DLL injection is used.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import sys
from pathlib import Path
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk

APP_NAME = "MWT遊戲視窗調整工具"
APP_VERSION = "1.5"

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
gdi32 = ctypes.windll.gdi32
shell32 = ctypes.windll.shell32

# Win32 constants
GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_MAXIMIZEBOX = 0x00010000
WS_MINIMIZEBOX = 0x00020000
WS_SYSMENU = 0x00080000
WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000
WS_BORDER = 0x00800000
WS_DLGFRAME = 0x00400000
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
VK_LBUTTON = 0x01
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
HWND_TOP = 0
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
user32.GetClientRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
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
user32.GetClientRect.restype = w.BOOL
user32.SetWindowPos.restype = w.BOOL
user32.MoveWindow.restype = w.BOOL
user32.MonitorFromWindow.argtypes = [w.HWND, ctypes.c_uint]
user32.MonitorFromWindow.restype = w.HMONITOR
user32.GetForegroundWindow.restype = w.HWND
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
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


def get_process_name(pid: int) -> str:
    """Return the executable file name for a process, or empty if inaccessible."""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = w.DWORD(260)
        buf = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return buf.value.rsplit("\\", 1)[-1]
    finally:
        kernel32.CloseHandle(handle)


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
    chosen manually via「列出所有視窗」.
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


def _collect_candidate_windows(show_all: bool = False) -> list[dict]:
    """Enumerate visible candidate windows (no snapshot side effects).

    By default only likely game windows are returned (prioritized). When
    show_all is True, other visible windows above MIN_LISTED_SIZE are included.
    """
    found: list[dict] = []
    own_pid = kernel32.GetCurrentProcessId()

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = get_window_title(hwnd)
        if not title:
            return True
        pid = get_process_id(hwnd)
        if pid == own_pid:
            return True
        exe = get_process_name(pid)
        class_name = get_class_name(hwnd)
        try:
            x, y, width, height = get_window_rect(hwnd)
            cw, ch = get_client_size(hwnd)
        except OSError:
            return True
        game = looks_like_game(
            title, exe, class_name, client_w=cw, client_h=ch,
        )
        if not game:
            if not show_all:
                return True
            if width < MIN_LISTED_SIZE[0] or height < MIN_LISTED_SIZE[1]:
                return True
        found.append(
            {
                "hwnd": int(hwnd),
                "title": title,
                "class": class_name,
                "pid": pid,
                "exe": exe,
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


def mouse_down() -> bool:
    """True while the left mouse button is held (drag vs game self-resize)."""
    return bool(user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)


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
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED | SWP_SHOWWINDOW,
        )
    )


def enable_resizable(hwnd: int) -> None:
    """Restore caption chrome and thick-frame so the user can drag edges."""
    remember_original(hwnd)
    style = int(GetWindowLongPtr(hwnd, GWL_STYLE))
    style |= WS_THICKFRAME | WS_MAXIMIZEBOX | WS_MINIMIZEBOX | WS_SYSMENU | WS_CAPTION
    style &= ~WS_POPUP
    _apply_style(hwnd, style)


def set_borderless(hwnd: int) -> None:
    """Strip caption/borders for borderless fullscreen (popup + visible)."""
    remember_original(hwnd)
    style = int(GetWindowLongPtr(hwnd, GWL_STYLE))
    style &= ~(
        WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX
        | WS_SYSMENU | WS_BORDER | WS_DLGFRAME
    )
    style |= WS_POPUP | WS_VISIBLE
    _apply_style(hwnd, style)


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
    set_topmost(hwnd, False)
    set_window_long(hwnd, GWL_STYLE, style)
    set_window_long(hwnd, GWL_EXSTYLE, exstyle & ~WS_EX_TOPMOST)
    _require_bool(
        user32.SetWindowPos(
            hwnd, HWND_NOTOPMOST, x, y, outer_w, outer_h,
            SWP_FRAMECHANGED | SWP_SHOWWINDOW,
        )
    )
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

    mx, my, mw, mh = monitor_area(hwnd, work_area=not cover)
    aspect = original_aspect(hwnd)
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
        """Add the tray icon. Returns False so the caller can quit instead."""
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
                SetWindowLongPtr(
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
            return hicon
        try:
            icon = shell32.ExtractIconW(None, sys.executable, 0)
            if icon and icon != 1:
                return int(icon)
        except OSError:
            pass
        return int(user32.LoadIconW(None, IDI_APPLICATION) or 0)

    def _note_tray_event(self, event: int) -> None:
        """Map WM_* click codes to a pending action for the Tk thread."""
        if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
            self._pending = TRAY_ACTION_SHOW
        elif event in (WM_RBUTTONDOWN, WM_RBUTTONUP, WM_CONTEXTMENU):
            # Defer menu so the notification overflow flyout can dismiss first
            if self._hwnd is not None:
                user32.PostMessageW(self._hwnd, WM_TRAY_SHOWMENU, 0, 0)

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
        Both paths converge on _pending. Right-click menus are posted as
        WM_TRAY_SHOWMENU and shown on the next pump when possible.
        """
        if self._hwnd is None:
            return
        msg = w.MSG()
        # If right-click posts SHOWMENU in this same Peek drain, re-post it so
        # the shell overflow panel can close before TrackPopupMenu runs.
        defer_menu = False
        try:
            while user32.PeekMessageW(
                ctypes.byref(msg), w.HWND(self._hwnd), 0, 0, PM_REMOVE
            ):
                if msg.message == WM_TRAY:
                    event = int(msg.lParam) & 0xFFFF
                    if event in (WM_RBUTTONDOWN, WM_RBUTTONUP, WM_CONTEXTMENU):
                        defer_menu = True
                    self._note_tray_event(event)
                elif msg.message == WM_TRAY_SHOWMENU:
                    if defer_menu:
                        user32.PostMessageW(self._hwnd, WM_TRAY_SHOWMENU, 0, 0)
                    else:
                        self._pending = TRAY_ACTION_MENU
                elif self._old_wndproc:
                    user32.CallWindowProcW(
                        self._old_wndproc, msg.hwnd, msg.message, msg.wParam, msg.lParam
                    )
        except OSError:
            # A display/Explorer transition can invalidate one shell callback;
            # keep the Tk pump alive and let TaskbarCreated re-register it.
            self._registered = False
            self._refresh_icon()
        action = self._pending
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
        self._icon = 0


class App(tk.Tk):
    """Main Tk UI: pick a game window, apply display modes, and poll state."""

    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME} v{APP_VERSION}")
        # Keep the adjustment tool above the game while it is visible.  Tk's
        # topmost flag is cleared while minimized/hidden and restored on Map.
        self.attributes("-topmost", True)
        self.bind("<Unmap>", self._on_ui_unmap, add="+")
        self.bind("<Map>", self._on_ui_map, add="+")
        self._hicon_big = 0
        self._hicon_small = 0
        self._apply_app_icon()

        self.scale = self._setup_scaling()

        self.windows: list[dict] = []
        # Last applied mode; auto-maintain re-applies this shape
        self._mode: str | None = None
        self._mode_hwnd: int | None = None
        self._mode_pid: int | None = None
        self._target_client: tuple[int, int] | None = None
        self._applied_rect: tuple[int, int, int, int] | None = None
        self._dragging = False
        self._drag_start: tuple[int, int] | None = None
        self._drag_last: tuple[int, int] | None = None
        self._tick_job: str | None = None
        self._discover_tick = 0
        self._tray: TrayIcon | None = None
        self._pump_job: str | None = None

        self._build_ui()
        self.refresh_windows()
        self._sync_option_states()
        self._fit_to_content()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._tick()

    def _on_ui_unmap(self, _event=None) -> None:
        """Stop forcing the tool above other windows while minimized/hidden."""
        try:
            self.attributes("-topmost", False)
        except tk.TclError:
            pass

    def _on_ui_map(self, _event=None) -> None:
        """Restore tool topmost state when it becomes visible again."""
        try:
            if self.state() == "normal":
                self.attributes("-topmost", True)
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
        """Size the window to its requested content (no empty or clipped edges)."""
        self.update_idletasks()
        width = max(self._px(500), self.winfo_reqwidth())
        height = self.winfo_reqheight()
        self.geometry(f"{width}x{height}")
        self.minsize(width, height)

    def _setup_styles(self) -> None:
        """Configure ttk fonts and shared widget styles."""
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        base = tkfont.nametofont("TkDefaultFont")
        family, size = base.cget("family"), abs(int(base.cget("size")))
        style.configure("Hint.TLabel", foreground="#5a5a5a")
        style.configure("Value.TLabel", font=(family, size, "bold"))
        style.configure("Group.TLabelframe.Label", font=(family, size, "bold"))
        style.configure("Mode.TButton", width=13, padding=(self._px(4), self._px(5)))
        style.configure("Status.TLabel", foreground="#333", padding=(self._px(6), self._px(4)))

    def _build_ui(self) -> None:
        """Build the three main groups and the status bar."""
        self._setup_styles()
        gap = self._px(10)

        root = ttk.Frame(self, padding=self._px(12))
        root.pack(fill=tk.BOTH, expand=True)

        self._build_target_group(root, gap)
        self._build_mode_group(root, gap)
        self._build_option_group(root, gap)

        self.status_var = tk.StringVar(value=f"{APP_NAME} v{APP_VERSION}｜就緒")
        ttk.Label(
            root, textvariable=self.status_var, style="Status.TLabel",
            relief=tk.SUNKEN, anchor=tk.W,
        ).pack(fill=tk.X, side=tk.BOTTOM)

    def _build_target_group(self, parent: ttk.Frame, gap: int) -> None:
        """Section 1: window picker, refresh, and show-all toggle."""
        group = ttk.LabelFrame(
            parent, text=" 1. 選擇遊戲視窗 ", style="Group.TLabelframe", padding=self._px(10)
        )
        group.pack(fill=tk.X, pady=(0, gap))

        row = ttk.Frame(group)
        row.pack(fill=tk.X)
        self.win_var = tk.StringVar()
        self.win_combo = ttk.Combobox(row, textvariable=self.win_var, state="readonly")
        self.win_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.win_combo.bind("<<ComboboxSelected>>", lambda _e: self.on_target_changed())
        ttk.Button(row, text="重新整理", command=self.refresh_windows).pack(
            side=tk.LEFT, padx=(self._px(6), 0)
        )

        self.show_all = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            group,
            text="找不到遊戲？列出所有視窗",
            variable=self.show_all,
            command=self.refresh_windows,
        ).pack(anchor=tk.W, pady=(self._px(6), 0))

        self.info_var = tk.StringVar(value="尚未選取視窗")
        ttk.Label(group, textvariable=self.info_var, style="Hint.TLabel").pack(anchor=tk.W)

    def _build_mode_group(self, parent: ttk.Frame, gap: int) -> None:
        """Section 2: display-mode buttons and current-mode label."""
        group = ttk.LabelFrame(
            parent, text=" 2. 選擇顯示模式 ", style="Group.TLabelframe", padding=self._px(10)
        )
        group.pack(fill=tk.X, pady=(0, gap))
        group.columnconfigure(1, weight=1)

        modes = [
            ("無邊框全螢幕", self.on_borderless_fullscreen, "去掉標題列，鋪滿整個螢幕"),
            ("有邊框全螢幕", self.on_windowed_fullscreen, "保留標題列，填滿工作列以外的範圍"),
            ("自由縮放", self.on_enable_resize, "解除鎖定，之後可用滑鼠拖曳視窗邊框"),
            ("還原原狀", self.on_restore, "回到遊戲原始視窗樣式、位置與預設尺寸"),
        ]
        for i, (text, command, hint) in enumerate(modes):
            ttk.Button(group, text=text, style="Mode.TButton", command=command).grid(
                row=i, column=0, sticky=tk.W, pady=self._px(2)
            )
            ttk.Label(group, text=hint, style="Hint.TLabel").grid(
                row=i, column=1, sticky=tk.W, padx=(self._px(10), 0)
            )

        current = ttk.Frame(group)
        current.grid(row=len(modes), column=0, columnspan=2, sticky=tk.W, pady=(self._px(8), 0))
        ttk.Label(current, text="目前模式：").pack(side=tk.LEFT)
        self.mode_var = tk.StringVar(value=MODE_LABELS[None])
        ttk.Label(current, textvariable=self.mode_var, style="Value.TLabel").pack(side=tk.LEFT)

        ttk.Label(
            group,
            text="畫面是被拉伸的，遊戲內部解析度不變；想要更清晰可先在遊戲內把解析度調高。",
            style="Hint.TLabel",
            wraplength=self._px(470),
            justify=tk.LEFT,
        ).grid(row=len(modes) + 1, column=0, columnspan=2, sticky=tk.W, pady=(self._px(4), 0))

    def _build_option_group(self, parent: ttk.Frame, gap: int) -> None:
        """Section 3: aspect lock, cover taskbar, auto-maintain, tray close."""
        group = ttk.LabelFrame(
            parent, text=" 3. 選項 ", style="Group.TLabelframe", padding=self._px(10)
        )
        group.pack(fill=tk.X, pady=(0, gap))
        group.columnconfigure(1, weight=1)

        self.keep_ratio = tk.BooleanVar(value=False)
        self.cover_taskbar = tk.BooleanVar(value=False)
        self.watch_var = tk.BooleanVar(value=False)
        self.minimize_to_tray = tk.BooleanVar(value=True)

        options = [
            (
                "保持原始長寬比",
                self.keep_ratio,
                self.on_ratio_toggle,
                "鎖定遊戲預設比例（一進清單就記住），全螢幕留白／拖曳即時修正",
            ),
            ("蓋住工作列", self.cover_taskbar, self.on_cover_toggle, "僅無邊框全螢幕可用"),
            ("自動維持", self.watch_var, self.on_watch_toggle, "尺寸被遊戲改回時自動套用回來"),
            (
                "關閉時縮到系統匣",
                self.minimize_to_tray,
                self.on_tray_toggle,
                "按 X 後留在通知區繼續維持設定",
            ),
        ]
        widgets = []
        for i, (text, var, command, hint) in enumerate(options):
            check = ttk.Checkbutton(group, text=text, variable=var, command=command)
            check.grid(row=i, column=0, sticky=tk.W, pady=self._px(2))
            label = ttk.Label(group, text=hint, style="Hint.TLabel")
            label.grid(row=i, column=1, sticky=tk.W, padx=(self._px(10), 0))
            widgets.append((check, label))

        self._cover_check, self._cover_hint = widgets[1]

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

    def refresh_windows(self) -> None:
        """Re-enumerate windows and reset the current mode selection."""
        self.windows = enum_top_level_windows(show_all=self.show_all.get())
        labels = self._window_labels()
        self.win_combo["values"] = labels
        self._reset_mode()
        if labels:
            self.win_combo.current(0)
            self._update_info()
            self.status_var.set(f"找到 {len(labels)} 個視窗")
        else:
            self.win_var.set("")
            self.info_var.set("找不到遊戲視窗，請先開啟遊戲後重新整理，或勾選「列出所有視窗」")
            self.status_var.set("未找到視窗")

    def _quiet_refresh_windows(self) -> None:
        """Fill an empty combo after auto-discover without clearing mode state."""
        self.windows = enum_top_level_windows(show_all=self.show_all.get())
        labels = self._window_labels()
        self.win_combo["values"] = labels
        if labels:
            self.win_combo.current(0)
            self._update_info()
            self.status_var.set(f"已自動發現 {len(labels)} 個視窗")
        else:
            self.win_var.set("")
            self.info_var.set("找不到遊戲視窗，請先開啟遊戲後重新整理，或勾選「列出所有視窗」")

    def _update_info(self) -> None:
        """Refresh the hint line under the window combo (size/pos/native)."""
        hwnd = self.selected_hwnd()
        if hwnd is None:
            return
        try:
            # Peek only: never create/rebind snapshots from the poll loop
            peek_native_snapshot(hwnd)
            cw, ch = get_client_size(hwnd)
            x, y, _, _ = get_window_rect(hwnd)
        except OSError:
            self.info_var.set("目標視窗暫時無法讀取，請重新整理")
            return
        native = native_client_size(hwnd)
        if native is not None:
            nw, nh = native
            aspect = format_aspect(nw / nh) if nh else "?"
            self.info_var.set(
                f"畫面 {cw} × {ch}　位置 ({x}, {y})　原始 {nw}×{nh}（{aspect}）"
            )
        else:
            self.info_var.set(
                f"畫面 {cw} × {ch}　位置 ({x}, {y})　"
                "尚未記錄原始狀態（請等待偵測或重新整理）"
            )
        self.mode_var.set(MODE_LABELS.get(self._mode, MODE_LABELS[None]))

    def _sync_option_states(self) -> None:
        """Enable "cover taskbar" only while borderless fullscreen is active."""
        available = self._mode == "borderless_fs"
        self._cover_check.state(["!disabled"] if available else ["disabled"])
        self._cover_hint.configure(
            text="僅無邊框全螢幕可用" if available else "先按「無邊框全螢幕」才能調整"
        )

    def _reset_mode(self) -> None:
        """Clear applied mode state and drop leftover topmost on the old target."""
        old = self._mode_hwnd
        old_pid = self._mode_pid
        # Only touch topmost when HWND still belongs to the managed process
        if (
            old is not None
            and old_pid is not None
            and user32.IsWindow(old)
            and get_process_id(old) == old_pid
            and is_topmost(old)
        ):
            try:
                set_topmost(old, False)
            except OSError:
                pass
        self._mode = None
        self._mode_hwnd = None
        self._mode_pid = None
        self._target_client = None
        self._applied_rect = None
        self._dragging = False
        self._drag_start = None
        self._drag_last = None
        if self.watch_var.get():
            self.watch_var.set(False)
        self._sync_option_states()

    def on_target_changed(self) -> None:
        """Combo selection changed: snapshot the new window and clear mode."""
        self._reset_mode()
        hwnd = self.selected_hwnd()
        if hwnd is not None:
            capture_native_snapshot(hwnd)
        self._update_info()
        self.status_var.set("已切換目標視窗")

    def _require_target(self) -> int | None:
        """Return selected HWND or warn if nothing is selected."""
        hwnd = self.selected_hwnd()
        if hwnd is None:
            messagebox.showwarning("提示", "請先選取要調整的遊戲視窗")
        return hwnd

    def _active_mode_hwnd(self) -> int | None:
        """Return the managed HWND only if it still belongs to the recorded PID."""
        hwnd = self._mode_hwnd
        if hwnd is None or self._mode_pid is None:
            return None
        if not user32.IsWindow(hwnd) or get_process_id(hwnd) != self._mode_pid:
            self._reset_mode()
            self.status_var.set("目標視窗已關閉或已被替換，請重新整理")
            return None
        return hwnd

    def _apply_fullscreen(self, hwnd: int, borderless: bool) -> None:
        """Apply fullscreen for the current option flags and record mode state."""
        fill_monitor(
            hwnd,
            borderless=borderless,
            keep_ratio=self.keep_ratio.get(),
            cover_taskbar=self.cover_taskbar.get(),
        )
        self._mode = "borderless_fs" if borderless else "windowed_fs"
        self._mode_hwnd = hwnd
        self._mode_pid = get_process_id(hwnd)
        self._applied_rect = get_window_rect(hwnd)
        self._target_client = get_client_size(hwnd)
        self._sync_option_states()

    def on_borderless_fullscreen(self) -> None:
        """UI: borderless fullscreen."""
        hwnd = self._require_target()
        if hwnd is None:
            return
        try:
            self._apply_fullscreen(hwnd, borderless=True)
            self.status_var.set("已切換無邊框全螢幕")
            self._update_info()
        except OSError as exc:
            messagebox.showerror("失敗", str(exc))

    def on_windowed_fullscreen(self) -> None:
        """UI: windowed fullscreen (work area, keep caption)."""
        hwnd = self._require_target()
        if hwnd is None:
            return
        try:
            self._apply_fullscreen(hwnd, borderless=False)
            self.status_var.set("已切換有邊框全螢幕")
            self._update_info()
        except OSError as exc:
            messagebox.showerror("失敗", str(exc))

    def on_enable_resize(self) -> None:
        """UI: unlock thick-frame free resize (optional aspect snap)."""
        hwnd = self._require_target()
        if hwnd is None:
            return
        try:
            enable_resizable(hwnd)
            set_topmost(hwnd, False)
            self._mode = "free"
            self._mode_hwnd = hwnd
            self._mode_pid = get_process_id(hwnd)
            self._applied_rect = None
            self._target_client = get_client_size(hwnd)
            self._dragging = False
            self._drag_last = None
            if self.keep_ratio.get():
                self._snap_free_aspect(hwnd)
            self._sync_option_states()
            self.status_var.set("已啟用自由縮放，可直接拖曳遊戲視窗邊框")
            self._update_info()
        except OSError as exc:
            messagebox.showerror("失敗", str(exc))

    def on_restore(self) -> None:
        """UI: restore first-seen native window state."""
        hwnd = self._require_target()
        if hwnd is None:
            return
        self._reset_mode()
        try:
            if restore_original(hwnd):
                native = native_client_size(hwnd)
                if native:
                    self.status_var.set(
                        f"已還原成遊戲原始視窗 {native[0]}×{native[1]}"
                    )
                else:
                    self.status_var.set("已還原成遊戲原始視窗")
            else:
                self.status_var.set("沒有原始狀態紀錄，未做變更")
            self._update_info()
        except OSError as exc:
            messagebox.showerror("失敗", str(exc))

    def on_ratio_toggle(self) -> None:
        """Re-apply the current mode immediately when aspect lock changes."""
        # Apply right away so the user sees the effect of the checkbox
        prev_mode = self._mode
        hwnd = self._active_mode_hwnd() if prev_mode is not None else None
        if prev_mode is not None and self._mode is None:
            return  # target died; status already set by _active_mode_hwnd
        if self._mode in ("borderless_fs", "windowed_fs") and hwnd is not None:
            try:
                self._apply_fullscreen(hwnd, self._mode == "borderless_fs")
                self._update_info()
            except OSError as exc:
                messagebox.showerror("失敗", str(exc))
        elif self._mode == "free" and hwnd is not None and self.keep_ratio.get():
            try:
                self._snap_free_aspect(hwnd)
                self._update_info()
            except OSError as exc:
                messagebox.showerror("失敗", str(exc))
        state = "開啟" if self.keep_ratio.get() else "關閉"
        aspect = original_aspect(hwnd) if hwnd is not None else None
        if state == "開啟" and aspect:
            native = native_client_size(hwnd)
            extra = f"（{format_aspect(aspect)}"
            if native:
                extra += f"／原始 {native[0]}×{native[1]}"
            extra += "）"
            self.status_var.set(f"保持原始長寬比已{state}{extra}")
        else:
            self.status_var.set(f"保持原始長寬比已{state}")

    def _snap_free_aspect(self, hwnd: int, *, prev: tuple[int, int] | None = None) -> bool:
        """Correct free-resize client size to native aspect; True if resized."""
        aspect = original_aspect(hwnd)
        if not aspect:
            return False
        cw, ch = get_client_size(hwnd)
        prev_w, prev_h = prev if prev is not None else (None, None)
        new_w, new_h = size_for_aspect(cw, ch, aspect, prev_w=prev_w, prev_h=prev_h)
        if abs(new_w - cw) <= TOLERANCE and abs(new_h - ch) <= TOLERANCE:
            self._target_client = (cw, ch)
            return False
        resize_client(hwnd, new_w, new_h)
        self._target_client = (new_w, new_h)
        self.status_var.set(
            f"已依遊戲原始比例（{format_aspect(aspect)}）修正為 {new_w}×{new_h}"
        )
        return True

    def on_cover_toggle(self) -> None:
        """Re-apply borderless fullscreen when cover-taskbar changes."""
        prev_mode = self._mode
        hwnd = self._active_mode_hwnd() if prev_mode == "borderless_fs" else None
        if prev_mode == "borderless_fs" and self._mode is None:
            return
        if self._mode == "borderless_fs" and hwnd is not None:
            try:
                self._apply_fullscreen(hwnd, borderless=True)
                self._update_info()
            except OSError as exc:
                messagebox.showerror("失敗", str(exc))
        state = "蓋住工作列" if self.cover_taskbar.get() else "保留工作列空間"
        self.status_var.set(f"無邊框全螢幕改為{state}")

    def on_watch_toggle(self) -> None:
        """Enable auto-maintain only after a mode has been applied."""
        if not self.watch_var.get():
            self.status_var.set("自動維持已關閉")
            return
        if self._mode is None:
            messagebox.showwarning("提示", "請先套用一種模式（全螢幕或自由縮放）")
            self.watch_var.set(False)
            return
        self.status_var.set("自動維持已開啟")

    def _tick(self) -> None:
        """Single poll loop for aspect correction and auto-maintain.

        Tk is not thread-safe; schedule on the main thread with after().
        """
        try:
            self._tick_body()
        except OSError as exc:
            self.status_var.set(f"操作失敗：{exc}")
        self._tick_job = self.after(TICK_MS, self._tick)

    def _tick_body(self) -> None:
        """One poll iteration: discover, refresh UI hints, then maintain mode."""
        self._discover_tick += 1
        if self._discover_tick >= DISCOVER_EVERY_N_TICKS:
            self._discover_tick = 0
            newly = discover_and_snapshot_windows(show_all=self.show_all.get())
            if (
                newly
                and not self.windows
                and self._mode is None
                and any(item["game"] for item in newly)
            ):
                self._quiet_refresh_windows()
        self._update_info()
        self._sync_option_states()
        if self._mode is None:
            return
        hwnd = self._active_mode_hwnd()
        if hwnd is None:
            return
        self._sync_topmost(hwnd)
        if self._mode == "free":
            self._tick_free(hwnd)
        else:
            self._tick_fullscreen(hwnd)

    def _sync_topmost(self, hwnd: int) -> None:
        """Keep topmost only while the game is foreground (avoid covering other apps)."""
        if self._mode != "borderless_fs" or not self.cover_taskbar.get():
            return
        want = int(user32.GetForegroundWindow() or 0) == hwnd
        if is_topmost(hwnd) != want:
            set_topmost(hwnd, want)

    def _tick_free(self, hwnd: int) -> None:
        """Free-resize poll: live aspect snap while dragging; optional maintain.

        Mouse-down covers both edge resize and title-bar / in-game clicks.
        Only client-size changes count as a resize; pure moves skip watch for
        this tick so maintain does not fight the user.
        """
        if mouse_down():
            if not self._dragging:
                self._dragging = True
                self._drag_start = get_client_size(hwnd)
                self._drag_last = self._drag_start
            elif self.keep_ratio.get():
                # Snap during the drag; do not wait for button-up
                cw, ch = get_client_size(hwnd)
                last = self._drag_last or self._drag_start
                if last is not None and (
                    abs(cw - last[0]) > TOLERANCE or abs(ch - last[1]) > TOLERANCE
                ):
                    if self._snap_free_aspect(hwnd, prev=last):
                        self._drag_last = self._target_client
                    else:
                        self._drag_last = (cw, ch)
            return

        if self._dragging:
            self._dragging = False
            start = self._drag_start
            self._drag_start = None
            self._drag_last = None
            cw, ch = get_client_size(hwnd)
            # Size change while LMB down counts as a resize (not an in-game click)
            resized = start is not None and (
                abs(cw - start[0]) > TOLERANCE or abs(ch - start[1]) > TOLERANCE
            )
            if resized:
                if self.keep_ratio.get():
                    self._snap_free_aspect(hwnd, prev=start)
                else:
                    self._target_client = (cw, ch)
            # Move / click release: skip watch this tick
            return

        if not self.watch_var.get() or self._target_client is None:
            return
        target_w, target_h = self._target_client
        cw, ch = get_client_size(hwnd)
        if abs(cw - target_w) > TOLERANCE or abs(ch - target_h) > TOLERANCE:
            enable_resizable(hwnd)
            resize_client(hwnd, target_w, target_h)
            self.status_var.set(f"已維持 {target_w}×{target_h}")

    def _tick_fullscreen(self, hwnd: int) -> None:
        """Fullscreen poll: re-apply if the game resets size (or borderless pos)."""
        if not self.watch_var.get() or self._applied_rect is None:
            return
        # Do not fight the user while they are dragging the window
        if mouse_down():
            return
        current = get_window_rect(hwnd)
        ax, ay, aw, ah = self._applied_rect
        cx, cy, cw, ch = current
        size_drift = abs(cw - aw) > TOLERANCE or abs(ch - ah) > TOLERANCE
        pos_drift = abs(cx - ax) > TOLERANCE or abs(cy - ay) > TOLERANCE
        if not size_drift and not pos_drift:
            return
        # Borderless must stay pinned; windowed FS may accept a title-bar move
        if size_drift or self._mode == "borderless_fs":
            self._apply_fullscreen(hwnd, self._mode == "borderless_fs")
            self.status_var.set("已重新套用全螢幕")
        else:
            self._applied_rect = current

    def on_tray_toggle(self) -> None:
        """Status feedback when minimize-to-tray is toggled."""
        state = "縮到系統匣" if self.minimize_to_tray.get() else "直接結束程式"
        self.status_var.set(f"按關閉時{state}")

    def on_close(self) -> None:
        """Hide to tray (after() ticks keep running) or quit if tray is off."""
        if not self.minimize_to_tray.get():
            self.quit_app()
            return
        if self._tray is None:
            self._tray = TrayIcon(
                f"{APP_NAME} v{APP_VERSION}", self.show_from_tray, self.quit_app
            )
        if not self._tray.create():
            # Fail closed: without a tray icon the process would be unreachable
            self._tray = None
            self.quit_app()
            return
        self.withdraw()
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
            self._pump_job = self.after(50, self._pump)
        else:
            self._pump_job = None

    def quit_app(self) -> None:
        """Tear down timers/tray and clear managed topmost before destroy."""
        for job in (self._tick_job, self._pump_job):
            if job is not None:
                self.after_cancel(job)
        self._tick_job = None
        self._pump_job = None
        if self._tray is not None:
            self._tray.destroy()
            self._tray = None
        # Topmost is ours to manage; leave it on and the game covers other apps forever
        hwnd = self._mode_hwnd
        pid = self._mode_pid
        if (
            hwnd is not None
            and pid is not None
            and user32.IsWindow(hwnd)
            and get_process_id(hwnd) == pid
            and is_topmost(hwnd)
        ):
            try:
                set_topmost(hwnd, False)
            except OSError:
                pass
        self.destroy()


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
