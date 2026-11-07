#!/bin/bash
# Runs inside the GUI image under `xvfb-run -a dbus-run-session --`.
# Usage: tray_check.sh BUNDLE. Exits 1 if a check fails.
set -u
BUNDLE=$1
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=$(mktemp -d)
export HOME=$(mktemp -d)                      # fake home, no accounts
export CLAUDE_USAGE_GITHUB=http://127.0.0.1:9 # no real release check
export CLAUDE_USAGE_API=http://127.0.0.1:9
FAILED=0
check() { if eval "$1"; then echo "[ok] $2"; else echo "[x] $2"; FAILED=1; fi; }

echo "python: $(/usr/bin/python3 --version)  DISPLAY=$DISPLAY"
/usr/bin/python3 - <<'PY'
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
theme = Gtk.IconTheme.get_default()
for n in ("dialog-error", "dialog-warning", "software-update-available",
          "utilities-system-monitor", "dialog-information"):
    print("icon %-28s %s" % (n, theme.has_icon(n)))
PY

/usr/bin/python3 "$HERE/sni_watcher.py" 70 > "$OUT/watcher.log" 2>&1 &
WATCHER=$!
sleep 1

timeout 25 /usr/bin/python3 "$BUNDLE" --tray > "$OUT/first.out" 2> "$OUT/first.err" &
FIRST=$!
sleep 4
START=$(date +%s)
timeout 20 /usr/bin/python3 "$BUNDLE" --tray > "$OUT/second.out" 2> "$OUT/second.err"
RC2=$?
WAITED=$(( $(date +%s) - START ))
wait $FIRST
RC1=$?

echo "--- first instance: exit $RC1 (124 = still running when timeout killed it)"
echo "stderr:"; sed 's/^/  /' "$OUT/first.err"
echo "--- second instance: exit $RC2 after ${WAITED}s"
echo "stderr:"; sed 's/^/  /' "$OUT/second.err"
echo "--- tray with one login (fake API, 13/8/1), 15 s"
ACCOUNT_HOME=$(mktemp -d)
mkdir -p "$ACCOUNT_HOME/.claude"
EXPIRES=$(( ($(date +%s) + 6 * 3600) * 1000 ))
printf '{"claudeAiOauth": {"accessToken": "fake-linux-access-token", "expiresAt": %s}}' "$EXPIRES" \
  > "$ACCOUNT_HOME/.claude/.credentials.json"
PYTHONPATH="$HERE/.." /usr/bin/python3 "$HERE/fake_api.py" "$OUT/api.url" 25 &
for _ in $(seq 50); do [ -s "$OUT/api.url" ] && break; sleep 0.1; done
HOME=$ACCOUNT_HOME CLAUDE_USAGE_API=$(cat "$OUT/api.url") CLAUDE_USAGE_GITHUB=$(cat "$OUT/api.url") \
  timeout 15 /usr/bin/python3 "$BUNDLE" --tray > "$OUT/third.out" 2> "$OUT/third.err"
RC3=$?
echo "exit $RC3, stderr:"; sed 's/^/  /' "$OUT/third.err"
sleep 2
kill $WATCHER 2>/dev/null; wait $WATCHER 2>/dev/null

echo "--- StatusNotifierWatcher stub log"
cat "$OUT/watcher.log"
echo "--- lock file: $(cat "$HOME/.cache/claude-usage/tray.lock" 2>/dev/null)"

check '[ "$RC1" = 124 ]' "first tray kept running until the timeout"
check '! grep -q Traceback "$OUT/first.err"' "first tray printed no traceback"
check '[ "$RC3" = 124 ] && ! grep -q Traceback "$OUT/third.err"' "tray with an account kept running"
check 'grep -q "XAyatanaLabel.: .C 13/8/1" "$OUT/watcher.log"' "indicator label shows C 13/8/1"
check 'grep -q "MENU C: claude - linux.user@example.com  \[off\]" "$OUT/watcher.log"' "menu row for the account, insensitive"
check 'grep -q "MENU Làm mới ngay$" "$OUT/watcher.log"' "Refresh item is clickable"
check '[ "$RC2" = 0 ]' "second tray exited 0"
check '[ ! -s "$OUT/second.err" ] && [ ! -s "$OUT/second.out" ]' "second tray exited quietly (no output at all)"
check '[ "$WAITED" -ge 9 ]' "second tray waited about 10 s for the lock first"
check 'grep -q "^REGISTERED" "$OUT/watcher.log"' "indicator registered with the StatusNotifierWatcher"
check 'grep -q "XAyatanaLabel.: .AI ?" "$OUT/watcher.log"' "indicator label shows the menu title"

echo "--- unit tests with a display"
cd "$HERE/.." && /usr/bin/python3 -m unittest test_tray_gtk 2>&1 | tail -4
[ "${PIPESTATUS[0]}" = 0 ] || FAILED=1
exit $FAILED
