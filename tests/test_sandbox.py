import pytest

from tvastar import SecurityViolation
from tvastar.sandbox import SecurityPolicy, VirtualSandbox


async def test_virtual_sandbox_echo_and_redirect():
    sb = VirtualSandbox()
    r = await sb.exec("echo hello > out.txt")
    assert r.ok
    r = await sb.exec("cat out.txt")
    assert "hello" in r.stdout


async def test_virtual_sandbox_chaining():
    sb = VirtualSandbox()
    r = await sb.exec("echo a > f.txt && cat f.txt")
    assert "a" in r.stdout


async def test_virtual_sandbox_grep():
    sb = VirtualSandbox({"a.txt": "foo\nbar\nbaz"})
    r = await sb.exec("grep ba a.txt")
    assert "bar" in r.stdout and "baz" in r.stdout


async def test_unknown_command():
    sb = VirtualSandbox()
    r = await sb.exec("frobnicate")
    assert r.exit_code == 127


async def test_security_policy_blocks_denied():
    sb = VirtualSandbox(policy=SecurityPolicy(denied_commands={"echo"}))
    with pytest.raises(SecurityViolation):
        await sb.exec("echo nope")


async def test_security_policy_allowlist():
    sb = VirtualSandbox(policy=SecurityPolicy(allowed_commands={"cat"}))
    with pytest.raises(SecurityViolation):
        await sb.exec("echo blocked")


def test_path_traversal_blocked():
    from tvastar.filesystem.base import normalize

    with pytest.raises(SecurityViolation):
        normalize("../etc/passwd")
