"""Entry point. Without arguments it prints BitBar output (SwiftBar, Argos)."""
from __future__ import annotations

import sys
from typing import List, Optional

from .i18n import LANGUAGES, Translator, normalize_language
from .platform_setup import LINUX, WINDOWS, Host, build
from .sources import COMPACT_KEY, set_account_hidden, set_conf_value
from .tray import TrayBackend, TrayController, relaunch
from .view import COMPACT, HIDE_ACCOUNT, LANGUAGE, SHOW_ACCOUNT, Action
from .xbar import ARGOS, SETTING_FLAGS, SWIFTBAR, XbarRenderer

USAGE = """usage: claude-usage [--force | --update | --tray | --lang vi|en | --show LABEL |
                     --hide LABEL | --compact on|off]
  (none)          print the menu for SwiftBar (macOS) or Argos (GNOME)
  --force         probe every account now, ignoring the cache
  --update        install the latest release over this file
  --tray          run as a tray icon (Windows, Linux desktops without Argos)
  --lang LANG     menu language: vi (default) or en
  --show LABEL    show this account in the bar title again
  --hide LABEL    leave this account out of the bar title (it stays in the menu)
  --compact on    show only an icon in the bar; off shows the numbers again"""


def tray_backend(os_name: str, tr: Translator) -> TrayBackend:
    if os_name == WINDOWS:
        from .tray_win32 import Win32TrayBackend
        return Win32TrayBackend(tr)
    if os_name == LINUX:
        from .tray_gtk import GtkTrayBackend
        return GtkTrayBackend(tr)
    raise SystemExit("--tray is for Windows and Linux; on macOS use SwiftBar")


def change_setting(host: Host, action: Action) -> None:
    """Write a settings action from the menu to the conf file."""
    if action.kind == LANGUAGE:
        set_conf_value(host.conf_path, "language", action.value)
    elif action.kind in (SHOW_ACCOUNT, HIDE_ACCOUNT):
        set_account_hidden(host.conf_path, host.home, action.value, action.kind == HIDE_ACCOUNT)
    elif action.kind == COMPACT:
        set_conf_value(host.conf_path, COMPACT_KEY, action.value)


def setting_from_args(args: List[str]) -> Optional[Action]:
    """["--hide", "c-m"] -> Action(HIDE_ACCOUNT, value="c-m"); None for anything else."""
    if len(args) != 2:
        return None
    kind = next((k for k, flag in SETTING_FLAGS.items() if flag == args[0]), None)
    value = args[1].strip()
    if kind == LANGUAGE:
        value = normalize_language(value) or ""
        return Action(kind, value=value) if value in LANGUAGES else None
    if kind == COMPACT:
        return Action(kind, value=value.lower()) if value.lower() in ("on", "off") else None
    return Action(kind, value=value) if kind and value else None


def run_tray(host: Host) -> None:
    # Services are rebuilt on every refresh so edits to the conf file (language, tokens)
    # apply without restarting the tray.
    services = build(host)
    TrayController(
        backend=tray_backend(host.os_name, services.tr),
        build_menu=lambda force: build(host).app.menu(force=force),
        refresh=lambda: build(host).app.refresh(),
        update=services.updater.update,
        restart=relaunch,
        translator=lambda: build(host).tr,
        change_setting=lambda action: change_setting(host, action),
    ).run()


def print_menu(host: Host) -> None:
    flavor = ARGOS if "ARGOS_MENU_OPEN" in host.env else SWIFTBAR
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # the menu text is not ASCII
    sys.stdout.write(XbarRenderer(host.self_path, flavor).render(build(host).app.menu()))


def main(argv: Optional[List[str]] = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    host = Host.current()
    if args == ["--update"]:
        build(host).updater.update()
    elif args == ["--force"]:
        build(host).app.refresh()
    elif args == ["--tray"]:
        run_tray(host)
    elif setting_from_args(args):
        change_setting(host, setting_from_args(args))
    elif not args:
        print_menu(host)
    else:
        print(USAGE, file=sys.stderr)
        return 2
    return 0
