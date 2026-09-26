"""
Tests for test case translation: STS + test cases (or traces) -> Pickles text.
"""
from pathlib import Path

from src.transformer import PicklesToSTS
from src.tc_translator import TestCaseTranslator
from src.trace_parser import parse_trace

_pickles = PicklesToSTS()
_parser  = _pickles.load_parser(lang="en")

_RECENT_SIZES = ('"recent sizes" is an array of at most 5 unique elements where each element '
                 'is an integer with range [1,5]')


def _sts(variables: str, then: str) -> dict:
    """Build the STS of a one-scenario coffee spec with a fixed "When the user clicks Brew".

    Args:
        variables: Variable Settings lines.
        then: Then step (with its guard lines).

    Returns:
        The STS dict.
    """
    text = (
        f"Variable Settings\n{variables}\n\n"
        "Scenario 01: test\n"
        "Given the system is in its initial state\n"
        f"When the user clicks Brew\n{then}\n"
    )
    sts_list, _ = _pickles.tree_to_sts(_parser.parse(_pickles._preprocess(text)), start_id=1)
    return sts_list[0]


def _switch_for_gate(sts: dict, gate: str) -> str:
    """ID of the one switch whose gate ID starts with gate.

    Args:
        sts: The STS dict.
        gate: Gate ID prefix, e.g. "In1".

    Returns:
        The switch ID.
    """
    matches = [sid for sid, sw in sts["switches"].items() if sw["gate"].startswith(gate)]
    assert len(matches) == 1
    return matches[0]


def _translate(tmp_path: Path, sts: dict, initial_values: dict) -> str:
    """Translate one test case (In1 then Out1) and return the text.

    Args:
        tmp_path: Directory for the output file.
        sts: The STS dict.
        initial_values: Initial values of the test case.

    Returns:
        The translated text.
    """
    test_case = {
        "initial_values": initial_values,
        "steps": [
            {"switch_id": _switch_for_gate(sts, "In1"), "values": {}},
            {"switch_id": _switch_for_gate(sts, "Out1"), "values": {}},
        ],
    }
    return TestCaseTranslator(sts).translate([test_case], str(tmp_path / "translated.txt"))


class TestTranslateGuards:
    def test_two_clauses_give_such_that_block(self, tmp_path: Path) -> None:
        # A Then guard with two AND clauses gives a "such that:" block with one clause on each line.
        sts = _sts('"water level" is an integer with range [0,210]',
                   'Then the machine has a current "water level" such that:\n'
                   '  "water level" is greater than 100 AND\n'
                   '  "water level" is lower than 200')
        result = _translate(tmp_path, sts, {"water-level": 150})
        assert 'Then the machine has a current "water level" such that:' in result
        assert '"water level" is greater than 100' in result
        assert 'AND "water level" is lower than 200' in result
        assert "'op':" not in result

    def test_count_guard(self, tmp_path: Path) -> None:
        # A "cardinality" guard shows as "has at least N elements where each element ...".
        sts = _sts(_RECENT_SIZES, 'Then the screen shows "recent sizes" such that:\n'
                                  '    "recent sizes" has at least 2 elements where each element is equal to 3')
        result = _translate(tmp_path, sts, {"recent-sizes": [3, 3]})
        assert 'has at least 2 elements where each element is equal to 3' in result
        assert "'op':" not in result

    def test_forall_guard(self, tmp_path: Path) -> None:
        # A "forall" guard shows as "has all elements where each element ...".
        sts = _sts(_RECENT_SIZES, 'Then the screen shows "recent sizes" such that:\n'
                                  '    "recent sizes" has all elements where each element is greater than 2')
        result = _translate(tmp_path, sts, {"recent-sizes": [3, 4]})
        assert 'has all elements where each element is greater than 2' in result
        assert "'op':" not in result

    def test_length_guard(self, tmp_path: Path) -> None:
        # A "len" guard shows as "has length ...".
        sts = _sts(_RECENT_SIZES, 'Then the screen shows "recent sizes" such that:\n'
                                  '    "recent sizes" has length greater than 2')
        result = _translate(tmp_path, sts, {"recent-sizes": [1, 2, 3]})
        assert 'has length greater than 2' in result
        assert '.len' not in result

    def test_contains_variable_shows_variable_name(self, tmp_path: Path) -> None:
        # "contains" with a variable shows the variable name, not its parameter ID ("_p").
        sts = _sts('"chosen bean" is a string with range {ARABICA, ROBUSTA}\n'
                   '"available beans" is an array of at most 2 unique elements where each element '
                   'is a string with range {ARABICA, ROBUSTA}',
                   'Then the screen shows "available beans" such that:\n'
                   '    "available beans" contains "chosen bean"')
        result = _translate(tmp_path, sts, {"available-beans": ["ARABICA"], "chosen-bean": "ARABICA"})
        assert 'contains "chosen bean"' in result
        assert "_p" not in result


class TestTranslateTraces:
    def test_output_shows_observed_value_not_variable_name(self, tmp_path: Path) -> None:
        # An output guard "x_p == y" shows the value from the trace, not the name of "y".
        sts = _sts('"water level" is an integer with range [0,210] and initial value 210\n'
                   '"shown water level" is an integer with range [0,210] and initial value 0',
                   'Then the screen shows "shown water level" such that:\n'
                   '  "shown water level" is equal to "water level" + 10')
        trace = (
            'Just [?"In1",?"check_Out1",'
            '!("Out1",Only,[Some (Constant {constType = Int, constValue = 160})],'
            '(("sts_001",pending !"Out1" [shown-water-level_p:Int] -> "L2_1"),'
            '{shown-water-level:=0,water-level:=150}))]'
        )
        test_case = parse_trace(trace, [sts])
        result = TestCaseTranslator([sts]).translate([test_case], str(tmp_path / "translated.txt"))
        assert 'Then the screen shows "shown water level" equal to 160' in result
        assert 'equal to "water level"' not in result
