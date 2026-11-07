"""Menu model: what to show, independent of how a frontend draws it."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Set, Tuple

from .domain import (AGY, CLAUDE, CODEX, FIVE_HOUR, PROVIDER_NAMES, PROVIDERS, WEEKLY, AccountRow,
                     Clock, Window)
from .i18n import EN, VI, Translator
from .pace import project_pace

REFRESH, UPDATE, OPEN, QUIT, LANGUAGE = "refresh", "update", "open", "quit", "language"
CHECK_UPDATE = "check_update"  # ask GitHub for the latest release now, then redraw
# Settings written to the conf file: show or hide one account in the bar title (value: its
# label), and the icon-only bar (value: "on" or "off").
SHOW_ACCOUNT, HIDE_ACCOUNT, COMPACT = "show_account", "hide_account", "compact"
SETTINGS = (LANGUAGE, SHOW_ACCOUNT, HIDE_ACCOUNT, COMPACT)
GRAY, GREEN, ORANGE, RED = "gray", "#2e7d32", "orange", "red"


@dataclass(frozen=True)
class Action:
    kind: str       # REFRESH | UPDATE | CHECK_UPDATE | OPEN | QUIT | one of SETTINGS
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
    # A semantic icon: a provider key (CLAUDE, CODEX, AGY) or WARNING_ICON. The xbar renderer
    # maps it to a SwiftBar / Argos icon; the tray frontends ignore it.
    icon: Optional[str] = None


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


WARNING_ICON = "warning"
# Product names, the same in every language.
# The provider-neutral brand of the bar title when it cannot name accounts.
TITLE_BRAND = "AI"

# Bar-title prefix of each provider's accounts: Claude keeps its plain letters (C, T, W...).
SHORT_PREFIX = {CLAUDE: "", CODEX: "Cx", AGY: "Ag"}


def short_name(label: str, taken: Set[str], provider: str = CLAUDE) -> str:
    """A short bar-title name per account, unique across all accounts.

    Claude: one letter ("claude-token" -> T, "claude (work)" -> W, plain "claude" keeps its C).
    Other providers: their prefix plus the label's first letter after the provider name
    ("codex" -> Cx, "codex-work" -> CxW, "agy" -> Ag). A digit is added when names collide.
    """
    prefix = SHORT_PREFIX.get(provider, "")
    base = re.sub(r"^%s[\s(_.-]*" % (provider if prefix else "claude"), "", label, flags=re.I)
    if prefix:
        first = base[:1].upper()
    else:
        first = (base or label)[:1].upper() or "?"
    s, n = prefix + first, 2
    while s in taken:
        s = prefix + first + str(n)
        n += 1
    taken.add(s)
    return s


def provider_order(rows: Sequence[AccountRow]) -> List[AccountRow]:
    """Rows grouped by provider in PROVIDERS order (unknown providers last), each group in
    its original order."""
    rank = {p: i for i, p in enumerate(PROVIDERS)}
    return sorted(rows, key=lambda r: rank.get(r.provider, len(PROVIDERS)))


def title_token(r: AccountRow, worst_only: bool = False) -> str:
    """One account in the bar title: "C 13/8/1", or "C 13" with worst_only. A limit the plan
    does not have is left out, never written as 0; unreadable usage is "C ?"."""
    if r.usage is None:
        return r.short + " ?"
    known = [x for x in r.usage.percentages() if x is not None]
    if worst_only:
        known = [max(known)] if known else []
    return "%s %s" % (r.short, "/".join("%d" % round(x) for x in known)) if known else r.short


class Presenter:
    def __init__(self, clock: Clock, tr: Translator, version: str, repo_url: str, warn: float,
                 crit: float, max_in_title: int, show_support: bool = False,
                 hidden: Iterable[str] = (), compact: bool = False):
        """hidden: labels of the accounts left out of the bar title. compact: icon-only bar.
        max_in_title: up to this many visible accounts show every number in the bar; up to
        twice as many show each account's highest number; more show the single highest."""
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

    def duration(self, seconds: float) -> str:
        """A span of time, e.g. "2 giờ 15 phút" / "2h 15m", minutes rounded up."""
        tr = self._tr
        mins = max(1, -(-int(seconds) // 60))
        days, rest = divmod(mins, 1440)
        hours, mins = divmod(rest, 60)
        if days:
            parts = [(days, "unit_day"), (hours, "unit_hour")]
        elif hours:
            parts = [(hours, "unit_hour"), (mins, "unit_minute")]
        else:
            parts = [(mins, "unit_minute")]
        return " ".join(tr(unit, n=n) for n, unit in parts if n)

    def time_left(self, epoch: float) -> str:
        """Time left until a reset, e.g. "sau 2 giờ 15 phút" / "in 2h 15m". Frontends redraw
        every minute, so the countdown stays current between probes."""
        left = int(epoch) - int(self._clock.now())
        if left <= 0:  # never "0 minutes" early: duration() rounds up
            return self._tr("left_now")
        return self._tr("left_prefix") + self.duration(left)

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

    def title(self, shown: Sequence[AccountRow], worst: Optional[float]) -> Tuple[str, Optional[str]]:
        """The bar title for the visible rows, and the footer key that explains its numbers
        (None when it has none)."""
        if not shown:
            return TITLE_BRAND, None
        if len(shown) <= self._max_in_title:
            return "  ".join(title_token(r) for r in shown), "number_order"
        if len(shown) <= 2 * self._max_in_title:
            return "  ".join(title_token(r, worst_only=True) for r in shown), "number_worst_each"
        if worst is None:  # nothing known: no number, never "0%"
            return TITLE_BRAND, None
        return "%s %d%%" % (TITLE_BRAND, round(worst)), "number_worst_all"

    def present(self, rows: Sequence[AccountRow], new_tag: Optional[str],
                up_to_date: bool = False) -> Menu:
        """new_tag: the newer release to offer, or None. up_to_date: the last release check
        found nothing newer (False when it could not tell)."""
        tr = self._tr
        update = self.update_items(new_tag)
        if not rows:
            return Menu(TITLE_BRAND + " ?", None, bool(new_tag), None, tuple(update + [
                MenuItem(tr("no_accounts")),
                MenuItem(tr("no_accounts_hint"), size=11),
                SEPARATOR,
                self.language_item(),
            ]))

        rows = provider_order(rows)
        # The bar (title, color, worst) covers the visible accounts; the menu lists them all,
        # so a hidden account can be shown again.
        shown = [r for r in rows if r.label not in self._hidden]
        known = [x for r in shown if r.usage for x in r.usage.percentages() if x is not None]
        worst = max(known) if known else None
        title, legend = self.title(shown, worst)
        # With nothing to show, the bar keeps an icon so the menu can still be opened.
        compact = self._compact or not shown

        items = list(update)
        if self._compact:
            # First in the menu: the way back from the icon-only bar.
            items += [MenuItem(tr("expand"), action=Action(COMPACT, value="off")), SEPARATOR]
        provider = None
        for r in rows:
            if r.provider != provider:
                provider = r.provider
                items.append(MenuItem(PROVIDER_NAMES.get(provider, provider), color=GRAY, size=12,
                                      icon=provider))
            items += self.account_items(r)
        items.append(MenuItem(tr("refresh_every"), color=GRAY, size=11))
        if legend:
            items.append(MenuItem(tr(legend), color=GRAY, size=11))
        items.append(MenuItem(tr("version", version=self._version), color=GRAY, size=11,
                              action=Action(OPEN, self._repo_url)))
        if self._show_support:
            items.append(MenuItem(tr("support"), action=Action(OPEN, self._repo_url + "#support")))
        if not self._compact:
            items.append(MenuItem(tr("collapse"), action=Action(COMPACT, value="on")))
        items.append(self.language_item())
        if not new_tag:  # with a newer release, the update items at the top stand in for it
            items.append(MenuItem(tr("check_update_done" if up_to_date else "check_update"),
                                  action=Action(CHECK_UPDATE)))
        items.append(MenuItem(tr("refresh_now"), action=Action(REFRESH)))
        return Menu(title, self.color(worst or 0), bool(new_tag), worst, tuple(items), compact)

    def window_name(self, w: Window) -> str:
        """"5 giờ", "Tuần", "Fable (tuần)", "Gemini (5 giờ)"; another cadence keeps the
        provider's own word: "Gemini (daily)"."""
        tr = self._tr
        if w.kind == FIVE_HOUR:
            return tr("model_five_hour", name=w.name) if w.name else tr("five_hour")
        if w.kind == WEEKLY:
            return tr("model_weekly", name=w.name) if w.name else tr("weekly")
        if not w.kind:  # a provider that names no cadence
            return w.name or "?"
        return "%s (%s)" % (w.name, w.kind) if w.name else w.kind

    def pace_item(self, w: Window) -> Optional[MenuItem]:
        """A warning under a window line, only when it calls for action: the limit is used up,
        or the current rate uses it up before the reset."""
        pace = project_pace(w, self._clock.now())
        if pace is None:
            return None
        if pace.exhausted:
            text, color = self._tr("pace_exhausted", left=self.time_left(w.reset)), RED
        elif pace.run_out_at is not None:
            left = self.duration(pace.run_out_at - self._clock.now())
            text, color = self._tr("pace_runs_out", left=left), self.color(w.pct or 0) or ORANGE
        else:
            return None
        return MenuItem(text, color=color, size=11, icon=WARNING_ICON)

    def account_items(self, r: AccountRow) -> List[MenuItem]:
        u = r.usage
        head = "%s: %s" % (r.short, r.label)
        if r.detail:
            head += " - " + r.detail
        if u is not None and u.plan:
            head += " (%s)" % u.plan
        tr = self._tr
        items = [MenuItem(head, size=13)]
        if u is None:
            items.append(MenuItem(tr("usage_unreadable"), color=GRAY))
        else:
            for w in u.windows:
                name = self.window_name(w)
                if w.pct is None:  # the plan has no such limit
                    items.append(MenuItem(tr("no_limit_line", name=name)))
                    continue
                text = tr("model_line", name=name, pct="%d" % round(w.pct))
                if w.reset:  # 0: unknown
                    text += tr("reset_suffix", left=self.time_left(w.reset))
                items.append(MenuItem(text, color=self.color(w.pct)))
                warning = self.pace_item(w)
                if warning:
                    items.append(warning)
            if r.error:
                items.append(MenuItem(tr("stale_data"), color=GRAY, size=11))
        if r.error:
            items.append(MenuItem(tr("error_line", error=r.error), color=GRAY, size=11))
        shown = r.label not in self._hidden
        items.append(MenuItem(tr("show_in_bar"), checked=shown,
                              action=Action(HIDE_ACCOUNT if shown else SHOW_ACCOUNT, value=r.label)))
        items.append(SEPARATOR)
        return items
