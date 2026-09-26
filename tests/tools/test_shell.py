"""Issue #54: controlled shell — timeout, denial, exit codes, truncation."""

from pathlib import Path

from harness.tools.policy import CommandPolicy
from harness.tools.shell import run_command


def _policy(**kwargs) -> CommandPolicy:
    kwargs.setdefault("default_timeout_seconds", 10)
    return CommandPolicy(**kwargs)


def test_successful_command_reports_exit_and_duration(tmp_path):
    result = run_command(
        "echo hello", policy=_policy(), working_dir=tmp_path
    )
    assert result.ok
    assert result.data["exit_code"] == 0
    assert "hello" in result.summary
    assert result.duration_ms >= 0
    assert result.error_kind is None


def test_nonzero_exit_is_distinct_from_spawn_failure(tmp_path):
    result = run_command(
        "false", policy=_policy(), working_dir=tmp_path
    )
    assert not result.ok
    assert result.error_kind == "nonzero_exit"
    assert result.data["exit_code"] == 1

    missing = run_command(
        "definitely-not-a-real-binary-xyz", policy=_policy(),
        working_dir=tmp_path,
    )
    assert missing.error_kind == "spawn_error"


def test_timeout_is_terminated_and_distinct(tmp_path):
    result = run_command(
        "sleep 5", policy=_policy(default_timeout_seconds=1),
        working_dir=tmp_path,
    )
    assert not result.ok
    assert result.error_kind == "timeout"
    assert "terminated" in result.summary


def test_denied_destructive_commands_never_execute(tmp_path):
    canary = tmp_path / "precious.txt"
    canary.write_text("keep me")
    policy = _policy()
    for bad in (
        "rm -rf /",
        "sudo rm precious.txt",
        "git push --force origin main",
        "echo hi | sh",
    ):
        result = run_command(bad, policy=policy, working_dir=tmp_path)
        assert not result.ok, bad
        assert result.error_kind == "denied", bad
    assert canary.read_text() == "keep me"  # nothing ran


def test_allowlist_blocks_everything_outside_it(tmp_path):
    policy = _policy(allow_patterns=[r"^python3"])
    assert run_command("python3 -c \"print(1)\"", policy=policy,
                       working_dir=tmp_path).error_kind is None
    denied = run_command("curl http://example.com", policy=policy,
                         working_dir=tmp_path)
    assert denied.error_kind == "denied"
    assert "allowlist" in denied.summary


def test_output_cap_truncates_and_persists_artifact(tmp_path):
    policy = _policy(max_output_bytes=200)
    result = run_command(
        "python3 -c \"print('x' * 5000)\"",
        policy=policy, working_dir=tmp_path, artifacts_dir=tmp_path / "art",
    )
    assert result.truncated
    assert len(result.summary) < 400
    assert result.artifacts, "full output must be persisted"
    full = Path(result.artifacts[0]).read_text()
    assert len(full) >= 5000
