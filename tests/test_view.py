"""Menu model: countdowns, colors, title, update and error items."""
import srcpath  # noqa: F401  (must come first)

import unittest

from claude_usage.domain import AccountRow, ModelWindow, Usage
from claude_usage.i18n import Translator
from claude_usage.view import (GRAY, GREEN, LANGUAGE, OPEN, ORANGE, RED, REFRESH, SEPARATOR, UPDATE,
                               Presenter, short_name)

NOW = 1_000_000
REPO_URL = "https://example.test/repo"


class FakeClock:
    def now(self):
        return float(NOW)


def presenter(lang="vi", max_in_title=3):
    return Presenter(FakeClock(), Translator(lang), "1.2.2", REPO_URL, 70, 90, max_in_title)


def usage(five=13.0, weekly=8.0, models=(), five_reset=NOW + 3600, weekly_reset=NOW + 86400):
    return Usage(five, five_reset, weekly, weekly_reset, tuple(models))


def row(short, usage_=None, label="claude", detail="", error=None):
    return AccountRow(short, label, detail, usage_, error)


class TimeLeft(unittest.TestCase):
    def test_boundaries_in_vietnamese(self):
        p = presenter()
        cases = [
            (-5000, "ngay bây giờ"),
            (-1, "ngay bây giờ"),
            (0, "ngay bây giờ"),
            (1, "sau 1 phút"),
            (59, "sau 1 phút"),
            (60, "sau 1 phút"),
            (61, "sau 2 phút"),
            (45 * 60, "sau 45 phút"),
            (3600, "sau 1 giờ"),
            (3601, "sau 1 giờ 1 phút"),
            (3660, "sau 1 giờ 1 phút"),
            (2 * 3600 + 15 * 60, "sau 2 giờ 15 phút"),
            (86400, "sau 1 ngày"),
            (86400 + 3 * 3600, "sau 1 ngày 3 giờ"),
            (86400 + 60, "sau 1 ngày"),  # minutes are dropped once there are days
            (2 * 86400 + 21 * 3600, "sau 2 ngày 21 giờ"),
        ]
        for offset, want in cases:
            with self.subTest(offset=offset):
                self.assertEqual(p.time_left(NOW + offset), want)

    def test_english(self):
        p = presenter("en")
        self.assertEqual(p.time_left(NOW), "now")
        self.assertEqual(p.time_left(NOW + 61), "in 2m")
        self.assertEqual(p.time_left(NOW + 3660), "in 1h 1m")
        self.assertEqual(p.time_left(NOW + 86400 + 3 * 3600), "in 1d 3h")


class Colors(unittest.TestCase):
    def test_thresholds(self):
        p = presenter()
        self.assertIsNone(p.color(0))
        self.assertIsNone(p.color(69.9))
        self.assertEqual(p.color(70), ORANGE)
        self.assertEqual(p.color(89.9), ORANGE)
        self.assertEqual(p.color(90), RED)
        self.assertEqual(p.color(100), RED)


class Title(unittest.TestCase):
    def test_up_to_three_rows_list_every_account(self):
        rows = [row("C", usage(13.4, 8.0, [ModelWindow("Fable", 1.0, 0)])),
                row("T", usage(0, 65.0, [ModelWindow("Fable", 1.0, 0)])),
                row("W", usage(1.0, 2.0))]
        menu = presenter().present(rows, None)
        self.assertEqual(menu.title, "C 13/8/1  T 0/65/1  W 1/2")

    def test_unknown_rows_show_a_question_mark(self):
        rows = [row("C", usage(10, 20)), row("T", None, error="boom")]
        menu = presenter().present(rows, None)
        self.assertEqual(menu.title, "C 10/20  T ?")
        self.assertEqual(menu.worst, 20)

    def test_more_than_three_rows_show_the_worst_percentage(self):
        rows = [row("A", usage(10, 20)), row("B", usage(55.6, 30)), row("C", usage(1, 2)),
                row("D", usage(5, 6, [ModelWindow("Fable", 40.0, 0)]))]
        menu = presenter().present(rows, None)
        self.assertEqual(menu.title, "Claude 56%")
        self.assertEqual(menu.worst, 55.6)

    def test_all_unknown_with_many_rows(self):
        menu = presenter().present([row(c) for c in "ABCD"], None)
        self.assertEqual(menu.title, "Claude 0%")
        self.assertIsNone(menu.worst)
        self.assertIsNone(menu.title_color)

    def test_title_color_follows_the_worst_value(self):
        self.assertIsNone(presenter().present([row("C", usage(10, 20))], None).title_color)
        self.assertEqual(presenter().present([row("C", usage(75, 20))], None).title_color, ORANGE)
        hot = usage(10, 20, [ModelWindow("Fable", 95.0, 0)])
        self.assertEqual(presenter().present([row("C", hot)], None).title_color, RED)

    def test_no_accounts(self):
        menu = presenter().present([], None)
        self.assertEqual(menu.title, "Claude ?")
        self.assertIsNone(menu.worst)
        self.assertEqual(menu.items[0].text, "Không tìm thấy tài khoản Claude Code nào")
        self.assertIn("claude-usage.conf", menu.items[1].text)


