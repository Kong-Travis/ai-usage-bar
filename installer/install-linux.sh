#!/bin/bash
# Installer for the "AI Usage" panel plugin on Linux.
# Legacy names are kept on purpose (claude-usage.1m.py, claude-usage.py, claude-usage.conf,
# claude-usage.desktop, ~/.cache/claude-usage): installs from before the rename self-update in
# place, so renaming them would leave two icons.
# GNOME: an Argos plugin in the top bar. Other desktops (KDE, XFCE, Cinnamon, MATE, Budgie...):
# a tray icon that starts at login.
# Run: bash ai-usage-install-linux.sh [--tray | --argos] [--lang vi|en]
# CLAUDE_USAGE_NONINTERACTIVE=1 answers every question with its default and never uses sudo.
set -u

ARGOS_UUID="argos@pew.worldwidemann.com"
ARGOS_REPO="https://github.com/p-e-w/argos"
ARGOS_EXT_DIR="$HOME/.local/share/gnome-shell/extensions/$ARGOS_UUID"
ARGOS_PLUGIN="$HOME/.config/argos/claude-usage.1m.py"
TRAY_DIR="$HOME/.local/share/claude-usage"
TRAY_PLUGIN="$TRAY_DIR/claude-usage.py"
AUTOSTART="$HOME/.config/autostart/claude-usage.desktop"
CONF="$HOME/.config/claude-usage.conf"
CACHE="$HOME/.cache/claude-usage"
PY=/usr/bin/python3
NONINTERACTIVE=${CLAUDE_USAGE_NONINTERACTIVE:-0}
UI_LANG=""   # not LANG: that is the locale every child process reads
MODE=""

# ---------- messages (one place, both languages) ----------

