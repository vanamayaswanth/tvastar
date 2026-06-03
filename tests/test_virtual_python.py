"""Tests for the headline feature: real Python execution in the in-memory
sandbox with no Docker and no extra dependencies."""

from tvastar.sandbox import VirtualSandbox


async def test_virtual_sandbox_runs_python():
    sb = VirtualSandbox({"app.py": "print('hello', 1 + 2)"})
    r = await sb.exec("python app.py")
    assert r.exit_code == 0
    assert "hello 3" in r.stdout


async def test_virtual_sandbox_syncs_created_files_back():
    sb = VirtualSandbox({"w.py": "open('out.txt', 'w').write('written')"})
    r = await sb.exec("python w.py")
    assert r.ok
    # File the script created is now visible in the in-memory FS.
    assert sb.fs.read("out.txt") == "written"


async def test_virtual_sandbox_pytest_red_then_green():
    sb = VirtualSandbox(
        {
            "calc.py": "def add(a, b):\n    return a - b\n",
            "test_calc.py": "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        }
    )
    red = await sb.exec("pytest -q")
    assert red.exit_code != 0  # bug -> failing

    sb.fs.write("calc.py", "def add(a, b):\n    return a + b\n")
    green = await sb.exec("pytest -q")
    assert green.exit_code == 0  # fixed -> passing


async def test_python_can_be_disabled():
    sb = VirtualSandbox({"app.py": "print(1)"}, allow_python=False)
    r = await sb.exec("python app.py")
    assert r.exit_code == 126
    assert "disabled" in r.render()


async def test_nonzero_exit_propagates():
    sb = VirtualSandbox({"boom.py": "raise SystemExit(3)"})
    r = await sb.exec("python boom.py")
    assert r.exit_code == 3
