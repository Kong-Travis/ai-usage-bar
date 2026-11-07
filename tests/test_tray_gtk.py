"""Linux tray backend: pure helpers run everywhere, Gtk tests only where PyGObject is installed."""
from __future__ import annotations

import importlib
import io
import os
import shutil
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr
from unittest import mock

from fakes import ROOT

sys.path.insert(0, os.path.join(ROOT, "src"))

from claude_usage import tray_gtk  # noqa: E402
from claude_usage.i18n import Translator  # noqa: E402
from claude_usage.view import ORANGE, RED, Action, Menu, MenuItem, SEPARATOR  # noqa: E402


def gi_available() -> bool:
    try:
        tray_gtk.gtk_load_modules()
        return True
    except (ImportError, ValueError):
        return False


def menu(color=None, update=False) -> Menu:
    return Menu("C 13/8/1", color, update, 13.0, (MenuItem("x"),))


class ImportWithoutGi(unittest.TestCase):
    def test_module_imports_when_gi_is_blocked(self):
        with mock.patch.dict(sys.modules, {"gi": None}):
            importlib.reload(tray_gtk)
            with self.assertRaises(ImportError):
                tray_gtk.gtk_load_modules()
        importlib.reload(tray_gtk)

    def test_missing_gi_exits_1_with_install_command(self):
        err = io.StringIO()
        with mock.patch.dict(sys.modules, {"gi": None}), redirect_stderr(err), \
                mock.patch.object(tray_gtk.shutil, "which",
                                  side_effect=lambda t: "/usr/bin/apt-get" if t == "apt-get" else None):
            with self.assertRaises(SystemExit) as cm:
                tray_gtk.GtkTrayBackend(Translator("vi"))
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("python3-gi gir1.2-ayatanaappindicator3-0.1", err.getvalue())
        self.assertIn("Thiếu thư viện", err.getvalue())

    def test_missing_gi_message_in_english(self):
        err = io.StringIO()
        with mock.patch.dict(sys.modules, {"gi": None}), redirect_stderr(err):
            with self.assertRaises(SystemExit):
                tray_gtk.GtkTrayBackend(Translator("en"))
        self.assertIn("Missing libraries", err.getvalue())


class InstallCommand(unittest.TestCase):
    def which_only(self, tool):
        return lambda t: "/usr/bin/" + t if t == tool else None

    def test_per_package_manager(self):
        self.assertIn("python3-gi gir1.2-ayatanaappindicator3-0.1",
                      tray_gtk.gtk_install_command(self.which_only("apt-get")))
        self.assertIn("python3-gobject libayatana-appindicator-gtk3",
                      tray_gtk.gtk_install_command(self.which_only("dnf")))
        self.assertIn("python-gobject libayatana-appindicator",
                      tray_gtk.gtk_install_command(self.which_only("pacman")))
        self.assertIn("typelib-1_0-AyatanaAppIndicator3-0_1",
                      tray_gtk.gtk_install_command(self.which_only("zypper")))

    def test_unknown_distro_lists_all(self):
        text = tray_gtk.gtk_install_command(lambda t: None)
        for tool in ("apt", "dnf", "pacman", "zypper"):
            self.assertIn("sudo " + tool, text)


class IconChoice(unittest.TestCase):
    def test_severity_from_menu(self):
        self.assertEqual(tray_gtk.gtk_severity(menu(RED)), "critical")
        self.assertEqual(tray_gtk.gtk_severity(menu(ORANGE)), "warning")
        self.assertEqual(tray_gtk.gtk_severity(menu(None, update=True)), "update")
        self.assertEqual(tray_gtk.gtk_severity(menu(RED, update=True)), "critical")
        self.assertEqual(tray_gtk.gtk_severity(menu()), "normal")

    def test_first_icon_the_theme_has(self):
        has = {"dialog-warning-symbolic", "dialog-information"}.__contains__
        self.assertEqual(tray_gtk.gtk_pick_icon("warning", has), "dialog-warning-symbolic")
        self.assertEqual(tray_gtk.gtk_pick_icon("normal", has), "dialog-information")

    def test_falls_back_to_first_candidate(self):
        self.assertEqual(tray_gtk.gtk_pick_icon("critical", lambda n: False), "dialog-error")
        self.assertEqual(tray_gtk.gtk_pick_icon("bogus", lambda n: False),
                         tray_gtk.GTK_ICON_NAMES["normal"][0])

    def test_severities_get_different_icons(self):
        firsts = {names[0] for names in tray_gtk.GTK_ICON_NAMES.values()}
        self.assertEqual(len(firsts), len(tray_gtk.GTK_ICON_NAMES))