# m KEY [printf args...]: the message KEY in the chosen language.
m() {
  local key=$1 vi en
  shift
  case $key in
    usage)          vi="Cách dùng: bash %s [--tray | --argos] [--lang vi|en]"
                    en="Usage: bash %s [--tray | --argos] [--lang vi|en]" ;;
    error)          vi="Lỗi: %s"; en="Error: %s" ;;
    not_linux)      vi="Bộ cài này chỉ chạy trên Linux. Trên macOS hãy dùng AI-Usage.dmg."
                    en="This installer only runs on Linux. On macOS use AI-Usage.dmg." ;;
    step_python)    vi="1/4  Kiểm tra python3"; en="1/4  Checking python3" ;;
    no_python)      vi="Không tìm thấy /usr/bin/python3. Cài python3 của bản phân phối (vd: sudo apt install python3) rồi chạy lại."
                    en="/usr/bin/python3 not found. Install your distribution's python3 (e.g. sudo apt install python3), then run this again." ;;
    old_python)     vi="Cần Python 3.9 trở lên, máy đang có %s."
                    en="Python 3.9 or newer is needed, this computer has %s." ;;
    ok)             vi="OK: %s"; en="OK: %s" ;;
    step_mode)      vi="2/4  Chọn cách hiển thị"; en="2/4  Choosing how to show it" ;;
    mode_argos)     vi="GNOME: hiện trên thanh trên cùng qua extension Argos."
                    en="GNOME: shown in the top bar through the Argos extension." ;;
    mode_tray)      vi="Hiện bằng biểu tượng trên khay hệ thống (system tray)."
                    en="Shown as an icon in the system tray." ;;
    mode_forced)    vi="(chọn bằng tham số %s)"; en="(chosen with %s)" ;;
    step_install)   vi="3/4  Cài đặt"; en="3/4  Installing" ;;
    argos_have)     vi="Đã có sẵn extension Argos."; en="The Argos extension is already installed." ;;
    argos_get)      vi="Tải extension Argos (%s) từ GitHub..."; en="Downloading the Argos extension (%s) from GitHub..." ;;
    argos_newer)    vi="GNOME %s mới hơn bản Argos đã khai báo hỗ trợ (đến 51), có thể Argos chưa chạy được."
                    en="GNOME %s is newer than Argos declares support for (up to 51), Argos may not load." ;;
    no_gnome_ver)   vi="Không đọc được phiên bản GNOME Shell, dùng bản Argos mới nhất."
                    en="Could not read the GNOME Shell version, using the latest Argos." ;;
    no_downloader)  vi="Cần curl hoặc wget để tải Argos."; en="curl or wget is needed to download Argos." ;;
    download_fail)  vi="Tải Argos không thành công."; en="Downloading Argos failed." ;;
    unpack_fail)    vi="Giải nén Argos không thành công."; en="Unpacking Argos failed." ;;
    argos_done)     vi="Đã cài Argos vào %s"; en="Argos installed in %s" ;;
    argos_enabled)  vi="Đã bật extension Argos."; en="The Argos extension is enabled." ;;
    argos_enable_later) vi="Đã đặt Argos tự bật ở lần đăng nhập tới."
                    en="Argos is set to turn on at your next login." ;;
    argos_enable_manual) vi="Không bật được Argos tự động: mở app \"Extensions\" (Tiện ích mở rộng) và bật Argos."
                    en="Could not turn Argos on automatically: open the \"Extensions\" app and turn Argos on." ;;
    user_ext_on)    vi="Đã bật lại extension của người dùng (trước đó đang bị tắt toàn bộ)."
                    en="User extensions were all turned off, they are on again now." ;;
    installed)      vi="Đã cài: %s"; en="Installed: %s" ;;
    removed_other)  vi="Đã gỡ bản cài kiểu cũ: %s"; en="Removed the other install: %s" ;;
    gi_ok)          vi="Đã có PyGObject và AppIndicator."; en="PyGObject and AppIndicator are installed." ;;
    gi_missing)     vi="Thiếu thư viện để hiện biểu tượng (PyGObject + AyatanaAppIndicator3). Lệnh cài:"
                    en="Missing libraries for the tray icon (PyGObject + AyatanaAppIndicator3). Install command:" ;;
    gi_ask)         vi="Chạy lệnh này bằng sudo ngay bây giờ? [y/N] "; en="Run it with sudo now? [y/N] " ;;
    gi_unknown_pm)  vi="Không nhận ra trình quản lý gói. Hãy cài PyGObject (Gtk 3) và libayatana-appindicator (gtk3) rồi chạy lại."
                    en="Unknown package manager. Install PyGObject (Gtk 3) and libayatana-appindicator (gtk3), then run this again." ;;
    gi_still)       vi="Vẫn thiếu thư viện. Chạy lệnh trên rồi chạy lại bộ cài này."
                    en="The libraries are still missing. Run the command above, then run this installer again." ;;
    autostart_ok)   vi="Tự chạy khi đăng nhập: %s"; en="Starts at login: %s" ;;
    lang_set)       vi="Ngôn ngữ: %s (đã ghi vào %s)"; en="Language: %s (saved in %s)" ;;
    step_start)     vi="4/4  Chạy thử"; en="4/4  Trying it out" ;;
    test_run)       vi="Đang chạy thử..."; en="Running it once..." ;;
    shows)          vi="Sẽ hiện: %s"; en="It will show: %s" ;;
    no_accounts)    vi="Chưa tìm thấy tài khoản Claude Code, Codex hay Antigravity nào. Xem mục \"Nếu thiếu tài khoản\" bên dưới."
                    en="No account found yet. See \"Missing an account\" below." ;;
    tray_started)   vi="Biểu tượng đã chạy (PID %s)."; en="The tray icon is running (PID %s)." ;;
    tray_died)      vi="Biểu tượng không chạy được. Nhật ký:"; en="The tray icon did not start. Log:" ;;
    tray_no_display) vi="Không có màn hình đồ hoạ (DISPLAY/WAYLAND_DISPLAY), lần đăng nhập tới biểu tượng sẽ tự chạy."
                    en="No graphical display (DISPLAY/WAYLAND_DISPLAY), the icon will start at your next login." ;;
    done)           vi="Xong."; en="Done." ;;
    *)              vi=$key; en=$key ;;
  esac
  if [ "$UI_LANG" = en ]; then
    # shellcheck disable=SC2059
    printf -- "$en" "$@"
  else
    # shellcheck disable=SC2059
    printf -- "$vi" "$@"
  fi
}

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
fail() { printf '\n\033[31m%s\033[0m\n' "$(m error "$*")"; exit 1; }

