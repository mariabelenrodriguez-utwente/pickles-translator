"""
Tests for STS generation: spec text -> parser -> transformer -> STS.
"""
import json
from pathlib import Path

import jsonschema
import pytest
from lark import UnexpectedToken
from lark.exceptions import VisitError

from src.transformer import PicklesToSTS, render_guard_expr
from src.exceptions import ConsistencyError

_ROOT = Path(__file__).parent.parent
_FIXTURES = Path(__file__).parent / "fixtures"
_COMPARISON_OPS = {"==", "!=", ">", "<", ">=", "<="}

_pickles_to_sts = PicklesToSTS()
_parsers = {lang: _pickles_to_sts.load_parser(lang=lang) for lang in ("en", "es")}


def _spec(variables: str, then: str, when: str = "When the user clicks Brew") -> str:
    """Build a one-scenario spec text.

    Args:
        variables: Variable Settings lines.
        then: Then step (with its guard lines).
        when: When step (with its guard lines).

    Returns:
        The spec text.
    """
    return (
        f"Variable Settings\n{variables}\n\n"
        "Scenario 01: test\n"
        "Given the system is in its initial state\n"
        f"{when}\n{then}\n"
    )


def _parse_spec(text: str, lang: str = "en") -> list[dict]:
    """Parse spec text and return the STS list (one STS for each scenario).

    Args:
        text: Spec text.
        lang: Spec language.

    Returns:
        List of STS dicts.
    """
    tree = _parsers[lang].parse(_pickles_to_sts._preprocess(text))
    sts_list, _ = _pickles_to_sts.tree_to_sts(tree, start_id=1)
    return sts_list


def _parse_spec_single(text: str) -> dict:
    """Parse a one-scenario spec and return its STS.

    Args:
        text: Spec text with one scenario.

    Returns:
        The STS dict.
    """
    sts_list = _parse_spec(text)
    assert len(sts_list) == 1
    return sts_list[0]


def _iter_comparisons(node: object):
    """Yield every comparison node in a guard tree.

    Args:
        node: Guard tree or leaf.

    Yields:
        Comparison nodes.
    """
    if not isinstance(node, dict):
        return
    if node.get("op") in _COMPARISON_OPS:
        yield node
        return
    for key in ("lhs", "rhs", "expression"):
        if key in node:
            yield from _iter_comparisons(node[key])


def _lhs_path(node: object) -> str | None:
    """Dotted path of a variable leaf or a "project" chain (e.g. "e1.size").

    Args:
        node: Guard node.

    Returns:
        The path, or None if node is not a variable or "project" node.
    """
    if not isinstance(node, dict):
        return None
    if "var" in node:
        return node["var"]
    if node.get("op") == "project":
        base = _lhs_path(node["lhs"])
        return f"{base}.{node['rhs']['var']}" if base else None
    return None


def _comparisons_on(guards: dict, varid: str) -> list[dict]:
    """All comparison nodes in all guards whose lhs is varid.

    Args:
        guards: The STS "guards" map.
        varid: Variable ID, e.g. "beans-level_p".

    Returns:
        List of comparison nodes.
    """
    return [c for tree in guards.values() for c in _iter_comparisons(tree) if _lhs_path(c.get("lhs")) == varid]


def _literal(node: object) -> object:
    """Bare value of a wrapped literal (e.g. {"integer": 5} -> 5), else None.

    Args:
        node: Guard node.

    Returns:
        The bare value, or None.
    """
    if isinstance(node, dict) and len(node) == 1:
        for typename in ("string", "integer", "float", "boolean"):
            if typename in node:
                return node[typename]
    return None


def _guard_for_gate(sts: dict, prefix: str) -> object:
    """Combined guard tree of the one switch whose gate starts with prefix ("In" or "Out").

    Args:
        sts: The STS dict.
        prefix: Gate ID prefix.

    Returns:
        The guard tree (guard IDs joined with "&&").
    """
    matches = [sw["guard"] for sw in sts["switches"].values() if sw["gate"].startswith(prefix)]
    assert len(matches) == 1
    nodes = [sts["guards"][gid] for gid in matches[0]]
    node = nodes[0]
    for n in nodes[1:]:
        node = {"lhs": node, "op": "&&", "rhs": n}
    return node


