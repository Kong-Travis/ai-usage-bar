"""Text output for SwiftBar (macOS) and Argos (GNOME), both BitBar-compatible."""
from __future__ import annotations

from typing import List

from .domain import AGY, CLAUDE, CODEX
from .view import (CHECK_UPDATE, COMPACT, CRIT_COLOR, HIDE_ACCOUNT, LANGUAGE, OK_COLOR, OPEN, QUIT, REFRESH,
                   SHOW_ACCOUNT, UPDATE, WARN_COLOR, WARNING_ICON, Menu, MenuItem)

SWIFTBAR, ARGOS = "swiftbar", "argos"
CLI_FLAGS = {REFRESH: "--force", UPDATE: "--update", CHECK_UPDATE: "--check-update"}
# Settings take one value: the language, the account label, or on / off.
SETTING_FLAGS = {LANGUAGE: "--lang", SHOW_ACCOUNT: "--show", HIDE_ACCOUNT: "--hide",
                 COMPACT: "--compact"}
# The icon of the compact bar. CoreGlyphs' name_availability.plist lists "speedometer" in the
# 2019 set (macOS 10.15) and name_aliases.strings maps it to gauge.with.dots.needle.67percent,
# so it resolves on Big Sur, the oldest macOS with sfimage, and on every later one.
COMPACT_SFIMAGE, UPDATE_SFIMAGE = "speedometer", "arrow.down.circle"
COMPACT_ARGOS_ICON = "utilities-system-monitor-symbolic"
UPDATE_ARGOS_ICON = "software-update-available-symbolic"
# MenuItem.icon -> SF Symbol. Each name is in the 2019 or 2020 set of CoreGlyphs'
# name_availability.plist (macOS 10.15 / 11), so it draws on Big Sur and later:
# sparkle 2020 (Anthropic's spark mark), terminal 2020 (Codex, a terminal agent),
# atom 2020 (Antigravity), exclamationmark.triangle 2019.
ITEM_SFIMAGE = {CLAUDE: "sparkle", CODEX: "terminal", AGY: "atom",
                WARNING_ICON: "exclamationmark.triangle"}
# The same for Argos: freedesktop / Adwaita symbolic names (not checked on a GNOME desktop
# here; an unknown name draws no icon, the text stays).
ITEM_ARGOS_ICON = {CLAUDE: "starred-symbolic", CODEX: "utilities-terminal-symbolic",
                   AGY: "applications-science-symbolic", WARNING_ICON: "dialog-warning-symbolic"}

# Level colors as SwiftBar "light,dark" pairs (no space: params split on spaces). The view's
# colors suit a dark background; a light menu bar or menu needs darker shades to stay readable.
# Argos has no pair syntax and the GNOME top bar is dark, so it keeps the view's color.
SWIFTBAR_COLORS = {OK_COLOR: "#248a3d," + OK_COLOR, WARN_COLOR: "#b8860b," + WARN_COLOR,
                   CRIT_COLOR: "#c93400," + CRIT_COLOR}


def xbar_quote(value: str) -> str:
    """A parameter value SwiftBar and Argos read as one word, spaces included."""
    if not any(c in value for c in " \t'\""):
        return value
    return "'%s'" % value if '"' in value else '"%s"' % value


class XbarRenderer:
    """Renders a Menu as BitBar plugin output.

    self_path is the plugin file, which the menu runs again for its refresh and update items.
    """

    def __init__(self, self_path: str, flavor: str = SWIFTBAR):
        self._self_path = self_path
        self._flavor = flavor

    def render(self, menu: Menu) -> str:
        lines = [self.title(menu), "---"]
        for item in menu.items:
            if item.action and item.action.kind == QUIT:
                continue  # the host app has its own quit
            lines.append("---" if item.separator else self.item(item))
        return "\n".join(lines) + "\n"

    def title(self, menu: Menu) -> str:
        if menu.compact:
            return self.compact_title(menu)
        line = menu.title + " |"
        if menu.title_color:
            line += " color=" + self.color(menu.title_color)
        if self._flavor == ARGOS:
            if menu.update_available:
                line += " iconName=" + UPDATE_ARGOS_ICON
            line += " dropdown=false"  # Argos repeats button lines in the menu otherwise
        elif menu.update_available:
            line += " sfimage=" + UPDATE_SFIMAGE
        return line

    def compact_title(self, menu: Menu) -> str:
        """An icon and no text, tinted with the level color of the worst limit. Without a
        tint (nothing known) SwiftBar draws it as a template image, like the other menu bar
        icons."""
        if self._flavor == ARGOS:
            icon = UPDATE_ARGOS_ICON if menu.update_available else COMPACT_ARGOS_ICON
            return " | iconName=%s dropdown=false" % icon
        line = " | sfimage=" + (UPDATE_SFIMAGE if menu.update_available else COMPACT_SFIMAGE)
        if menu.title_color:
            line += " sfcolor=" + self.color(menu.title_color)
        return line + " tooltip=" + xbar_quote(menu.title.replace("|", "/"))

    def item(self, item: MenuItem) -> str:
        params: List[str] = []
        if item.color:
            params.append("color=" + self.color(item.color))
        if item.size:
            params.append("size=%d" % item.size)
        if item.checked:  # Argos has no checkmark; a check icon stands in for it
            params.append("iconName=object-select-symbolic" if self._flavor == ARGOS
                          else "checked=true")
        params += self.icon_params(item)
        a = item.action
        if a and a.kind == OPEN:
            params.append("href=" + a.url)
        elif a and a.kind in CLI_FLAGS:
            params.append('bash="%s" param1=%s terminal=false refresh=true'
                          % (self._self_path, CLI_FLAGS[a.kind]))
        elif a and a.kind in SETTING_FLAGS:
            params.append('bash="%s" param1=%s param2=%s terminal=false refresh=true'
                          % (self._self_path, SETTING_FLAGS[a.kind], xbar_quote(a.value)))
        text = item.text.replace("|", "/")
        return text + (" | " + " ".join(params) if params else "")

    def icon_params(self, item: MenuItem) -> List[str]:
        """The item's icon, tinted like its text. On Argos a checked item already uses its
        one icon for the checkmark, which wins."""
        if self._flavor == ARGOS:
            name = ITEM_ARGOS_ICON.get(item.icon or "")
            return ["iconName=" + name] if name and not item.checked else []
        name = ITEM_SFIMAGE.get(item.icon or "")
        if not name:
            return []
        return ["sfimage=" + name] + (["sfcolor=" + self.color(item.color)] if item.color else [])

    def color(self, color: str) -> str:
        """A view color as this flavor writes it."""
        return SWIFTBAR_COLORS.get(color, color) if self._flavor == SWIFTBAR else color
