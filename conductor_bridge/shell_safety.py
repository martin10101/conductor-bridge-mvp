"""Shell safety policy for `run_shell_command`.

Design goals:
- Default-deny: only allow a small, read-only command subset.
- Avoid parsing full PowerShell; use high-signal heuristics.
- Keep behavior deterministic and testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ShellPolicyDecision:
    allowed: bool
    reason: str = ""


_DISALLOWED_TOKENS = [
    (r"(?i)\b(remove-item|del|erase|rm|rmdir|rd)\b", "delete files"),
    (r"(?i)\bformat\b", "format disk"),
    (r"(?i)\bdiskpart\b", "disk partitioning"),
    (r"(?i)\breg(\.exe)?\b", "registry editing"),
    (r"(?i)\bshutdown\b|\brestart-computer\b|\bstop-computer\b", "shutdown/restart"),
    (r"(?i)\btaskkill\b|\bstop-process\b", "kill processes"),
    (r"(?i)\binvoke-expression\b|\biex\b", "execute dynamic code"),
    (r"(?i)\bgit\s+clean\b", "destructive git clean"),
    (r"(?i)\bgit\s+reset\s+--hard\b", "destructive git reset"),
    (r"(?i)\bgit\s+push\b", "git push"),
]


_ALLOWED_PREFIXES = [
    # Git (read-only)
    r"(?i)^\s*git\s+(status|diff|log|show|rev-parse|branch)\b",
    # File listing (read-only)
    r"(?i)^\s*(ls|dir)\b",
    r"(?i)^\s*get-childitem\b",
    # File reading (read-only)
    r"(?i)^\s*get-content\b",
    r"(?i)^\s*type\b",
    # Grep-like search (read-only)
    r"(?i)^\s*select-string\b",
    # Misc read-only info
    r"(?i)^\s*pwd\b",
]


def decide_shell_command(command: str) -> ShellPolicyDecision:
    if not isinstance(command, str) or not command.strip():
        return ShellPolicyDecision(False, "command must be a non-empty string")

    cmd = command.strip()
    if len(cmd) > 8000:
        return ShellPolicyDecision(False, "command too long")

    # Block multi-statement / composition. This is the single biggest risk reducer.
    if any(tok in cmd for tok in ("\n", "\r", ";", "&&", "||", "|")):
        return ShellPolicyDecision(False, "compound commands are not allowed")

    # Block redirection / file writes.
    if any(tok in cmd for tok in (">", "<")):
        return ShellPolicyDecision(False, "redirection is not allowed")

    for pat, why in _DISALLOWED_TOKENS:
        if re.search(pat, cmd):
            return ShellPolicyDecision(False, f"blocked potentially dangerous command ({why})")

    if any(re.search(pat, cmd) for pat in _ALLOWED_PREFIXES):
        return ShellPolicyDecision(True, "")

    return ShellPolicyDecision(False, "command not in allowlist")

