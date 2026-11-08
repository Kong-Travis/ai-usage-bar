"""Windows tray icon: a hidden window plus Shell_NotifyIconW, stdlib ctypes only.

Win32TrayBackend holds the behaviour (what the icon shows, what a click does, timers, the
single-instance rule); _Win32Api is the thin ctypes layer it talks to. Everything that needs
Windows is loaded when _Win32Api is created, never at import time, because this module is also
part of the single-file plugin that runs on macOS and Linux.
"""
from __future__ import annotations

import collections
import sys
import time
import traceback
from typing import Callable, Deque, Dict, List, Optional, Sequence, Tuple

from .i18n import EN, VI, Translator
from .tray import TrayBackend
from .view import COMPACT, CRIT_COLOR, OK_COLOR, WARN_COLOR, Action, Menu, MenuItem


class _W32:
    """The Win32 constants used here (winuser.h, shellapi.h, wingdi.h, winerror.h)."""
    WM_NULL = 0x0000
    WM_CLOSE = 0x0010
    WM_TIMER = 0x0113
    WM_LBUTTONUP = 0x0202
    WM_RBUTTONUP = 0x0205
    WM_APP = 0x8000
    NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
    NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
    NIIF_WARNING = 0x2
    MF_STRING, MF_GRAYED, MF_CHECKED, MF_SEPARATOR = 0x0, 0x1, 0x8, 0x800
    TPM_RIGHTBUTTON, TPM_NONOTIFY, TPM_RETURNCMD = 0x2, 0x80, 0x100
    SM_CXSMICON = 49
    BI_RGB, DIB_RGB_COLORS = 0, 0
    FW_BOLD, DEFAULT_CHARSET, ANTIALIASED_QUALITY, FF_SWISS = 700, 1, 4, 0x20
    TRANSPARENT = 1
    DT_CENTER, DT_VCENTER, DT_SINGLELINE, DT_NOPREFIX = 0x1, 0x4, 0x20, 0x800
    ERROR_ALREADY_EXISTS = 183


Win32Rgb = Tuple[int, int, int]
Win32MenuEntry = Tuple[int, int, str]  # (MF_* flags, command id or 0, label)

WIN32_TIP_MAX = 127                    # szTip holds 128 UTF-16 units including the NUL
WIN32_INFO_TITLE_MAX = 63              # szInfoTitle: 64 units with the NUL
WIN32_INFO_MAX = 255                   # szInfo: 256 units with the NUL
WIN32_TEXT_RGB: Win32Rgb = (0xFF, 0xFF, 0xFF)
WIN32_DARK_TEXT_RGB: Win32Rgb = (0x21, 0x21, 0x21)
WIN32_NEUTRAL_RGB: Win32Rgb = (0x45, 0x5A, 0x64)
# (background, text) of the icon per level color; yellow needs dark text to stay readable.
WIN32_LEVEL_RGB: Dict[str, Tuple[Win32Rgb, Win32Rgb]] = {
    OK_COLOR: ((0x2E, 0x7D, 0x32), WIN32_TEXT_RGB),
    WARN_COLOR: ((0xF9, 0xA8, 0x25), WIN32_DARK_TEXT_RGB),
    CRIT_COLOR: ((0xE6, 0x5C, 0x00), WIN32_TEXT_RGB),
}

# Strings of this frontend only; menu text arrives already translated in the Menu.
WIN32_TEXT = {
    "win32_tip_starting": {VI: "AI: đang khởi động...", EN: "AI: starting..."},
}


# ---------- pure helpers (tested on every OS) ----------

def win32_fit(text: str, limit: int) -> str:
    """`text` without NULs, cut to `limit` UTF-16 units (a fixed WCHAR field minus its NUL)."""
    text = text.replace("\0", "")[:limit]
    while len(text.encode("utf-16-le")) // 2 > limit:  # characters outside the BMP
        text = text[:-1]
    return text