class UpdateItems(unittest.TestCase):
    def test_none_without_a_tag(self):
        p = presenter()
        self.assertEqual(p.update_items(None), [])
        self.assertEqual(p.update_items(""), [])
        menu = p.present([row("C", usage())], None)
        self.assertFalse(menu.update_available)
        self.assertFalse([i for i in menu.items if i.action and i.action.kind == UPDATE])

    def test_offered_first_when_a_tag_is_passed(self):
        menu = presenter().present([row("C", usage())], "v1.3.0")
        self.assertTrue(menu.update_available)
        head = menu.items[:4]
        self.assertEqual(head[0].text, "Có bản mới v1.3.0 (đang dùng v1.2.2)")
        self.assertEqual(head[0].color, GREEN)
        self.assertEqual(head[1].action.kind, UPDATE)
        self.assertEqual(head[2].action.kind, OPEN)
        self.assertEqual(head[2].action.url, REPO_URL + "/releases/tag/v1.3.0")
        self.assertIs(head[3], SEPARATOR)

    def test_offered_when_there_are_no_accounts_too(self):
        menu = presenter().present([], "v1.3.0")
        self.assertTrue(menu.update_available)
        self.assertEqual(menu.items[1].action.kind, UPDATE)


class AccountItems(unittest.TestCase):
    def test_header_with_and_without_detail(self):
        p = presenter()
        self.assertEqual(p.account_items(row("C", usage(), detail="a@b.c"))[0].text, "C: claude - a@b.c")
        self.assertEqual(p.account_items(row("C", usage()))[0].text, "C: claude")

    def test_usage_lines_and_colors(self):
        items = presenter().account_items(row("C", usage(72.0, 95.0)))
        self.assertEqual(items[1].text, "5 giờ: 72%  (reset sau 1 giờ)")
        self.assertEqual(items[1].color, ORANGE)
        self.assertEqual(items[2].text, "Tuần: 95%  (reset sau 1 ngày)")
        self.assertEqual(items[2].color, RED)
        self.assertIs(items[-1], SEPARATOR)

    def test_model_window_with_and_without_reset(self):
        models = [ModelWindow("Fable", 50.0, NOW + 7200), ModelWindow("Other", 1.0, 0)]
        items = presenter().account_items(row("C", usage(models=models)))
        self.assertEqual(items[3].text, "Fable (tuần): 50%  (reset sau 2 giờ)")
        self.assertEqual(items[4].text, "Other (tuần): 1%")

    def test_unreadable_usage(self):
        items = presenter().account_items(row("C", None))
        self.assertEqual(items[1].text, "Không đọc được hạn mức")
        self.assertEqual(items[1].color, GRAY)
        self.assertEqual(len(items), 4)  # head, unreadable, show-in-bar toggle, separator

    def test_stale_numbers_note_and_error_line(self):
        items = presenter().account_items(row("C", usage(), error="RuntimeError: x"))
        texts = [i.text for i in items]
        self.assertIn("Số liệu cũ, lần cập nhật vừa rồi bị lỗi", texts)
        self.assertIn("Lỗi: RuntimeError: x", texts)

    def test_error_without_usage_has_no_stale_note(self):
        texts = [i.text for i in presenter().account_items(row("C", None, error="Boom: x"))]
        self.assertIn("Lỗi: Boom: x", texts)
        self.assertNotIn("Số liệu cũ, lần cập nhật vừa rồi bị lỗi", texts)

    def test_a_pipe_in_an_error_stays_in_the_model_text(self):
        # The pipe is only escaped by the renderer (see test_xbar).
        items = presenter().account_items(row("C", None, error="a | b"))
        self.assertIn("Lỗi: a | b", [i.text for i in items])


