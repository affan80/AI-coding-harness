import pytest
from harness.tools.policy import CommandPolicy, actor_allowed

def test_command_policy_deny():
    policy = CommandPolicy()
    allowed, reason = policy.check("rm -rf /")
    assert not allowed
    assert "matches destructive policy" in reason

def test_command_policy_allow_list():
    policy = CommandPolicy(allow_patterns=[r"^ls", r"^cat"])
    allowed, reason = policy.check("ls -la")
    assert allowed
    assert reason == ""

    allowed, reason = policy.check("echo hello")
    assert not allowed
    assert "does not match the configured allowlist" in reason

def test_command_policy_default_allow():
    policy = CommandPolicy(allow_patterns=[])
    allowed, reason = policy.check("echo hello")
    assert allowed
    assert reason == ""

def test_actor_allowed():
    assert actor_allowed("executor", "shell")
    assert not actor_allowed("intent", "shell")
    assert not actor_allowed("unknown_actor", "shell")