def _then_guard(sts: dict) -> object:
    return _guard_for_gate(sts, "Out")


def _when_guard(sts: dict) -> object:
    return _guard_for_gate(sts, "In")


def _step_guards(sts: dict) -> list:
    """Guards written in the steps (without range guards and "true" guards).

    Args:
        sts: The STS dict.

    Returns:
        List of guard trees.
    """
    return [g for gid, g in sts["guards"].items() if not gid.startswith("G_") and g != {"boolean": True}]


_BEANS = ('"beans" is an array of at most 3 unique elements where each element '
          'is a string with range {ARABICA, ROBUSTA, LIBERICA}')


class TestVariableRanges:
    def test_closed_range_is_inclusive(self) -> None:
        # A [lo,hi] range gives >= lo and <= hi.
        sts = _parse_spec_single(_spec('"beans level" is an integer with range [0,12]',
                                'Then the machine has a current "beans level" not equal to 6'))
        ops = {(c["op"], _literal(c["rhs"])) for c in _comparisons_on(sts["guards"], "beans-level_p")}
        assert {(">=", 0), ("<=", 12)} <= ops
        assert not any(op in (">", "<") for op, _ in ops)

    def test_open_range_is_exclusive(self) -> None:
        # A (lo,hi) range gives > lo and < hi.
        sts = _parse_spec_single(_spec('"temperature" is a float with range (80.0,96.0)',
                                'Then the machine has a current "temperature" not equal to 90.0'))
        ops = {(c["op"], _literal(c["rhs"])) for c in _comparisons_on(sts["guards"], "temperature_p")}
        assert {(">", 80.0), ("<", 96.0)} <= ops
        assert not any(op in (">=", "<=") for op, _ in ops)

    def test_set_range_is_membership_not_bounds(self) -> None:
        # A {a, b} range means "== a or == b", not a lower and upper bound.
        sts = _parse_spec_single(_spec('"cups" is an integer with range {1, 10}',
                                'Then the screen shows "cups" not equal to 3'))
        comparisons = _comparisons_on(sts["guards"], "cups_p")
        assert [c["rhs"] for c in comparisons if c["op"] == "=="] == [{"integer": 1}, {"integer": 10}]
        assert not any(c["op"] in (">=", "<=", ">", "<") for c in comparisons)

    def test_string_without_range_has_no_range_guard(self) -> None:
        # A variable with no range gets no range guard.
        sts = _parse_spec_single(_spec('"machine name" is a string',
                                "Then the screen shows \"machine name\" equal to 'Barista'"))
        assert sts["locationVariables"]["machine-name"]["type"] == "string"
        assert _comparisons_on(sts["guards"], "machine-name_p") == [
            {"lhs": {"var": "machine-name_p"}, "op": "==", "rhs": {"string": "Barista"}}
        ]

    def test_boolean_with_initial_value_and_no_range(self) -> None:
        # "with initial value" sets an initial value without a range.
        sts = _parse_spec_single(_spec('"available" is a boolean with initial value true',
                                'Then the screen shows "available" equal to true'))
        assert sts["initialValuation"] == {"available": True}

    @pytest.mark.parametrize("declaration", [
        '"beans level" is an integer with range (1,2,3)',
        '"beans level" is an integer with range [1,2,3]',
    ])
    def test_open_or_closed_range_needs_two_values(self, declaration: str) -> None:
        # A (..) or [..] range with more than two values gives an error.
        text = _spec(declaration, 'Then the screen shows "beans level" not equal to 3')
        with pytest.raises(VisitError) as exc_info:
            _parse_spec(text)
        assert isinstance(exc_info.value.orig_exc, ConsistencyError)

    @pytest.mark.parametrize("declaration", [
        '"beans level" is an integer with range [1,10}',
        '"beans level" is an integer with range (1,250]',
        '"beans level" is an integer with range {1,20)',
    ])
    def test_mixed_brackets_raise_parse_error(self, declaration: str) -> None:
        # A range with an opening and closing bracket that do not match does not parse.
        text = _spec(declaration, 'Then the screen shows "beans level" not equal to 3')
        with pytest.raises(UnexpectedToken):
            _parse_spec(text)

    @pytest.mark.parametrize("declaration", [
        '"beans level" is an integer with range [1,"dummy"]',
        '"beans level" is an integer with range {"dummy", 20}',
        '"beans level" is an integer with range (1,20.0)',
    ])
    def test_non_integer_value_in_integer_range_raises(self, declaration: str) -> None:
        # An integer range with a value that is not an integer gives an error.
        text = _spec(declaration, 'Then the screen shows "beans level" not equal to 3')
        with pytest.raises(ConsistencyError, match="Invalid integer value in range"):
            _parse_spec(text)


