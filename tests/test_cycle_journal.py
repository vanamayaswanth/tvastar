"""Tests for CycleEntry and CycleJournal.

Validates: Requirement 3
"""

import time

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tvastar import CycleEntry, CycleJournal, CyclePolicy, Harness, TaskGraph, create_agent
from tvastar.model import MockModel


# ---------------------------------------------------------------------------
# CycleEntry basics
# ---------------------------------------------------------------------------


def test_entry_truncates_result_text_to_500():
    """result_text longer than 500 chars is truncated."""
    long_text = "x" * 1000
    entry = CycleEntry(iteration=1, timestamp=1.0, result_text=long_text, continued=True)
    assert len(entry.result_text) == 500


def test_entry_preserves_short_text():
    entry = CycleEntry(iteration=0, timestamp=0.0, result_text="hello", continued=True)
    assert entry.result_text == "hello"


# ---------------------------------------------------------------------------
# CycleJournal: append-only invariant
# ---------------------------------------------------------------------------


def test_append_only_entries_grow():
    """Entries list only grows via append; never shrinks."""
    journal = CycleJournal()
    assert journal.entries == []

    e1 = CycleEntry(iteration=0, timestamp=1.0, result_text="first", continued=True)
    journal.append(e1)
    assert len(journal.entries) == 1

    e2 = CycleEntry(iteration=1, timestamp=2.0, result_text="second", continued=True)
    journal.append(e2)
    assert len(journal.entries) == 2
    assert journal.entries[0] == e1
    assert journal.entries[1] == e2


def test_entries_returns_copy():
    """entries property returns a copy; mutating it doesn't affect journal."""
    journal = CycleJournal()
    journal.append(CycleEntry(iteration=0, timestamp=1.0, result_text="a", continued=True))
    snapshot = journal.entries
    snapshot.clear()
    assert len(journal.entries) == 1  # original unaffected


# ---------------------------------------------------------------------------
# CycleJournal: iterations count
# ---------------------------------------------------------------------------


def test_iterations_counts_continued_true():
    journal = CycleJournal()
    journal.append(CycleEntry(iteration=0, timestamp=1.0, result_text="a", continued=True))
    journal.append(CycleEntry(iteration=1, timestamp=2.0, result_text="b", continued=True))
    journal.append(CycleEntry(iteration=2, timestamp=3.0, result_text="ttl_reached", continued=False))
    assert journal.iterations == 2


def test_iterations_zero_when_empty():
    assert CycleJournal().iterations == 0


# ---------------------------------------------------------------------------
# CycleJournal: terminated flag
# ---------------------------------------------------------------------------


def test_terminated_true_when_last_continued_false():
    journal = CycleJournal()
    journal.append(CycleEntry(iteration=0, timestamp=1.0, result_text="a", continued=True))
    journal.append(CycleEntry(iteration=1, timestamp=2.0, result_text="ttl_reached", continued=False))
    assert journal.terminated is True


def test_terminated_false_when_last_continued_true():
    journal = CycleJournal()
    journal.append(CycleEntry(iteration=0, timestamp=1.0, result_text="a", continued=True))
    assert journal.terminated is False


def test_terminated_false_when_empty():
    assert CycleJournal().terminated is False


# ---------------------------------------------------------------------------
# CycleJournal: termination_reason
# ---------------------------------------------------------------------------


def test_termination_reason_from_last_entry():
    journal = CycleJournal()
    journal.append(CycleEntry(iteration=0, timestamp=1.0, result_text="a", continued=True))
    journal.append(CycleEntry(iteration=1, timestamp=2.0, result_text="predicate_false", continued=False))
    assert journal.termination_reason == "predicate_false"


def test_termination_reason_none_when_not_terminated():
    journal = CycleJournal()
    journal.append(CycleEntry(iteration=0, timestamp=1.0, result_text="a", continued=True))
    assert journal.termination_reason is None


# ---------------------------------------------------------------------------
# CycleJournal: converging property
# ---------------------------------------------------------------------------


def test_converging_true_when_texts_differ():
    """converging is True when last 2 results differ (distance > 0.1)."""
    journal = CycleJournal()
    journal.append(CycleEntry(iteration=0, timestamp=1.0, result_text="aaa", continued=True))
    journal.append(CycleEntry(iteration=1, timestamp=2.0, result_text="zzz", continued=True))
    assert journal.converging is True


