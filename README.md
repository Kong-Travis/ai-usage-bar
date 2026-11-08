# AI Usage

See the usage limits of your AI coding agents at a glance: Claude Code, OpenAI Codex and Google
Antigravity. In the macOS menu bar, the GNOME top bar, or the Windows / Linux system tray.

```
C 87/88/94  Cx 78/73  Ag 0/0
```

Each group is one account:

- `C`, `T`, ... (a letter per account): Claude Code. The numbers are the 5-hour limit % / weekly
  limit % / Fable weekly limit %.
- `Cx`: OpenAI Codex. 5-hour limit % / weekly limit %. The menu also shows the plan and the email.
- `Ag`: Google Antigravity (`agy`). The weekly limit % of each model group.

Green below 70%, yellow from 70%, orange from 90%. Click it to see account names and how long until each limit resets.
A limit that is on course to run out before its reset gets a "will run out before the reset"
warning. The menu is in Vietnamese by default; click "Switch to English" to change it.

## Install

Download the file for your system from [Releases](../../releases/latest).

| System | File | Shows up in |
|---|---|---|
| macOS | `AI-Usage.dmg` | the menu bar (via [SwiftBar](https://github.com/swiftbar/SwiftBar)) |
| Windows 10/11 | `AI-Usage-Windows.zip` | the system tray (icon with the highest %, hover for all accounts) |
| Linux, GNOME | `ai-usage-install-linux.sh` | the top bar (via [Argos](https://github.com/p-e-w/argos)) |
| Linux, KDE / XFCE / Cinnamon / others | `ai-usage-install-linux.sh` | the system tray (AppIndicator) |

The installers ask for a language first (Vietnamese or English).

**macOS:** open the dmg and double-click **`Cài đặt AI Usage.command`**, then follow the steps
in the Terminal window. If macOS blocks the file, go to **System Settings > Privacy & Security >
Open Anyway**, or run `bash "/Volumes/AI Usage/Cài đặt AI Usage.command"`.

**Windows:** extract the zip, then double-click **`Cài đặt AI Usage.cmd`**. It installs Python
for your user if needed (winget or python.org), puts the tray app in
`%LOCALAPPDATA%\claude-usage` and starts it at login. Windows may hide new tray icons in the
overflow area (the `^` arrow): drag the icon onto the taskbar to keep it visible.

**Linux:** run `bash ai-usage-install-linux.sh` (add `--tray` or `--argos` to pick the mode).
On GNOME it installs the Argos extension if missing; on Wayland log out and back in once. In tray
mode it needs PyGObject and libayatana-appindicator and tells you the package command for your
distribution.

## Good to know

- **Claude Code** works with browser logins (`claude`) and `claude setup-token` tokens. Accounts
  are found automatically: the macOS keychain, `~/.claude/.credentials.json` (and other
  `CLAUDE_CONFIG_DIR` folders), `CLAUDE_CODE_OAUTH_TOKEN` in shell files, PowerShell profiles,
  scripts in `~/bin`, and the environment.
- **Codex** needs the `codex` CLI installed and logged in with ChatGPT (`codex login`). Limits are
  read through the official `codex app-server`, for `~/.codex` (or `CODEX_HOME`) and every
  `~/.codex-*` folder, one account each.
- **Antigravity** needs the `agy` CLI installed and logged in. Limits are read with the official
  read-only `agy -p /usage --output-format json`.
- Cost: a Codex or Antigravity check spends no quota. A Claude check is a tiny 1-token request per
  account.
- Refreshes every 3 minutes (or click "Làm mới ngay" / "Refresh now"). All accounts are read at the
  same time, so 10 accounts refresh as fast as one.
- Notifications: you get a desktop notification when a limit reaches 90% and again at 100%.
  Turn them off with `notify = off`.
- On Linux and Windows a Claude browser login can expire while `claude` is not running; the menu
  then says so, and opening `claude` once renews it.
- Too many accounts in the bar? Untick "Hiện trên thanh menu" / "Show in the menu bar" under an
  account to leave it out of the bar; it stays in the menu, so you can tick it again. Click
  "Thu gọn" / "Collapse to an icon" to show only an icon (tinted green, yellow or orange like the numbers),
  and "Mở rộng" / "Expand" at the top of the menu to bring the numbers back. On Windows the tray
  is always an icon, so there is no collapse item there.
- New version available? The menu checks GitHub every hour and then shows "Cập nhật ngay" /
  "Update now". One click updates it. Click "Kiểm tra cập nhật" / "Check for updates" to check
  at once instead of waiting.
- Settings go in `~/.config/claude-usage.conf` (Windows: `%USERPROFILE%\.config\claude-usage.conf`),
  one per line:

  ```
  work = ~/tokens/work.txt      # add a Claude token the scan cannot find
  keychain = off                # hide Claude browser logins
  language = en                 # English menu (default: vi)
  hide = c-m, c-nsc             # accounts (by name) left out of the bar
  compact = on                  # only an icon in the bar
  notify = off                  # no notifications (they come at the crit % and at 100%)
  warn = 70                     # yellow from this % (default 70)
  crit = 90                     # orange and a notification from this % (default 90)
  codex = off                   # hide Codex accounts
  agy = off                     # hide Antigravity
  ```

- Shows `?`: click it to see the error, or check `~/.cache/claude-usage/last-run.log`.
- The file, folder and shortcut names that still say `claude-usage` (the plugin file, the config,
  the cache, the Windows Startup shortcut) are kept from the old name so existing installs update
  in place.

## Uninstall

- macOS: delete `~/.config/swiftbar/claude-usage.1m.py` (and SwiftBar if you don't need it).
- Windows: delete the "Claude Code usage" shortcut in the Startup folder (`shell:startup`) and the
  `%LOCALAPPDATA%\claude-usage` folder.
- Linux: delete `~/.config/argos/claude-usage.1m.py` (Argos), or run `pkill -f "claude-usage.py --tray"` and delete `~/.local/share/claude-usage` and
  `~/.config/autostart/claude-usage.desktop`.

## Development

The code lives in `src/claude_usage/` (Python 3.9, standard library only):

| Module | Responsibility |
|---|---|
| `domain.py` | core types: provider-neutral usage windows, `Account`, `AccountSource`, `Clock` |
| `sources.py`, `scan.py` | finding accounts: keychain, credentials files, shell/PowerShell files, env, conf |
| `usage.py` | reading Claude limits from the API, caching, profile email |
| `codex.py` | Codex accounts (`~/.codex`, `CODEX_HOME`, `~/.codex-*`) and the `codex app-server` reader |
| `antigravity.py` | the `agy` account and the `agy -p /usage` reader |
| `pace.py` | pace and run-out projection ("will run out before the reset") |
| `notify.py` | threshold notifications (90% and 100%) |
| `updater.py` | release check and self-update |
| `view.py`, `i18n.py` | the menu model and its Vietnamese / English text |
| `xbar.py`, `tray.py`, `tray_win32.py`, `tray_gtk.py` | frontends: SwiftBar/Argos text, tray icons |
| `app.py`, `platform_setup.py`, `cli.py` | use cases, per-OS wiring, entry point |

`./build.sh` flattens it into the single file `plugin/claude-usage.1m.py` (what every OS runs and
what older versions download when they self-update) and builds the installers into `dist/`.
Tests: `cd tests && python3 -m unittest discover -p "test_*.py"`. MIT license.
