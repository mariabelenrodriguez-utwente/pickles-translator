"""
Tests for the Cucumber .feature renderer.
"""
from pathlib import Path

from src.transformer import PicklesToSTS
from src.tc_translator import TestCaseTranslator
from src.cucumber_renderer import render_cucumber

_SPEC = (
    'Variable Settings\n'
    '"code" is an integer with range [0,10]\n'
    '\n'
    'Scenario 01: test\n'
    'Given the system is in its initial state\n'
    'When the controller is on\n'
    'Then the system reports "code" such that:\n'
    '  "code" is greater than 1\n'
)

_TEMPLATE = (
    'Feature: dummy\n'
    '{% if background_given %}\n'
    '  Background:\n'
    '{{ background_given }}\n'
    '\n'
    '{% endif %}\n'
    '{% for test in test_cases %}\n'
    '  Scenario: {{ test.id }}\n'
    '{{ test.body }}\n'
    '\n'
    '{% endfor %}'
)

_WHEN_THEN = (
    '    When the controller is on\n'
    '    Then the system reports "code" such that:\n'
    '        "code" is greater than 1\n'
    '        AND "code" is greater or equal to 0\n'
    '        AND "code" is lower or equal to 10\n'
) # TODO: This breaks Cucumber format, find a workaround


def _render(tmp_path: Path, initial_values: list[dict]) -> str:
    """Render one test case per initial value set, write it to a file and return the file text.

    Args:
        tmp_path: Directory for the template and the output file.
        initial_values: One initial value dict per test case.

    Returns:
        Text of the written .feature file.
    """
    pickles = PicklesToSTS()
    tree = pickles.load_parser(lang="en").parse(pickles._preprocess(_SPEC))
    sts = pickles.tree_to_sts(tree, start_id=1)[0][0]
    switch_in = next(sid for sid, sw in sts["switches"].items() if sw["gate"].startswith("In1"))
    switch_out = next(sid for sid, sw in sts["switches"].items() if sw["gate"].startswith("Out1"))
    steps = [{"switch_id": switch_in, "values": {}}, {"switch_id": switch_out, "values": {}}]
    test_cases = [{"initial_values": values, "steps": steps} for values in initial_values]

    template_path = tmp_path / "template.feature"
    template_path.write_text(_TEMPLATE)
    out_path = tmp_path / "out.feature"
    out_path.write_text(render_cucumber(test_cases, TestCaseTranslator(sts), str(template_path)))
    return out_path.read_text()


class TestRenderCucumber:
    """render_cucumber puts a shared Given in Background, else one Given per Scenario."""

    def test_same_initial_values_render_background(self, tmp_path: Path) -> None:
        result = _render(tmp_path, [{"code": 5}, {"code": 5}])

        assert result == (
            'Feature: dummy\n'
            '  Background:\n'
            '    Given the system is initialized with values:\n'
            '        "code": 5\n'
            '\n'
            '  Scenario: Test Case 1\n'
            + _WHEN_THEN +
            '\n'
            '  Scenario: Test Case 2\n'
            + _WHEN_THEN +
            '\n'
        )

    def test_different_initial_values_render_given_per_scenario(self, tmp_path: Path) -> None:
        result = _render(tmp_path, [{"code": 3}, {"code": 7}])

        assert result == (
            'Feature: dummy\n'
            '  Scenario: Test Case 1\n'
            '    Given the system is initialized with values:\n'
            '        "code": 3\n'
            + _WHEN_THEN +
            '\n'
            '  Scenario: Test Case 2\n'
            '    Given the system is initialized with values:\n'
            '        "code": 7\n'
            + _WHEN_THEN +
            '\n'
        )