class TestArraysAndStructures:
    @pytest.mark.parametrize("declaration, varid, elem_type, max_length", [
        ('"recent levels" is an array of at most 10 unique elements where each element '
         'is an integer with range [0,12]', "recent-levels", "integer", 10),
        (_BEANS, "beans", "string", 3),
    ])
    def test_unique_array_shape(self, declaration: str, varid: str, elem_type: str, max_length: int) -> None:
        # A unique array gives an "array" location variable with unique, element type and lengths.
        sts = _parse_spec_single(_spec(declaration, 'Then the screen shows done'))
        assert sts["locationVariables"][varid] == {
            "type": "array", "unique": True, "elements": {"type": elem_type},
            "minLength": 0, "maxLength": max_length,
        }

    def test_non_unique_array_has_unique_false(self) -> None:
        # An array without "unique" has unique == false.
        sts = _parse_spec_single(_spec('"recent levels" is an array of at most 3 elements where each element '
                                'is an integer with range [0,12]', 'Then the screen shows done'))
        assert sts["locationVariables"]["recent-levels"]["unique"] is False

    def test_unique_array_of_floats_is_accepted(self) -> None:
        # A unique array of floats (continuous range) is valid.
        sts = _parse_spec_single(_spec('"temperatures" is an array of at most 5 unique elements where each element '
                                'is a float with range (80.0,96.0)',
                                'Then the screen shows "temperatures" contains 90.5'))
        assert sts["locationVariables"]["temperatures"]["unique"] is True

    def test_unique_array_of_structures_shape(self) -> None:
        # An array of structures gives "structure" elements with their attributes.
        sts = _parse_spec_single(_spec(
            '"orders" is an array of at most 6 unique elements where each element is a structure with '
            'attributes "type", "size" such that:\n'
            '    "type" is a string with range {Espresso, Americano}\n'
            '    "size" is a string with range {Small, Large}',
            'Then the screen shows done'))
        orders = sts["locationVariables"]["orders"]
        assert orders["type"] == "array"
        assert orders["unique"] is True
        assert orders["maxLength"] == 6
        assert orders["elements"] == {
            "type": "structure", "attributes": {"type": {"type": "string"}, "size": {"type": "string"}},
        }

    def test_structure_attributes_without_range_are_accepted(self) -> None:
        # Structure attributes in an array do not need a range.
        sts = _parse_spec_single(_spec(
            '"orders" is an array of at most 3 unique elements where each element is a structure with '
            'attributes "type" such that:\n'
            '    "type" is a string',
            'Then the screen shows done'))
        assert sts["locationVariables"]["orders"]["unique"] is True

    def test_plain_structure_is_one_variable_and_assignment_target(self) -> None:
        # A structure is one location variable. Its assignment sets the whole structure.
        sts = _parse_spec_single(_spec(
            '"drink" is a structure with attributes "type", "size" such that:\n'
            '    "type" is a string with range {Espresso, Americano}\n'
            '    "size" is a string with range {Small, Large}',
            'Then the screen shows done',
            when='When the user selects a "drink" such that:\n'
                 '    "drink" has attributes such that:\n'
                 "        \"type\" is equal to 'Americano'"))
        assert sts["locationVariables"]["drink"] == {
            "type": "structure",
            "attributes": {"type": {"type": "string"}, "size": {"type": "string"}},
        }
        assignments = [a for a in sts["assignments"].values() if a["target"] == "drink"]
        assert assignments == [{"target": "drink", "expression": {"var": "drink_p"}}]
        assert "drink_p[type]" in render_guard_expr(_when_guard(sts))

    def test_element_guard_on_array_of_structures(self) -> None:
        # "has at least 1 elements where each element has attributes such that" parses.
        sts = _parse_spec_single(_spec(
            '"orders" is an array of at most 6 unique elements where each element is a structure with '
            'attributes "type", "size" such that:\n'
            '    "type" is a string with range {Espresso, Americano}\n'
            '    "size" is a string with range {Small, Large}',
            'Then the screen shows done',
            when='When the user checks "orders" such that:\n'
                 '    "orders" has at least 1 elements where each element has attributes such that:\n'
                 "        \"type\" is equal to 'Espresso'"))
        assert "type" in render_guard_expr(_when_guard(sts))


