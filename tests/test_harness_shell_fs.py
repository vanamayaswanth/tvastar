"""Tests for harness.shell() and harness.fs operations.

Validates:
- Requirement 29.1: harness.shell() executes commands via the sandbox and returns ExecResult
- Requirement 29.2: harness.fs file read/write operations through the virtual sandbox
"""

import pytest

from tvastar import Harness, create_agent
from tvastar.model import MockModel
from tvastar.sandbox.virtual import VirtualSandbox


@pytest.fixture
def sandbox():
    """A fresh VirtualSandbox for each test."""
    return VirtualSandbox()


@pytest.fixture
def harness(sandbox):
    """A Harness wired to a shared VirtualSandbox."""
    model = MockModel(["ok"])
    agent = create_agent(
        "shell-fs-test",
        model=model,
        sandbox=lambda: sandbox,
        detect=False,
    )
    return Harness(agent)


# ── harness.shell() tests ────────────────────────────────────────────────────


async def test_shell_executes_echo_and_returns_output(harness):
    """harness.shell() executes a command and returns the rendered output string.

    Validates: Requirement 29.1
    """
    output = await harness.shell("echo hello")
    assert "hello" in output


async def test_shell_returns_error_output_for_unknown_command(harness):
    """harness.shell() returns error info for commands that don't exist.

    Validates: Requirement 29.1
    """
    output = await harness.shell("nosuchcommand")
    # Virtual sandbox returns exit code 127 for unknown commands
    assert "command not found" in output or "exit 127" in output


async def test_shell_executes_via_virtual_sandbox(sandbox, harness):
    """harness.shell() operates through the configured VirtualSandbox.

    Validates: Requirement 29.1
    """
    # Write a file via the sandbox's fs, then read it via shell
    sandbox.fs.write("test.txt", "sandbox content")
    output = await harness.shell("cat test.txt")
    assert "sandbox content" in output


async def test_shell_with_redirect_creates_file(harness):
    """harness.shell() supports shell redirection to create files.

    Validates: Requirement 29.1
    """
    await harness.shell("echo data > output.txt")
    output = await harness.shell("cat output.txt")
    assert "data" in output


async def test_shell_command_chaining(harness):
    """harness.shell() supports && chained commands.

    Validates: Requirement 29.1
    """
    output = await harness.shell("echo first > a.txt && cat a.txt")
    assert "first" in output


# ── harness.fs tests ─────────────────────────────────────────────────────────


async def test_fs_write_and_read_file(harness):
    """harness.fs can write and read files through the virtual sandbox.

    Validates: Requirement 29.2
    """
    await harness.fs.write_file("hello.txt", "world")
    content = await harness.fs.read_file("hello.txt")
    assert content == "world"


async def test_fs_read_nonexistent_raises(harness):
    """harness.fs.read_file raises FileNotFoundError for missing files.

    Validates: Requirement 29.2
    """
    with pytest.raises(FileNotFoundError):
        await harness.fs.read_file("does_not_exist.txt")


async def test_fs_exists(harness):
    """harness.fs.exists returns correct boolean for file presence.

    Validates: Requirement 29.2
    """
    assert not await harness.fs.exists("new.txt")
    await harness.fs.write_file("new.txt", "content")
    assert await harness.fs.exists("new.txt")


async def test_fs_list_dir(harness):
    """harness.fs.list_dir lists files in the sandbox workspace.

    Validates: Requirement 29.2
    """
    await harness.fs.write_file("a.txt", "aaa")
    await harness.fs.write_file("b.txt", "bbb")
    entries = await harness.fs.list_dir(".")
    assert "a.txt" in entries
    assert "b.txt" in entries


async def test_fs_delete_file(harness):
    """harness.fs.delete_file removes a file from the sandbox.

    Validates: Requirement 29.2
    """
    await harness.fs.write_file("temp.txt", "temporary")
    assert await harness.fs.exists("temp.txt")
    await harness.fs.delete_file("temp.txt")
    assert not await harness.fs.exists("temp.txt")


async def test_fs_overwrite_file(harness):
    """harness.fs.write_file overwrites existing file content.

    Validates: Requirement 29.2
    """
    await harness.fs.write_file("data.txt", "version1")
    await harness.fs.write_file("data.txt", "version2")
    content = await harness.fs.read_file("data.txt")
    assert content == "version2"


async def test_fs_write_nested_path(harness):
    """harness.fs supports writing files to nested paths.

    Validates: Requirement 29.2
    """
    await harness.fs.write_file("dir/sub/file.txt", "nested")
    content = await harness.fs.read_file("dir/sub/file.txt")
    assert content == "nested"
