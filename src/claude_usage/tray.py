"""Long-running tray frontend (Windows, Linux desktops without Argos).

TrayController owns the behaviour (when to refresh, what each action does); a TrayBackend only
draws a Menu and reports clicks, so each OS needs nothing but a small backend.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import webbrowser
from abc import ABC, abstractmethod
from dataclasses import replace
from typing import Callable, List, Optional, Sequence

from .i18n import Translator
from .view import (CHECK_UPDATE, OPEN, QUIT, REFRESH, SETTINGS, UPDATE, Action, Menu, MenuItem,
                   SEPARATOR)

TRAY_REFRESH_SECONDS = 60


class TrayBackend(ABC):
    """A native tray icon. All methods except call_soon are called on the UI thread."""

    @abstractmethod
    def run(self, on_start: Callable[[], None]) -> None:
        """Create the icon, call on_start on the UI thread, then block in the UI loop."""

    @abstractmethod
    def show(self, menu: Menu) -> None:
        """Replace the icon text/tooltip and the menu. Clicking an item with an action must
        call the handler given to set_action_handler with that Action."""

    @abstractmethod
    def set_action_handler(self, handler: Callable[[Action], None]) -> None: ...

    @abstractmethod
    def call_soon(self, fn: Callable[[], None]) -> None:
        """Run fn on the UI thread. Safe to call from any thread."""

    @abstractmethod
    def every(self, seconds: int, fn: Callable[[], None]) -> None:
        """Call fn on the UI thread every `seconds`."""

    @abstractmethod
    def quit(self) -> None: ...


class TrayController:
    """Refreshes the tray every minute and runs menu actions.

    Network work happens on a worker thread; results go back to the UI thread through
    backend.call_soon, so a slow probe never freezes the icon.
    """

    def __init__(self, backend: TrayBackend, build_menu: Callable[[bool], Menu],
                 refresh: Callable[[], None], update: Callable[[], bool],
                 restart: Callable[[], None], translator: Callable[[], Translator],
                 change_setting: Callable[[Action], None],
                 check_update: Callable[[], object],
                 open_url: Callable[[str], object] = webbrowser.open,
                 spawn: Callable[[Callable[[], None]], None] = None):
        """translator is called each time, so a language switch applies at once.
        change_setting writes a settings action (language, shown accounts, compact) to the
        conf file; the menu is rebuilt from it right after."""
        self._backend = backend
        self._translator = translator
        self._change_setting = change_setting
        self._build_menu = build_menu
        self._refresh = refresh
        self._update = update
        self._check_update = check_update
        self._restart = restart
        self._open_url = open_url
        self._spawn = spawn or _start_daemon_thread
        self._busy = threading.Lock()
        self._state = threading.Lock()  # guards _busy hand-over and _again together
        self._again = None  # None, or the force flag of a reload asked for while one ran

    def run(self) -> None:
        self._backend.set_action_handler(self.on_action)
        self._backend.run(self.start)

    def start(self) -> None:
        self._backend.show(loading_menu(self._translator()))
        self._backend.every(TRAY_REFRESH_SECONDS, self.reload)
        self.reload()

    def reload(self, force: bool = False) -> None:
        """Rebuild the menu in the background. If a rebuild is already running, run one more
        right after it, so a click (refresh, language) is never lost."""
        with self._state:
            if not self._busy.acquire(blocking=False):
                self._again = bool(self._again) or force
                return

        def work():
            try:
                if force:
                    self._refresh()
                menu = with_quit(self._build_menu(False), self._translator())
            except Exception as e:  # keep the icon alive whatever happens
                menu = error_menu(e, self._translator())
            finally:
                with self._state:
                    again, self._again = self._again, None
                    self._busy.release()
            self._backend.call_soon(lambda: self._backend.show(menu))
            if again is not None:
                self.reload(force=again)

        self._spawn(work)

    def on_action(self, action: Action) -> None:
        if action.kind == REFRESH:
            self.reload(force=True)
        elif action.kind == OPEN:
            self._open_url(action.url)
        elif action.kind == UPDATE:
            self._spawn(self._do_update)
        elif action.kind == CHECK_UPDATE:
            self._spawn(self._do_check_update)
        elif action.kind in SETTINGS:
            self._change_setting(action)
            self.reload()
        elif action.kind == QUIT:
            self._backend.quit()

    def _do_update(self) -> None:
        try:
            replaced = self._update()
        except Exception as e:
            menu = error_menu(e, self._translator())
            self._backend.call_soon(lambda: self._backend.show(menu))
            return
        if replaced:
            self._backend.call_soon(self._restart_now)
        else:
            self.reload()

    def _do_check_update(self) -> None:
        self._check_update()  # never raises: offline, the last known answer stays
        self.reload()

    def _restart_now(self) -> None:
        self._restart()
        self._backend.quit()


def _start_daemon_thread(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, daemon=True).start()


def quit_item(tr: Translator) -> MenuItem:
    return MenuItem(tr("quit"), action=Action(QUIT))


def with_quit(menu: Menu, tr: Translator) -> Menu:
    return replace(menu, items=menu.items + (SEPARATOR, quit_item(tr)))


def loading_menu(tr: Translator) -> Menu:
    return Menu("Claude ...", None, False, None, (MenuItem(tr("loading")), SEPARATOR, quit_item(tr)))


def error_menu(e: Exception, tr: Translator) -> Menu:
    return Menu("Claude ?", None, False, None, (
        MenuItem(tr("error_line", error="%s: %s" % (type(e).__name__, str(e)[:160])), color="gray"),
        MenuItem(tr("refresh_now"), action=Action(REFRESH)),
        SEPARATOR, quit_item(tr)))


def relaunch_command(argv: Sequence[str], executable: str) -> List[str]:
    """The command that starts this tray again, e.g. after a self-update."""
    return [executable, os.path.realpath(argv[0])] + list(argv[1:])


def relaunch(argv: Optional[Sequence[str]] = None, executable: Optional[str] = None) -> None:
    cmd = relaunch_command(argv or sys.argv, executable or sys.executable)
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, close_fds=True, **kwargs)
