"""SwiftBar output of the shipped plugin, pinned byte for byte.

Run with UPDATE_GOLDEN=1 to rewrite the files in tests/golden/ (only when an output change is
intended).
"""
from __future__ import annotations

import json
import os
import time
import unittest

from fakes import FakeHome, FakeServer, Plan, run_plugin, tok

GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")
HOUR, DAY = 3600, 86400
CONF_NO_KEYCHAIN = "keychain = off\n"


def scenario_two_accounts(home: FakeHome):
    home.write(".config/claude-usage.conf", CONF_NO_KEYCHAIN + "work = ~/tokens/work.txt\n")
    home.write("tokens/work.txt", tok("WORK") + "\n")
    home.write(".zshrc", "# alias claude-old='CLAUDE_CODE_OAUTH_TOKEN=%s claude'\n"
                         "alias claude-token='CLAUDE_CODE_OAUTH_TOKEN=%s claude'\n"
               % (tok("COMMENTED"), tok("TOKEN")))
    return {
        tok("TOKEN"): Plan({"5h": (0.0, 2 * HOUR + 15 * 60), "7d": (0.65, 2 * DAY + 21 * HOUR),
                            "7d_oi": (0.01, 2 * DAY + 21 * HOUR)}),
        tok("WORK"): Plan({"5h": (0.93, 45 * 60), "7d": (0.72, 6 * DAY), "7d_oi": (0.5, 6 * DAY)},
                          status=429),
    }, "v1.2.2"


def scenario_update_and_haiku_fallback(home: FakeHome):
    home.write(".config/claude-usage.conf", CONF_NO_KEYCHAIN)
    home.write("bin/claude-ci", "#!/bin/sh\nexport CLAUDE_CODE_OAUTH_TOKEN=$(cat ~/.ci-token)\n",
               0o755)
    home.write(".ci-token", tok("CI"))
    return {tok("CI"): Plan({"5h": (0.5, HOUR), "7d": (0.2, DAY)}, fable=False)}, "v9.9.9"


def scenario_many_accounts_and_stale(home: FakeHome):
    lines = []
    for i, name in enumerate(["a1", "a2", "a3", "b1"]):
        home.write("t/%s" % name, tok(name.upper()))
        lines.append("claude-%s = ~/t/%s" % (name, name))
    home.write(".config/claude-usage.conf", CONF_NO_KEYCHAIN + "\n".join(lines) + "\n")
    # b1 fails now but has an old cached result to fall back on.
    import hashlib
    key = hashlib.sha256(tok("B1").encode()).hexdigest()[:16]
    now = int(time.time())
    p = home.write(".cache/claude-usage/%s.json" % key, json.dumps(
        {"5h": 10.0, "5h_reset": now + 3 * HOUR, "7d": 20.0, "7d_reset": now + 3 * DAY,
         "models": []}))
    os.utime(p, (now - 3600, now - 3600))
    win = {"5h": (0.1, HOUR + 30), "7d": (0.2, DAY + 30)}
    return {tok("A1"): Plan(win), tok("A2"): Plan(win), tok("A3"): Plan(win),
            tok("B1"): Plan({}, status=500)}, "v1.0.0"


def scenario_hidden_and_compact(home: FakeHome):
    plans, tag = scenario_two_accounts(home)
    # claude-token is left out of the bar, so the icon takes work's red (93%).
    home.append(".config/claude-usage.conf", "hide = claude-token\ncompact = on\n")
    return plans, tag


def scenario_no_accounts(home: FakeHome):
    home.write(".config/claude-usage.conf", CONF_NO_KEYCHAIN)
    return {}, "v1.2.2"


SCENARIOS = {
    "two_accounts": scenario_two_accounts,
    "update_and_haiku_fallback": scenario_update_and_haiku_fallback,
    "many_accounts_and_stale": scenario_many_accounts_and_stale,
    "no_accounts": scenario_no_accounts,
    "hidden_and_compact": scenario_hidden_and_compact,
}


