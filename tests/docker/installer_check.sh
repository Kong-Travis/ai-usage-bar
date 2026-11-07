#!/bin/bash
# Runs install-linux.sh non-interactively inside a container and shows what it created.
# Usage: installer_check.sh INSTALLER gui|nogui
#   gui:   run under `xvfb-run -a dbus-run-session --` in the GUI image: tray mode (KDE),
#          Argos mode forced (--argos --lang en), Argos auto-detected through fake gnome-shell
#          binaries (46 -> master, 44 -> GNOME-44), and a switch back to tray mode.
#   nogui: python3 only: tray mode must stop with the install command.
set -u
INSTALLER=$1
KIND=${2:-gui}
export CLAUDE_USAGE_NONINTERACTIVE=1
export CLAUDE_USAGE_GITHUB=http://127.0.0.1:9 CLAUDE_USAGE_API=http://127.0.0.1:9
FAILED=0
check() { if eval "$1"; then echo "[ok] $2"; else echo "[x] $2"; FAILED=1; fi; }
files() { echo "--- files under \$HOME"; (cd "$HOME" && find . -type f -not -path './.cache/*' | sort | head -40); }
fake_shell() {  # fake_shell VERSION: a gnome-shell on PATH that only answers --version
  BIN=$(mktemp -d); printf '#!/bin/sh\necho "GNOME Shell %s"\n' "$1" > "$BIN/gnome-shell"
  chmod +x "$BIN/gnome-shell"; echo "$BIN"
}

if [ "$KIND" = nogui ]; then
  echo "===== tray mode without PyGObject (python $(/usr/bin/python3 --version 2>&1))"
  export HOME=$(mktemp -d)
  XDG_CURRENT_DESKTOP=KDE bash "$INSTALLER" > out.log 2>&1; RC=$?
  cat out.log; files
  check '[ "$RC" = 1 ]' "installer exits 1"
  check 'grep -q "sudo apt-get install -y python3-gi gir1.2-ayatanaappindicator3-0.1" out.log' "shows the apt command"
  check '[ ! -e "$HOME/.local/share/claude-usage/claude-usage.py" ]' "nothing installed"
  exit $FAILED
fi

echo "===== 1. tray mode (XDG_CURRENT_DESKTOP=KDE, language from the default)"
export HOME=$(mktemp -d)
XDG_CURRENT_DESKTOP=KDE bash "$INSTALLER" > out.log 2>&1; RC=$?
cat out.log; files
echo "--- autostart entry"; cat "$HOME/.config/autostart/claude-usage.desktop"
check '[ "$RC" = 0 ]' "installer exits 0"
check '[ -x "$HOME/.local/share/claude-usage/claude-usage.py" ]' "plugin installed and executable"
check 'grep -q "^Exec=\"/usr/bin/python3\" \"$HOME/.local/share/claude-usage/claude-usage.py\" --tray$" "$HOME/.config/autostart/claude-usage.desktop"' "autostart Exec line"
check 'pgrep -f "claude-usage/claude-usage.py --tray" >/dev/null' "tray is running after install"
check '[ ! -e "$HOME/.config/claude-usage.conf" ]' "Vietnamese default writes no conf"
check 'grep -q "Biểu tượng đã chạy" out.log' "messages in Vietnamese"
echo "--- running: $(pgrep -af 'claude-usage.py --tray')"
pkill -f "claude-usage.py --tray"

echo; echo "===== 2. Argos mode forced (--argos --lang en), no gnome-shell installed"
export HOME=$(mktemp -d)
mkdir -p "$HOME/.config"; printf 'work = ~/tok.txt\n' > "$HOME/.config/claude-usage.conf"
XDG_CURRENT_DESKTOP=KDE bash "$INSTALLER" --argos --lang en > out.log 2>&1; RC=$?
cat out.log; files
echo "--- ~/.config/claude-usage.conf"; cat "$HOME/.config/claude-usage.conf"
EXT="$HOME/.local/share/gnome-shell/extensions/argos@pew.worldwidemann.com"
check '[ "$RC" = 0 ]' "installer exits 0"
check '[ -x "$HOME/.config/argos/claude-usage.1m.py" ]' "Argos plugin installed and executable"
check '[ -f "$EXT/metadata.json" ] && [ -f "$EXT/extension.js" ]' "Argos extension unpacked"
check 'grep -q "\"51\"" "$EXT/metadata.json"' "master Argos (declares GNOME 51)"
check 'grep -qx "language = en" "$HOME/.config/claude-usage.conf" && grep -qx "work = ~/tok.txt" "$HOME/.config/claude-usage.conf"' "conf: language = en added, other lines kept"
check 'grep -q "It will show: AI ?" out.log' "test run printed the English title"

echo; echo "===== 3. Argos auto-detected: XDG_CURRENT_DESKTOP=ubuntu:GNOME, fake GNOME Shell 44.3"
export HOME=$(mktemp -d)
PATH="$(fake_shell 44.3):$PATH" XDG_CURRENT_DESKTOP=ubuntu:GNOME XDG_SESSION_TYPE=wayland \
  bash "$INSTALLER" > out.log 2>&1; RC=$?
cat out.log
EXT="$HOME/.local/share/gnome-shell/extensions/argos@pew.worldwidemann.com"
check '[ "$RC" = 0 ]' "installer exits 0"
check 'grep -q "GNOME-44" out.log && grep -q "\"44\"" "$EXT/metadata.json" && ! grep -q "\"45\"" "$EXT/metadata.json"' "GNOME 44 gets the GNOME-44 tag"
check 'grep -q "Wayland" out.log' "explains the Wayland log out/in"

echo; echo "===== 4. Budgie (Budgie:GNOME) with gnome-shell on PATH still gets the tray"
PATH="$(fake_shell 46.0):$PATH" XDG_CURRENT_DESKTOP=Budgie:GNOME bash "$INSTALLER" > out.log 2>&1; RC=$?
grep -E "Biểu tượng|khay|gỡ|Đã cài" out.log
check '[ "$RC" = 0 ] && [ -x "$HOME/.local/share/claude-usage/claude-usage.py" ]' "tray mode installed"
check '[ ! -e "$HOME/.config/argos/claude-usage.1m.py" ]' "the Argos copy from step 3 was removed"
pkill -f "claude-usage.py --tray"
exit $FAILED