def win32_tooltip(title: str) -> str:
    """The tray tooltip: the menu title cut to what szTip can hold."""
    return win32_fit(title, WIN32_TIP_MAX)


def win32_balloon_fields(title: str, body: str) -> Dict[str, object]:
    """NOTIFYICONDATAW members for a NIM_MODIFY that shows a warning balloon (a toast on
    Windows 10 and 11) and leaves the icon and tooltip as they are."""
    return {"uFlags": _W32.NIF_INFO, "szInfoTitle": win32_fit(title, WIN32_INFO_TITLE_MAX),
            "szInfo": win32_fit(body, WIN32_INFO_MAX) or " ",  # an empty szInfo hides it
            "dwInfoFlags": _W32.NIIF_WARNING}


def win32_icon_text(worst: Optional[float]) -> str:
    """The number drawn on the icon: the worst percentage, or "?" when nothing is known."""
    return "?" if worst is None else "%d" % round(worst)


def win32_icon_colors(title_color: Optional[str]) -> Tuple[Win32Rgb, Win32Rgb]:
    """(background, text) colors: the level of the worst limit, neutral when nothing is known."""
    return WIN32_LEVEL_RGB.get(title_color or "", (WIN32_NEUTRAL_RGB, WIN32_TEXT_RGB))


def win32_font_height(size: int, text: str) -> int:
    """Digit height in pixels so that up to three digits fit an icon of `size` pixels."""
    scale = {1: 0.9, 2: 0.8}.get(len(text), 0.6)
    return max(6, int(round(size * scale)))