# ask PROMPT: read a line from the terminal; empty (the default) when non-interactive.
ask() {
  REPLY=""
  [ "$NONINTERACTIVE" = 1 ] && return 0
  printf '%s' "$1"
  read -r REPLY 2>/dev/null </dev/tty || REPLY=""
}

# ---------- arguments and language ----------

while [ $# -gt 0 ]; do
  case $1 in
    --tray)   MODE=tray ;;
    --argos)  MODE=argos ;;
    --lang)   shift; UI_LANG=${1:-} ;;
    --lang=*) UI_LANG=${1#--lang=} ;;
    *) m usage "$0"; echo; exit 2 ;;
  esac
  shift
done
FORCED_MODE=$MODE

normalize_lang() {
  case $(printf '%s' "$1" | tr '[:upper:]' '[:lower:]') in
    en*|english) echo en ;;
    vi*|"tiếng việt") echo vi ;;
    *) echo "" ;;
  esac
}
UI_LANG=$(normalize_lang "$UI_LANG")
[ -n "$UI_LANG" ] || UI_LANG=$(normalize_lang "${CLAUDE_USAGE_LANG:-}")
if [ -z "$UI_LANG" ]; then
  ask "Chọn ngôn ngữ / Choose language: [1] Tiếng Việt (mặc định) [2] English: "
  case $REPLY in
    2|en*|EN*|[Ee]nglish) UI_LANG=en ;;
    *) UI_LANG=vi ;;
  esac
fi

[ "$(uname)" = "Linux" ] || fail "$(m not_linux)"

# ---------- 1. python3 (the plugin's shebang is /usr/bin/python3) ----------

say "$(m step_python)"
[ -x "$PY" ] || fail "$(m no_python)"
"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null ||
  fail "$(m old_python "$("$PY" --version 2>&1)")"
info "$(m ok "$("$PY" --version 2>&1)")"

# ---------- 2. mode ----------

is_gnome() {
  case ":${XDG_CURRENT_DESKTOP:-}:" in
    *[Bb]udgie*) return 1 ;;  # Budgie says "Budgie:GNOME" but has no gnome-shell
    *:GNOME:*|*:gnome:*) command -v gnome-shell >/dev/null 2>&1 ;;
    *) return 1 ;;
  esac
}

say "$(m step_mode)"
if [ -z "$MODE" ]; then
  if is_gnome; then MODE=argos; else MODE=tray; fi
fi
if [ "$MODE" = argos ]; then info "$(m mode_argos)"; else info "$(m mode_tray)"; fi
[ -n "$FORCED_MODE" ] && info "$(m mode_forced "--$FORCED_MODE")"

# ---------- 3. install ----------

say "$(m step_install)"

# The plugin, staged once and copied to where the chosen mode needs it.
STAGE=$(mktemp) || fail "mktemp"
trap 'rm -f "$STAGE"' EXIT
cat > "$STAGE" <<'PLUGIN_EOF'
__PLUGIN__
PLUGIN_EOF

install_plugin() {  # install_plugin DEST
  mkdir -p "$(dirname "$1")" || fail "mkdir $(dirname "$1")"
  cp "$STAGE" "$1" && chmod +x "$1" || fail "cp $1"
  info "$(m installed "$1")"
}

stop_tray() {
  pkill -f "claude-usage/claude-usage.py --tray" 2>/dev/null || return 0
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    pgrep -f "claude-usage/claude-usage.py --tray" >/dev/null 2>&1 || return 0
    sleep 0.3
  done
}

download() {  # download URL FILE
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL -o "$2" "$1"
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$2" "$1"
  else
    fail "$(m no_downloader)"
  fi
}

# GNOME Shell major version: "GNOME Shell 46.2" -> 46, "GNOME Shell 3.38.4" -> 3.38.
gnome_version() {
  gnome-shell --version 2>/dev/null | sed -n 's/^GNOME Shell \([0-9][0-9]*\)\(\.[0-9]*\)\{0,1\}.*/\1\2/p' | head -1
}