def test_converging_false_when_texts_same():
    """converging is False when texts are identical (distance = 0)."""
    journal = CycleJournal()
    journal.append(CycleEntry(iteration=0, timestamp=1.0, result_text="same", continued=True))
    journal.append(CycleEntry(iteration=1, timestamp=2.0, result_text="same", continued=True))
    assert journal.converging is False


def test_converging_false_when_fewer_than_two_entries():
    journal = CycleJournal()
    assert journal.converging is False
    journal.append(CycleEntry(iteration=0, timestamp=1.0, result_text="only", continued=True))
    assert journal.converging is False


# ---------------------------------------------------------------------------
# Integration: _validate() creates journals for cycle edges
# ---------------------------------------------------------------------------


def _make_graph():
    agent = create_agent("t", model=MockModel(script=["ok"]))
    return TaskGraph(Harness(agent))


def test_validate_creates_journals_for_cycle_edges():
    """_validate() creates a CycleJournal for each registered cycle edge."""
    g = _make_graph()
    g.task("write", "write", depends_on=[("review", CyclePolicy.ALLOW_TTL(3))])
    g.task("review", "review", depends_on=["write"])
    g._validate()

    assert len(g._cycle_journals) == 1
    key = list(g._cycle_journals.keys())[0]
    assert isinstance(g._cycle_journals[key], CycleJournal)
    # Journal starts empty
    assert g._cycle_journals[key].entries == []


def test_validate_no_journals_for_dag():
    """A DAG graph has no cycle journals."""
    g = _make_graph()
    g.task("a", "A")
    g.task("b", "B", depends_on=["a"])
    g._validate()
    assert g._cycle_journals == {}


def test_validate_multiple_cycle_edges_get_separate_journals():
    """Each cycle edge gets its own journal."""
    g = _make_graph()
    g.task("write", "write", depends_on=[
        ("review", CyclePolicy.ALLOW_TTL(3)),
        ("edit", CyclePolicy.ALLOW_TTL(2)),
    ])
    g.task("review", "review", depends_on=["write"])
    g.task("edit", "edit", depends_on=["write"])
    g._validate()
    assert len(g._cycle_journals) == 2
    for journal in g._cycle_journals.values():
        assert isinstance(journal, CycleJournal)


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------


@given(
    entries=st.lists(
        st.tuples(
            st.integers(min_value=0, max_value=100),
            st.floats(min_value=0, max_value=1e9, allow_nan=False, allow_infinity=False),
            st.text(min_size=0, max_size=600),
            st.booleans(),
        ),
        min_size=1,
        max_size=20,
    )
)
@settings(max_examples=100)
def test_property_append_only_invariant(entries):
    """
    **Validates: Requirements 3**

    Property: CycleJournal entries are append-only — the list only grows,
    entries never disappear or change position.
    """
    journal = CycleJournal()
    seen: list[CycleEntry] = []
    for iteration, ts, text, continued in entries:
        entry = CycleEntry(iteration=iteration, timestamp=ts, result_text=text, continued=continued)
        journal.append(entry)
        seen.append(entry)
        current = journal.entries
        assert len(current) == len(seen)
        for i, e in enumerate(seen):
            assert current[i] == e


@given(
    text_a=st.text(min_size=1, max_size=100),
    text_b=st.text(min_size=1, max_size=100),
)
@settings(max_examples=100)
def test_property_converging(text_a, text_b):
    """
    **Validates: Requirements 3**

    Property: converging is True iff the text distance between the last
    two entries exceeds 0.1 (as measured by 1 - SequenceMatcher.ratio()).
    """
    from difflib import SequenceMatcher

    journal = CycleJournal()
    journal.append(CycleEntry(iteration=0, timestamp=1.0, result_text=text_a, continued=True))
    journal.append(CycleEntry(iteration=1, timestamp=2.0, result_text=text_b, continued=True))

    ratio = SequenceMatcher(None, journal.entries[0].result_text, journal.entries[1].result_text).ratio()
    expected_converging = (1.0 - ratio) > 0.1
    assert journal.converging == expected_converging