def render(name: str, *args: str, plugin: str | None = None, extra_env=None) -> str:
    with FakeHome() as home:
        plans, tag = SCENARIOS[name](home)
        with FakeServer(plans, latest_tag=tag) as server:
            kw = {"plugin": plugin} if plugin else {}
            return run_plugin(home, server, *args, extra_env=extra_env, **kw)


class GoldenSwiftBar(unittest.TestCase):
    def check(self, name: str, lang: str = ""):
        got = render(name, extra_env={"CLAUDE_USAGE_LANG": lang} if lang else None)
        path = os.path.join(GOLDEN, "swiftbar_%s%s.txt" % (name, "_" + lang if lang else ""))
        if os.environ.get("UPDATE_GOLDEN"):
            os.makedirs(GOLDEN, exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(got)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), got)

    def test_two_accounts(self):
        self.check("two_accounts")

    def test_update_and_haiku_fallback(self):
        self.check("update_and_haiku_fallback")

    def test_many_accounts_and_stale(self):
        self.check("many_accounts_and_stale")

    def test_no_accounts(self):
        self.check("no_accounts")

    def test_hidden_and_compact(self):
        self.check("hidden_and_compact")

    def test_two_accounts_english(self):
        self.check("two_accounts", lang="en")

    def test_many_accounts_and_stale_english(self):
        self.check("many_accounts_and_stale", lang="en")


class LanguageSwitch(unittest.TestCase):
    def test_lang_flag_persists_in_conf_and_keeps_other_lines(self):
        with FakeHome() as home:
            plans, tag = scenario_two_accounts(home)
            with FakeServer(plans, latest_tag=tag) as server:
                self.assertEqual(run_plugin(home, server, "--lang", "en"), "")
                self.assertIn("5-hour: 0%", run_plugin(home, server))
                run_plugin(home, server, "--lang", "vi")
                self.assertIn("5 giờ: 0%", run_plugin(home, server))
            with open(os.path.join(home.path, ".config", "claude-usage.conf"), encoding="utf-8") as f:
                conf = f.read()
            self.assertEqual(conf, "keychain = off\nwork = ~/tokens/work.txt\nlanguage = vi\n")


class BarChoices(unittest.TestCase):
    """Picking the accounts shown in the bar and collapsing it to an icon, through the menu's
    own commands, as SwiftBar runs them."""

    def conf(self, home: FakeHome) -> str:
        with open(os.path.join(home.path, ".config", "claude-usage.conf"), encoding="utf-8") as f:
            return f.read()

    def test_hide_compact_and_back_through_the_menu_commands(self):
        with FakeHome() as home:
            plans, tag = scenario_two_accounts(home)
            with FakeServer(plans, latest_tag=tag) as server:
                before = run_plugin(home, server)
                self.assertTrue(before.startswith("T 0/65/1  W 93/72/50 | color=red"))
                self.assertIn('Hiện trên thanh menu | checked=true bash="<SELF>" param1=--hide '
                              'param2=work ', before)

                self.assertEqual(run_plugin(home, server, "--hide", "work"), "")
                hidden = run_plugin(home, server)
                self.assertTrue(hidden.startswith("T 0/65/1 |\n"), hidden[:40])
                self.assertIn("W: work", hidden)  # still in the menu, to show it again
                self.assertIn('Hiện trên thanh menu | bash="<SELF>" param1=--show param2=work ',
                              hidden)
                self.assertNotIn("H: hide", hidden)  # the setting is not read as a token

                run_plugin(home, server, "--compact", "on")
                compact = run_plugin(home, server)
                self.assertTrue(compact.startswith(
                    ' | sfimage=speedometer tooltip="T 0/65/1"\n---\n'
                    'Mở rộng (hiện số liệu) | bash="<SELF>" param1=--compact param2=off '),
                    compact[:160])

                run_plugin(home, server, "--compact", "off")
                run_plugin(home, server, "--show", "work")
                self.assertEqual(run_plugin(home, server), before)
                self.assertEqual(self.conf(home), "keychain = off\nwork = ~/tokens/work.txt\n"
                                                  "compact = off\n")
                probes = [r for r in server.requests if r[1] == "/v1/messages"]
                self.assertEqual(len(probes), 2)  # one per account: settings never re-probe

    def test_hiding_every_account_leaves_an_icon_to_click(self):
        with FakeHome() as home:
            plans, tag = scenario_two_accounts(home)
            home.append(".config/claude-usage.conf", "hide = work, claude-token\n")
            with FakeServer(plans, latest_tag=tag) as server:
                out = run_plugin(home, server)
        self.assertTrue(out.startswith(' | sfimage=speedometer tooltip=Claude\n'), out[:60])

    def test_a_label_with_spaces_round_trips(self):
        with FakeHome() as home:
            home.write(".config/claude-usage.conf", CONF_NO_KEYCHAIN + "my work = ~/t/w\n")
            home.write("t/w", tok("W"))
            plans = {tok("W"): Plan({"5h": (0.1, HOUR), "7d": (0.2, DAY)})}
            with FakeServer(plans) as server:
                self.assertIn('param1=--hide param2="my work" ', run_plugin(home, server))
                run_plugin(home, server, "--hide", "my work")
                self.assertTrue(run_plugin(home, server).startswith(" | sfimage=speedometer"))
            self.assertIn("hide = my work\n", self.conf(home))