# Argos branch or tag for a GNOME version: master (45+), GNOME-44 (40-44 and 3.34+), else GNOME-41.
argos_ref() {
  local v=$1 major=${1%%.*} minor
  if [ -z "$v" ]; then echo master; return; fi
  if [ "$major" -ge 45 ]; then echo master
  elif [ "$major" -ge 40 ]; then echo GNOME-44
  else
    minor=${v#*.}; [ "$minor" = "$v" ] && minor=0
    if [ "$minor" -ge 34 ] 2>/dev/null; then echo GNOME-44; else echo GNOME-41; fi
  fi
}

install_argos_extension() {
  if [ -d "$ARGOS_EXT_DIR" ] || [ -d "/usr/share/gnome-shell/extensions/$ARGOS_UUID" ]; then
    info "$(m argos_have)"
    return
  fi
  local ver ref url tmp src
  ver=$(gnome_version)
  [ -n "$ver" ] || info "$(m no_gnome_ver)"
  ref=$(argos_ref "$ver")
  [ -n "$ver" ] && [ "${ver%%.*}" -gt 51 ] && info "$(m argos_newer "$ver")"
  if [ "$ref" = master ]; then
    url="$ARGOS_REPO/archive/refs/heads/master.tar.gz"
  else
    url="$ARGOS_REPO/archive/refs/tags/$ref.tar.gz"
  fi
  info "$(m argos_get "$ref")"
  tmp=$(mktemp -d)
  download "$url" "$tmp/argos.tar.gz" || { rm -rf "$tmp"; fail "$(m download_fail)"; }
  tar -xzf "$tmp/argos.tar.gz" -C "$tmp" || { rm -rf "$tmp"; fail "$(m unpack_fail)"; }
  src=$(find "$tmp" -mindepth 2 -maxdepth 2 -type d -name "$ARGOS_UUID" | head -1)
  [ -n "$src" ] && [ -f "$src/metadata.json" ] || { rm -rf "$tmp"; fail "$(m unpack_fail)"; }
  mkdir -p "$(dirname "$ARGOS_EXT_DIR")" && cp -R "$src" "$ARGOS_EXT_DIR" ||
    { rm -rf "$tmp"; fail "$(m unpack_fail)"; }
  rm -rf "$tmp"
  info "$(m argos_done "$ARGOS_EXT_DIR")"
}

enable_argos() {
  if command -v gsettings >/dev/null 2>&1 &&
     [ "$(gsettings get org.gnome.shell disable-user-extensions 2>/dev/null)" = true ]; then
    gsettings set org.gnome.shell disable-user-extensions false 2>/dev/null && info "$(m user_ext_on)"
  fi
  if command -v gnome-extensions >/dev/null 2>&1 &&
     gnome-extensions enable "$ARGOS_UUID" >/dev/null 2>&1; then
    info "$(m argos_enabled)"
    return
  fi
  # A freshly unpacked extension is unknown to the running shell until the next login, so
  # put it in the enabled list directly.
  local cur
  if command -v gsettings >/dev/null 2>&1 &&
     cur=$(gsettings get org.gnome.shell enabled-extensions 2>/dev/null); then
    case $cur in
      *"'$ARGOS_UUID'"*) ;;
      "@as []"|"[]") gsettings set org.gnome.shell enabled-extensions "['$ARGOS_UUID']" ;;
      *) gsettings set org.gnome.shell enabled-extensions "${cur%]}, '$ARGOS_UUID']" ;;
    esac && { info "$(m argos_enable_later)"; return; }
  fi
  info "$(m argos_enable_manual)"
}

gi_ok() {
  "$PY" - <<'EOF' >/dev/null 2>&1
import gi
gi.require_version("Gtk", "3.0")
try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3
except (ImportError, ValueError):
    gi.require_version("AppIndicator3", "0.1")
    from gi.repository import AppIndicator3
from gi.repository import Gtk
EOF
}

gi_install_command() {
  if command -v apt-get >/dev/null 2>&1; then
    echo "apt-get install -y python3-gi gir1.2-ayatanaappindicator3-0.1"
  elif command -v dnf >/dev/null 2>&1; then
    echo "dnf install -y python3-gobject libayatana-appindicator-gtk3"
  elif command -v pacman >/dev/null 2>&1; then
    echo "pacman -S --needed --noconfirm python-gobject libayatana-appindicator"
  elif command -v zypper >/dev/null 2>&1; then
    echo "zypper install -y python3-gobject python3-gobject-Gdk typelib-1_0-AyatanaAppIndicator3-0_1"
  fi
}