class TestCollectionGuards:
    def test_contains(self) -> None:
        # "contains" gives an "elem" node.
        sts = _parse_spec_single(_spec(_BEANS, "Then the screen shows \"beans\" contains 'ARABICA'"))
        assert _step_guards(sts) == [{"lhs": {"string": "ARABICA"}, "op": "elem", "rhs": {"var": "beans_p"}}]

    def test_does_not_contain(self) -> None:
        # "does not contain" gives "not" over an "elem" node.
        sts = _parse_spec_single(_spec(_BEANS, "Then the screen shows \"beans\" does not contain 'ARABICA'"))
        assert _step_guards(sts) == [
            {"op": "not", "rhs": {"lhs": {"string": "ARABICA"}, "op": "elem", "rhs": {"var": "beans_p"}}}
        ]

    def test_contains_only(self) -> None:
        # "contains only" gives an "elem" node AND a length of 1.
        sts = _parse_spec_single(_spec(_BEANS, "Then the screen shows \"beans\" contains only 'ARABICA'"))
        assert _step_guards(sts) == [{
            "lhs": {"lhs": {"string": "ARABICA"}, "op": "elem", "rhs": {"var": "beans_p"}},
            "op": "&&",
            "rhs": {"lhs": {"op": "len", "rhs": {"var": "beans_p"}}, "op": "==", "rhs": {"integer": 1}},
        }]

    def test_contains_in_such_that_block(self) -> None:
        # "contains" works in a "such that" block with other conditions.
        sts = _parse_spec_single(_spec(_BEANS + '\n"available" is a boolean', 'Then the screen shows done',
                                when='When the user checks "beans", "available" such that:\n'
                                     "    \"beans\" contains 'ARABICA' AND\n"
                                     '    "available" is equal to true'))
        assert "available_p == True" in render_guard_expr(_when_guard(sts))

    def test_is_empty_and_is_not_empty(self) -> None:
        # "is empty" gives length == 0. "is not empty" gives length != 0.
        sts = _parse_spec_single(_spec(_BEANS, 'Then the screen shows "beans" is empty\n'
                                        'And the screen shows "beans" is not empty'))
        length = {"op": "len", "rhs": {"var": "beans_p"}}
        guards = list(sts["guards"].values())
        assert {"lhs": length, "op": "==", "rhs": {"integer": 0}} in guards
        assert {"lhs": length, "op": "!=", "rhs": {"integer": 0}} in guards

    def test_is_empty_in_such_that_block(self) -> None:
        # "is empty" works in a "such that" block with other conditions.
        sts = _parse_spec_single(_spec(_BEANS + '\n"available" is a boolean', 'Then the screen shows done',
                                when='When the user checks "beans", "available" such that:\n'
                                     '    "beans" is empty AND\n'
                                     '    "available" is equal to true'))
        assert "beans_p.len == 0" in render_guard_expr(_when_guard(sts))

    def test_contains_all_possible_elements(self) -> None:
        # "contains all possible elements" gives one check for each range value, with AND.
        sts = _parse_spec_single(_spec(_BEANS, 'Then the screen shows "beans" contains all possible elements'))
        guard = _then_guard(sts)
        assert guard["op"] == "&&"
        rendered = render_guard_expr(guard)
        for bean in ("ARABICA", "ROBUSTA", "LIBERICA"):
            assert bean in rendered