class MenuLabel(unittest.TestCase):
    def test_underscores_are_not_mnemonics(self):
        self.assertEqual(tray_gtk.gtk_menu_label("claude - john_doe@x.com"),
                         "claude - john__doe@x.com")


@unittest.skipIf(os.name == "nt", "fcntl is POSIX only")
class SingleInstanceLock(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cub-lock-")
        self.path = os.path.join(self.dir, "nested", "tray.lock")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_second_holder_gives_up_after_waiting(self):
        first = tray_gtk.gtk_acquire_lock(self.path, wait_seconds=0)
        self.assertIsNotNone(first)
        sleeps = []
        now = [0.0]

        def sleep(s):
            sleeps.append(s)
            now[0] += s

        try:
            second = tray_gtk.gtk_acquire_lock(self.path, wait_seconds=1.0, sleep=sleep,
                                               clock=lambda: now[0])
            self.assertIsNone(second)
            self.assertGreaterEqual(sum(sleeps), 1.0)  # it did wait before giving up
        finally:
            os.close(first)
        with open(self.path) as f:
            self.assertEqual(f.read().strip(), str(os.getpid()))

    def test_takes_over_when_the_old_tray_quits_during_the_wait(self):
        # A self-update starts the new tray, then the old one quits a moment later.
        first = [tray_gtk.gtk_acquire_lock(self.path, wait_seconds=0)]
        now = [0.0]

        def sleep(s):
            now[0] += s
            if first[0] is not None and now[0] >= 1.0:
                os.close(first[0])
                first[0] = None

        second = tray_gtk.gtk_acquire_lock(self.path, wait_seconds=10.0, sleep=sleep,
                                           clock=lambda: now[0])
        self.assertIsNotNone(second)
        self.assertIsNone(first[0])
        os.close(second)

    def test_lock_is_free_again_after_close(self):
        first = tray_gtk.gtk_acquire_lock(self.path, wait_seconds=0)
        os.close(first)
        again = tray_gtk.gtk_acquire_lock(self.path, wait_seconds=0)
        self.assertIsNotNone(again)
        os.close(again)

    def test_default_path(self):
        self.assertEqual(tray_gtk.gtk_lock_path("/home/u"), "/home/u/.cache/claude-usage/tray.lock")


@unittest.skipUnless(gi_available(), "PyGObject with AppIndicator3 is not installed")
class Notifications(unittest.TestCase):
    def test_notify_goes_to_the_desktop_notifier_off_the_ui_thread(self):
        shown, done = [], threading.Event()

        class Recording:
            def notify(self, title, body):
                shown.append((title, body, threading.current_thread() is ui_thread))
                done.set()

        ui_thread = threading.current_thread()
        backend = tray_gtk.GtkTrayBackend(Translator("en"), notifier=Recording())
        backend.notify("Antigravity", "Ag agy, Weekly: limit reached")
        self.assertTrue(done.wait(5))
        self.assertEqual(shown, [("Antigravity", "Ag agy, Weekly: limit reached", False)])


@unittest.skipUnless(gi_available(), "PyGObject with AppIndicator3 is not installed")
@unittest.skipUnless(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"), "no display")
class GtkMenu(unittest.TestCase):
    def setUp(self):
        self.backend = tray_gtk.GtkTrayBackend(Translator("en"))
        self.backend._gtk, self.backend._glib, self.backend._appindicator = \
            tray_gtk.gtk_load_modules()
        self.clicked = []
        self.backend.set_action_handler(self.clicked.append)

    def test_items_separators_and_actions(self):
        refresh = Action("refresh")
        items = (MenuItem("a_b"), SEPARATOR, MenuItem("Refresh", action=refresh))
        gtk_menu = self.backend._build_gtk_menu(items)
        children = gtk_menu.get_children()
        self.assertEqual(len(children), 3)
        self.assertFalse(children[0].get_sensitive())
        self.assertEqual(children[0].get_label(), "a__b")
        self.assertEqual(type(children[1]).__name__, "SeparatorMenuItem")
        self.assertTrue(children[2].get_sensitive())
        children[2].activate()
        self.assertEqual(self.clicked, [refresh])

    def test_checked_item_is_a_ticked_check_item_that_fires_once(self):
        hide = Action("hide_account", value="c-m")
        gtk_menu = self.backend._build_gtk_menu((MenuItem("Show", checked=True, action=hide),))
        child = gtk_menu.get_children()[0]
        self.assertEqual(type(child).__name__, "CheckMenuItem")
        self.assertTrue(child.get_active())
        self.assertEqual(self.clicked, [])  # ticking it while building is not a click
        child.activate()
        self.assertEqual(self.clicked, [hide])


if __name__ == "__main__":
    unittest.main()
