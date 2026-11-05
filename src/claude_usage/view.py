"""Menu model: what to show, independent of how a frontend draws it."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Set, Tuple

from .domain import AccountRow, Clock
from .i18n import EN, VI, Translator

REFRESH, UPDATE, OPEN, QUIT, LANGUAGE = "refresh", "update", "open", "quit", "language"
# Settings written to the conf file: show or hide one account in the bar title (value: its
# label), and the icon-only bar (value: "on" or "off").
SHOW_ACCOUNT, HIDE_ACCOUNT, COMPACT = "show_account", "hide_account", "compact"
SETTINGS = (LANGUAGE, SHOW_ACCOUNT, HIDE_ACCOUNT, COMPACT)
GRAY, GREEN, ORANGE, RED = "gray", "#2e7d32", "orange", "red"


@dataclass(frozen=True)
class Action:
    kind: str       # REFRESH | UPDATE | OPEN | QUIT | one of SETTINGS
    url: str = ""   # for OPEN
    value: str = ""  # for SETTINGS: the language, the account label, or "on" / "off"


@dataclass(frozen=True)
class MenuItem:
    text: str = ""
    color: Optional[str] = None
    size: Optional[int] = None
    action: Optional[Action] = None
    separator: bool = False
    checked: bool = False  # drawn with the frontend's native checkmark


SEPARATOR = MenuItem(separator=True)


@dataclass(frozen=True)
class Menu:
    title: str                    # e.g. "C 13/8/1  T 0/65/1"
    title_color: Optional[str]    # ORANGE / RED when a limit is close
    update_available: bool
    worst: Optional[float]        # highest known percentage, None when nothing is known
    items: Tuple[MenuItem, ...]
    # Draw only an icon in the bar. title still holds the numbers, for tooltips.
    compact: bool = False


def short_name(label: str, taken: Set[str]) -> str:
    """One letter per account (C, T, W...), with a digit when letters collide."""
    # "claude-token" -> T, "claude (work)" -> W; plain "claude" keeps its C.
    base = re.sub(r"^claude[\s(_.-]*", "", label, flags=re.I) or label
    s = base[:1].upper() or "?"
    n = 2
    while s in taken:
        s = base[:1].upper() + str(n)
        n += 1
    taken.add(s)
    return s


class Presenter:
    def __init__(self, clock: Clock, tr: Translator, version: str, repo_url: str, warn: float,
                 crit: float, max_in_title: int, show_support: bool = False,
                 hidden: Iterable[str] = (), compact: bool = False):
        """hidden: labels of the accounts left out of the bar title. compact: icon-only bar."""
        self._clock = clock
        self._hidden = frozenset(hidden)
        self._compact = compact
        self._show_support = show_support
        self._tr = tr
        self._version = version
        self._repo_url = repo_url
        self._warn = warn
        self._crit = crit
        self._max_in_title = max_in_title

    def color(self, pct: float) -> Optional[str]:
        return RED if pct >= self._crit else ORANGE if pct >= self._warn else None

    def time_left(self, epoch: int) -> str:
        """Time left until a reset, e.g. "sau 2 giờ 15 phút" / "in 2h 15m". Frontends redraw
        every minute, so the countdown stays current between probes."""
        tr = self._tr
        mins = -(-(epoch - int(self._clock.now())) // 60)  # round up: never "0 minutes" early
        if mins <= 0:
            return tr("left_now")
        days, rest = divmod(mins, 1440)
        hours, mins = divmod(rest, 60)
        if days:
            parts = [(days, "unit_day"), (hours, "unit_hour")]
        elif hours:
            parts = [(hours, "unit_hour"), (mins, "unit_minute")]
        else:
            parts = [(mins, "unit_minute")]
        return tr("left_prefix") + " ".join(tr(unit, n=n) for n, unit in parts if n)

    def update_items(self, tag: Optional[str]) -> List[MenuItem]:
        if not tag:
            return []
        tr = self._tr
        return [
            MenuItem(tr("update_available", tag=tag, version=self._version), color=GREEN),
            MenuItem(tr("update_now"), action=Action(UPDATE)),
            MenuItem(tr("view_changes"), action=Action(OPEN, "%s/releases/tag/%s" % (self._repo_url, tag))),
            SEPARATOR,
        ]

    def language_item(self) -> MenuItem:
        """Switches to the other language. Written in the target language, so a user who
        cannot read the current one still finds it."""
        target = EN if self._tr.lang == VI else VI
        return MenuItem(Translator(target)("switch_language"), action=Action(LANGUAGE, value=target))

    def present(self, rows: Sequence[AccountRow], new_tag: Optional[str]) -> Menu:
        """new_tag: the newer release to offer, or None."""
        tr = self._tr
        update = self.update_items(new_tag)
        if not rows:
            return Menu("Claude ?", None, bool(new_tag), None, tuple(update + [
                MenuItem(tr("no_accounts")),
                MenuItem(tr("no_accounts_hint"), size=11),
                SEPARATOR,
                self.language_item(),
            ]))

        # The bar (title, color, worst) covers the visible accounts; the menu lists them all,
        # so a hidden account can be shown again.
        shown = [r for r in rows if r.label not in self._hidden]
        known = [r.usage for r in shown if r.usage]
        worst = max([max(u.percentages()) for u in known] or [0])
        if not shown:
            title = "Claude"
        elif len(shown) <= self._max_in_title:
            title = "  ".join(
                "%s %s" % (r.short, "/".join("%d" % round(x) for x in r.usage.percentages()))
                if r.usage else r.short + " ?"
                for r in shown)
        else:
            title = "Claude %d%%" % round(worst)
        # With nothing to show, the bar keeps an icon so the menu can still be opened.
        compact = self._compact or not shown

        items = list(update)
        if self._compact:
            # First in the menu: the way back from the icon-only bar.
            items += [MenuItem(tr("expand"), action=Action(COMPACT, value="off")), SEPARATOR]
        for r in rows:
            items += self.account_items(r)
        items += [
            MenuItem(tr("refresh_every"), color=GRAY, size=11),
            MenuItem(tr("number_order"), color=GRAY, size=11),
            MenuItem(tr("version", version=self._version), color=GRAY, size=11,
                     action=Action(OPEN, self._repo_url)),
        ]
        if self._show_support:
            items.append(MenuItem(tr("support"), action=Action(OPEN, self._repo_url + "#support")))
        if not self._compact:
            items.append(MenuItem(tr("collapse"), action=Action(COMPACT, value="on")))
        items += [
            self.language_item(),
            MenuItem(tr("refresh_now"), action=Action(REFRESH)),
        ]
        return Menu(title, self.color(worst), bool(new_tag), worst if known else None, tuple(items),
                    compact)

    def account_items(self, r: AccountRow) -> List[MenuItem]:
        head = "%s: %s" % (r.short, r.label)
        if r.detail:
            head += " - " + r.detail
        tr = self._tr
        items = [MenuItem(head, size=13)]
        u = r.usage
        if u is None:
            items.append(MenuItem(tr("usage_unreadable"), color=GRAY))
        else:
            windows = [(tr("five_hour"), u.five_hour, u.five_hour_reset),
                       (tr("weekly"), u.weekly, u.weekly_reset)]
            windows += [(tr("model_weekly", name=m.name), m.pct, m.reset) for m in u.models]
            for name, pct, reset in windows:
                text = tr("model_line", name=name, pct="%d" % round(pct))
                if reset:  # 0: unknown, or the window is not running
                    text += tr("reset_suffix", left=self.time_left(reset))
                items.append(MenuItem(text, color=self.color(pct)))
            if r.error:
                items.append(MenuItem(tr("stale_data"), color=GRAY, size=11))
        if r.error:
            items.append(MenuItem(tr("error_line", error=r.error), color=GRAY, size=11))
        shown = r.label not in self._hidden
        items.append(MenuItem(tr("show_in_bar"), checked=shown,
                              action=Action(HIDE_ACCOUNT if shown else SHOW_ACCOUNT, value=r.label)))
        items.append(SEPARATOR)
        return items
