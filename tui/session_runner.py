"""M1 session pipeline: request to evidence-backed (partial) result.

The full orchestrator and planning arrive with later milestones (#1, #5,
#10). This runner executes the honest M1 prefix of the PRD state machine:
accept the request, start the evidence run directory, inspect the repository
with the real profiler, persist everything, and stop with a PARTIAL status
that says exactly why. Nothing claims verification that did not happen.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from harness.core.models import SessionStatus, UserRequest
from harness.repository import RepositoryProfile, profile_repository
from harness.telemetry import RunStore
from tui.render import Renderer, SessionResult


def run_session(
    request: UserRequest,
    runs_root: str | Path = "runs",
    renderer: Renderer | None = None,
    profiler: Callable[[str], RepositoryProfile] | None = None,
) -> SessionResult:
    """Run the M1 pipeline and leave a reconstructible run directory."""
    renderer = renderer or Renderer()
    # Resolved at call time so tests can substitute the profiler.
    profiler = profiler or profile_repository
    store: RunStore | None = None
    try:
        store = RunStore.start(request, runs_root)
        for state in ("INITIALIZE", "UNDERSTAND", "INSPECT_REPOSITORY"):
            renderer.show_state(state)
            store.append_event("state", state, state=state)

        profile = profiler(request.repository)
        store.write_document("repository.json", profile.to_dict())
        renderer.show_info(
            f"repository: {profile.total_files} files, {profile.total_bytes} bytes"
        )
        languages = ", ".join(
            f"{lang.language} ({lang.files})" for lang in profile.languages[:5]
        )
        if languages:
            renderer.show_info(f"languages: {languages}")
        store.append_event(
            "info",
            f"profiled {profile.total_files} files",
            data={
                "total_files": profile.total_files,
                "total_bytes": profile.total_bytes,
                "fingerprint": profile.fingerprint,
            },
        )

        stop_reason = (
            "M1 pipeline stops after repository inspection; "
            "planning and execution arrive with later milestones"
        )
        store.write_verification(
            {"status": "not_run", "checks": [], "notes": [stop_reason]}
        )
        status = SessionStatus.PARTIAL
        store.finalize(status, stop_reason=stop_reason)
        return SessionResult(
            status=status,
            limitations=[stop_reason],
            report_path=str(store.run_dir),
        )
    except KeyboardInterrupt:
        return _finalize_failure(
            store, renderer, SessionStatus.CANCELLED, "interrupted by user"
        )
    except Exception as exc:  # noqa: BLE001 - surfaced as session failure evidence
        return _finalize_failure(
            store, renderer, SessionStatus.FAILED, f"{type(exc).__name__}: {exc}"
        )


def _finalize_failure(
    store: RunStore | None,
    renderer: Renderer,
    status: SessionStatus,
    reason: str,
) -> SessionResult:
    """Best-effort terminal state; never masks the original failure."""
    if store is not None:
        try:
            store.finalize(status, stop_reason=reason)
        except OSError:
            pass
    renderer.show_info(f"session {status.value}: {reason}")
    return SessionResult(
        status=status,
        limitations=[reason],
        report_path=str(store.run_dir) if store is not None else None,
    )