ensure_gi() {
  if gi_ok; then info "$(m gi_ok)"; return; fi
  local cmd
  cmd=$(gi_install_command)
  [ -n "$cmd" ] || fail "$(m gi_unknown_pm)"
  info "$(m gi_missing)"
  info "  sudo $cmd"
  ask "  $(m gi_ask)"
  case $REPLY in
    [yY]*) sudo $cmd ;;  # word splitting intended: cmd is a fixed command line
  esac
  gi_ok || fail "$(m gi_still)"
  info "$(m gi_ok)"
}

set_conf_language() {  # set_conf_language vi|en: replace or add "language = ..."
  mkdir -p "$(dirname "$CONF")" && touch "$CONF" || return 1
  awk -v v="$1" '
    /^[[:space:]]*language[[:space:]]*=/ { if (!done) print "language = " v; done = 1; next }
    { print }
    END { if (!done) print "language = " v }' "$CONF" > "$CONF.tmp" && mv "$CONF.tmp" "$CONF"
}

if [ "$MODE" = argos ]; then
  install_argos_extension
  enable_argos
  install_plugin "$ARGOS_PLUGIN"
  PLUGIN=$ARGOS_PLUGIN
  if [ -f "$TRAY_PLUGIN" ] || [ -f "$AUTOSTART" ]; then
    stop_tray
    rm -f "$TRAY_PLUGIN" "$AUTOSTART"
    info "$(m removed_other "$TRAY_PLUGIN")"
  fi
else
  ensure_gi
  install_plugin "$TRAY_PLUGIN"
  PLUGIN=$TRAY_PLUGIN
  mkdir -p "$(dirname "$AUTOSTART")" || fail "mkdir $(dirname "$AUTOSTART")"
  cat > "$AUTOSTART" <<EOF
[Desktop Entry]
Type=Application
Name=AI Usage
Comment=Claude Code, Codex and Antigravity usage limits in the system tray
Exec="$PY" "$TRAY_PLUGIN" --tray
Icon=utilities-system-monitor
Terminal=false
X-GNOME-Autostart-enabled=true
EOF
  info "$(m autostart_ok "$AUTOSTART")"
  if [ -f "$ARGOS_PLUGIN" ]; then
    rm -f "$ARGOS_PLUGIN"
    info "$(m removed_other "$ARGOS_PLUGIN")"
  fi
fi

if [ "$UI_LANG" = en ] || grep -qs '^[[:space:]]*language[[:space:]]*=' "$CONF"; then
  set_conf_language "$UI_LANG" && info "$(m lang_set "$UI_LANG" "$CONF")"
fi

# ---------- 4. try it ----------

say "$(m step_start)"
info "$(m test_run)"
if command -v timeout >/dev/null 2>&1; then T="timeout 90"; else T=""; fi
OUT=$($T "$PY" "$PLUGIN" 2>&1 | head -1 | sed 's/ |.*//')
info "$(m shows "$OUT")"
case "$OUT" in
  "AI ?") info "$(m no_accounts)" ;;
esac

