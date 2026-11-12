"""User-facing text in Vietnamese (default) and English.

Each entry keeps both languages side by side, so a new string is added in one place.
Frontends with strings of their own pass an extra catalog to Translator.with_catalog.
"""
from __future__ import annotations

from typing import Dict, Mapping, Optional

VI, EN = "vi", "en"
LANGUAGES = (VI, EN)
DEFAULT_LANGUAGE = VI
LANGUAGE_NAMES = {VI: "Tiếng Việt", EN: "English"}

Catalog = Mapping[str, Mapping[str, str]]

CATALOG: Dict[str, Dict[str, str]] = {
    "five_hour": {VI: "5 giờ", EN: "5-hour"},
    "weekly": {VI: "Tuần", EN: "Weekly"},
    "model_five_hour": {VI: "{name} (5 giờ)", EN: "{name} (5-hour)"},
    "model_weekly": {VI: "{name} (tuần)", EN: "{name} (weekly)"},
    "model_line": {VI: "{name}: {pct}%", EN: "{name}: {pct}%"},
    "no_limit_line": {VI: "{name}: không giới hạn", EN: "{name}: no limit"},
    "reset_suffix": {VI: "  (reset {left})", EN: "  (resets {left})"},
    # Under a window line, only when the limit needs attention. {left}: "1 giờ 20 phút" /
    # "1h 20m" for pace_runs_out, "sau 2 giờ" / "in 2h" for pace_exhausted.
    "pace_runs_out": {VI: "Theo đà này sẽ hết sau ~{left}, trước khi reset",
                      EN: "At this pace it runs out in ~{left}, before the reset"},
    "pace_exhausted": {VI: "Đã hết hạn mức, dùng lại được {left}", EN: "Limit reached, back {left}"},
    "left_now": {VI: "ngay bây giờ", EN: "now"},
    "left_prefix": {VI: "sau ", EN: "in "},
    "unit_day": {VI: "{n} ngày", EN: "{n}d"},
    "unit_hour": {VI: "{n} giờ", EN: "{n}h"},
    "unit_minute": {VI: "{n} phút", EN: "{n}m"},
    "update_available": {VI: "Có bản mới {tag} (đang dùng v{version})",
                         EN: "New version {tag} (you have v{version})"},
    "update_now": {VI: "Cập nhật ngay", EN: "Update now"},
    "view_changes": {VI: "Xem thay đổi", EN: "What's new"},
    "check_update": {VI: "Kiểm tra cập nhật", EN: "Check for updates"},
    "check_update_done": {VI: "Kiểm tra cập nhật (đã là bản mới nhất)",
                          EN: "Check for updates (up to date)"},
    "no_accounts": {VI: "Chưa tìm thấy tài khoản Claude Code, Codex hay Antigravity nào",
                    EN: "No Claude Code, Codex or Antigravity account found"},
    "no_accounts_hint": {
        VI: "Đăng nhập bằng `claude`, `codex login` hoặc `agy`, "
            "hoặc khai báo token trong ~/.config/claude-usage.conf",
        EN: "Log in with `claude`, `codex login` or `agy`, "
            "or add a token to ~/.config/claude-usage.conf"},
    "usage_unreadable": {VI: "Không đọc được hạn mức", EN: "Could not read the limits"},
    "stale_data": {VI: "Số liệu cũ, lần cập nhật vừa rồi bị lỗi",
                   EN: "Old numbers, the last refresh failed"},
    "error_line": {VI: "Lỗi: {error}", EN: "Error: {error}"},
    "refresh_every": {VI: "Cập nhật mỗi 3 phút", EN: "Refreshes every 3 minutes"},
    # What the bar numbers mean, one key per title layout (see Presenter.title).
    "number_order": {VI: "Số trên thanh: % đã dùng của từng hạn mức, theo thứ tự ở trên",
                     EN: "Bar numbers: % used of each limit, in the order above"},
    "number_worst_each": {VI: "Số trên thanh: % của hạn mức dùng nhiều nhất ở mỗi tài khoản",
                          EN: "Bar numbers: % of each account's most-used limit"},
    "number_worst_all": {VI: "Số trên thanh: % của hạn mức dùng nhiều nhất trong mọi tài khoản",
                         EN: "Bar number: % of the most-used limit across all accounts"},
    "version": {VI: "Phiên bản v{version}", EN: "Version v{version}"},
    "support": {VI: "Ủng hộ tác giả", EN: "Support the author"},
    "refresh_now": {VI: "Làm mới ngay", EN: "Refresh now"},
    "show_in_bar": {VI: "Hiện trên thanh menu", EN: "Show in the menu bar"},
    "collapse": {VI: "Thu gọn (chỉ hiện biểu tượng)", EN: "Collapse to an icon"},
    "expand": {VI: "Mở rộng (hiện số liệu)", EN: "Expand (show the numbers)"},
    "color_by_level": {VI: "Tô màu số liệu theo mức", EN: "Color the numbers by level"},
    # Shown in the language it switches to (see Presenter.language_item).
    "switch_language": {VI: "Chuyển sang tiếng Việt", EN: "Switch to English"},
    "quit": {VI: "Thoát", EN: "Quit"},
    "loading": {VI: "Đang đọc hạn mức...", EN: "Reading limits..."},
    "login_expired": {VI: "đăng nhập đã hết hạn, mở `claude` một lần để làm mới",
                      EN: "login expired, open `claude` once to renew it"},
}


def normalize_language(value: Optional[str]) -> Optional[str]:
    """"en", "EN", "en_US.UTF-8", "English" -> "en"; unknown -> None."""
    v = (value or "").strip().lower()
    for lang in LANGUAGES:
        if v.startswith(lang) or v == LANGUAGE_NAMES[lang].lower():
            return lang
    return None


class Translator:
    def __init__(self, lang: str = DEFAULT_LANGUAGE, catalog: Catalog = CATALOG):
        self.lang = normalize_language(lang) or DEFAULT_LANGUAGE
        self._catalog = catalog

    def __call__(self, key: str, **kwargs: object) -> str:
        entry = self._catalog[key]
        text = entry.get(self.lang) or entry[DEFAULT_LANGUAGE]
        return text.format(**kwargs) if kwargs else text

    def with_catalog(self, extra: Catalog) -> Translator:
        merged: Dict[str, Mapping[str, str]] = dict(self._catalog)
        merged.update(extra)
        return Translator(self.lang, merged)
