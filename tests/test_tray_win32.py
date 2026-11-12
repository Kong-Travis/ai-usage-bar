"""Windows tray backend: pure helpers and behaviour (any OS, with a fake Win32 layer), plus
Windows-only checks against the real API."""
from __future__ import annotations

import os
import sys
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from claude_usage import tray_win32 as tw  # noqa: E402  (must import without Windows)
from claude_usage.i18n import Translator  # noqa: E402
from claude_usage.tray import TrayBackend, TrayController  # noqa: E402
from claude_usage.domain import AccountRow, Usage  # noqa: E402
from fakes import claude_usage  # noqa: E402
from claude_usage.view import (COMPACT, CRIT_COLOR, OK_COLOR, OPEN, QUIT, REFRESH, WARN_COLOR,  # noqa: E402
                               Action, Menu, MenuItem, SEPARATOR, Presenter)


class FakeClock:
    def now(self):
        return 0.0

ON_WINDOWS = sys.platform == "win32"


def sample_menu(worst=87.4, color=WARN_COLOR, title="C 87/8/1  T 0/65/1") -> Menu:
    return Menu(title, color, False, worst, (
        MenuItem("C: claude - a@b.c", size=13),
        MenuItem("5 giờ: 87%", color=color),
        SEPARATOR,
        MenuItem("Làm mới ngay", action=Action(REFRESH)),
        MenuItem("Ủng hộ tác giả", action=Action(OPEN, "https://example.com")),
        SEPARATOR,
        MenuItem("Thoát", action=Action(QUIT)),
    ))


