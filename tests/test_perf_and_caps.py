"""Performance benchmarks and cap enforcement tests.

Perf assertions (NFRs from .agent/prd.md):
  - VirtualSandbox.snapshot() must complete in < 150 ms even at ~1 MB of content
  - LTMStore.retrieve() must complete in < 200 ms across 500 nodes

Cap enforcement:
  - memory_cap_mb on AgentSpec stops the run when messages exceed the limit

OpenAI retry:
  - OpenAIModel retries on transient errors and raises ModelError after exhaustion
"""

import time

import pytest

from tvastar import Harness, create_agent
from tvastar.model.mock import MockModel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _elapsed_ms(fn, *args, **kwargs) -> float:
    t0 = time.perf_counter()
    fn(*args, **kwargs)
    return (time.perf_counter() - t0) * 1000


# ---------------------------------------------------------------------------
# Perf: VirtualSandbox.snapshot() < 150 ms
# ---------------------------------------------------------------------------


def test_snapshot_perf_under_150ms():
    """snapshot() on a ~1 MB virtual filesystem must complete in < 150 ms."""
    from tvastar.sandbox.virtual import VirtualSandbox

    # 200 files × 5 KB each ≈ 1 MB
    content = "x" * 5_000
    files = {f"dir/file_{i:04d}.txt": content for i in range(200)}
    sb = VirtualSandbox(files)

    elapsed = _elapsed_ms(sb.snapshot)
    assert elapsed < 150, f"snapshot took {elapsed:.1f} ms (limit 150 ms)"


def test_local_sandbox_snapshot_perf_under_500ms():
    """LocalSandbox.snapshot() on ~500 KB of files must complete in < 500 ms."""
    import tempfile
    from pathlib import Path as _P

    from tvastar.sandbox.local import LocalSandbox

    content = b"x" * 5_000
    with tempfile.TemporaryDirectory() as tmpdir:
        for i in range(100):
            _P(tmpdir, f"file_{i:04d}.bin").write_bytes(content)
        sb = LocalSandbox(root=tmpdir)
        elapsed = _elapsed_ms(sb.snapshot)
    assert elapsed < 500, f"LocalSandbox.snapshot took {elapsed:.1f} ms (limit 500 ms)"


def test_restore_perf_under_150ms():
    """restore() on a ~1 MB snapshot must complete in < 150 ms."""
    from tvastar.sandbox.virtual import VirtualSandbox

    content = "x" * 5_000
    files = {f"dir/file_{i:04d}.txt": content for i in range(200)}
    sb = VirtualSandbox(files)
    snap = sb.snapshot()

    elapsed = _elapsed_ms(sb.restore, snap)
    assert elapsed < 150, f"restore took {elapsed:.1f} ms (limit 150 ms)"


# ---------------------------------------------------------------------------
# Perf: LTMStore.retrieve() < 200 ms across 500 nodes
# ---------------------------------------------------------------------------


def _build_ltm_store(n: int):
    from tvastar.contrib.ltm import LTMNode, LTMStore
    from tvastar.memory.store import InMemoryStore

    store = LTMStore(InMemoryStore())
    for i in range(n):
        node = LTMNode(
            id=f"node{i:06d}",
            type="factual" if i % 2 == 0 else "procedural",
            content=f"fact about module {i} configuration and dependency resolution",
            tags=["module", "config", "dependency", f"item{i}"],
        )
        store._save(node)
    return store


def test_ltm_retrieve_perf_under_200ms():
    """LTMStore.retrieve() across 500 nodes must complete in < 200 ms."""
    store = _build_ltm_store(500)
    elapsed = _elapsed_ms(store.retrieve, "module configuration dependency", k=5)
    assert elapsed < 200, f"retrieve took {elapsed:.1f} ms (limit 200 ms)"


def test_ltm_retrieve_perf_1000_nodes_under_200ms():
    """LTMStore.retrieve() across 1000 nodes stays under 200 ms."""
    store = _build_ltm_store(1000)
    elapsed = _elapsed_ms(store.retrieve, "dependency resolution config", k=5)
    assert elapsed < 200, f"retrieve (1000 nodes) took {elapsed:.1f} ms (limit 200 ms)"


