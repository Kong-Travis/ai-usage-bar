"""Product constants and the settings a test or a power user can override."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

VERSION = "1.4.1"
REPO = "Kong-Travis/ai-usage-bar"
PLUGIN_PATH_IN_REPO = "plugin/claude-usage.1m.py"
REPO_URL = "https://github.com/" + REPO

WARN_PCT, CRIT_PCT = 70, 90
PROBE_EVERY = 180      # each probe costs a few tokens of quota
EMAIL_EVERY = 86400
UPDATE_EVERY = 3600  # one GitHub call an hour; the unauthenticated limit is 60 per IP
MAX_IN_TITLE = 4
# Fable first: only a Fable request returns the per-model weekly windows.
PROBE_MODELS = ("claude-fable-5-1", "claude-haiku-5-5")
# Per-model weekly windows seen in the headers, by suffix of "7d_<suffix>".
MODEL_WINDOWS = {"oi": "Fable"}
KEYCHAIN_SERVICE = "Claude Code-credentials"
# The "Support the author" menu item, off until the new donation account is ready.
SHOW_SUPPORT = False


@dataclass(frozen=True)
class Endpoints:
    api: str = "https://api.anthropic.com"
    github_api: str = "https://api.github.com"
    github_raw: str = "https://raw.githubusercontent.com"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Endpoints:
        default = cls()
        return cls(
            api=env.get("CLAUDE_USAGE_API", default.api),
            github_api=env.get("CLAUDE_USAGE_GITHUB", default.github_api),
            github_raw=env.get("CLAUDE_USAGE_GITHUB_RAW", default.github_raw),
        )