class BarChoices(unittest.TestCase):
    def rows(self):
        return [row("C", usage(95.0, 10.0), label="claude"), row("C2", usage(5.0, 6.0), label="c-m"),
                row("C3", usage(7.0, 8.0), label="c-nsc"), row("C4", usage(1.0, 2.0), label="x")]

    def present(self, rows, **kw):
        return Presenter(FakeClock(), Translator("vi"), "1.2.2", REPO_URL, 70, 90, 3,
                         **kw).present(rows, None)

    def test_hidden_accounts_leave_the_title_color_and_worst_but_not_the_menu(self):
        menu = self.present(self.rows(), hidden=["claude"])
        # Four accounts would collapse to "Claude 95%"; three visible ones fit the title.
        self.assertEqual(menu.title, "C2 5/6  C3 7/8  C4 1/2")
        self.assertIsNone(menu.title_color)
        self.assertEqual(menu.worst, 8.0)
        self.assertFalse(menu.compact)
        heads = [i.text for i in menu.items if i.size == 13]
        self.assertEqual(heads, ["C: claude", "C2: c-m", "C3: c-nsc", "C4: x"])

    def test_compact_menu_starts_with_expand_and_drops_collapse(self):
        menu = self.present(self.rows()[:1], compact=True)
        self.assertTrue(menu.compact)
        self.assertEqual(menu.title, "C 95/10")  # kept for tooltips
        self.assertEqual(menu.items[0].text, "Mở rộng (hiện số liệu)")
        self.assertNotIn("Thu gọn (chỉ hiện biểu tượng)", [i.text for i in menu.items])

    def test_no_collapse_item_where_the_bar_is_always_an_icon(self):
        texts = [i.text for i in self.present(self.rows()[:1], offer_compact=False).items]
        self.assertNotIn("Thu gọn (chỉ hiện biểu tượng)", texts)


class Footer(unittest.TestCase):
    def test_links_and_refresh(self):
        menu = presenter().present([row("C", usage())], None)
        by_text = {i.text: i for i in menu.items}
        self.assertEqual(by_text["Phiên bản v1.2.2"].action.url, REPO_URL)
        self.assertNotIn("Ủng hộ tác giả", by_text)
        self.assertEqual(menu.items[-1].action.kind, REFRESH)

    def test_support_item_only_when_enabled(self):
        p = Presenter(FakeClock(), Translator("vi"), "1.2.2", REPO_URL, 70, 90, 3,
                      show_support=True)
        by_text = {i.text: i for i in p.present([row("C", usage())], None).items}
        self.assertEqual(by_text["Ủng hộ tác giả"].action.url, REPO_URL + "#support")

    def test_language_item_offers_the_other_language(self):
        vi = presenter("vi").present([row("C", usage())], None)
        en = presenter("en").present([row("C", usage())], None)
        vi_item = [i for i in vi.items if i.action and i.action.kind == LANGUAGE][0]
        en_item = [i for i in en.items if i.action and i.action.kind == LANGUAGE][0]
        self.assertEqual(vi_item.action.value, "en")
        self.assertEqual(en_item.action.value, "vi")
        self.assertNotEqual(vi_item.text, en_item.text)

    def test_english_menu(self):
        items = presenter("en").account_items(row("C", usage(72.0, 5.0)))
        self.assertEqual(items[1].text, "5-hour: 72%  (resets in 1h)")


class ShortName(unittest.TestCase):
    def test_letters(self):
        taken = set()
        self.assertEqual(short_name("claude", taken), "C")
        self.assertEqual(short_name("claude-work", taken), "W")
        self.assertEqual(short_name("Tokens", taken), "T")
        self.assertEqual(taken, {"C", "W", "T"})

    def test_collisions_get_a_digit(self):
        taken = set()
        got = [short_name(l, taken) for l in ("claude", "claude-ci", "calc", "claude_cd", "work", "wolf")]
        self.assertEqual(got, ["C", "C2", "C3", "C4", "W", "W2"])

    def test_empty_label(self):
        self.assertEqual(short_name("", set()), "?")

    def test_config_dir_labels_get_a_letter_not_a_space(self):
        # Labels from config dirs and keychain services: "claude (work)" must give "W".
        taken = set()
        self.assertEqual(short_name("claude (work)", taken), "W")
        self.assertEqual(short_name("claude (a1b2)", taken), "A")


if __name__ == "__main__":
    unittest.main()
