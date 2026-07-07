"""Unit tests for _build_child_spec respecting profile.detect field.

Validates Requirements 23.2, 23.3, 23.4:
- detect=None → inherit parent's detector configuration
- detect=False → disable detection for child
- detect=True → use default_detectors()
- detect=list → use the provided list directly
"""

from tvastar import Harness, create_agent
from tvastar.detect import default_detectors
from tvastar.model.mock import MockModel
from tvastar.profiles import define_agent_profile


def _dummy_detector(ctx):
    """A custom detector for testing."""
    return []


def _another_detector(ctx):
    """Another custom detector for testing."""
    return []


class TestChildSpecDetectField:
    """Tests for _build_child_spec respecting profile.detect."""

    def test_detect_none_inherits_parent_detectors(self):
        """When detect is None on profile, child inherits parent's detectors."""
        parent_detectors = [_dummy_detector, _another_detector]
        profile = define_agent_profile("worker", detect=None)

        spec = create_agent(
            "parent",
            model=MockModel(["done"]),
            instructions="parent",
            detect=parent_detectors,
            subagents=[profile],
        )

        h = Harness(spec)
        sess = h.session()

        child_spec = sess._build_child_spec(
            profile=profile,
            instructions_override=None,
            model_override=None,
            thinking_level_override=None,
            max_steps_override=None,
        )

        # Child should have the same detectors as parent
        assert child_spec.detectors == parent_detectors

    def test_detect_none_no_profile_inherits_parent(self):
        """When no profile is provided, child inherits parent's detectors."""
        parent_detectors = [_dummy_detector]

        spec = create_agent(
            "parent",
            model=MockModel(["done"]),
            instructions="parent",
            detect=parent_detectors,
        )

        h = Harness(spec)
        sess = h.session()

        child_spec = sess._build_child_spec(
            profile=None,
            instructions_override=None,
            model_override=None,
            thinking_level_override=None,
            max_steps_override=None,
        )

        # Child should inherit parent detectors
        assert child_spec.detectors == parent_detectors

    def test_detect_false_disables_detection(self):
        """When detect is False on profile, child has no detectors."""
        parent_detectors = [_dummy_detector, _another_detector]
        profile = define_agent_profile("worker", detect=False)

        spec = create_agent(
            "parent",
            model=MockModel(["done"]),
            instructions="parent",
            detect=parent_detectors,
            subagents=[profile],
        )

        h = Harness(spec)
        sess = h.session()

        child_spec = sess._build_child_spec(
            profile=profile,
            instructions_override=None,
            model_override=None,
            thinking_level_override=None,
            max_steps_override=None,
        )

        # Child should have no detectors
        assert child_spec.detectors == []

    def test_detect_true_uses_default_detectors(self):
        """When detect is True on profile, child uses default_detectors()."""
        profile = define_agent_profile("worker", detect=True)

        spec = create_agent(
            "parent",
            model=MockModel(["done"]),
            instructions="parent",
            detect=False,  # Parent has no detectors
            subagents=[profile],
        )

        h = Harness(spec)
        sess = h.session()

        child_spec = sess._build_child_spec(
            profile=profile,
            instructions_override=None,
            model_override=None,
            thinking_level_override=None,
            max_steps_override=None,
        )

        # Child should have default detectors
        expected = default_detectors()
        assert len(child_spec.detectors) == len(expected)
        for actual, exp in zip(child_spec.detectors, expected):
            assert actual is exp

    def test_detect_list_uses_provided_list(self):
        """When detect is a list on profile, child uses that list directly."""
        custom_detectors = [_dummy_detector, _another_detector]
        profile = define_agent_profile("worker", detect=custom_detectors)

        spec = create_agent(
            "parent",
            model=MockModel(["done"]),
            instructions="parent",
            detect=False,  # Parent has no detectors
            subagents=[profile],
        )

        h = Harness(spec)
        sess = h.session()

        child_spec = sess._build_child_spec(
            profile=profile,
            instructions_override=None,
            model_override=None,
            thinking_level_override=None,
            max_steps_override=None,
        )

        # Child should have exactly the custom detectors
        assert child_spec.detectors == custom_detectors

    def test_detect_none_with_parent_no_detectors(self):
        """When detect is None and parent has no detectors, child also has none."""
        profile = define_agent_profile("worker", detect=None)

        spec = create_agent(
            "parent",
            model=MockModel(["done"]),
            instructions="parent",
            detect=False,  # Parent explicitly has no detectors
            subagents=[profile],
        )

        h = Harness(spec)
        sess = h.session()

        child_spec = sess._build_child_spec(
            profile=profile,
            instructions_override=None,
            model_override=None,
            thinking_level_override=None,
            max_steps_override=None,
        )

        # Parent has no detectors, so child should also have none
        assert child_spec.detectors == []