# ---------------------------------------------------------------------------
# Memory cap enforcement
# ---------------------------------------------------------------------------


async def test_memory_cap_stops_run():
    """When messages exceed memory_cap_mb the run stops with stopped='memory_cap'."""
    # Use a tiny cap (0.0001 MB = ~100 bytes) so a single model reply triggers it
    agent = create_agent(
        "cap-test",
        model=MockModel(["A" * 200, "B" * 200, "C" * 200]),
        instructions="base",
        memory_cap_mb=0.0001,
        detect=False,
    )
    h = Harness(agent)
    r = await h.run("go")
    assert r.stopped == "memory_cap"


async def test_memory_cap_none_does_not_stop():
    """No cap (default) lets the run complete normally regardless of message size."""
    big_reply = "word " * 5000  # ~25 KB per reply
    agent = create_agent(
        "nocap-test",
        model=MockModel([big_reply]),
        instructions="base",
        memory_cap_mb=None,
        detect=False,
    )
    h = Harness(agent)
    r = await h.run("go")
    assert r.stopped == "end_turn"


async def test_memory_cap_field_on_spec():
    """memory_cap_mb is stored on the AgentSpec and passed through create_agent."""
    agent = create_agent("spec-test", model=MockModel([]), memory_cap_mb=50.0)
    assert agent.memory_cap_mb == 50.0


# ---------------------------------------------------------------------------
# OpenAI retry
# ---------------------------------------------------------------------------


async def test_openai_model_retries_on_transient_error():
    """OpenAIModel retries transient errors and succeeds on the last attempt."""
    from unittest.mock import AsyncMock, MagicMock

    from tvastar.model.base import ModelRetryPolicy
    from tvastar.model.openai import OpenAIModel
    from tvastar.types import Message

    call_count = 0

    class FakeChoice:
        finish_reason = "stop"

        class message:
            content = "recovered"
            tool_calls = []

    class FakeResp:
        choices = [FakeChoice()]
        usage = None

    async def flaky(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise Exception("rate limit exceeded 429")
        return FakeResp()

    stub = MagicMock()
    stub.chat = MagicMock()
    stub.chat.completions = MagicMock()
    stub.chat.completions.create = AsyncMock(side_effect=flaky)

    m = OpenAIModel(
        client=stub,
        retry=ModelRetryPolicy(max_attempts=3, backoff_base=0.0, jitter=0.0),
    )
    result = await m.generate([Message("user", "hi")])
    assert result.message.text == "recovered"
    assert call_count == 3


async def test_openai_model_raises_after_max_attempts():
    """OpenAIModel raises ModelError after exhausting all retry attempts."""
    from unittest.mock import AsyncMock, MagicMock

    from tvastar.errors import ModelError
    from tvastar.model.base import ModelRetryPolicy
    from tvastar.model.openai import OpenAIModel
    from tvastar.types import Message

    stub = MagicMock()
    stub.chat = MagicMock()
    stub.chat.completions = MagicMock()
    stub.chat.completions.create = AsyncMock(side_effect=Exception("503 server error"))

    m = OpenAIModel(
        client=stub,
        retry=ModelRetryPolicy(max_attempts=2, backoff_base=0.0, jitter=0.0),
    )
    with pytest.raises(ModelError):
        await m.generate([Message("user", "hi")])


async def test_openai_model_no_retry_by_default():
    """OpenAIModel without retry= raises immediately on any error."""
    from unittest.mock import AsyncMock, MagicMock

    from tvastar.errors import ModelError
    from tvastar.model.openai import OpenAIModel
    from tvastar.types import Message

    stub = MagicMock()
    stub.chat = MagicMock()
    stub.chat.completions = MagicMock()
    stub.chat.completions.create = AsyncMock(side_effect=Exception("429 rate limit"))

    m = OpenAIModel(client=stub)
    with pytest.raises(ModelError):
        await m.generate([Message("user", "hi")])
