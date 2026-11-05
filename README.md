# claude-usage-bar

See your Claude Code usage limits at a glance, for every account on your computer:
in the macOS menu bar, the GNOME top bar, or the Windows / Linux system tray.

```
C 87/88/94  T 79/17/0
```

Each letter is one account. The numbers are: 5-hour limit % / weekly limit % / Fable weekly limit %.
Orange at 70%, red at 90%. Click it to see account names and how long until each limit resets.
The menu is in Vietnamese by default; click "Switch to English" to change it.

## Install

Download the file for your system from [Releases](../../releases/latest).

| System | File | Shows up in |
|---|---|---|
| macOS | `Claude-Usage.dmg` | the menu bar (via [SwiftBar](https://github.com/swiftbar/SwiftBar)) |
| Windows 10/11 | `Claude-Usage-Windows.zip` | the system tray (icon with the highest %, hover for all accounts) |
| Linux, GNOME | `claude-usage-install-linux.sh` | the top bar (via [Argos](https://github.com/p-e-w/argos)) |
| Linux, KDE / XFCE / Cinnamon / others | `claude-usage-install-linux.sh` | the system tray (AppIndicator) |

The installers ask for a language first (Vietnamese or English).

**macOS:** open the dmg and double-click **`Cài đặt Claude Usage.command`**, then follow the steps
in the Terminal window. If macOS blocks the file, go to **System Settings > Privacy & Security >
Open Anyway**, or run `bash "/Volumes/Claude Usage/Cài đặt Claude Usage.command"`.

**Windows:** extract the zip, then double-click **`Cài đặt Claude Usage.cmd`**. It installs Python
for your user if needed (winget or python.org), puts the tray app in
`%LOCALAPPDATA%\claude-usage` and starts it at login. Windows may hide new tray icons in the
overflow area (the `^` arrow): drag the icon onto the taskbar to keep it visible.

**Linux:** run `bash claude-usage-install-linux.sh` (add `--tray` or `--argos` to pick the mode).
On GNOME it installs the Argos extension if missing; on Wayland log out and back in once. In tray
mode it needs PyGObject and libayatana-appindicator and tells you the package command for your
distribution.

## Good to know

- Works with browser logins (`claude`) and `claude setup-token` tokens. Accounts are found
  automatically: the macOS keychain, `~/.claude/.credentials.json` (and other `CLAUDE_CONFIG_DIR`
  folders), `CLAUDE_CODE_OAUTH_TOKEN` in shell files, PowerShell profiles, scripts in `~/bin`,
  and the environment.
- Refreshes every 3 minutes (or click "Làm mới ngay" / "Refresh now"), using a tiny 1-token request
  per account. All accounts are read at the same time, so 10 accounts refresh as fast as one.
- On Linux and Windows a browser login can expire while `claude` is not running; the menu then
  says so, and opening `claude` once renews it.
- Too many accounts in the bar? Untick "Hiện trên thanh menu" / "Show in the menu bar" under an
  account to leave it out of the bar; it stays in the menu, so you can tick it again. Click
  "Thu gọn" / "Collapse to an icon" to show only an icon (orange or red when a limit is close),
  and "Mở rộng" / "Expand" at the top of the menu to bring the numbers back. On Windows the tray
  is always an icon, so there is no collapse item there.
- New version available? The menu checks GitHub every hour and then shows "Cập nhật ngay" /
  "Update now". One click updates it. Click "Kiểm tra cập nhật" / "Check for updates" to check
  at once instead of waiting.
- Settings go in `~/.config/claude-usage.conf` (Windows: `%USERPROFILE%\.config\claude-usage.conf`),
  one per line:

  ```
  work = ~/tokens/work.txt      # add a token the scan cannot find
  keychain = off                # hide browser logins
  language = en                 # English menu (default: vi)
  hide = c-m, c-nsc             # accounts (by name) left out of the bar
  compact = on                  # only an icon in the bar
  ```

- Shows `?`: click it to see the error, or check `~/.cache/claude-usage/last-run.log`.

## Uninstall

- macOS: delete `~/.config/swiftbar/claude-usage.1m.py` (and SwiftBar if you don't need it).
- Windows: delete the "Claude Usage" shortcut in the Startup folder (`shell:startup`) and the
  `%LOCALAPPDATA%\claude-usage` folder.
- Linux: delete `~/.config/argos/claude-usage.1m.py` (Argos), or run `pkill -f "claude-usage.py --tray"` and delete `~/.local/share/claude-usage` and
  `~/.config/autostart/claude-usage.desktop`.

## Development

The code lives in `src/claude_usage/` (Python 3.9, standard library only):

| Module | Responsibility |
|---|---|
| `domain.py` | core types: `Usage`, `Account`, `AccountSource`, `Clock` |
| `sources.py`, `scan.py` | finding accounts: keychain, credentials files, shell/PowerShell files, env, conf |
| `usage.py` | reading limits from the API, caching, profile email |
| `updater.py` | release check and self-update |
| `view.py`, `i18n.py` | the menu model and its Vietnamese / English text |
| `xbar.py`, `tray.py`, `tray_win32.py`, `tray_gtk.py` | frontends: SwiftBar/Argos text, tray icons |
| `app.py`, `platform_setup.py`, `cli.py` | use cases, per-OS wiring, entry point |

`./build.sh` flattens it into the single file `plugin/claude-usage.1m.py` (what every OS runs and
what older versions download when they self-update) and builds the installers into `dist/`.
Tests: `cd tests && python3 -m unittest discover -p "test_*.py"`. MIT license.
