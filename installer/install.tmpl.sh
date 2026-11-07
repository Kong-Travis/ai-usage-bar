#!/bin/bash
# Installer for the "AI Usage" menu bar plugin (SwiftBar, macOS).
# Double-click in Finder, or run: bash ai-usage-install.command [--lang vi|en]
# Legacy names are kept on purpose (claude-usage.1m.py, claude-usage.conf, ~/.cache/claude-usage):
# installs from before the rename self-update in place, so renaming them would leave two icons.
set -u

CONF="$HOME/.config/claude-usage.conf"

# ---------- language ----------
LANG_CHOICE="${CLAUDE_USAGE_LANG:-}"
[ "${1:-}" = "--lang" ] && LANG_CHOICE="${2:-}"
case "$LANG_CHOICE" in
  en*|EN*) LANG_CHOICE=en ;;
  vi*|VI*) LANG_CHOICE=vi ;;
  *)
    printf 'Chọn ngôn ngữ / Choose language:\n  [1] Tiếng Việt (mặc định)\n  [2] English\n> '
    read -r ans 2>/dev/null </dev/tty || ans=""
    case "$ans" in 2|en*|EN*) LANG_CHOICE=en ;; *) LANG_CHOICE=vi ;; esac
    ;;
esac

# m KEY: the message for KEY in the chosen language.
m() {
  if [ "$LANG_CHOICE" = en ]; then
    case "$1" in
      error) echo "Error" ;;
      press_enter) echo "Press Enter to continue..." ;;
      macos_only) echo "This installer only runs on macOS." ;;
      step1) echo "1/4  Checking python3" ;;
      no_clt) echo "Command Line Tools are missing (needed for python3). Opening the installer dialog..." ;;
      clt_retry) echo "Click \"Install\" in the dialog, wait until it finishes, then run this file again." ;;
      step2) echo "2/4  Installing SwiftBar (the app that shows text in the menu bar)" ;;
      has_swiftbar) echo "SwiftBar is already installed." ;;
      brew_failed) echo "brew install --cask swiftbar failed." ;;
      no_brew) echo "No Homebrew, downloading the latest release from GitHub..." ;;
      no_url) echo "Could not get the SwiftBar download link." ;;
      dl_failed) echo "Downloading SwiftBar failed." ;;
      unzip_failed) echo "Unzipping SwiftBar failed." ;;
      copy_failed) echo "Could not copy SwiftBar to /Applications." ;;
      not_found) echo "Not found after install:" ;;
      step3) echo "3/4  Installing the AI Usage plugin" ;;
      installed) echo "Installed:" ;;
      test_run) echo "Test run..." ;;
      will_show) echo "The menu bar will show:" ;;
      no_account) echo "No Claude Code, Codex or Antigravity account found yet. See \"Missing an account\" below." ;;
      step4) echo "4/4  Opening SwiftBar and the settings that need a click" ;;
      gatekeeper) cat <<'EOF'

  a) The first time SwiftBar opens, macOS may ask "SwiftBar is an app downloaded
     from the Internet. Are you sure you want to open it?" -> click "Open".

  b) If macOS asks "security wants to use your confidential information stored in
     'Claude Code-credentials' in your keychain" -> type your Mac password and click
     "Always Allow". This reads the limits of the account you logged in with `claude`.

  c) No text in the menu bar? It may be full (especially with a notch). Hold Command
     and drag other icons out to make room.
EOF
      ;;
      login_title) echo "Start SwiftBar at login" ;;
      login_on) echo "Already on: SwiftBar opens every time you log in." ;;
      login_ask) echo "  Start at login? (recommended, otherwise you open SwiftBar yourself after each restart) [Y/n] " ;;
      login_skip) echo "Skipped. To turn it on later: SwiftBar > Preferences... > tick \"Launch at Login\"." ;;
      login_allow) echo "If macOS asks \"Terminal wants to control System Events\" -> click \"OK\"." ;;
      login_done) echo "Done: SwiftBar opens every time you log in." ;;
      login_manual) cat <<'EOF'
  Could not turn it on automatically. Do it once by hand:
    Click the menu bar text > SwiftBar > Preferences... > tick "Launch at Login".
  Or: System Settings > General > Login Items > "Open at Login" > "+" > pick SwiftBar.
