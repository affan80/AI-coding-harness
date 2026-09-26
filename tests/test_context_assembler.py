"""Issue #46: progressive ranking/assembly with per-item tokens and reasons."""

from pathlib import Path

from harness.context.assembler import (
    Candidate,
    assemble_context,
    score_candidate,
)
from harness.context.manager import (
    ContextItem,
    ContextManager,
    Level,
    ModelCapabilities,
    Priority,
)


def _caps() -> ModelCapabilities:
    return ModelCapabilities(max_context_tokens=2_000, max_output_tokens=200)


def _make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "auth.py").write_text(
        "def authenticate(user, password):\n    return verify(password)\n"
    )
    (root / "helpers.py").write_text(
        "def verify(password):\n    return bool(password)\n"
    )
    (root / "billing.py").write_text("def charge(user):\n    return True\n")
    (root / "test_auth.py").write_text(
        "from auth import authenticate\n\n"
        "def test_bad_password():\n    assert not authenticate('u', 'bad')\n"
    )
    return root


def _candidates(root: Path) -> list[Candidate]:
    return [
        Candidate(
            path="auth.py",
            summary="authentication entry point",
            source=(root / "auth.py").read_text(),
        ),
        Candidate(
            path="helpers.py",
            summary="password verification helper",
            source=(root / "helpers.py").read_text(),
        ),
        Candidate(path="billing.py", summary="billing charges",
                  source=(root / "billing.py").read_text()),
        Candidate(
            path="test_auth.py", is_test=True,
            summary="tests for authentication",
            source=(root / "test_auth.py").read_text(),
        ),
    ]


def test_scoring_ranks_goal_relevant_files_above_the_rest(tmp_path):
    root = _make_repo(tmp_path)
    scored = {
        c.path: score_candidate(c, goal_text="fix authenticate bad password")
        for c in _candidates(root)
    }
    assert scored["auth.py"][0] > scored["billing.py"][0]
    assert scored["test_auth.py"][0] > scored["billing.py"][0]
    assert "matches goal terms" in scored["auth.py"][1]


def test_dependency_and_test_relationships_raise_scores(tmp_path):
    candidate = Candidate(path="helpers.py", summary="verification helper",
                          source="def verify(p):\n    return True\n")
    base, _ = score_candidate(candidate, goal_text="unrelated objective")
    boosted, reason = score_candidate(
        candidate,
        goal_text="unrelated objective",
        dependency_neighbors={"helpers.py": 2},
        test_targets={"helpers.py": 1},
    )
    assert boosted > base
    assert "dependency of selected files" in reason
    assert "has failing/affected tests" in reason


def test_scan_floor_keeps_unmatched_candidates_but_last():
    candidate = Candidate(path="unknown.py", summary="", source="")
    score, reason = score_candidate(candidate, goal_text="fix authenticate")
    assert score == 1
    assert reason == "repository scan"


def test_assembly_is_progressive_l0_l1_l2(tmp_path):
    root = _make_repo(tmp_path)
    manager = ContextManager(_caps())
    result = assemble_context(
        manager,
        root,
        _candidates(root),
        goal_text="fix authenticate bad password",
        test_targets={"auth.py": 1},
        l2_slots=2,
    )

    levels = {item.level.value for item in manager.items}
    assert levels == {"L0", "L1", "L2"}  # progressive disclosure happened
    l2_ids = [i.id for i in manager.items if i.level is Level.L2_EXACT]
    assert len(l2_ids) == 2
    # the goal-relevant file and its test are the promoted exact sources
    assert any("auth.py" in item_id for item_id in l2_ids)
    assert any("billing" not in item_id for item_id in l2_ids)
    assert result.selected


def test_every_selected_item_has_tokens_and_reason(tmp_path):
    root = _make_repo(tmp_path)
    manager = ContextManager(_caps())
    result = assemble_context(
        manager, root, _candidates(root),
        goal_text="fix authenticate", l2_slots=2,
    )

    for item in manager.items:
        assert item.tokens > 0
        assert item.reason
        assert ("score" in item.reason and ";" in item.reason) or "L0 scan" in (
            item.reason
        )
    assert result.reasons()


def test_assembly_stays_within_budget_with_p0_protected(tmp_path):
    root = _make_repo(tmp_path)
    manager = ContextManager(_caps())
    manager.add_item(ContextItem(
        "objective", "O" * 200, Priority.P0_CRITICAL, Level.L0_METADATA,
        "user objective",
    ))
    big_candidates = [
        Candidate(path=f"file_{i}.py", summary="s", source="F" * 800)
        for i in range(20)
    ]
    assemble_context(
        manager, root, big_candidates + _candidates(root),
        goal_text="fix authenticate", l2_slots=3,
    )

    assert manager.metrics.total_tokens <= manager.budget()
    assert any(i.id == "objective" for i in manager.items)


def test_unselected_candidates_are_reported(tmp_path):
    root = _make_repo(tmp_path)
    manager = ContextManager(_caps())
    result = assemble_context(
        manager, root, _candidates(root),
        goal_text="fix authenticate", l2_slots=1,
    )

    assert result.unselected
    assert all(isinstance(c, Candidate) for c in result.unselected)
    # billing.py is unrelated and should not receive exact-source budget
    assert all("billing" not in i.id for i in manager.items if i.level is Level.L2_EXACT)
