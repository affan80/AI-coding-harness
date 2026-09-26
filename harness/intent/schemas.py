"""Typed intent and goal-graph schemas (issue #33; PRD §§6, 9, 14; FR-03).

The model's intent output is parsed into these frozen dataclasses — never
into prose. One serialized graph can represent mixed create / fix / audit /
refactor / test / verify objectives because every goal carries an explicit
:class:`GoalKind`. Validation is structural: unknown dependencies, cycles,
missing acceptance/verification criteria, and dangling constraint
references are rejected before anything reaches planning.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from harness.core.errors import HarnessError, SerializationError
from harness.core.models import EvidenceRef, GoalStatus


class IntentError(HarnessError):
    """Intent extraction failed after bounded correction retries."""

    code = "intent_failed"


class GoalKind(StrEnum):
    """The strategy a goal represents — the model infers it, never the user."""

    CREATE = "create"
    FEATURE = "feature"
    FIX = "fix"
    AUDIT = "audit"
    REFACTOR = "refactor"
    OPTIMIZE = "optimize"
    TEST = "test"
    VERIFY = "verify"


GOAL_KIND_VALUES = frozenset(k.value for k in GoalKind)


@dataclass(frozen=True)
class Constraint:
    """A restriction the work must respect."""

    constraint_id: str
    description: str
    source: str = "user"  # "user" | "inferred"

    def to_dict(self) -> dict[str, Any]:
        return {
            "constraint_id": self.constraint_id,
            "description": self.description,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Constraint:
        return cls(
            constraint_id=_require_str(data, "constraint_id", "Constraint"),
            description=_require_str(data, "description", "Constraint"),
            source=data.get("source", "user"),
        )


@dataclass(frozen=True)
class Assumption:
    """Something the model inferred that the user did not state."""

    assumption_id: str
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "assumption_id": self.assumption_id,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Assumption:
        return cls(
            assumption_id=_require_str(data, "assumption_id", "Assumption"),
            description=_require_str(data, "description", "Assumption"),
        )


@dataclass(frozen=True)
class Goal:
    """One goal with explicit dependencies, criteria, and evidence."""

    goal_id: str
    title: str
    kind: GoalKind
    status: GoalStatus = GoalStatus.PENDING
    description: str = ""
    depends_on: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    verification_criteria: tuple[str, ...] = ()
    constraint_ids: tuple[str, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()
    is_assumption: bool = False
    """True when this goal encodes an inference, not a user requirement."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "title": self.title,
            "kind": self.kind.value,
            "status": self.status.value,
            "description": self.description,
            "depends_on": list(self.depends_on),
            "acceptance_criteria": list(self.acceptance_criteria),
            "verification_criteria": list(self.verification_criteria),
            "constraint_ids": list(self.constraint_ids),
            "evidence": [ref.to_dict() for ref in self.evidence],
            "is_assumption": self.is_assumption,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Goal:
        try:
            kind = GoalKind(data.get("kind", ""))
        except ValueError:
            raise SerializationError(
                f"unknown goal kind {data.get('kind')!r}",
                details={"field": "kind", "model": "Goal"},
            ) from None
        try:
            status = GoalStatus(data.get("status", GoalStatus.PENDING.value))
        except ValueError:
            raise SerializationError(
                f"unknown goal status {data.get('status')!r}",
                details={"field": "status", "model": "Goal"},
            ) from None
        return cls(
            goal_id=_require_str(data, "goal_id", "Goal"),
            title=_require_str(data, "title", "Goal"),
            kind=kind,
            status=status,
            description=data.get("description", ""),
            depends_on=tuple(data.get("depends_on", ())),
            acceptance_criteria=tuple(data.get("acceptance_criteria", ())),
            verification_criteria=tuple(data.get("verification_criteria", ())),
            constraint_ids=tuple(data.get("constraint_ids", ())),
            evidence=tuple(
                EvidenceRef.from_dict(item) for item in data.get("evidence", ())
            ),
            is_assumption=bool(data.get("is_assumption", False)),
        )