def win32_icon_mask(size: int) -> List[bool]:
    """Row-major opacity of a rounded square filling a size x size icon."""
    radius = max(1, size // 5)
    mask = []
    for y in range(size):
        for x in range(size):
            # distance from the nearest corner circle center, zero away from the corners
            dx = max(radius - x - 0.5, x + 0.5 - (size - radius), 0)
            dy = max(radius - y - 0.5, y + 0.5 - (size - radius), 0)
            mask.append(dx * dx + dy * dy <= radius * radius)
    return mask


def win32_argb(rgb: Win32Rgb, alpha: int = 0xFF) -> int:
    """A 32bpp DIB pixel (bytes B, G, R, A in memory)."""
    r, g, b = rgb
    return (alpha << 24) | (r << 16) | (g << 8) | b


def win32_colorref(rgb: Win32Rgb) -> int:
    """A GDI COLORREF (0x00BBGGRR)."""
    r, g, b = rgb
    return r | (g << 8) | (b << 16)


def win32_menu_label(text: str) -> str:
    """Menus read & as a mnemonic marker and a tab as the accelerator column."""
    return text.replace("&", "&&").replace("\t", " ")


def win32_menu_entries(items: Sequence[MenuItem]) -> Tuple[List[Win32MenuEntry], Dict[int, Action]]:
    """Popup menu rows and the Action behind each command id. Items with an action get ids
    1, 2, 3...; items without one are shown grayed and cannot be chosen. The collapse / expand
    item is left out: the notification-area icon is always icon-only."""
    entries: List[Win32MenuEntry] = []
    actions: Dict[int, Action] = {}
    for item in items:
        if item.action is not None and item.action.kind == COMPACT:
            continue
        if item.separator:
            if entries and entries[-1][0] != _W32.MF_SEPARATOR:  # no leading or double ones
                entries.append((_W32.MF_SEPARATOR, 0, ""))
        elif item.action is not None:
            command = len(actions) + 1
            actions[command] = item.action
            flags = _W32.MF_STRING | (_W32.MF_CHECKED if item.checked else 0)
            entries.append((flags, command, win32_menu_label(item.text)))
        else:
            entries.append((_W32.MF_STRING | _W32.MF_GRAYED, 0, win32_menu_label(item.text)))
    return entries, actions


def _w32_report_error() -> None:
    """Print the current exception if there is a console (pythonw has no stderr)."""
    if sys.stderr is not None:
        try:
            traceback.print_exc()
        except Exception:
            pass


# ---------- behaviour ----------

class Win32TrayBackend(TrayBackend):
    """A notification-area icon showing the worst percentage, with the menu on click."""

    MUTEX_NAME = "Local\\claude-usage-bar-tray"  # legacy name: one tray across upgrades
    WAIT_FOR_OTHER_SECONDS = 10.0  # a self-update starts the new tray just before the old quits
    WM_WAKE = _W32.WM_APP + 1      # call_soon has queued work
    WM_TRAY = _W32.WM_APP + 2      # mouse events on the icon
    ICON_ID = 1

    def __init__(self, tr: Optional[Translator] = None, api=None, mutex_name: str = MUTEX_NAME,
                 wait_seconds: float = WAIT_FOR_OTHER_SECONDS,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self._tr = (tr or Translator()).with_catalog(WIN32_TEXT)
        self._api = api if api is not None else _Win32Api()
        self._mutex_name = mutex_name
        self._wait_seconds = wait_seconds
        self._sleep = sleep
        self._clock = clock
        self._queue: Deque[Callable[[], None]] = collections.deque()
        self._handler: Optional[Callable[[Action], None]] = None
        self._timers: Dict[int, Callable[[], None]] = {}
        self._intervals: Dict[int, int] = {}  # timer id -> milliseconds
        self._hwnd = None
        self._menu: Optional[Menu] = None
        self._hicon = None
        self._icon_added = False
        self._taskbar_created = 0
        self._menu_open = False
        self._quitting = False

    # TrayBackend

    def run(self, on_start: Callable[[], None]) -> None:
        mutex = self._acquire_single_instance()
        if mutex is None:
            return  # another tray is running: leave quietly
        try:
            self._hwnd = self._api.create_window(self._on_message)
            self._taskbar_created = self._api.register_message("TaskbarCreated")
            for timer_id, seconds in self._pending_timers():
                self._api.set_timer(self._hwnd, timer_id, seconds)
            self._api.post(self._hwnd, self.WM_WAKE)  # run anything queued before the window
            on_start()
            self._api.run_loop()
        finally:
            self._teardown()
            self._api.close_handle(mutex)

    def show(self, menu: Menu) -> None:
        if self._quitting:
            return  # a refresh that finished after quit
        self._menu = menu
        old_icon = self._hicon
        background, text = win32_icon_colors(menu.title_color)
        self._hicon = self._api.create_icon(win32_icon_text(menu.worst), background, text)
        self._sync_icon()
        if old_icon:
            self._api.destroy_icon(old_icon)  # only after Explorer has the new one

    def set_action_handler(self, handler: Callable[[Action], None]) -> None:
        self._handler = handler

    def call_soon(self, fn: Callable[[], None]) -> None:
        self._queue.append(fn)  # deque.append is atomic; append before reading _hwnd
        hwnd = self._hwnd
        if hwnd is not None:
            self._api.post(hwnd, self.WM_WAKE)

    def every(self, seconds: int, fn: Callable[[], None]) -> None:
        timer_id = len(self._timers) + 1
        self._timers[timer_id] = fn
        self._intervals[timer_id] = int(seconds * 1000)
        if self._hwnd is not None:
            self._api.set_timer(self._hwnd, timer_id, self._intervals[timer_id])

    def quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self._remove_icon()
        self._api.post_quit()

    def notify(self, title: str, body: str) -> None:
        if self._hwnd is None or self._quitting or not self._icon_added:
            return  # a balloon belongs to the icon: nothing to attach it to yet
        self._api.balloon(self._hwnd, self.ICON_ID, win32_balloon_fields(title, body))

    # window messages

    def _on_message(self, msg: int, wparam: int, lparam: int) -> Optional[int]:
        """Handle a window message; None means "let DefWindowProc handle it"."""
        if msg == self.WM_WAKE:
            self._drain_queue()
        elif msg == self.WM_TRAY:
            if lparam & 0xFFFF in (_W32.WM_LBUTTONUP, _W32.WM_RBUTTONUP):
                self._open_menu()
        elif msg == _W32.WM_TIMER and wparam in self._timers:
            self._safely(self._timers[wparam])
        elif self._taskbar_created and msg == self._taskbar_created:
            self._icon_added = False  # Explorer restarted and forgot every icon
            self._sync_icon()
        elif msg == _W32.WM_CLOSE:
            self.quit()
        else:
            return None
        return 0

    def _drain_queue(self) -> None:
        while self._queue:
            self._safely(self._queue.popleft())

    def _open_menu(self) -> None:
        if self._menu is None or self._menu_open or self._quitting:
            return
        entries, actions = win32_menu_entries(self._menu.items)
        self._menu_open = True
        try:
            command = self._api.popup_menu(self._hwnd, entries)
        finally:
            self._menu_open = False
        action = actions.get(command)
        handler = self._handler
        if action is not None and handler is not None:
            self._safely(lambda: handler(action))

    # icon

    def _sync_icon(self) -> None:
        """Add or update the notification-area icon. Failure is not fatal: at logon Explorer
        may not be ready yet, and TaskbarCreated tells us when to try again."""
        if self._hwnd is None or self._quitting:
            return
        tip = win32_tooltip(self._menu.title if self._menu else self._tr("win32_tip_starting"))
        if self._icon_added and self._notify(_W32.NIM_MODIFY, tip):
            return
        self._icon_added = self._notify(_W32.NIM_ADD, tip)

    def _notify(self, operation: int, tip: str = "") -> bool:
        return self._api.notify(operation, self._hwnd, self.ICON_ID, self.WM_TRAY, self._hicon, tip)

    def _remove_icon(self) -> None:
        for timer_id in self._timers:
            if self._hwnd is not None:
                self._api.kill_timer(self._hwnd, timer_id)
        if self._icon_added:
            self._notify(_W32.NIM_DELETE)
            self._icon_added = False
        if self._hicon:
            self._api.destroy_icon(self._hicon)
            self._hicon = None

    # lifecycle

    def _acquire_single_instance(self):
        """The named mutex handle, or None if another tray still holds it after waiting."""
        deadline = self._clock() + self._wait_seconds
        while True:
            handle, already_running = self._api.create_mutex(self._mutex_name)
            if not already_running:
                return handle or 0  # 0: the mutex could not be created, run anyway
            self._api.close_handle(handle)
            if self._clock() >= deadline:
                return None
            self._sleep(0.25)

    def _pending_timers(self) -> List[Tuple[int, int]]:
        return sorted(self._intervals.items())

    def _teardown(self) -> None:
        if not self._quitting:
            self._quitting = True
            self._remove_icon()
        if self._hwnd is not None:
            self._api.destroy_window(self._hwnd)
            self._hwnd = None

    @staticmethod
    def _safely(fn: Callable[[], None]) -> None:
        """Run a callback without letting an exception unwind into the Win32 message loop."""
        try:
            fn()
        except Exception:
            _w32_report_error()


# ---------- ctypes layer (Windows only) ----------

class _Win32Api:
    """Thin ctypes wrapper over user32, gdi32, shell32 and kernel32. Loads the DLLs on
    creation, so create it only on Windows. Every function that takes or returns a handle
    has argtypes and restype set, which 64-bit Python needs."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self._ct = ctypes
        self._wt = wintypes
        self._define_types()
        self._bind_functions()
        self._hinstance = self.fn.GetModuleHandleW(None)
        self._class_name = ""
        self._wndproc = None  # must outlive the window: Windows calls it until DestroyWindow

    # setup

    def _define_types(self) -> None:
        ct, wt = self._ct, self._wt
        self.LRESULT = wt.LPARAM
        self.WNDPROC = ct.WINFUNCTYPE(self.LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

        class WNDCLASSW(ct.Structure):
            _fields_ = [("style", wt.UINT), ("lpfnWndProc", self.WNDPROC),
                        ("cbClsExtra", ct.c_int), ("cbWndExtra", ct.c_int),
                        ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                        ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                        ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]

        class GUID(ct.Structure):
            _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                        ("Data4", wt.BYTE * 8)]

        class NOTIFYICONDATAW(ct.Structure):
            _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT),
                        ("uFlags", wt.UINT), ("uCallbackMessage", wt.UINT),
                        ("hIcon", wt.HICON), ("szTip", wt.WCHAR * 128),
                        ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD),
                        ("szInfo", wt.WCHAR * 256), ("uVersion", wt.UINT),
                        ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
                        ("guidItem", GUID), ("hBalloonIcon", wt.HICON)]

        class BITMAPINFOHEADER(ct.Structure):
            _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                        ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                        ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                        ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]

        class BITMAPINFO(ct.Structure):
            _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 1)]

        class ICONINFO(ct.Structure):
            _fields_ = [("fIcon", wt.BOOL), ("xHotspot", wt.DWORD), ("yHotspot", wt.DWORD),
                        ("hbmMask", wt.HBITMAP), ("hbmColor", wt.HBITMAP)]

        self.WNDCLASSW, self.NOTIFYICONDATAW = WNDCLASSW, NOTIFYICONDATAW
        self.BITMAPINFOHEADER, self.BITMAPINFO, self.ICONINFO = BITMAPINFOHEADER, BITMAPINFO, ICONINFO

    def _bind_functions(self) -> None:
        ct, wt = self._ct, self._wt
        P = ct.POINTER
        # Private DLL objects, so argtypes set here never clash with other ctypes users.
        user32 = ct.WinDLL("user32", use_last_error=True)
        gdi32 = ct.WinDLL("gdi32", use_last_error=True)
        shell32 = ct.WinDLL("shell32", use_last_error=True)
        kernel32 = ct.WinDLL("kernel32", use_last_error=True)

        class Functions:
            pass

        self.fn = Functions()

        def bind(dll, name, restype, *argtypes):
            f = getattr(dll, name)
            f.restype = restype
            f.argtypes = list(argtypes)
            setattr(self.fn, name, f)

        UINT_PTR = wt.WPARAM  # pointer-sized unsigned
        bind(kernel32, "GetModuleHandleW", wt.HMODULE, wt.LPCWSTR)
        bind(kernel32, "CreateMutexW", wt.HANDLE, wt.LPVOID, wt.BOOL, wt.LPCWSTR)
        bind(kernel32, "CloseHandle", wt.BOOL, wt.HANDLE)

        bind(user32, "SetProcessDPIAware", wt.BOOL)
        bind(user32, "OpenDesktopW", wt.HANDLE, wt.LPCWSTR, wt.DWORD, wt.BOOL, wt.DWORD)
        bind(user32, "SetThreadDesktop", wt.BOOL, wt.HANDLE)
        bind(user32, "RegisterClassW", wt.ATOM, P(self.WNDCLASSW))
        bind(user32, "UnregisterClassW", wt.BOOL, wt.LPCWSTR, wt.HINSTANCE)
        bind(user32, "CreateWindowExW", wt.HWND, wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
             ct.c_int, ct.c_int, ct.c_int, ct.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID)
        bind(user32, "DestroyWindow", wt.BOOL, wt.HWND)
        bind(user32, "DefWindowProcW", self.LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
        bind(user32, "RegisterWindowMessageW", wt.UINT, wt.LPCWSTR)
        bind(user32, "GetMessageW", wt.BOOL, P(wt.MSG), wt.HWND, wt.UINT, wt.UINT)
        bind(user32, "TranslateMessage", wt.BOOL, P(wt.MSG))
        bind(user32, "DispatchMessageW", self.LRESULT, P(wt.MSG))
        bind(user32, "PostMessageW", wt.BOOL, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
        bind(user32, "PostQuitMessage", None, ct.c_int)
        bind(user32, "SetTimer", UINT_PTR, wt.HWND, UINT_PTR, wt.UINT, wt.LPVOID)
        bind(user32, "KillTimer", wt.BOOL, wt.HWND, UINT_PTR)
        bind(user32, "CreatePopupMenu", wt.HMENU)
        bind(user32, "AppendMenuW", wt.BOOL, wt.HMENU, wt.UINT, UINT_PTR, wt.LPCWSTR)
        bind(user32, "TrackPopupMenu", ct.c_int, wt.HMENU, wt.UINT, ct.c_int, ct.c_int,
             ct.c_int, wt.HWND, wt.LPVOID)
        bind(user32, "DestroyMenu", wt.BOOL, wt.HMENU)
        bind(user32, "GetCursorPos", wt.BOOL, P(wt.POINT))
        bind(user32, "SetForegroundWindow", wt.BOOL, wt.HWND)
        bind(user32, "GetSystemMetrics", ct.c_int, ct.c_int)
        bind(user32, "DrawTextW", ct.c_int, wt.HDC, wt.LPCWSTR, ct.c_int, P(wt.RECT), wt.UINT)
        bind(user32, "CreateIconIndirect", wt.HICON, P(self.ICONINFO))
        bind(user32, "DestroyIcon", wt.BOOL, wt.HICON)

        bind(gdi32, "CreateCompatibleDC", wt.HDC, wt.HDC)
        bind(gdi32, "DeleteDC", wt.BOOL, wt.HDC)
        bind(gdi32, "CreateDIBSection", wt.HBITMAP, wt.HDC, P(self.BITMAPINFO), wt.UINT,
             P(wt.LPVOID), wt.HANDLE, wt.DWORD)
        bind(gdi32, "CreateBitmap", wt.HBITMAP, ct.c_int, ct.c_int, wt.UINT, wt.UINT, wt.LPVOID)
        bind(gdi32, "SelectObject", wt.HGDIOBJ, wt.HDC, wt.HGDIOBJ)
        bind(gdi32, "DeleteObject", wt.BOOL, wt.HGDIOBJ)
        bind(gdi32, "CreateFontW", wt.HFONT, ct.c_int, ct.c_int, ct.c_int, ct.c_int, ct.c_int,
             wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD,
             wt.LPCWSTR)
        bind(gdi32, "SetBkMode", ct.c_int, wt.HDC, ct.c_int)
        bind(gdi32, "SetTextColor", wt.DWORD, wt.HDC, wt.DWORD)
        bind(gdi32, "GdiFlush", wt.BOOL)

        bind(shell32, "Shell_NotifyIconW", wt.BOOL, wt.DWORD, P(self.NOTIFYICONDATAW))

    def _raise_last_error(self, what: str):
        error = self._ct.get_last_error()
        raise OSError(error, "%s failed (Win32 error %d)" % (what, error))

    # window and message loop

    def create_window(self, on_message: Callable[[int, int, int], Optional[int]]):
        """A hidden top-level window (a message-only window would miss TaskbarCreated)."""
        ct, f = self._ct, self.fn
        try:
            h_desk = f.OpenDesktopW("Default", 0, False, 0x01FF)
            if h_desk:
                f.SetThreadDesktop(h_desk)
        except Exception:
            pass
        f.SetProcessDPIAware()  # crisp icon and menu on scaled displays

        def wndproc(hwnd, msg, wparam, lparam):
            try:
                result = on_message(msg, wparam, lparam)
            except Exception:
                _w32_report_error()
                result = None
            return f.DefWindowProcW(hwnd, msg, wparam, lparam) if result is None else result

        self._wndproc = self.WNDPROC(wndproc)
        self._class_name = "ClaudeUsageBarTray%x" % id(self)
        wc = self.WNDCLASSW()
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = self._hinstance
        wc.lpszClassName = self._class_name
        if not f.RegisterClassW(ct.byref(wc)):
            self._raise_last_error("RegisterClassW")
        hwnd = f.CreateWindowExW(0, self._class_name, "claude-usage-bar", 0, 0, 0, 0, 0,
                                 None, None, self._hinstance, None)
        if not hwnd:
            self._raise_last_error("CreateWindowExW")
        return hwnd

    def destroy_window(self, hwnd) -> None:
        self.fn.DestroyWindow(hwnd)
        self.fn.UnregisterClassW(self._class_name, self._hinstance)

    def register_message(self, name: str) -> int:
        return self.fn.RegisterWindowMessageW(name)

    def run_loop(self) -> None:
        ct, f = self._ct, self.fn
        msg = self._wt.MSG()
        while True:
            result = f.GetMessageW(ct.byref(msg), None, 0, 0)
            if result == 0:  # WM_QUIT
                return
            if result == -1:
                self._raise_last_error("GetMessageW")
            f.TranslateMessage(ct.byref(msg))
            f.DispatchMessageW(ct.byref(msg))

    def post(self, hwnd, msg: int) -> bool:
        return bool(self.fn.PostMessageW(hwnd, msg, 0, 0))

    def post_quit(self) -> None:
        self.fn.PostQuitMessage(0)

    def set_timer(self, hwnd, timer_id: int, milliseconds: int) -> None:
        self.fn.SetTimer(hwnd, timer_id, milliseconds, None)

    def kill_timer(self, hwnd, timer_id: int) -> None:
        self.fn.KillTimer(hwnd, timer_id)

    # single instance

    def create_mutex(self, name: str):
        """(handle, True if the mutex already existed)."""
        self._ct.set_last_error(0)
        handle = self.fn.CreateMutexW(None, False, name)
        return handle, self._ct.get_last_error() == _W32.ERROR_ALREADY_EXISTS

    def close_handle(self, handle) -> None:
        if handle:
            self.fn.CloseHandle(handle)

    # notification icon

    def notify(self, operation: int, hwnd, icon_id: int, callback_msg: int, hicon, tip: str) -> bool:
        data = self.NOTIFYICONDATAW()
        data.cbSize = self._ct.sizeof(data)
        data.hWnd = hwnd
        data.uID = icon_id
        if operation != _W32.NIM_DELETE:
            data.uFlags = _W32.NIF_MESSAGE | _W32.NIF_ICON | _W32.NIF_TIP
            data.uCallbackMessage = callback_msg
            data.hIcon = hicon
            data.szTip = tip
        return bool(self.fn.Shell_NotifyIconW(operation, self._ct.byref(data)))

    def balloon(self, hwnd, icon_id: int, fields: Dict[str, object]) -> bool:
        """NIM_MODIFY the icon with `fields` (see win32_balloon_fields)."""
        data = self.NOTIFYICONDATAW()
        data.cbSize = self._ct.sizeof(data)
        data.hWnd = hwnd
        data.uID = icon_id
        for name, value in fields.items():
            setattr(data, name, value)
        return bool(self.fn.Shell_NotifyIconW(_W32.NIM_MODIFY, self._ct.byref(data)))

    def create_icon(self, text: str, background: Win32Rgb, foreground: Win32Rgb):
        """An HICON with `text` on a rounded square, sized for the tray."""
        ct, f = self._ct, self.fn
        size = f.GetSystemMetrics(_W32.SM_CXSMICON) or 16
        mask = win32_icon_mask(size)
        color_bitmap, pixels = self._create_dib(size)
        fill = win32_argb(background)
        for i, opaque in enumerate(mask):
            pixels[i] = fill if opaque else 0
        self._draw_text(color_bitmap, size, text, foreground)
        for i, opaque in enumerate(mask):  # GDI text clears alpha: make the square opaque again
            pixels[i] = (pixels[i] & 0xFFFFFF) | 0xFF000000 if opaque else 0
        mask_bits = (ct.c_ubyte * (((size + 15) // 16) * 2 * size))()  # zeroed, unused with alpha
        mask_bitmap = f.CreateBitmap(size, size, 1, 1, mask_bits)
        info = self.ICONINFO(True, 0, 0, mask_bitmap, color_bitmap)
        hicon = f.CreateIconIndirect(ct.byref(info))
        f.DeleteObject(mask_bitmap)
        f.DeleteObject(color_bitmap)
        if not hicon:
            self._raise_last_error("CreateIconIndirect")
        return hicon

    def _create_dib(self, size: int):
        """A top-down 32bpp bitmap and a ctypes view of its pixels."""
        ct, f = self._ct, self.fn
        bmi = self.BITMAPINFO()
        header = bmi.bmiHeader
        header.biSize = ct.sizeof(self.BITMAPINFOHEADER)
        header.biWidth, header.biHeight = size, -size
        header.biPlanes, header.biBitCount, header.biCompression = 1, 32, _W32.BI_RGB
        bits = self._wt.LPVOID()
        bitmap = f.CreateDIBSection(None, ct.byref(bmi), _W32.DIB_RGB_COLORS, ct.byref(bits), None, 0)
        if not bitmap or not bits.value:
            self._raise_last_error("CreateDIBSection")
        return bitmap, (ct.c_uint32 * (size * size)).from_address(bits.value)

    def _draw_text(self, bitmap, size: int, text: str, foreground: Win32Rgb) -> None:
        ct, f = self._ct, self.fn
        dc = f.CreateCompatibleDC(None)
        old_bitmap = f.SelectObject(dc, bitmap)
        font = f.CreateFontW(-win32_font_height(size, text), 0, 0, 0, _W32.FW_BOLD, 0, 0, 0,
                             _W32.DEFAULT_CHARSET, 0, 0, _W32.ANTIALIASED_QUALITY, _W32.FF_SWISS,
                             "Segoe UI")
        old_font = f.SelectObject(dc, font)
        f.SetBkMode(dc, _W32.TRANSPARENT)
        f.SetTextColor(dc, win32_colorref(foreground))
        rect = self._wt.RECT(0, 0, size, size)
        f.DrawTextW(dc, text, -1, ct.byref(rect),
                    _W32.DT_CENTER | _W32.DT_VCENTER | _W32.DT_SINGLELINE | _W32.DT_NOPREFIX)
        f.GdiFlush()
        f.SelectObject(dc, old_font)
        f.DeleteObject(font)
        f.SelectObject(dc, old_bitmap)
        f.DeleteDC(dc)

    def destroy_icon(self, hicon) -> None:
        self.fn.DestroyIcon(hicon)

    # popup menu

    def popup_menu(self, hwnd, entries: Sequence[Win32MenuEntry]) -> int:
        """Show the menu at the cursor and return the chosen command id (0 if dismissed)."""
        ct, f = self._ct, self.fn
        menu = f.CreatePopupMenu()
        try:
            for flags, command, label in entries:
                f.AppendMenuW(menu, flags, command, None if flags & _W32.MF_SEPARATOR else label)
            point = self._wt.POINT()
            f.GetCursorPos(ct.byref(point))
            f.SetForegroundWindow(hwnd)  # otherwise the menu does not close on an outside click
            command = f.TrackPopupMenu(menu, _W32.TPM_RIGHTBUTTON | _W32.TPM_RETURNCMD
                                       | _W32.TPM_NONOTIFY, point.x, point.y, 0, hwnd, None)
            f.PostMessageW(hwnd, _W32.WM_NULL, 0, 0)
            return command
        finally:
            f.DestroyMenu(menu)