class PureHelpers(unittest.TestCase):
    def test_tooltip_is_the_title_cut_to_127_utf16_units(self):
        self.assertEqual(tw.win32_tooltip("C 13/8/1  T 0/65/1"), "C 13/8/1  T 0/65/1")
        self.assertEqual(tw.win32_tooltip("x" * 300), "x" * 127)
        self.assertEqual(tw.win32_tooltip("a\0b"), "ab")
        # A character outside the BMP takes two UTF-16 units in szTip.
        tip = tw.win32_tooltip("\U0001F600" * 100)
        self.assertLessEqual(len(tip.encode("utf-16-le")) // 2, 127)
        self.assertEqual(len(tip), 63)

    def test_balloon_is_a_warning_that_leaves_icon_and_tooltip_alone(self):
        fields = tw.win32_balloon_fields("Claude Code", "C claude, 5 giờ: đã dùng 92%")
        self.assertEqual(fields, {"uFlags": tw._W32.NIF_INFO, "szInfoTitle": "Claude Code",
                                  "szInfo": "C claude, 5 giờ: đã dùng 92%",
                                  "dwInfoFlags": tw._W32.NIIF_WARNING})
        self.assertEqual((tw._W32.NIF_INFO, tw._W32.NIIF_WARNING), (0x10, 0x2))

    def test_balloon_text_fits_its_fixed_size_fields(self):
        # ctypes raises ValueError for a string longer than a WCHAR array.
        fields = tw.win32_balloon_fields("t" * 100, "\U0001F600" * 200)
        self.assertEqual(fields["szInfoTitle"], "t" * 63)
        self.assertLessEqual(len(fields["szInfo"].encode("utf-16-le")) // 2, 255)
        self.assertEqual(tw.win32_balloon_fields("a\0b", "")["szInfo"], " ")  # empty hides it
        self.assertEqual(tw.win32_balloon_fields("a\0b", "x")["szInfoTitle"], "ab")

    def test_icon_text_is_the_rounded_worst_or_question_mark(self):
        self.assertEqual(tw.win32_icon_text(None), "?")
        self.assertEqual(tw.win32_icon_text(0), "0")
        self.assertEqual(tw.win32_icon_text(87.6), "88")
        self.assertEqual(tw.win32_icon_text(100.0), "100")

    def test_icon_colors_follow_the_level(self):
        green, yellow, orange = (tw.win32_icon_colors(c) for c in (OK_COLOR, WARN_COLOR, CRIT_COLOR))
        self.assertEqual(len({green[0], yellow[0], orange[0], tw.WIN32_NEUTRAL_RGB}), 4)
        self.assertEqual(tw.win32_icon_colors(None)[0], tw.WIN32_NEUTRAL_RGB)  # nothing known
        self.assertEqual(tw.win32_icon_colors("gray")[0], tw.WIN32_NEUTRAL_RGB)
        # White digits on green and orange; dark ones on yellow, where white is unreadable.
        self.assertEqual((green[1], orange[1]), (tw.WIN32_TEXT_RGB, tw.WIN32_TEXT_RGB))
        self.assertEqual(yellow[1], tw.WIN32_DARK_TEXT_RGB)

    def test_font_shrinks_for_three_digits_and_stays_inside_the_icon(self):
        for size in (16, 20, 24, 32):
            heights = [tw.win32_font_height(size, t) for t in ("?", "88", "100")]
            self.assertEqual(heights, sorted(heights, reverse=True))
            self.assertTrue(all(6 <= h <= size for h in heights))

    def test_icon_mask_is_a_rounded_square(self):
        size = 16
        mask = tw.win32_icon_mask(size)
        self.assertEqual(len(mask), size * size)
        at = lambda x, y: mask[y * size + x]  # noqa: E731
        for corner in ((0, 0), (size - 1, 0), (0, size - 1), (size - 1, size - 1)):
            self.assertFalse(at(*corner))
        for inside in ((size // 2, 0), (0, size // 2), (size // 2, size // 2), (3, 3)):
            self.assertTrue(at(*inside))
        self.assertEqual(mask, mask[::-1])  # symmetric under a 180 degree turn

    def test_pixel_and_colorref_byte_orders(self):
        self.assertEqual(tw.win32_argb((0x11, 0x22, 0x33)), 0xFF112233)
        self.assertEqual(tw.win32_argb((0x11, 0x22, 0x33), 0), 0x00112233)
        self.assertEqual(tw.win32_colorref((0x11, 0x22, 0x33)), 0x00332211)

    def test_menu_label_escapes_mnemonics_and_tabs(self):
        self.assertEqual(tw.win32_menu_label("A & B\tC"), "A && B C")

    def test_menu_entries_map_actions_to_command_ids(self):
        entries, actions = tw.win32_menu_entries(sample_menu().items)
        W = tw._W32
        self.assertEqual(entries, [
            (W.MF_STRING | W.MF_GRAYED, 0, "C: claude - a@b.c"),
            (W.MF_STRING | W.MF_GRAYED, 0, "5 giờ: 87%"),
            (W.MF_SEPARATOR, 0, ""),
            (W.MF_STRING, 1, "Làm mới ngay"),
            (W.MF_STRING, 2, "Ủng hộ tác giả"),
            (W.MF_SEPARATOR, 0, ""),
            (W.MF_STRING, 3, "Thoát"),
        ])
        self.assertEqual(actions, {1: Action(REFRESH), 2: Action(OPEN, "https://example.com"),
                                   3: Action(QUIT)})

    def test_checked_item_gets_the_native_checkmark(self):
        hide = Action("hide_account", value="c-m")
        entries, _ = tw.win32_menu_entries((MenuItem("Show", checked=True, action=hide),))
        W = tw._W32
        self.assertEqual(entries, [(W.MF_STRING | W.MF_CHECKED, 1, "Show")])

    def test_no_collapse_or_expand_item_since_the_icon_is_always_compact(self):
        for compact in (False, True):
            with self.subTest(compact=compact):
                p = Presenter(FakeClock(), Translator("vi"), "1.3.2", "https://x.test", 70, 90, 3,
                              compact=compact)
                menu = p.present([AccountRow("C", "claude", "", claude_usage(1.0, 0, 2.0, 0), None)], None)
                entries, actions = tw.win32_menu_entries(menu.items)
                self.assertNotIn(COMPACT, [a.kind for a in actions.values()])
                self.assertNotEqual(entries[0][0], tw._W32.MF_SEPARATOR)
                flags = [e[0] for e in entries]
                self.assertFalse(any(a == b == tw._W32.MF_SEPARATOR for a, b in zip(flags, flags[1:])))

    def test_backend_implements_every_abstract_method(self):
        self.assertTrue(issubclass(tw.Win32TrayBackend, TrayBackend))
        self.assertEqual(tw.Win32TrayBackend.__abstractmethods__, frozenset())

    def test_own_strings_exist_in_both_languages(self):
        for key, entry in tw.WIN32_TEXT.items():
            self.assertEqual(set(entry), {"vi", "en"}, key)


class FakeApi:
    """Records what the backend asks of Win32. run_loop runs `script`, then returns."""

    def __init__(self, mutex_taken_for=0, chosen_command=0, notify_ok=True):
        self.calls = []
        self.mutex_taken_for = mutex_taken_for  # create_mutex answers "exists" this many times
        self.chosen_command = chosen_command
        self.notify_ok = notify_ok
        self.on_message = None
        self.script = []
        self.icons = 0
        self.live_icons = set()

    def create_window(self, on_message):
        self.on_message = on_message
        return 0xABC

    def register_message(self, name):
        assert name == "TaskbarCreated"
        return 0xC123

    def run_loop(self):
        for step in self.script:
            step()

    def post(self, hwnd, msg):
        self.calls.append(("post", msg))
        return True

    def post_quit(self):
        self.calls.append(("post_quit",))

    def set_timer(self, hwnd, timer_id, ms):
        self.calls.append(("set_timer", timer_id, ms))

    def kill_timer(self, hwnd, timer_id):
        self.calls.append(("kill_timer", timer_id))

    def destroy_window(self, hwnd):
        self.calls.append(("destroy_window",))

    def create_mutex(self, name):
        self.calls.append(("create_mutex", name))
        if self.mutex_taken_for > 0:
            self.mutex_taken_for -= 1
            return 7, True
        return 8, False

    def close_handle(self, handle):
        self.calls.append(("close_handle", handle))

    def notify(self, op, hwnd, icon_id, callback_msg, hicon, tip):
        self.calls.append(("notify", op, hicon, tip))
        return self.notify_ok

    def create_icon(self, text, background, foreground):
        self.icons += 1
        self.live_icons.add(self.icons)
        self.calls.append(("create_icon", text, background))
        return self.icons

    def destroy_icon(self, hicon):
        self.live_icons.discard(hicon)
        self.calls.append(("destroy_icon", hicon))

    def balloon(self, hwnd, icon_id, fields):
        self.calls.append(("balloon", icon_id, fields))
        return True

    def popup_menu(self, hwnd, entries):
        self.calls.append(("popup_menu", list(entries)))
        return self.chosen_command

    def named(self, name):
        return [c for c in self.calls if c[0] == name]


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def make_backend(api, **kwargs):
    clock = Clock()
    return tw.Win32TrayBackend(api=api, sleep=clock.sleep, clock=clock, **kwargs), clock


class BackendBehaviour(unittest.TestCase):
    W = tw._W32

    def started(self, **api_kwargs):
        """A backend whose run() has finished on_start; returns (backend, api)."""
        api = FakeApi(**api_kwargs)
        backend, _ = make_backend(api)
        backend.run(lambda: None)
        backend._quitting = False  # run() tore down at the end of the scripted loop: reopen
        backend._hwnd = 0xABC
        return backend, api

    def test_run_creates_the_window_starts_and_cleans_up(self):
        api = FakeApi()
        backend, _ = make_backend(api)
        started = []
        backend.run(lambda: started.append(True))
        self.assertEqual(started, [True])
        self.assertIn(("post", backend.WM_WAKE), api.calls)
        self.assertIn(("destroy_window",), api.calls)
        self.assertEqual(api.calls[-1], ("close_handle", 8))
        self.assertEqual(api.named("create_mutex"), [("create_mutex", "Local\\claude-usage-bar-tray")])

    def test_show_draws_the_icon_and_tooltip_then_frees_the_old_icon(self):
        api = FakeApi()
        backend, _ = make_backend(api)
        api.script = [lambda: backend.show(sample_menu(worst=12.4, color=None)),
                      lambda: backend.show(sample_menu(worst=95, color=CRIT_COLOR, title="T 95/1/1"))]
        backend.run(lambda: None)
        self.assertEqual(api.named("create_icon"), [
            ("create_icon", "12", tw.WIN32_NEUTRAL_RGB), ("create_icon", "95", tw.WIN32_LEVEL_RGB[CRIT_COLOR][0])])
        notes = api.named("notify")
        self.assertEqual(notes[0], ("notify", self.W.NIM_ADD, 1, "C 87/8/1  T 0/65/1"))
        self.assertEqual(notes[1], ("notify", self.W.NIM_MODIFY, 2, "T 95/1/1"))
        self.assertEqual(notes[2][1], self.W.NIM_DELETE)  # teardown removes the icon
        self.assertEqual(api.live_icons, set())  # every HICON destroyed

    def test_tooltip_before_the_first_menu_is_translated(self):
        api = FakeApi()
        backend = tw.Win32TrayBackend(Translator("en"), api=api)
        backend._hwnd = 0xABC
        backend._sync_icon()
        self.assertEqual(api.named("notify")[0][3], "AI: starting...")
        api2 = FakeApi()
        backend2 = tw.Win32TrayBackend(api=api2)
        backend2._hwnd = 0xABC
        backend2._sync_icon()
        self.assertEqual(api2.named("notify")[0][3], "AI: đang khởi động...")

    def test_failed_add_is_retried_on_next_show(self):
        backend, api = self.started(notify_ok=False)
        backend.show(sample_menu())
        api.notify_ok = True
        backend.show(sample_menu())
        ops = [c[1] for c in api.named("notify")]
        self.assertEqual(ops, [self.W.NIM_ADD, self.W.NIM_ADD])

    def test_click_opens_the_menu_and_dispatches_the_chosen_action(self):
        backend, api = self.started(chosen_command=2)
        got = []
        backend.set_action_handler(got.append)
        backend.show(sample_menu())
        self.assertEqual(backend._on_message(backend.WM_TRAY, 1, self.W.WM_RBUTTONUP), 0)
        self.assertEqual(got, [Action(OPEN, "https://example.com")])
        backend._on_message(backend.WM_TRAY, 1, self.W.WM_LBUTTONUP)
        self.assertEqual(len(api.named("popup_menu")), 2)
        backend._on_message(backend.WM_TRAY, 1, 0x0200)  # WM_MOUSEMOVE: nothing
        self.assertEqual(len(api.named("popup_menu")), 2)

    def test_dismissed_menu_dispatches_nothing(self):
        backend, api = self.started(chosen_command=0)
        got = []
        backend.set_action_handler(got.append)
        backend.show(sample_menu())
        backend._on_message(backend.WM_TRAY, 1, self.W.WM_LBUTTONUP)
        self.assertEqual(got, [])

    def test_handler_errors_do_not_escape_into_the_message_loop(self):
        backend, api = self.started(chosen_command=1)
        backend.set_action_handler(lambda action: 1 / 0)
        backend.show(sample_menu())
        stderr, sys.stderr = sys.stderr, None  # as under pythonw
        try:
            self.assertEqual(backend._on_message(backend.WM_TRAY, 1, self.W.WM_LBUTTONUP), 0)
        finally:
            sys.stderr = stderr

    def test_call_soon_queues_and_wakes_the_ui_thread(self):
        api = FakeApi()
        backend, _ = make_backend(api)
        ran = []
        backend.call_soon(lambda: ran.append("early"))  # before the window: no post
        self.assertEqual(api.named("post"), [])
        api.script = [lambda: backend._on_message(backend.WM_WAKE, 0, 0)]

        def start():
            worker = threading.Thread(target=lambda: backend.call_soon(lambda: ran.append("late")))
            worker.start()
            worker.join()

        backend.run(start)
        self.assertEqual(ran, ["early", "late"])
        self.assertEqual(len(api.named("post")), 2)  # run's own wake + the worker's

    def test_every_sets_a_timer_and_the_timer_calls_back(self):
        backend, api = self.started()
        ticks = []
        backend.every(60, lambda: ticks.append(1))
        self.assertEqual(api.named("set_timer"), [("set_timer", 1, 60000)])
        backend._on_message(self.W.WM_TIMER, 1, 0)
        backend._on_message(self.W.WM_TIMER, 99, 0)  # not ours: left to DefWindowProc
        self.assertEqual(ticks, [1])
        self.assertIsNone(backend._on_message(self.W.WM_TIMER, 99, 0))

    def test_timers_registered_before_run_start_with_the_window(self):
        api = FakeApi()
        backend, _ = make_backend(api)
        backend.every(5, lambda: None)
        self.assertEqual(api.named("set_timer"), [])
        backend.run(lambda: None)
        self.assertEqual(api.named("set_timer"), [("set_timer", 1, 5000)])

    def test_taskbar_created_adds_the_icon_again(self):
        backend, api = self.started()
        backend.show(sample_menu())
        self.assertEqual(backend._on_message(0xC123, 0, 0), 0)
        ops = [c[1] for c in api.named("notify")]
        self.assertEqual(ops, [self.W.NIM_ADD, self.W.NIM_ADD])

    def test_quit_removes_the_icon_and_ends_the_loop_once(self):
        backend, api = self.started()
        backend.every(60, lambda: None)
        backend.show(sample_menu())
        backend.quit()
        backend.quit()
        self.assertEqual(api.named("post_quit"), [("post_quit",)])
        self.assertIn(("kill_timer", 1), api.calls)
        self.assertEqual(api.named("notify")[-1][1], self.W.NIM_DELETE)
        self.assertEqual(api.live_icons, set())
        backend.show(sample_menu())  # a late show after quit must not re-add the icon
        self.assertEqual(api.named("notify")[-1][1], self.W.NIM_DELETE)

    def test_notify_shows_a_balloon_on_the_added_icon(self):
        backend, api = self.started()
        backend.show(sample_menu())
        backend.notify("Codex", "Cx codex, Tuần: đã hết hạn mức")
        self.assertEqual(api.named("balloon"), [
            ("balloon", backend.ICON_ID, tw.win32_balloon_fields("Codex", "Cx codex, Tuần: đã hết hạn mức"))])

    def test_notify_without_an_icon_or_after_quit_does_nothing(self):
        backend, api = self.started(notify_ok=False)  # Explorer not ready: icon not added
        backend.show(sample_menu())
        backend.notify("Codex", "x")
        api.notify_ok = True
        backend.show(sample_menu())
        backend.quit()
        backend.notify("Codex", "x")
        self.assertEqual(api.named("balloon"), [])

    def test_unknown_messages_go_to_defwindowproc(self):
        backend, _ = self.started()
        self.assertIsNone(backend._on_message(0x0001, 0, 0))

    def test_second_instance_waits_for_the_first_then_runs(self):
        api = FakeApi(mutex_taken_for=3)  # the old tray quits after a self-update
        backend, clock = make_backend(api)
        started = []
        backend.run(lambda: started.append(True))
        self.assertEqual(started, [True])
        self.assertEqual(len(api.named("create_mutex")), 4)
        self.assertLess(clock.now, 10)

    def test_second_instance_gives_up_quietly_after_ten_seconds(self):
        api = FakeApi(mutex_taken_for=10 ** 6)
        backend, clock = make_backend(api)
        started = []
        backend.run(lambda: started.append(True))
        self.assertEqual(started, [])
        self.assertGreaterEqual(clock.now, 10)
        self.assertLess(clock.now, 11)
        self.assertIsNone(api.on_message)  # never created a window


class ControllerWiring(unittest.TestCase):
    """TrayController driving the backend on the fake Win32 layer: the same wiring as the
    Windows-only smoke test, checked on every OS."""

    def test_controller_shows_loading_then_the_built_menu_and_quits_from_the_menu(self):
        api = FakeApi()
        backend, _ = make_backend(api)
        menu = sample_menu()
        controller = TrayController(backend, build_menu=lambda force: menu, refresh=lambda: None,
                                    update=lambda: False, restart=lambda: None,
                                    translator=lambda: Translator("en"),
                                    change_setting=lambda action: None,
                                    check_update=lambda: None, spawn=lambda fn: fn())
        quit_command = []

        def click_quit():
            entries, actions = tw.win32_menu_entries(backend._menu.items)
            quit_command.append(max(c for c, a in actions.items() if a.kind == QUIT))
            api.chosen_command = quit_command[-1]
            backend._on_message(backend.WM_TRAY, 1, tw._W32.WM_LBUTTONUP)

        api.script = [lambda: backend._on_message(backend.WM_WAKE, 0, 0), click_quit]
        controller.run()
        texts = [c[1] for c in api.named("create_icon")]
        self.assertEqual(texts, ["?", "87"])  # loading menu, then the built one
        self.assertEqual(backend._menu.items[-1].text, "Quit")  # tray_menu added by the controller
        self.assertEqual(api.named("post_quit"), [("post_quit",)])
        self.assertEqual(api.named("set_timer"), [("set_timer", 1, 60000)])
        self.assertEqual(api.live_icons, set())


@unittest.skipUnless(ON_WINDOWS, "needs the Win32 API")
class RealWin32(unittest.TestCase):
    def test_notifyicondata_has_the_documented_size(self):
        import ctypes
        api = tw._Win32Api()
        expected = 976 if ctypes.sizeof(ctypes.c_void_p) == 8 else 956
        self.assertEqual(ctypes.sizeof(api.NOTIFYICONDATAW), expected)

    def test_balloon_fields_fit_the_real_struct(self):
        api = tw._Win32Api()
        data = api.NOTIFYICONDATAW()
        for name, value in tw.win32_balloon_fields("t" * 100, "b" * 400).items():
            setattr(data, name, value)  # raises ValueError if a string is too long
        self.assertEqual((data.uFlags, data.dwInfoFlags), (0x10, 0x2))
        self.assertEqual(len(data.szInfo), 255)

    def test_icons_can_be_drawn_and_destroyed(self):
        api = tw._Win32Api()
        for text in ("?", "7", "88", "100"):
            hicon = api.create_icon(text, *tw.WIN32_LEVEL_RGB[WARN_COLOR])
            self.assertTrue(hicon)
            api.destroy_icon(hicon)

    def test_held_mutex_makes_a_second_tray_leave(self):
        api = tw._Win32Api()
        name = "Local\\claude-usage-bar-test-%d" % os.getpid()
        handle, existed = api.create_mutex(name)
        self.assertFalse(existed)
        try:
            backend = tw.Win32TrayBackend(mutex_name=name, wait_seconds=0.5)
            started = []
            backend.run(lambda: started.append(True))
            self.assertEqual(started, [])
        finally:
            api.close_handle(handle)

    def test_smoke_tray_controller_shows_a_menu_and_quits(self):
        menu = sample_menu()
        shown = []

        class Recording(tw.Win32TrayBackend):
            def show(self, m):
                shown.append(m)
                super().show(m)

        backend = Recording(mutex_name="Local\\claude-usage-bar-smoke-%d" % os.getpid())
        controller = TrayController(backend, build_menu=lambda force: menu, refresh=lambda: None,
                                    update=lambda: False, restart=lambda: None,
                                    translator=Translator, change_setting=lambda action: None,
                                    check_update=lambda: None)
        quitter = threading.Timer(2.0, lambda: backend.call_soon(backend.quit))
        watchdog = threading.Timer(30.0, lambda: os._exit(3))  # a missed quit must not hang CI
        quitter.start()
        watchdog.start()
        try:
            controller.run()
        finally:
            quitter.cancel()
            watchdog.cancel()
        self.assertGreaterEqual(len(shown), 2)  # loading menu, then the built one
        self.assertEqual(shown[-1].title, menu.title)
        self.assertEqual(shown[-1].items[:len(menu.items)], menu.items)


if __name__ == "__main__":
    unittest.main()
