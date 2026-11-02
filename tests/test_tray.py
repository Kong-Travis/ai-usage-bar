"""TrayController behaviour with a fake backend and a synchronous spawn."""
import srcpath  # noqa: F401  (must come first)

import os
import unittest
from unittest import mock

from claude_usage.i18n import Translator
from claude_usage.tray import (TRAY_REFRESH_SECONDS, TrayBackend, TrayController, error_menu,
                               loading_menu, quit_item, relaunch, relaunch_command, with_quit)
from claude_usage.view import (COMPACT, HIDE_ACCOUNT, LANGUAGE, OPEN, QUIT, REFRESH, SEPARATOR,
                               SHOW_ACCOUNT, UPDATE, Action, Menu, MenuItem)

VI = Translator("vi")
REAL = Menu("C 1/2", None, False, 2.0, (MenuItem("row"), SEPARATOR, MenuItem("Làm mới ngay",
                                                                           action=Action(REFRESH))))


class FakeBackend(TrayBackend):
    """Records everything; call_soon runs at once (the test is the UI thread)."""

    def __init__(self):
        self.shown = []
        self.every_calls = []
        self.handler = None
        self.quit_calls = 0
        self.events = []

    def run(self, on_start):
        on_start()

    def show(self, menu):
        self.shown.append(menu)

    def set_action_handler(self, handler):
        self.handler = handler

    def call_soon(self, fn):
        fn()

    def every(self, seconds, fn):
        self.every_calls.append((seconds, fn))

    def quit(self):
        self.quit_calls += 1
        self.events.append("quit")


class Harness:
    def __init__(self, build_menu=None, update=None, spawn=None, translator=None):
        self.backend = FakeBackend()
        self.build_calls = []
        self.refresh_calls = 0
        self.restarts = 0
        self.opened = []
        self.settings = []
        self.translator = translator or (lambda: VI)

        def default_build(force):
            self.build_calls.append(force)
            return REAL

        def refresh():
            self.refresh_calls += 1

        def restart():
            self.restarts += 1
            self.backend.events.append("restart")

        self.controller = TrayController(
            backend=self.backend,
            build_menu=build_menu or default_build,
            refresh=refresh,
            update=update or (lambda: False),
            restart=restart,
            translator=self.translator,
            change_setting=self.settings.append,
            open_url=self.opened.append,
            spawn=spawn or (lambda fn: fn()),
        )


class StartTest(unittest.TestCase):
    def test_run_wires_the_handler_and_start_shows_loading_then_the_real_menu(self):
        h = Harness()
        h.controller.run()
        self.assertEqual(h.backend.handler, h.controller.on_action)
        loading, real = h.backend.shown
        self.assertEqual(loading.title, "Claude ...")
        self.assertEqual(loading.items[0].text, "Đang đọc hạn mức...")
        self.assertEqual(real.title, "C 1/2")
        self.assertEqual(real.items[-2:], (SEPARATOR, quit_item(VI)))
        self.assertEqual(real.items[-1].text, "Thoát")
        self.assertEqual(real.items[-1].action.kind, QUIT)

    def test_schedules_a_reload_every_minute(self):
        h = Harness()
        h.controller.run()
        self.assertEqual(TRAY_REFRESH_SECONDS, 60)
        self.assertEqual([(s, fn) for s, fn in h.backend.every_calls],
                         [(60, h.controller.reload)])

    def test_scheduled_reload_does_not_force(self):
        h = Harness()
        h.controller.run()
        h.backend.every_calls[0][1]()
        self.assertEqual(h.refresh_calls, 0)
        self.assertEqual(h.build_calls, [False, False])


class ActionsTest(unittest.TestCase):
    def test_refresh_forces_then_rebuilds(self):
        h = Harness()
        h.controller.on_action(Action(REFRESH))
        self.assertEqual(h.refresh_calls, 1)
        self.assertEqual(h.build_calls, [False])
        self.assertEqual(h.backend.shown[-1].title, "C 1/2")

    def test_open_calls_open_url(self):
        h = Harness()
        h.controller.on_action(Action(OPEN, "https://example.test/x"))
        self.assertEqual(h.opened, ["https://example.test/x"])
        self.assertEqual(h.build_calls, [])

    def test_quit(self):
        h = Harness()
        h.controller.on_action(Action(QUIT))
        self.assertEqual(h.backend.quit_calls, 1)

    def test_update_replaced_restarts_then_quits(self):
        h = Harness(update=lambda: True)
        h.controller.on_action(Action(UPDATE))
        self.assertEqual(h.backend.events, ["restart", "quit"])
        self.assertEqual(h.build_calls, [])

    def test_update_not_replaced_reloads(self):
        h = Harness(update=lambda: False)
        h.controller.on_action(Action(UPDATE))
        self.assertEqual(h.backend.events, [])
        self.assertEqual(h.build_calls, [False])
        self.assertEqual(h.backend.shown[-1].title, "C 1/2")

    def test_update_failure_shows_an_error_menu(self):
        def boom():
            raise RuntimeError("download HTTP 404")

        h = Harness(update=boom)
        h.controller.on_action(Action(UPDATE))
        menu = h.backend.shown[-1]
        self.assertEqual(menu.title, "Claude ?")
        self.assertEqual(menu.items[0].text, "Lỗi: RuntimeError: download HTTP 404")
        self.assertEqual(h.restarts, 0)
        self.assertEqual(h.backend.quit_calls, 0)

    def test_language_action_saves_the_choice_and_redraws_in_it(self):
        current = [Translator("vi")]
        h = Harness(translator=lambda: current[0])

        def change_setting(action):
            h.settings.append(action)
            current[0] = Translator(action.value)

        h.controller._change_setting = change_setting
        h.controller.on_action(Action(LANGUAGE, value="en"))
        self.assertEqual(h.settings, [Action(LANGUAGE, value="en")])
        self.assertEqual(h.backend.shown[-1].items[-1].text, "Quit")

    def test_account_and_compact_actions_are_saved_then_the_menu_is_rebuilt(self):
        h = Harness()
        for action in (Action(HIDE_ACCOUNT, value="c-m"), Action(SHOW_ACCOUNT, value="c-m"),
                       Action(COMPACT, value="on")):
            h.controller.on_action(action)
        self.assertEqual([a.kind for a in h.settings], [HIDE_ACCOUNT, SHOW_ACCOUNT, COMPACT])
        self.assertEqual(len(h.build_calls), 3)

    def test_with_quit_keeps_the_compact_flag(self):
        menu = with_quit(Menu("C 1/2", None, False, 2.0, (), compact=True), Translator("vi"))
        self.assertTrue(menu.compact)


