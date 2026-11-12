"""SwiftBar / Argos text output."""
import srcpath  # noqa: F401  (must come first)

import unittest

from claude_usage.domain import AGY, CLAUDE, CODEX
from claude_usage.view import (CRIT_COLOR, LANGUAGE, OPEN, QUIT, REFRESH, SEPARATOR, UPDATE, WARN_COLOR,
                               WARNING_ICON, Action, Menu, MenuItem)
from claude_usage.xbar import ARGOS, SWIFTBAR, XbarRenderer

SELF = "/opt/claude usage/plugin.py"


def menu(items=(), title="C 1/2/3", color=None, update=False, colored=False):
    return Menu(title, color, update, None, tuple(items), colored=colored)


class Title(unittest.TestCase):
    def test_swiftbar_title_text_is_never_colored_and_update_icon(self):
        r = XbarRenderer(SELF, SWIFTBAR)
        self.assertEqual(r.title(menu()), "C 1/2/3 |")
        # Colored numbers were too glaring: the system text color at every level.
        self.assertEqual(r.title(menu(color=WARN_COLOR)), "C 1/2/3 |")
        self.assertEqual(r.title(menu(color=CRIT_COLOR, update=True)),
                         "C 1/2/3 | sfimage=arrow.down.circle")
        # With the colors setting on, the level color as a "light,dark" pair for a light bar.
        self.assertEqual(r.title(menu(color=WARN_COLOR, colored=True)), "C 1/2/3 | color=#b8860b,#ffd60a")
        self.assertNotIn("dropdown", r.title(menu()))

    def test_argos_uses_icon_name_and_hides_the_dropdown_line(self):
        r = XbarRenderer(SELF, ARGOS)
        self.assertEqual(r.title(menu()), "C 1/2/3 | dropdown=false")
        self.assertEqual(r.title(menu(color=CRIT_COLOR, update=True)),
                         "C 1/2/3 | iconName=software-update-available-symbolic dropdown=false")
        # No pair syntax on Argos: the dark-panel shade alone.
        self.assertEqual(r.title(menu(color=CRIT_COLOR, colored=True)), "C 1/2/3 | color=#ff9f0a dropdown=false")
        self.assertNotIn("sfimage", r.title(menu(update=True)))

    def test_default_flavor_is_swiftbar(self):
        self.assertEqual(XbarRenderer(SELF).title(menu(update=True)),
                         "C 1/2/3 | sfimage=arrow.down.circle")


class Items(unittest.TestCase):
    def test_render_layout(self):
        out = XbarRenderer(SELF).render(menu([
            MenuItem("plain"),
            SEPARATOR,
            MenuItem("styled", color="gray", size=11),
        ]))
        self.assertEqual(out, "C 1/2/3 |\n---\nplain\n---\nstyled | color=gray size=11\n")

    def test_actions(self):
        r = XbarRenderer(SELF)
        self.assertEqual(r.item(MenuItem("Open", action=Action(OPEN, "https://x/y#z"))),
                         "Open | href=https://x/y#z")
        self.assertEqual(r.item(MenuItem("Refresh", action=Action(REFRESH))),
                         'Refresh | bash="%s" param1=--force terminal=false refresh=true' % SELF)
        self.assertEqual(r.item(MenuItem("Update", color="#2e7d32", action=Action(UPDATE))),
                         'Update | color=#2e7d32 bash="%s" param1=--update terminal=false refresh=true' % SELF)

    def test_language_action_runs_the_plugin_with_the_language(self):
        line = XbarRenderer(SELF).item(MenuItem("EN", action=Action(LANGUAGE, value="en")))
        self.assertEqual(line, 'EN | bash="%s" param1=--lang param2=en terminal=false refresh=true' % SELF)

    def test_quit_items_are_skipped(self):
        out = XbarRenderer(SELF).render(menu([MenuItem("a"), MenuItem("Quit", action=Action(QUIT)),
                                              MenuItem("b")]))
        self.assertEqual(out, "C 1/2/3 |\n---\na\nb\n")

    def test_pipe_in_text_is_replaced(self):
        line = XbarRenderer(SELF).item(MenuItem("Lỗi: a | b", color="gray", size=11))
        self.assertEqual(line, "Lỗi: a / b | color=gray size=11")
        self.assertEqual(line.count("|"), 1)


class Icons(unittest.TestCase):
    def test_swiftbar_sf_symbols_tinted_with_the_item_color(self):
        r = XbarRenderer(SELF, SWIFTBAR)
        self.assertEqual(r.item(MenuItem("Claude Code", color="gray", size=12, icon=CLAUDE)),
                         "Claude Code | color=gray size=12 sfimage=sparkle sfcolor=gray")
        self.assertEqual(r.item(MenuItem("Codex", icon=CODEX)), "Codex | sfimage=terminal")
        self.assertEqual(r.item(MenuItem("Antigravity", icon=AGY)), "Antigravity | sfimage=atom")
        # A level color tints the icon (light,dark pair) but not the text.
        self.assertEqual(r.item(MenuItem("w", color=CRIT_COLOR, icon=WARNING_ICON)),
                         "w | sfimage=exclamationmark.triangle sfcolor=#c93400,#ff9f0a")
        self.assertEqual(r.item(MenuItem("Weekly: 75%", color=WARN_COLOR)), "Weekly: 75%")
        self.assertEqual(r.item(MenuItem("Weekly: 75%", color=WARN_COLOR), colored=True),
                         "Weekly: 75% | color=#b8860b,#ffd60a")

    def test_argos_icon_names(self):
        r = XbarRenderer(SELF, ARGOS)
        self.assertEqual(r.item(MenuItem("Codex", icon=CODEX)),
                         "Codex | iconName=utilities-terminal-symbolic")
        self.assertEqual(r.item(MenuItem("w", color=WARN_COLOR, icon=WARNING_ICON)),
                         "w | iconName=dialog-warning-symbolic")

    def test_argos_checkmark_wins_over_the_icon(self):
        line = XbarRenderer(SELF, ARGOS).item(MenuItem("x", checked=True, icon=CLAUDE))
        self.assertEqual(line, "x | iconName=object-select-symbolic")

    def test_unknown_or_missing_icon_adds_nothing(self):
        for flavor in (SWIFTBAR, ARGOS):
            r = XbarRenderer(SELF, flavor)
            self.assertEqual(r.item(MenuItem("x", icon="nope")), "x")
            self.assertEqual(r.item(MenuItem("x")), "x")


if __name__ == "__main__":
    unittest.main()
