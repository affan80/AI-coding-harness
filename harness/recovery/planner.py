"""Bounded recovery planning through the shared model client (#64).

The model receives exactly the :class:`FailureEvidence` bundle and the
allowed scope — never the run history. Plans come back as JSON, are parsed
strictly, and are rejected unless every repair target is inside the allowed
scope and the required re-verification stages are present.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Protocol

from harness.recovery.models import FailureClass, FailureEvidence, RecoveryPlan, RepairAction
from harness.recovery.reverify import reverification_sequence
from harness.verification.models import StageName


class RecoveryModelClient(Protocol):
    """Minimal slice of the shared model client contract (#6 stays open)."""

    async def generate(self, messages: list[dict], *, response_schema: dict | None = None) -> str:
        ...


RecoveryClientFactory = Callable[[], RecoveryModelClient]


class RecoveryPlanError(Exception):
    """The model client returned an unusable or out-of-scope recovery plan."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.details: dict = {}


def build_recovery_prompt(evidence: FailureEvidence, allowed_scope: Sequence[str]) -> str:
    """Serialize ONLY the focused evidence bundle into a recovery prompt."""
    return json.dumps(
        {
            "task": (
                "Propose a minimal repair for this failure. Respond with JSON: "
                '{"failure_class": str, "root_cause": str, "repair_summary": str, '
                '"repair_actions": [{"kind": "patch"|"command"|"inspect", '
                '"target": str, "detail": str}], '
                '"reverify_stages": [str...]}'
            ),
            "failure": evidence.to_dict(),
            "allowed_scope": list(allowed_scope) or ["<entire repository>"],
            "constraints": [
                "repairs must stay within the allowed scope",
                "reverify_stages must start with the failed stage, then widen",
                "propose materially different repairs from prior attempts",
            ],
        },
        indent=2,
        sort_keys=True,
    )


def parse_recovery_plan(
    raw: str,
    evidence: FailureEvidence,
    allowed_scope: Sequence[str],
) -> RecoveryPlan:
    """Parse and validate a model-produced recovery plan.

    Raises :class:`RecoveryPlanError` on invalid JSON, unknown stages,
    missing re-verification of the failed stage, or out-of-scope targets.
    """
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise RecoveryPlanError(f"recovery plan is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RecoveryPlanError("recovery plan must be a JSON object")

    try:
        failure_class = FailureClass(data["failure_class"])
        root_cause = str(data["root_cause"]).strip()
        repair_summary = str(data["repair_summary"]).strip()
        actions = tuple(RepairAction.from_dict(a) for a in data["repair_actions"])
        reverify = tuple(str(s) for s in data["reverify_stages"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RecoveryPlanError(f"recovery plan is missing or malformed: {exc}") from exc

    if not root_cause or not repair_summary:
        raise RecoveryPlanError("recovery plan has an empty root cause or repair summary")

    known_stages = {stage.value for stage in StageName}
    unknown = [s for s in reverify if s not in known_stages]
    if unknown:
        raise RecoveryPlanError(f"unknown reverify stages: {', '.join(unknown)}")

    if evidence.stage is not None:
        expected_first = reverification_sequence(StageName(evidence.stage))
        if tuple(reverify) != tuple(s.value for s in expected_first):
            raise RecoveryPlanError(
                "reverify_stages must rerun the failed stage first, then widen: "
                f"expected {[s.value for s in expected_first]}"
            )

    for action in actions:
        if action.kind not in ("patch", "command", "inspect"):
            raise RecoveryPlanError(f"unknown repair action kind: {action.kind}")
        if allowed_scope and not _within_scope(action.target, allowed_scope):
            raise RecoveryPlanError(
                f"repair target {action.target!r} is outside the allowed scope"
            )

    return RecoveryPlan(
        failure_class=failure_class,
        root_cause=root_cause,
        repair_summary=repair_summary,
        repair_actions=actions,
        reverify_stages=reverify,
        within_scope=True,
    )


async def propose_recovery_plan(
    evidence: FailureEvidence,
    model_client: RecoveryModelClient,
    allowed_scope: Sequence[str] = (),
) -> RecoveryPlan:
    """Ask the model client for a repair plan over the narrow evidence only."""
    prompt = build_recovery_prompt(evidence, allowed_scope)
    raw = await model_client.generate(
        [{"role": "user", "content": prompt}],
        response_schema={"type": "object"},
    )
    return parse_recovery_plan(raw, evidence, allowed_scope)


def _within_scope(target: str, allowed_scope: Sequence[str]) -> bool:
    return any(
        target == scope or target.startswith(scope.rstrip("/") + "/") for scope in allowed_scope
    )