@dataclass(frozen=True)
class GoalGraph:
    """The structured intent output: ordered goals, constraints, assumptions."""

    objective: str
    goals: tuple[Goal, ...] = ()
    constraints: tuple[Constraint, ...] = ()
    assumptions: tuple[Assumption, ...] = ()
    completion_criteria: tuple[str, ...] = ()
    root_goal_id: str = ""

    def goal(self, goal_id: str) -> Goal:
        for goal in self.goals:
            if goal.goal_id == goal_id:
                return goal
        raise KeyError(goal_id)

    def pending_goals(self) -> tuple[Goal, ...]:
        return tuple(g for g in self.goals if g.status is GoalStatus.PENDING)

    def user_constraints(self) -> tuple[Constraint, ...]:
        return tuple(c for c in self.constraints if c.source == "user")

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "root_goal_id": self.root_goal_id,
            "goals": [goal.to_dict() for goal in self.goals],
            "constraints": [c.to_dict() for c in self.constraints],
            "assumptions": [a.to_dict() for a in self.assumptions],
            "completion_criteria": list(self.completion_criteria),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> GoalGraph:
        graph = cls(
            objective=_require_str(data, "objective", "GoalGraph"),
            goals=tuple(Goal.from_dict(item) for item in data.get("goals", ())),
            constraints=tuple(
                Constraint.from_dict(item) for item in data.get("constraints", ())
            ),
            assumptions=tuple(
                Assumption.from_dict(item) for item in data.get("assumptions", ())
            ),
            completion_criteria=tuple(data.get("completion_criteria", ())),
            root_goal_id=data.get("root_goal_id", ""),
        )
        problems = graph.validate()
        if problems:
            raise SerializationError(
                "goal graph failed validation",
                details={"field": "goals", "problems": problems},
            )
        return graph

    def validate(self) -> list[str]:
        """Structural problems; empty list means the graph is well-formed."""
        problems: list[str] = []
        ids = [g.goal_id for g in self.goals]
        if not self.goals:
            problems.append("goal graph has no goals")
        if len(ids) != len(set(ids)):
            problems.append("duplicate goal ids")
        id_set = set(ids)
        if self.root_goal_id and self.root_goal_id not in id_set:
            problems.append(f"root goal {self.root_goal_id!r} is not in goals")
        known_constraints = {c.constraint_id for c in self.constraints}
        for goal in self.goals:
            for dep in goal.depends_on:
                if dep not in id_set:
                    problems.append(
                        f"goal {goal.goal_id} depends on unknown goal {dep}"
                    )
            for cid in goal.constraint_ids:
                if cid not in known_constraints:
                    problems.append(
                        f"goal {goal.goal_id} references unknown constraint {cid}"
                    )
            if not goal.acceptance_criteria:
                problems.append(f"goal {goal.goal_id} has no acceptance criteria")
            if not goal.verification_criteria:
                problems.append(f"goal {goal.goal_id} has no verification criteria")
        if _has_cycle(self.goals):
            problems.append("goal dependencies contain a cycle")
        return problems


def parse_goal_payload(payload: Any) -> GoalGraph:
    """Parse raw model output into a validated GoalGraph (#34 feeds this)."""
    if not isinstance(payload, Mapping):
        raise SerializationError(
            "intent output must be a JSON object",
            details={"field": "payload", "value": repr(type(payload).__name__)},
        )
    data = dict(payload)
    goals = data.get("goals")
    if not goals:
        raise SerializationError(
            "intent output contains no goals",
            details={"field": "goals"},
        )
    return GoalGraph.from_dict(data)


def _has_cycle(goals: tuple[Goal, ...]) -> bool:
    edges = {g.goal_id: g.depends_on for g in goals}
    state: dict[str, int] = {}

    def visit(node: str, stack: tuple[str, ...]) -> bool:
        if node in stack:
            return True
        if state.get(node):
            return False
        state[node] = 1
        for dep in edges.get(node, ()):
            if dep in edges and visit(dep, stack + (node,)):
                return True
        return False

    return any(visit(node, ()) for node in edges)


def _require_str(data: Mapping[str, Any], key: str, model: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SerializationError(
            f"missing or empty field {key!r} on {model}",
            details={"field": key, "model": model},
        )
    return value