class TestMembership:
    _SIZES = ('"sizes" is the array of unique strings {SMALL, LARGE}\n'
              '"size" is a string with range {SMALL, LARGE, XL}')

    def test_in_constant(self) -> None:
        # "in" with a constant array gives an "elem" node on the constant.
        sts = _parse_spec_single(_spec(self._SIZES, 'Then the screen shows done',
                                when='When the user selects a "size" such that:\n'
                                     '    "size" in "sizes"'))
        assert _step_guards(sts) == [{"lhs": {"var": "size_p"}, "op": "elem", "rhs": {"var": "sizes"}}]

    def test_not_in_constant(self) -> None:
        # "not in" gives "not" over an "elem" node.
        sts = _parse_spec_single(_spec(self._SIZES, 'Then the screen shows done',
                                when='When the user selects a "size" such that:\n'
                                     '    "size" not in "sizes"'))
        assert _step_guards(sts) == [
            {"op": "not", "rhs": {"lhs": {"var": "size_p"}, "op": "elem", "rhs": {"var": "sizes"}}}
        ]

    def test_in_without_such_that(self) -> None:
        # The short form ("size" in "sizes") gives the same "elem" node.
        sts = _parse_spec_single(_spec(self._SIZES, 'Then the screen shows "size" in "sizes"'))
        assert _step_guards(sts) == [{"lhs": {"var": "size_p"}, "op": "elem", "rhs": {"var": "sizes"}}]

    def test_in_variable_array(self) -> None:
        # "in" with an array variable (not a constant) gives an "elem" node on the state variable.
        sts = _parse_spec_single(_spec(_BEANS + '\n"bean" is a string with range {ARABICA, ROBUSTA, LIBERICA}',
                                'Then the screen shows "bean" in "beans"'))
        assert _step_guards(sts) == [{"lhs": {"var": "bean_p"}, "op": "elem", "rhs": {"var": "beans"}}]

    def test_in_undeclared_name_raises(self) -> None:
        # "in" with a name that is not declared gives an error.
        text = _spec('"size" is a string with range {SMALL, LARGE}',
                     'Then the screen shows "size" in "unknown sizes"')
        with pytest.raises(ConsistencyError, match="Variable 'unknown sizes' referenced in guard value but never declared"):
            _parse_spec(text)


class TestBooleanSugar:
    @pytest.mark.parametrize("then, expected", [
        ('Then the machine is "available"', True),
        ('Then the machine is not "available"', False),
        ('Then the machine does not care but reports "available"', True),
    ])
    def test_bare_variable_in_then(self, then: str, expected: bool) -> None:
        # A bare variable means == true. A negation word at the end of the step text means == false.
        sts = _parse_spec_single(_spec('"available" is a boolean', then))
        assert _then_guard(sts) == {"lhs": {"var": "available_p"}, "op": "==", "rhs": {"boolean": expected}}

    def test_bare_variable_in_when(self) -> None:
        # The bare variable form also works in a When step.
        sts = _parse_spec_single(_spec('"available" is a boolean', 'Then the screen shows done',
                                when='When the machine is not "available"'))
        assert _when_guard(sts) == {"lhs": {"var": "available_p"}, "op": "==", "rhs": {"boolean": False}}

    def test_structure_attribute_sugar(self) -> None:
        # "drink" is [not] "hot" gives the attribute == true (or false).
        sts = _parse_spec_single(_spec('"drink" is a structure with attributes "hot" such that:\n'
                                '    "hot" is a boolean',
                                'Then the screen shows the "drink" is not "hot"\n'
                                'And the screen shows the "drink" is "hot"'))
        hot = {"lhs": {"var": "drink_p"}, "op": "project", "rhs": {"var": "hot"}}
        guards = list(sts["guards"].values())
        assert {"lhs": hot, "op": "==", "rhs": {"boolean": False}} in guards
        assert {"lhs": hot, "op": "==", "rhs": {"boolean": True}} in guards


