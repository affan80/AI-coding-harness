"""Intent engine: objective -> validated GoalGraph (issue #34; PRD §§6, 9).

Prompts the shared model client to infer ordered goals with kinds, then:

* validates the structured output against the #33 schemas;
* retries bounded corrections when the model returns malformed output;
* attaches explicit user scope and constraints to every relevant goal as
  first-class constraint references (the model never owns user facts);
* keeps inferred assumptions separately labeled from user requirements.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from harness.core.errors import SerializationError
from harness.core.models import EvidenceRef, UserRequest
from harness.intent.schemas import (
    Constraint,
    Goal,
    GoalGraph,
    IntentError,
    parse_goal_payload,
)
from harness.model.types import Message, ModelClient, ResponseSchema

MAX_CORRECTION_RETRIES = 1

SYSTEM_PROMPT = """\
You convert one natural-language software-engineering objective into a \
structured goal graph. Infer the strategy yourself (create, fix, audit, \
refactor, optimize, test, verify, feature); never ask the user to pick a mode.

Return JSON matching the goal_graph schema. Rules:
- Order goals so dependencies come first; depends_on references goal_ids.
- Every goal needs at least one acceptance criterion and one verification \
criterion.
- Only restate what the user asked for in goals and user-source constraints.
- Anything you inferred that the user did not state belongs in assumptions \
(or a goal with is_assumption=true).
"""

_RESPONSE_SCHEMA = ResponseSchema(
    name="goal_graph",
    schema={
        "type": "object",
        "required": ["objective", "goals"],
        "properties": {
            "objective": {"type": "string"},
            "root_goal_id": {"type": "string"},
            "goals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["goal_id", "title", "kind",
                                 "acceptance_criteria", "verification_criteria"],
                    "properties": {
                        "goal_id": {"type": "string"},
                        "title": {"type": "string"},
                        "kind": {
                            "type": "string",
                            "enum": ["create", "feature", "fix", "audit",
                                     "refactor", "optimize", "test", "verify"],
                        },
                        "description": {"type": "string"},
                        "depends_on": {"type": "array",
                                       "items": {"type": "string"}},
                        "acceptance_criteria": {"type": "array",
                                                "items": {"type": "string"}},
                        "verification_criteria": {"type": "array",
                                                  "items": {"type": "string"}},
                        "is_assumption": {"type": "boolean"},
                    },
                },
            },
            "constraints": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["constraint_id", "description"],
                    "properties": {
                        "constraint_id": {"type": "string"},
                        "description": {"type": "string"},
                        "source": {"type": "string",
                                   "enum": ["user", "inferred"]},
                    },
                },
            },
            "assumptions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["assumption_id", "description"],
                    "properties": {
                        "assumption_id": {"type": "string"},
                        "description": {"type": "string"},
                    },
                },
            },
            "completion_criteria": {"type": "array", "items": {"type": "string"}},
        },
    },
)


def intent_response_schema() -> ResponseSchema:
    """The schema handed to the model client for structured output."""
    return _RESPONSE_SCHEMA


class IntentEngine:
    """Extracts a validated GoalGraph with bounded correction retries."""

    def __init__(
        self,
        client: ModelClient,
        *,
        max_correction_retries: int = MAX_CORRECTION_RETRIES,
        register_retry: Callable[[str], bool] | None = None,
    ) -> None:
        self._client = client
        self._max_retries = max_correction_retries
        self._register_retry = register_retry

    async def extract(self, request: UserRequest) -> GoalGraph:
        messages = self._initial_messages(request)
        attempts = self._max_retries + 1
        last_problems: list[str] = []
        attempts_evidence: list[EvidenceRef] = []

        for attempt in range(attempts):
            response = await self._client.generate(
                messages, response_schema=intent_response_schema()
            )
            payload = response.structured
            if payload is None:
                last_problems = ["model returned no structured output"]
            else:
                try:
                    graph = self._finalize(request, payload)
                    return graph
                except SerializationError as exc:
                    last_problems = list(exc.details.get("problems", [])) or [
                        exc.message
                    ]
            attempts_evidence.append(
                EvidenceRef(
                    kind="model_response",
                    description=(
                        f"intent attempt {attempt + 1} rejected by schema "
                        f"validation: {'; '.join(last_problems)}"
                    ),
                    metadata={
                        "attempt": attempt + 1,
                        "problems": list(last_problems),
                    },
                )
            )
            if attempt >= attempts - 1 or not self._spend_retry():
                break
            messages = [
                *messages,
                Message(role="assistant", content=json.dumps(payload)),
                Message(
                    role="user",
                    content="That JSON was rejected by schema validation. Fix "
                    "these problems and return the full corrected JSON object:\n- "
                    + "\n- ".join(last_problems),
                ),
            ]

        raise IntentError(
            "intent extraction returned malformed output after "
            f"{attempts} attempt(s)",
            details={
                "validation_problems": last_problems,
                # Structured failure WITH evidence (issue #35): every rejected
                # attempt is referenceable, so planning never sees it.
                "evidence": [ref.to_dict() for ref in attempts_evidence],
                "reached_planning": False,
            },
        )

    # -- internals -------------------------------------------------------------

    def _spend_retry(self) -> bool:
        if self._register_retry is None:
            return True  # unmanaged mode; retries allowed
        return self._register_retry("intent")

    @staticmethod
    def _initial_messages(request: UserRequest) -> list[Message]:
        parts = [f"Objective:\n{request.objective}"]
        if request.scope_paths:
            parts.append(
                "Approved scope (writes outside these paths are forbidden): "
                + ", ".join(request.scope_paths)
            )
        if request.constraints:
            parts.append(
                "User constraints:\n"
                + "\n".join(f"- {c}" for c in request.constraints)
            )
        return [
            Message(role="system", content=SYSTEM_PROMPT),
            Message(role="user", content="\n\n".join(parts)),
        ]

    def _finalize(self, request: UserRequest, payload: Any) -> GoalGraph:
        graph = parse_goal_payload(payload)
        return _attach_user_facts(graph, request)


def _attach_user_facts(graph: GoalGraph, request: UserRequest) -> GoalGraph:
    """Attach user scope and constraints to every goal as explicit references.

    User facts belong to the request, not the model. Each approved scope path
    and each user-stated constraint becomes a first-class constraint, and
    every goal in the graph references them so planning inherits the limits.
    """
    constraints = list(graph.constraints)
    user_constraint_ids = [
        c.constraint_id for c in constraints if c.source == "user"
    ]
    for index, scope_path in enumerate(request.scope_paths):
        constraint_id = f"scope-{index + 1}"
        if constraint_id not in {c.constraint_id for c in constraints}:
            constraints.append(
                Constraint(
                    constraint_id=constraint_id,
                    description=f"approved scope: {scope_path}",
                    source="user",
                )
            )
            user_constraint_ids.append(constraint_id)
    for index, text in enumerate(request.constraints):
        constraint_id = f"user-{index + 1}"
        if constraint_id not in {c.constraint_id for c in constraints}:
            constraints.append(
                Constraint(constraint_id=constraint_id, description=text,
                           source="user")
            )
            user_constraint_ids.append(constraint_id)

    goals = tuple(
        Goal(
            goal_id=g.goal_id,
            title=g.title,
            kind=g.kind,
            status=g.status,
            description=g.description,
            depends_on=g.depends_on,
            acceptance_criteria=g.acceptance_criteria,
            verification_criteria=g.verification_criteria,
            constraint_ids=tuple(
                dict.fromkeys(g.constraint_ids + tuple(user_constraint_ids))
            ),
            evidence=g.evidence,
            is_assumption=g.is_assumption,
        )
        for g in graph.goals
    )
    return GoalGraph(
        objective=graph.objective,
        goals=goals,
        constraints=tuple(constraints),
        assumptions=graph.assumptions,
        completion_criteria=graph.completion_criteria,
        root_goal_id=graph.root_goal_id,
    )


__all__ = ["IntentEngine", "intent_response_schema"]
