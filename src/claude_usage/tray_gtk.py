"""Linux tray icon (KDE, XFCE, Cinnamon, MATE, Budgie...): Gtk 3 + Ayatana AppIndicator.

GNOME uses the Argos extension instead (plain BitBar output), so this backend is for the other
desktops. Everything that touches `gi` or `fcntl` is imported inside functions, because the
flat plugin is also imported on macOS and Windows.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from typing import Callable, Optional, Sequence, Tuple

from .i18n import EN, VI, Translator
from .tray import TrayBackend
from .view import ORANGE, RED, Action, Menu, MenuItem

GTK_LOCK_WAIT_SECONDS = 10.0
GTK_APP_ID = "claude-usage"

GTK_TEXT = {
    "gtk_missing": {
        VI: "Thiếu thư viện để hiện biểu tượng trên khay hệ thống (PyGObject + AyatanaAppIndicator3).\n"
            "Cài bằng lệnh sau rồi chạy lại:\n  {command}",
        EN: "Missing libraries for the tray icon (PyGObject + AyatanaAppIndicator3).\n"
            "Install them with this command, then run again:\n  {command}",
    },
    "gtk_no_display": {
        VI: "Không mở được màn hình (chưa có DISPLAY hoặc WAYLAND_DISPLAY). "
            "Hãy chạy lệnh này trong phiên đăng nhập đồ hoạ.",
        EN: "Cannot open the display (no DISPLAY or WAYLAND_DISPLAY). "
            "Run this from a graphical session.",
    },
    "gtk_title": {VI: "Hạn mức Claude Code", EN: "Claude Code usage"},
}

# Install commands by package manager, for the "missing gi" message.
GTK_INSTALL_COMMANDS: Tuple[Tuple[str, str], ...] = (
    ("apt-get", "sudo apt install python3-gi gir1.2-ayatanaappindicator3-0.1"),
    ("dnf", "sudo dnf install python3-gobject libayatana-appindicator-gtk3"),
    ("pacman", "sudo pacman -S --needed python-gobject libayatana-appindicator"),
    ("zypper", "sudo zypper install python3-gobject python3-gobject-Gdk typelib-1_0-AyatanaAppIndicator3-0_1"),
)

# Themed icon names per severity, best first. The first one the icon theme has is used; these
# exist in Adwaita, Breeze, Yaru, Papirus and the freedesktop fallback set.
GTK_ICON_NAMES = {
    "critical": ("dialog-error", "dialog-error-symbolic", "emblem-important"),
    "warning": ("dialog-warning", "dialog-warning-symbolic", "dialog-error"),
    "update": ("software-update-available", "software-update-available-symbolic",
               "dialog-information"),
    "normal": ("utilities-system-monitor", "utilities-system-monitor-symbolic",
               "dialog-information", "dialog-information-symbolic"),
}


def gtk_severity(menu: Menu) -> str:
    """"critical" | "warning" | "update" | "normal": what the icon should signal."""
    if menu.title_color == RED:
        return "critical"
    if menu.title_color == ORANGE:
        return "warning"
    if menu.update_available:
        return "update"
    return "normal"


def gtk_pick_icon(severity: str, has_icon: Callable[[str], bool]) -> str:
    """The first icon for `severity` that the theme has, else the first candidate."""
    names = GTK_ICON_NAMES.get(severity, GTK_ICON_NAMES["normal"])
    for name in names:
        if has_icon(name):
            return name
    return names[0]


def gtk_install_command(which: Optional[Callable[[str], Optional[str]]] = None) -> str:
    """The install command for this distro's package manager (all of them if none is found)."""
    which = which or shutil.which
    for tool, command in GTK_INSTALL_COMMANDS:
        if which(tool):
            return command
    return "\n  ".join(command for _, command in GTK_INSTALL_COMMANDS)


def gtk_menu_label(text: str) -> str:
    """Escape underscores: AppIndicator menus (dbusmenu) treat them as mnemonics."""
    return text.replace("_", "__")


def gtk_lock_path(home: Optional[str] = None) -> str:
    return os.path.join(home or os.path.expanduser("~"), ".cache", "claude-usage", "tray.lock")


def gtk_acquire_lock(path: str, wait_seconds: float = GTK_LOCK_WAIT_SECONDS,
                     sleep: Callable[[float], None] = time.sleep,
                     clock: Callable[[], float] = time.monotonic) -> Optional[int]:
    """An fd holding an exclusive flock on `path`, or None if another tray kept it for
    `wait_seconds`. Waiting matters: a self-update starts the new tray right before the old
    one quits. Keep the fd open for the life of the process."""
    import fcntl

    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)  # not inherited by children (PEP 446)
    deadline = clock() + wait_seconds
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            if clock() >= deadline:
                os.close(fd)
                return None
            sleep(0.25)
            continue
        os.ftruncate(fd, 0)
        os.write(fd, ("%d\n" % os.getpid()).encode())
        return fd


def gtk_require_versions() -> str:
    """Check that Gtk 3 and an AppIndicator typelib exist, without loading Gtk (loading it
    connects to the display). Returns the AppIndicator namespace; raises ImportError or
    ValueError when something is missing."""
    import gi

    gi.require_version("Gtk", "3.0")
    for namespace in ("AyatanaAppIndicator3", "AppIndicator3"):
        try:
            gi.require_version(namespace, "0.1")
            return namespace
        except ValueError:
            pass
    raise ValueError("no AppIndicator3 typelib")