class TestConstants:
    def test_integer_constant(self) -> None:
        # A constant is a location variable with no parameter and no guard. Its value is in initialValuation.
        sts = _parse_spec_single(_spec('"max beans level" is the integer 12\n'
                                '"beans level" is an integer with range [0,12]',
                                'Then the machine has a current "beans level" equal to "max beans level"'))
        assert sts["locationVariables"]["max-beans-level"] == {"type": "integer"}
        assert "max-beans-level_p" not in sts["parameters"]
        assert "G_max-beans-level" not in sts["guards"]
        assert sts["initialValuation"]["max-beans-level"] == 12
        comparisons = _comparisons_on(sts["guards"], "beans-level_p")
        assert {"var": "max-beans-level"} in [c["rhs"] for c in comparisons if c["op"] == "=="]

    @pytest.mark.parametrize("declaration, primtype, value", [
        ('"c" is the float 1.5', "float", 1.5),
        ("\"c\" is the string 'READY'", "string", "READY"),
        ('"c" is the boolean true', "boolean", True),
    ])
    def test_other_constant_types(self, declaration: str, primtype: str, value: object) -> None:
        # Float, string and boolean constants work the same way.
        sts = _parse_spec_single(_spec(f'{declaration}\n"available" is a boolean',
                                'Then the machine reports "available" equal to "c"'))
        assert sts["locationVariables"]["c"] == {"type": primtype}
        assert sts["initialValuation"]["c"] == value
        assert "G_c" not in sts["guards"]

    def test_array_constant(self) -> None:
        # An array constant is an array location variable with its elements as length. It has no parameter.
        sts = _parse_spec_single(_spec('"sizes" is the array of unique strings {SMALL, LARGE, XL}',
                                'Then the screen shows done'))
        assert sts["locationVariables"]["sizes"] == {
            "type": "array", "unique": True, "minLength": 3, "maxLength": 3, "elements": {"type": "string"},
        }
        assert "sizes_p" not in sts["parameters"]


class TestGateIdConsistency:
    _LEVELS = '"water level" is an integer with range [0,210]\n"beans level" is an integer with range [0,12]'

    def _input_gates(self, when_1: str, when_2: str) -> tuple[list[str], list[str]]:
        """Input gate IDs of two scenarios, each with its own When step.

        Args:
            when_1: When step of scenario 1.
            when_2: When step of scenario 2.

        Returns:
            The list of input gate IDs of each scenario.
        """
        text = (
            f'Variable Settings\n{self._LEVELS}\n\n'
            f'Scenario 01: first\nGiven the system is in its initial state\n{when_1}\nThen the screen shows done\n\n'
            f'Scenario 02: second\nGiven the system is in its initial state\n{when_2}\nThen the screen shows done\n'
        )
        sts_list = _parse_spec(text)
        return list(sts_list[0]["inputGates"]), list(sts_list[1]["inputGates"])

    def test_same_action_and_parameters_share_a_gate(self) -> None:
        # The same action with the same parameters has the same gate ID in all scenarios.
        gates_1, gates_2 = self._input_gates(
            'When the user fills "water level" such that:\n    "water level" is equal to 100',
            'When the user fills "water level" such that:\n    "water level" is equal to 200')
        assert gates_1 == gates_2

    def test_different_action_gets_a_different_gate(self) -> None:
        # A different action text gets a different gate ID.
        gates_1, gates_2 = self._input_gates(
            'When the user fills "water level" such that:\n    "water level" is equal to 100',
            'When the user clicks Brew')
        assert gates_1 != gates_2

    def test_same_action_different_parameters_gets_a_different_gate(self) -> None:
        # The same action text with different parameters gets a different gate ID.
        gates_1, gates_2 = self._input_gates(
            'When the user fills "water level" such that:\n    "water level" is equal to 100',
            'When the user fills "water level", "beans level" such that:\n'
            '    "water level" is equal to 100 AND\n    "beans level" is equal to 6')
        assert gates_1 != gates_2
