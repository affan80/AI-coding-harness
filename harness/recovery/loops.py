"""Action fingerprinting and loop detection (#65; PRD §20).

A fingerprint covers tool name + normalized arguments + result digest, so
"materially identical" attempts are recognized even when argument key order
or whitespace differs. At the configured threshold the detector blocks the
action and forces replanning instead of a fourth blind retry.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter

from harness.telemetry.models import ArtifactRef


def normalize_arguments(arguments: dict | None) -> str:
    """Canonical JSON for arguments: sorted keys, compact separators."""
    return json.dumps(arguments or {}, sort_keys=True, separators=(",", ":"), default=str)


def result_digest(result: str | bytes | None, artifact: ArtifactRef | None = None) -> str:
    """Stable digest of a tool result; artifact hashes take precedence."""
    if artifact is not None:
        return artifact.sha256
    if result is None:
        return hashlib.sha256(b"").hexdigest()
    if isinstance(result, str):
        result = result.encode("utf-8")
    return hashlib.sha256(result).hexdigest()


def action_fingerprint(
    tool_name: str,
    arguments: dict | None,
    result: str | bytes | None = None,
    artifact: ArtifactRef | None = None,
) -> str:
    """Fingerprint = tool name + normalized arguments + result hash."""
    payload = "\x1f".join(
        (tool_name, normalize_arguments(arguments), result_digest(result, artifact))
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class LoopDetector:
    """Blocks actions that repeat identically at or above the threshold."""

    def __init__(self, threshold: int = 3) -> None:
        if threshold < 2:
            raise ValueError("loop threshold must be at least 2")
        self.threshold = threshold
        self._counts: Counter[str] = Counter()

    def record(self, fingerprint: str) -> bool:
        """Record one attempt; return True when the action is now blocked."""
        self._counts[fingerprint] += 1
        return self._counts[fingerprint] >= self.threshold

    def count_for(self, fingerprint: str) -> int:
        return self._counts[fingerprint]

    def blocked(self, fingerprint: str) -> bool:
        return self._counts[fingerprint] >= self.threshold