class RejectedToken(unittest.TestCase):
    """Bug fixed after v1.3.1: an expired token showed only "no rate-limit headers"."""

    def test_the_menu_shows_the_api_reason_and_the_token_is_probed_once(self):
        expired = "OAuth access token has expired. Re-authenticate to continue."
        with FakeHome() as home:
            home.write(".config/claude-usage.conf", CONF_NO_KEYCHAIN + "old = ~/t/old\n"
                                                    "ok = ~/t/ok\n")
            home.write("t/old", tok("OLD"))
            home.write("t/ok", tok("OK"))
            plans = {tok("OLD"): Plan(status=401, error=expired),
                     tok("OK"): Plan({"5h": (0.1, HOUR), "7d": (0.2, DAY)})}
            with FakeServer(plans) as server:
                out = run_plugin(home, server, extra_env={"CLAUDE_USAGE_LANG": "en"})
                probes = [r for r in server.requests if r[1] == "/v1/messages"]
        self.assertIn("Error: RuntimeError: HTTP 401: " + expired, out)
        self.assertIn("5-hour: 10%", out)  # the other account is unaffected
        self.assertEqual(len(probes), 2)  # the rejected token is not retried on another model


class NoFiveHourLimit(unittest.TestCase):
    """Bug fixed after v1.3.2: a verified Ultra account has no 5-hour limit, so the API sends
    only the weekly headers, and the menu showed stale data with "no rate-limit headers"."""

    def test_the_menu_shows_the_weekly_usage_and_no_five_hour_limit(self):
        with FakeHome() as home:
            home.write(".config/claude-usage.conf", CONF_NO_KEYCHAIN + "ultra = ~/t/ultra\n")
            home.write("t/ultra", tok("ULTRA"))
            plans = {tok("ULTRA"): Plan({"7d": (0.58, DAY), "7d_oi": (0.53, DAY)})}
            with FakeServer(plans) as server:
                out = run_plugin(home, server, extra_env={"CLAUDE_USAGE_LANG": "en"})
        lines = out.splitlines()
        self.assertEqual(lines[0], "U 58/53 |")  # no 5-hour number, not a 0
        self.assertNotIn("Error", out)
        self.assertIn("5-hour: no limit", lines)
        self.assertIn("Weekly: 58%  (resets in 1d)", lines)
        self.assertIn("Fable (weekly): 53%  (resets in 1d)", lines)


if __name__ == "__main__":
    unittest.main()