if [ "$MODE" = tray ]; then
  stop_tray
  if [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
    mkdir -p "$CACHE"
    LOG="$CACHE/tray-start.log"
    if command -v setsid >/dev/null 2>&1; then
      setsid "$PY" "$TRAY_PLUGIN" --tray >"$LOG" 2>&1 </dev/null &
    else
      nohup "$PY" "$TRAY_PLUGIN" --tray >"$LOG" 2>&1 </dev/null &
    fi
    sleep 3
    PID=$(pgrep -f "claude-usage/claude-usage.py --tray" | head -1)
    if [ -n "$PID" ]; then
      info "$(m tray_started "$PID")"
    else
      info "$(m tray_died)"
      sed 's/^/    /' "$LOG" | tail -20
    fi
  else
    info "$(m tray_no_display)"
  fi
fi

# ---------- what the user sees, and how to uninstall ----------

printf '\n\033[1m%s\033[0m\n' "$(m done)"
if [ "$UI_LANG" = en ]; then
  cat <<'EOF'
  The panel shows something like "C 87/88/94  Cx 78/73  Ag 0/0": a letter per Claude account
  (% of the 5-hour / weekly / Fable weekly limit), Cx for Codex (5-hour / weekly), Ag for
  Antigravity (weekly per model group). Click it for account names and how long until
  each limit resets. It refreshes every 3 minutes; when a new version is out, the menu offers
  "Update now". If it shows "?", click it to read the "Error: ..." line, or look at
  ~/.cache/claude-usage/last-run.log.
EOF
  if [ "$MODE" = argos ]; then
    if [ "${XDG_SESSION_TYPE:-}" = x11 ]; then
      echo "  If Argos was just installed: press Alt+F2, type r, press Enter (or log out and in)."
    else
      echo "  If Argos was just installed: log out and log in again (Wayland loads new extensions only at login)."
    fi
  else
    cat <<'EOF'
  No icon? On XFCE add the "Status Notifier Plugin" to the panel; on MATE the "Indicator Applet".
EOF
  fi
  cat <<'EOF'

  Missing an account:
  - Claude Code browser login: run `claude` once to log in (it is read from ~/.claude/.credentials.json).
  - Codex: install the codex CLI and run `codex login` (ChatGPT login). Antigravity: install
    the agy CLI and log in. Their checks cost no quota.
  - Token (claude setup-token): aliases/exports of CLAUDE_CODE_OAUTH_TOKEN in ~/.bashrc,
    ~/.zshrc... and scripts in ~/bin are found automatically. Elsewhere: add a line
    "name = /path/to/token-file" to ~/.config/claude-usage.conf, then click "Refresh now".
EOF
  echo
  if [ "$MODE" = argos ]; then
    echo "  Uninstall: rm $ARGOS_PLUGIN"
  else
    echo "  Uninstall: pkill -f 'claude-usage.py --tray'; rm $TRAY_PLUGIN $AUTOSTART"
  fi
else
  cat <<'EOF'
  Thanh hiện dạng "C 87/88/94  Cx 78/73  Ag 0/0": mỗi tài khoản Claude là một chữ cái
  (% giới hạn 5 giờ / tuần / Fable tuần), Cx là Codex (5 giờ / tuần), Ag là Antigravity
  (tuần, theo từng nhóm model). Bấm vào để xem tên tài khoản và còn bao lâu nữa thì
  reset. Tự cập nhật mỗi 3 phút, có bản mới thì menu hiện nút "Cập nhật ngay". Nếu hiện "?",
  bấm vào để xem dòng "Lỗi: ..." hoặc xem file ~/.cache/claude-usage/last-run.log.
EOF
  if [ "$MODE" = argos ]; then
    if [ "${XDG_SESSION_TYPE:-}" = x11 ]; then
      echo "  Nếu Argos vừa được cài: bấm Alt+F2, gõ r rồi Enter (hoặc đăng xuất rồi đăng nhập lại)."
    else
      echo "  Nếu Argos vừa được cài: đăng xuất rồi đăng nhập lại (Wayland chỉ nạp extension mới khi đăng nhập)."
    fi
  else
    cat <<'EOF'
  Không thấy biểu tượng? Trên XFCE thêm "Status Notifier Plugin" vào panel; trên MATE thêm
  "Indicator Applet".
EOF
  fi
  cat <<'EOF'

  Nếu thiếu tài khoản:
  - Claude Code, đăng nhập trình duyệt: chạy `claude` một lần để đăng nhập (đọc từ ~/.claude/.credentials.json).
  - Codex: cài codex CLI rồi chạy `codex login` (đăng nhập ChatGPT). Antigravity: cài agy CLI
    rồi đăng nhập. Hai loại này đọc hạn mức không tốn quota.
  - Token (claude setup-token): plugin tự tìm alias/export CLAUDE_CODE_OAUTH_TOKEN trong
    ~/.bashrc, ~/.zshrc... và script trong ~/bin. Nếu token để chỗ khác, thêm một dòng
    "tên = /đường/dẫn/file-token" vào ~/.config/claude-usage.conf rồi bấm "Làm mới ngay".
EOF
  echo
  if [ "$MODE" = argos ]; then
    echo "  Gỡ cài đặt: rm $ARGOS_PLUGIN"
  else
    echo "  Gỡ cài đặt: pkill -f 'claude-usage.py --tray'; rm $TRAY_PLUGIN $AUTOSTART"
  fi
fi
