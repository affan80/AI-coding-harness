"""Command policy and tool permissions (PRD §§7, 10, 20).

Destructive commands require a policy decision, not raw model discretion:
anything matching the deny list never executes, and when an allowlist is
configured, everything outside it is denied as well.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

DEFAULT_DENY_PATTERNS = [
    r"\brm\s+(-[a-zA-Z]*\s+)*-?[rfR]{2,}",  # rm -rf and friends
    r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f",
    r"\bsudo\b",
    r"\bshutdown\b|\breboot\b|\bhalt\b|\bpoweroff\b",
    r"\bmkfs\b|\bfdisk\b|\bdiskutil\s+erase",
    r"\bdd\s+if=",
    r":\(\)\s*\{.*\};\s*:",  # fork bomb
    r"\bgit\s+push\s+.*--force",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+clean\s+-[a-zA-Z]*f",
    r"\bchmod\s+-R\s+777\s+/",
    r"\bcurl\b[^|]*\|\s*(ba)?sh",  # curl | sh
    r"\bwget\b[^|]*\|\s*(ba)?sh",
    # Commands run without a shell, so pipes would be passed as literal
    # arguments — deny them rather than let them silently misbehave.
    r"\|",
    r">\s*/dev/sd[a-z]",
]

# Environment variables a sanitized child still needs to run ordinary tools:
# PATH for executable lookup, HOME/TMPDIR for well-behaved CLIs, LANG/LC_ALL
# and TERM for sane output encoding. Everything else in the harness
# environment (tokens, API keys) stays behind the policy boundary.
SANITIZED_ENV_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TERM")


@dataclass
class CommandPolicy:
    """Decides whether a shell command may run, and what environment it sees."""

    deny_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_DENY_PATTERNS))
    allow_patterns: list[str] = field(default_factory=list)  # empty = allow rest
    max_output_bytes: int = 20_000
    default_timeout_seconds: float = 120.0
    # Environment policy (issue #54): the child starts from a minimal allowlist
    # by default, so harness secrets never reach model-suggested commands.
    env_allowlist: list[str] = field(
        default_factory=lambda: list(SANITIZED_ENV_KEYS)
    )
    env_extra: dict[str, str] = field(default_factory=dict)
    inherit_environment: bool = False

    def build_env(self, parent: Mapping[str, str] | None = None) -> dict[str, str]:
        """Child-process environment per policy: sanitized unless told otherwise."""
        parent_env = os.environ if parent is None else parent
        if self.inherit_environment:
            env = dict(parent_env)
        else:
            env = {
                key: parent_env[key]
                for key in self.env_allowlist
                if key in parent_env
            }
        env.update(self.env_extra)
        return env

    def check(self, command: str) -> tuple[bool, str]:
        """Return (allowed, reason). Denied commands are never executed."""
        for pattern in self.deny_patterns:
            if re.search(pattern, command):
                return False, f"command matches destructive policy: {pattern}"
        if self.allow_patterns:
            for pattern in self.allow_patterns:
                if re.search(pattern, command):
                    return True, ""
            return False, "command does not match the configured allowlist"
        return True, ""


# Per-agent tool permissions (PRD §10 agent matrix, subset used by execution).
Search = "search"
Read = "read"
Patch = "patch"
Shell = "shell"
Tests = "tests"
Git = "git"

PERMISSIONS: dict[str, set[str]] = {
    "executor": {Search, Read, Patch, Shell, Tests, Git},
    "verification": {Search, Read, Shell, Tests, Git},
    "recovery": {Search, Read, Tests, Git},
    "audit": {Search, Read, Shell, Tests, Git},
    "repository": {Search, Read, Tests, Git},
    "planner": {Search, Read, Git},
    "intent": set(),
}


def actor_allowed(actor: str, capability: str) -> bool:
    return capability in PERMISSIONS.get(actor, set())