class ReloadTest(unittest.TestCase):
    def test_reloads_asked_while_busy_coalesce_into_one_follow_up(self):
        queue = []
        h = Harness(spawn=queue.append)
        h.controller.reload()
        h.controller.reload()
        h.controller.reload()
        self.assertEqual(len(queue), 1)  # still one worker while busy
        queue.pop()()
        self.assertEqual(len(queue), 1)  # exactly one follow-up, not two
        queue.pop()()
        self.assertEqual(queue, [])
        self.assertEqual(len(h.build_calls), 2)
        self.assertEqual(h.refresh_calls, 0)

    def test_a_refresh_clicked_while_busy_still_refreshes(self):
        queue = []
        h = Harness(spawn=queue.append)
        h.controller.reload()
        h.controller.on_action(Action(REFRESH))
        queue.pop()()
        self.assertEqual(h.refresh_calls, 0)
        queue.pop()()  # the follow-up keeps the force flag
        self.assertEqual(h.refresh_calls, 1)

    def test_build_menu_failure_shows_an_error_menu_and_frees_the_lock(self):
        calls = []

        def build(force):
            calls.append(force)
            if len(calls) == 1:
                raise ValueError("x" * 300)
            return REAL

        h = Harness(build_menu=build)
        h.controller.reload()
        menu = h.backend.shown[-1]
        self.assertEqual(menu.title, "Claude ?")
        self.assertTrue(menu.items[0].text.startswith("Lỗi: ValueError: xxx"))
        self.assertEqual(len(menu.items[0].text), len("Lỗi: ValueError: ") + 160)
        self.assertEqual(menu.items[1].action.kind, REFRESH)
        self.assertEqual(menu.items[-1].action.kind, QUIT)
        h.controller.reload()
        self.assertEqual(h.backend.shown[-1].title, "C 1/2")

    def test_refresh_failure_shows_an_error_menu(self):
        h = Harness()

        def boom():
            raise OSError("offline")

        h.controller._refresh = boom
        h.controller.reload(force=True)
        self.assertEqual(h.backend.shown[-1].items[0].text, "Lỗi: OSError: offline")
        self.assertEqual(h.build_calls, [])


class MenuHelpers(unittest.TestCase):
    def test_with_quit_appends_a_separator_and_quit_without_mutating(self):
        before = REAL.items
        out = with_quit(REAL, VI)
        self.assertEqual(out.items, before + (SEPARATOR, quit_item(VI)))
        self.assertEqual(REAL.items, before)
        self.assertEqual((out.title, out.worst), (REAL.title, REAL.worst))

    def test_english_quit_and_loading(self):
        en = Translator("en")
        self.assertEqual(quit_item(en).text, "Quit")
        self.assertEqual(loading_menu(en).items[0].text, "Reading limits...")
        self.assertEqual(error_menu(KeyError("k"), en).items[0].text, "Error: KeyError: 'k'")


class Relaunch(unittest.TestCase):
    def test_command(self):
        script = os.path.join(os.path.abspath(os.sep), "opt", "p.py")
        self.assertEqual(relaunch_command([script, "--tray", "x"], "/py"),
                         ["/py", os.path.realpath(script), "--tray", "x"])

    def test_relaunch_starts_a_detached_process(self):
        script = os.path.join(os.path.abspath(os.sep), "opt", "p.py")
        with mock.patch("claude_usage.tray.subprocess.Popen") as popen:
            relaunch([script, "--tray"], "/py")
        args, kwargs = popen.call_args
        self.assertEqual(args[0], ["/py", os.path.realpath(script), "--tray"])
        if os.name == "nt":
            self.assertIn("creationflags", kwargs)
        else:
            self.assertTrue(kwargs["start_new_session"])


if __name__ == "__main__":
    unittest.main()