EOF
      ;;
      done) echo "Done." ;;
      summary) cat <<EOF
  The menu bar shows "C 87/88/94  Cx 78/73  Ag 0/0": a letter per Claude account (% of the
  5-hour / weekly / Fable weekly limit), Cx for Codex (5-hour / weekly), Ag for Antigravity
  (weekly per model group). Click it for account names and how long until each limit
  resets. Refreshes every 3 minutes; when a new version is out the menu shows "Update now".
  If it shows "?", click it to see the "Error: ..." line, or open
  ~/.cache/claude-usage/last-run.log.

  Missing an account:
  - Claude Code browser login: run \`claude\` once to log in.
  - Codex: install the codex CLI and run \`codex login\` (ChatGPT login). Antigravity: install
    the agy CLI and log in. Their checks cost no quota.
  - Token (claude setup-token): aliases/exports of CLAUDE_CODE_OAUTH_TOKEN in
    ~/.zshrc, ~/.bashrc... and scripts in ~/bin are found automatically. If the token
    lives elsewhere, add a line "name = /path/to/token-file" to ~/.config/claude-usage.conf
    and click "Refresh now" in the menu.
  - To hide browser logins: add the line "keychain = off" to that file.
  - Language: "Chuyển sang tiếng Việt" in the menu, or "language = vi" in that file.

  Each Claude account costs one 1-token request every 3 minutes to read its limits;
  Codex and Antigravity checks cost no quota.
  Uninstall: delete $PLUGIN (and the SwiftBar app if you don't need it).
EOF
      ;;
    esac
  else
    case "$1" in
      error) echo "Lỗi" ;;
      press_enter) echo "Nhấn Enter để tiếp tục..." ;;
      macos_only) echo "Bộ cài này chỉ chạy trên macOS." ;;
      step1) echo "1/4  Kiểm tra python3" ;;
      no_clt) echo "Máy chưa có Command Line Tools (cần cho python3). Đang mở hộp thoại cài đặt..." ;;
      clt_retry) echo "Bấm \"Cài đặt\" trong hộp thoại vừa hiện, chờ cài xong rồi chạy lại file này." ;;
      step2) echo "2/4  Cài SwiftBar (app hiển thị chữ trên thanh menu)" ;;
      has_swiftbar) echo "Đã có sẵn SwiftBar." ;;
      brew_failed) echo "brew install --cask swiftbar không thành công." ;;
      no_brew) echo "Không có Homebrew, tải bản mới nhất từ GitHub..." ;;
      no_url) echo "Không lấy được link tải SwiftBar." ;;
      dl_failed) echo "Tải SwiftBar không thành công." ;;
      unzip_failed) echo "Giải nén SwiftBar không thành công." ;;
      copy_failed) echo "Không chép được SwiftBar vào /Applications." ;;
      not_found) echo "Không tìm thấy sau khi cài:" ;;
      step3) echo "3/4  Cài plugin AI Usage" ;;
      installed) echo "Đã cài:" ;;
      test_run) echo "Đang chạy thử..." ;;
      will_show) echo "Thanh menu sẽ hiện:" ;;
      no_account) echo "Chưa tìm thấy tài khoản Claude Code, Codex hay Antigravity nào. Xem mục \"Nếu thiếu tài khoản\" bên dưới." ;;
      step4) echo "4/4  Mở SwiftBar và các cài đặt cần thiết" ;;
      gatekeeper) cat <<'EOF'

  a) Lần đầu mở SwiftBar, macOS có thể hỏi "SwiftBar là app tải từ Internet,
     bạn có chắc muốn mở?" -> bấm "Mở" (Open).

  b) Nếu macOS hỏi "security muốn dùng thông tin bí mật 'Claude Code-credentials'
     trong chuỗi khoá (keychain)" -> nhập mật khẩu máy rồi bấm "Luôn cho phép"
     (Always Allow). Bước này để đọc hạn mức của tài khoản đăng nhập bằng `claude`.

  c) Nếu không thấy chữ trên thanh menu: thanh menu có thể đã đầy (nhất là máy
     có tai thỏ). Giữ phím Command rồi kéo bớt icon khác ra ngoài để nhường chỗ.
EOF
      ;;
      login_title) echo "Cho SwiftBar tự chạy khi bật máy" ;;
      login_on) echo "Đã bật sẵn, mỗi lần bật máy SwiftBar sẽ tự mở." ;;
      login_ask) echo "  Bật tự chạy khi mở máy? (nên bật, không thì mỗi lần khởi động lại phải tự mở SwiftBar) [Y/n] " ;;
      login_skip) echo "Bỏ qua. Muốn bật sau: SwiftBar > Preferences... > tích \"Launch at Login\"." ;;
      login_allow) echo "Nếu macOS hỏi \"Terminal muốn điều khiển System Events\" -> bấm \"OK\" (Allow)." ;;
      login_done) echo "Đã bật: mỗi lần bật máy SwiftBar sẽ tự mở." ;;
      login_manual) cat <<'EOF'
  Chưa bật được tự động. Làm tay (chỉ 1 lần):
    Bấm vào chữ trên thanh menu > SwiftBar > Preferences... > tích "Launch at Login".
  Hoặc: System Settings > General > Login Items > "Open at Login" > bấm "+" > chọn SwiftBar.
EOF
      ;;
      done) echo "Xong." ;;
      summary) cat <<EOF
  Thanh menu hiện dạng "C 87/88/94  Cx 78/73  Ag 0/0": mỗi tài khoản Claude là một chữ cái
  (% giới hạn 5 giờ / tuần / Fable tuần), Cx là Codex (5 giờ / tuần), Ag là Antigravity
  (tuần, theo từng nhóm model). Bấm vào để xem tên tài khoản và còn bao lâu nữa thì
  reset. Tự cập nhật mỗi 3 phút, có bản mới thì menu hiện nút "Cập nhật ngay".
  Nếu hiện "?", bấm vào để xem dòng "Lỗi: ..." hoặc xem file ~/.cache/claude-usage/last-run.log.

  Nếu thiếu tài khoản:
  - Claude Code, đăng nhập trình duyệt: chạy \`claude\` một lần để đăng nhập.
  - Codex: cài codex CLI rồi chạy \`codex login\` (đăng nhập ChatGPT). Antigravity: cài agy CLI
    rồi đăng nhập. Hai loại này đọc hạn mức không tốn quota.
  - Token (claude setup-token): plugin tự tìm alias/export CLAUDE_CODE_OAUTH_TOKEN
    trong ~/.zshrc, ~/.bashrc... và script trong ~/bin. Nếu token để chỗ khác,
    thêm một dòng "tên = /đường/dẫn/file-token" vào ~/.config/claude-usage.conf
    rồi bấm "Làm mới ngay" trong menu.
  - Muốn ẩn tài khoản đăng nhập trình duyệt: thêm dòng "keychain = off" vào file trên.
  - Ngôn ngữ: bấm "Switch to English" trong menu, hoặc thêm "language = en" vào file trên.

  Lưu ý: mỗi tài khoản Claude tốn một request 1 token mỗi 3 phút để đọc hạn mức;
  Codex và Antigravity không tốn quota.
  Gỡ cài đặt: xoá $PLUGIN (và app SwiftBar nếu không cần).
EOF
      ;;
    esac
  fi
}

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
fail() { printf '\n\033[31m%s: %s\033[0m\n' "$(m error)" "$*"; pause; exit 1; }
pause() { printf '\n%s' "$(m press_enter)"; read -r _ 2>/dev/null </dev/tty; }

# Remember the choice for the menu too (Vietnamese is the default, nothing to write).
set_conf_language() {
  mkdir -p "$(dirname "$CONF")"
  touch "$CONF"
  grep -v -E '^[[:space:]]*language[[:space:]]*=' "$CONF" > "$CONF.tmp" || true
  echo "language = $1" >> "$CONF.tmp"
  mv "$CONF.tmp" "$CONF"
}

[ "$(uname)" = "Darwin" ] || fail "$(m macos_only)"

# 1. python3 (ships with the Xcode Command Line Tools)
say "$(m step1)"
if ! /usr/bin/xcrun --find python3 >/dev/null 2>&1 || ! /usr/bin/python3 -c 'import sys' >/dev/null 2>&1; then
  info "$(m no_clt)"
  xcode-select --install >/dev/null 2>&1
  fail "$(m clt_retry)"
fi
info "OK: $(/usr/bin/python3 --version)"

# 2. SwiftBar
say "$(m step2)"
APP=/Applications/SwiftBar.app
if [ -d "$APP" ]; then
  info "$(m has_swiftbar)"
elif command -v brew >/dev/null 2>&1; then
  brew install --cask swiftbar || fail "$(m brew_failed)"
else
  info "$(m no_brew)"
  URL=$(curl -fsSL https://api.github.com/repos/swiftbar/SwiftBar/releases/latest |
    /usr/bin/python3 -c 'import json,sys; print(next(a["browser_download_url"] for a in json.load(sys.stdin)["assets"] if a["name"].endswith(".zip")))') ||
    fail "$(m no_url)"
  TMP=$(mktemp -d)
  curl -fL -o "$TMP/SwiftBar.zip" "$URL" || fail "$(m dl_failed)"
  ditto -x -k "$TMP/SwiftBar.zip" "$TMP" || fail "$(m unzip_failed)"
  mv "$TMP/SwiftBar.app" /Applications/ || fail "$(m copy_failed)"
  rm -rf "$TMP"
fi
[ -d "$APP" ] || fail "$(m not_found) $APP"

# 3. Plugin. Reuse an existing SwiftBar plugin folder if one is configured.
say "$(m step3)"
DIR=$(defaults read com.ameba.SwiftBar PluginDirectory 2>/dev/null || true)
if [ -z "$DIR" ]; then
  DIR="$HOME/.config/swiftbar"
  defaults write com.ameba.SwiftBar PluginDirectory -string "$DIR"
fi
mkdir -p "$DIR"
PLUGIN="$DIR/claude-usage.1m.py"
rm -f "$DIR/claude-usage.5m.py"  # earlier version
cat > "$PLUGIN" <<'PLUGIN_EOF'
__PLUGIN__
PLUGIN_EOF
chmod +x "$PLUGIN"
# Vietnamese is the default: only rewrite the line if the file already sets a language.
if [ "$LANG_CHOICE" = en ] || grep -q -E '^[[:space:]]*language[[:space:]]*=' "$CONF" 2>/dev/null; then
  set_conf_language "$LANG_CHOICE"
fi
info "$(m installed) $PLUGIN"

info "$(m test_run)"
OUT=$(/usr/bin/python3 "$PLUGIN" 2>&1 | head -1 | sed 's/ |.*//')
info "$(m will_show) $OUT"
case "$OUT" in
  "AI ?") info "$(m no_account)" ;;
esac

# 4. Launch and guide through the macOS settings that need a human click.
say "$(m step4)"
pkill -x SwiftBar 2>/dev/null
open "$APP"
m gatekeeper
pause

say "$(m login_title)"
login_items() {
  osascript -e 'tell application "System Events" to get the name of every login item' 2>/dev/null
}
if login_items | grep -q "SwiftBar"; then
  info "$(m login_on)"
else
  printf '%s' "$(m login_ask)"
  read -r ans 2>/dev/null </dev/tty || ans=""
  case "$ans" in
    [nN]*) info "$(m login_skip)" ;;
    *)
      info "$(m login_allow)"
      osascript -e 'tell application "System Events" to make login item at end with properties {path:"/Applications/SwiftBar.app", hidden:false}' >/dev/null 2>&1
      if login_items | grep -q "SwiftBar"; then
        info "$(m login_done)"
      else
        m login_manual
        pause
        open "x-apple.systempreferences:com.apple.LoginItems-Settings.extension"
      fi
      ;;
  esac
fi

printf '\n\033[1m%s\033[0m\n' "$(m done)"
m summary
pause