def gtk_load_modules() -> Tuple[object, object, object]:
    """(Gtk, GLib, AppIndicator) or raise ImportError/ValueError when something is missing."""
    import importlib

    indicator_module = importlib.import_module("gi.repository." + gtk_require_versions())
    from gi.repository import GLib, Gtk
    return Gtk, GLib, indicator_module


class GtkTrayBackend(TrayBackend):
    """AppIndicator (StatusNotifierItem) tray icon: the label shows the menu title, the icon
    shows the severity, and the menu is rebuilt on every show()."""

    def __init__(self, tr: Optional[Translator] = None, lock_path: Optional[str] = None,
                 lock_wait: float = GTK_LOCK_WAIT_SECONDS):
        self._tr = (tr or Translator()).with_catalog(GTK_TEXT)
        try:
            gtk_require_versions()  # fail fast, before waiting for the lock
        except (ImportError, ValueError):
            self._exit_missing_gi()
        self._gtk = self._glib = self._appindicator = None
        self._lock_path = lock_path or gtk_lock_path()
        self._lock_wait = lock_wait
        self._lock_fd: Optional[int] = None
        self._indicator = None
        self._handler: Callable[[Action], None] = lambda action: None
        self._shown: Optional[Tuple[Sequence[MenuItem], str]] = None

    # ---------- TrayBackend ----------

    def run(self, on_start: Callable[[], None]) -> None:
        self._lock_fd = gtk_acquire_lock(self._lock_path, self._lock_wait)
        if self._lock_fd is None:
            raise SystemExit(0)  # another tray is already running
        try:  # loaded only now, so a second instance exits without touching the display
            self._gtk, self._glib, self._appindicator = gtk_load_modules()
        except (ImportError, ValueError):
            self._exit_missing_gi()
        Gtk = self._gtk
        if not Gtk.init_check(sys.argv)[0]:
            print(self._tr("gtk_no_display"), file=sys.stderr)
            raise SystemExit(1)
        ind = self._appindicator
        self._indicator = ind.Indicator.new(GTK_APP_ID, gtk_pick_icon("normal", self._has_icon),
                                            ind.IndicatorCategory.APPLICATION_STATUS)
        self._indicator.set_title(self._tr("gtk_title"))
        self._indicator.set_status(ind.IndicatorStatus.ACTIVE)
        self._indicator.set_menu(self._build_gtk_menu(()))  # AppIndicator needs a menu to show
        self._install_signal_handlers()
        on_start()
        Gtk.main()

    def show(self, menu: Menu) -> None:
        icon = gtk_pick_icon(gtk_severity(menu), self._has_icon)
        self._indicator.set_icon_full(icon, menu.title)
        label = "" if menu.compact else menu.title  # compact: the icon alone
        self._indicator.set_label(label, label)
        self._indicator.set_title("%s: %s" % (self._tr("gtk_title"), menu.title))
        if self._shown != (menu.items, icon):  # rebuilding closes an open menu, so skip if same
            self._shown = (menu.items, icon)
            self._indicator.set_menu(self._build_gtk_menu(menu.items))

    def set_action_handler(self, handler: Callable[[Action], None]) -> None:
        self._handler = handler

    def call_soon(self, fn: Callable[[], None]) -> None:
        def once():
            self._safely(fn)
            return False  # run once
        self._glib.idle_add(once)

    def every(self, seconds: int, fn: Callable[[], None]) -> None:
        def tick():
            self._safely(fn)
            return True  # keep repeating even if fn failed
        self._glib.timeout_add_seconds(seconds, tick)

    def quit(self) -> None:
        self._gtk.main_quit()

    # ---------- helpers ----------

    def _exit_missing_gi(self) -> None:
        print(self._tr("gtk_missing", command=gtk_install_command()), file=sys.stderr)
        raise SystemExit(1)

    def _has_icon(self, name: str) -> bool:
        theme = self._gtk.IconTheme.get_default()
        return bool(theme and theme.has_icon(name))

    def _build_gtk_menu(self, items: Sequence[MenuItem]):
        Gtk = self._gtk
        gtk_menu = Gtk.Menu()
        for item in items:
            if item.separator:
                gtk_menu.append(Gtk.SeparatorMenuItem())
                continue
            if item.checked:
                widget = Gtk.CheckMenuItem.new_with_mnemonic(gtk_menu_label(item.text))
                widget.set_active(True)  # before connect, so this does not fire the action
            else:
                widget = Gtk.MenuItem.new_with_mnemonic(gtk_menu_label(item.text))
            if item.action is None:
                widget.set_sensitive(False)
            else:
                widget.connect("activate", lambda _w, a=item.action: self._dispatch(a))
            gtk_menu.append(widget)
        gtk_menu.show_all()
        return gtk_menu

    def _dispatch(self, action: Action) -> None:
        self._safely(lambda: self._handler(action))

    def _install_signal_handlers(self) -> None:
        import signal

        unix_signal_add = getattr(self._glib, "unix_signal_add", None)
        if unix_signal_add is None:
            return
        for sig in (signal.SIGINT, signal.SIGTERM):
            unix_signal_add(self._glib.PRIORITY_DEFAULT, sig, self._on_signal)

    def _on_signal(self, *_args) -> bool:
        self.quit()
        return False

    @staticmethod
    def _safely(fn: Callable[[], None]) -> None:
        """A raising GLib callback would drop the timer or kill the update; log and go on."""
        try:
            fn()
        except Exception as e:  # keep the icon alive whatever happens
            print("claude-usage tray: %s: %s" % (type(e).__name__, e), file=sys.stderr)

