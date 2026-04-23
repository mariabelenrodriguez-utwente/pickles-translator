"""
Tests for pure helper functions.
"""
import pytest
from lark import Token

from src.transformer import (
    PicklesToSTS,
    _format_id,
    _serialize_guard,
    PrimGuard, StructGuard, ArrayGuard,
    AttrGuardEntry,
)

_pickles = PicklesToSTS()


class TestFormatId:
    @pytest.mark.parametrize("raw, expected", [
        ("foo bar",       "foo-bar"),
        ("no spaces",     "no-spaces"),
        ("already-clean", "already-clean"),
        ("a b c",         "a-b-c"),
        ("x",             "x"),
    ])
    def test_replaces_spaces(self, raw: str, expected: str) -> None:
        assert _format_id(raw) == expected


class TestInterpRange:
    @pytest.mark.parametrize("values, primtype, expected", [
        (["0", "120"],    "integer", [0, 120]),
        (["1.0", "3.0"],  "decimal", [1.0, 3.0]),
        (["true", "false"], "boolean", [True, False]),
        (["AV", "PART AV"], "string", ["AV", "PART AV"]),
        (["inf"],          "integer", ["inf"]),   # non-numeric falls back to str
        ([" 5 ", " 10 "], "integer", [5, 10]),    # strips whitespace
    ])
    def test_interp_range(
        self, values: list[str], primtype: str, expected: list
    ) -> None:
        assert _pickles._interp_range(values, primtype) == expected


class TestPreprocess:
    def test_no_struct_body_unchanged(self) -> None:
        text = 'Variable Settings\n"x" is a boolean with range [true,false]\n'
        assert _pickles._preprocess(text) == text

    def test_endstruct_appended_to_last_attrdesc(self) -> None:
        text = (
            '"obj" is a structure with attributes "a" such that:\n'
            '    "a" is a boolean with range [true,false]\n'
            'Scenario 01 "S"\n'
        )
        result = _pickles._preprocess(text)
        assert "<endstruct>" in result
        # The marker must appear on the attrdesc line, not after the blank line.
        attrdesc_line = [l for l in result.splitlines() if '"a" is a boolean' in l][0]
        assert attrdesc_line.endswith("<endstruct>")

    def test_endstruct_appended_at_end_of_file(self) -> None:
        text = '    "z" is a integer with range [0,10]'
        result = _pickles._preprocess(text)
        assert result.endswith("<endstruct>")

    def test_non_attrdesc_line_triggers_insertion_on_previous(self) -> None:
        text = (
            '    "a" is a boolean with range [true,false]\n'
            'SomeOtherKeyword\n'
        )
        result = _pickles._preprocess(text)
        lines = result.splitlines()
        assert lines[0].endswith("<endstruct>")
        assert "<endstruct>" not in lines[1]


class TestSerializeGuard:
    def _num(self, v: str) -> Token:
        return Token("UNSIGNED_NUMBER", v)

    def test_primguard_simple(self) -> None:
        guard = PrimGuard("==", self._num("5"))
        assert _serialize_guard(guard, "x") == "x == 5"

    def test_primguard_with_sanitized_context(self) -> None:
        guard = PrimGuard(">", self._num("18"))
        assert _serialize_guard(guard, "user-age") == "user-age > 18"

    def test_structguard_single_attribute(self) -> None:
        inner = PrimGuard("!=", self._num("0"))
        entry = AttrGuardEntry("lane", inner)
        guard = StructGuard([entry])
        assert _serialize_guard(guard, "det") == "det.lane != 0"

    def test_structguard_two_attributes_with_conjunction(self) -> None:
        e1 = AttrGuardEntry("lane",   PrimGuard("==", self._num("1")))
        e2 = AttrGuardEntry("length", PrimGuard(">",  self._num("2")), conj="AND")
        guard = StructGuard([e1, e2])
        result = _serialize_guard(guard, "obj")
        assert result == "obj.lane == 1 && obj.length > 2"

    @pytest.mark.parametrize("quantifier, count, max_slots", [
        ("exactly",  2, 3),
        ("at_least", 1, 3),
        ("at_most",  2, 3),
    ])
    def test_arrayguard_count_condition(
        self,
        quantifier: str,
        count: int,
        max_slots: int,
    ) -> None:
        inner = PrimGuard("==", self._num("1"))
        guard = ArrayGuard(quantifier, count, inner)
        result = _serialize_guard(guard, "items", {"items": (1, max_slots)})
        assert f"items.len() == {count}" in result

    def test_arrayguard_exactly_covers_only_declared_slots(self) -> None:
        inner = PrimGuard(">", self._num("0"))
        guard = ArrayGuard("exactly", 2, inner)
        result = _serialize_guard(guard, "arr", {"arr": (2, 2)})
        assert "(arr[0] > 0)" in result
        assert "(arr[1] > 0)" in result
        assert "arr[2]" not in result

    def test_arrayguard_at_most_all_slots_are_conditional(self) -> None:
        inner = PrimGuard("==", self._num("1"))
        guard = ArrayGuard("at_most", 1, inner)
        result = _serialize_guard(guard, "arr", {"arr": (1, 2)})
        assert "arr[0]" in result
        assert "arr[1]" in result
