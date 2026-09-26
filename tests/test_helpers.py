"""
Tests for pure helper functions.
"""
import pytest
from lark import Token

from src.exceptions import ConsistencyError
from src.transformer import (
    PicklesToSTS,
    _format_id,
    _serialize_guard,
    _serialize_value,
    _var,
    _ends_with_negation,
    render_guard_expr,
)
from src.ast_nodes import (
    PrimGuard, CollectionGuard, StructGuard, ArrayGuard, AttrBoolGuard,
    AttrGuardEntry, VarRef,
    RangeSpec,
    PrimitiveType, StructType, AttrDesc,
    ArrayType, Cardinality,
)

_pickles_to_sts = PicklesToSTS()
_INT_ARRAY = ArrayType(Cardinality("between", [0, 3]), PrimitiveType("integer", None))


def _num(v: str) -> Token:
    return Token("UNSIGNED_NUMBER", v)


class TestFormatId:
    @pytest.mark.parametrize("raw, expected", [
        ("Foo Bar",         "Foo-Bar"),
        ("no spaces",       "no-spaces"),
        ("already-clean",   "already-clean"),
        ("a B c",           "a-B-c"),
        ("x",               "x"),
        ("VARIABLE",        "VARIABLE"),
        ("año nuevo",       "año-nuevo"),
        ("señal de café",   "señal-de-café"),
        ("initiële waarde", "initiële-waarde"),
        ("Ñandú",           "Ñandú"),
    ])
    def test_replaces_spaces(self, raw: str, expected: str) -> None:
        # A name becomes an ID: spaces become dashes. The case does not change.
        assert _format_id(raw) == expected


class TestInterpRangeSpec:
    def test_none_stays_none(self) -> None:
        # No range gives no range.
        assert _pickles_to_sts._interp_range_spec(None, "boolean") is None

    @pytest.mark.parametrize("kind, values, primtype, expected_values", [
        ("open",   ["1.0", "3.0"], "float", [1.0, 3.0]),
        ("closed", ["0", "100"],   "integer", [0, 100]),
        ("set",    ["1", "10"],    "integer", [1, 10]),
        ("set",    ["READY", "BREWING", "ERROR"], "string",
         ["READY", "BREWING", "ERROR"]),
    ])
    def test_preserves_kind_and_types_values(
        self, kind: str, values: list[str], primtype: str, expected_values: list
    ) -> None:
        # A range keeps its kind. Its values get the type of the variable.
        result = _pickles_to_sts._interp_range_spec(RangeSpec(kind, values), primtype)
        assert result == RangeSpec(kind, expected_values)


class TestEndsWithNegation:
    @pytest.mark.parametrize("steptext", [
        "the system is not",
        "the system is NOT",
        "het systeem is niet",
        "not",
    ])
    def test_trailing_negation_word_detected(self, steptext: str) -> None:
        # A step text that ends with a negation word ("not", "niet") is found.
        assert _ends_with_negation(steptext) is True

    @pytest.mark.parametrize("steptext", [
        "the system is",
        "the system does not care but reports",  # "not" isn't the last word
        "",
        "   ",
    ])
    def test_non_trailing_or_absent_negation_not_detected(self, steptext: str) -> None:
        # A step text with no negation word at the end is not found.
        assert _ends_with_negation(steptext) is False


class TestSerializeValue:
    def test_serialize_bool(self) -> None:
        # A boolean value becomes a "boolean" literal.
        assert _serialize_value(True) == {"boolean": True}
        assert _serialize_value(False) == {"boolean": False}

    def test_serialize_float(self) -> None:
        # A decimal value (text or number) becomes a "float" literal.
        assert _serialize_value("1.5") == {"float": 1.5}
        assert _serialize_value(1.2) == {"float": 1.2}

    def test_serialize_loc_var(self) -> None:
        # A reference to a constant stays the variable itself (no "_p").
        assert _serialize_value(VarRef("mynum"), constant_ids={"mynum"}) == {"var": "mynum"}

    def test_serialize_param(self) -> None:
        # A reference to a variable that is not a constant becomes its parameter ("_p").
        assert _serialize_value(VarRef("mynum"), constant_ids={"other"}) == {"var": "mynum_p"}


class TestPreprocess:
    def test_no_struct_body_unchanged(self) -> None:
        # Text with no structure declaration does not change.
        text = 'Variable Settings\n"x" is a boolean with range [true,false]\n'
        assert _pickles_to_sts._preprocess(text) == text

    def test_endstruct_appended_to_last_attrdesc(self) -> None:
        # The end marker goes on the last attribute line of a structure.
        text = (
            '"obj" is a structure with attributes "a" such that:\n'
            '    "a" is a boolean with range [true,false]\n'
            'Scenario 01 "S"\n'
        )
        result = _pickles_to_sts._preprocess(text)
        assert "<endstruct>" in result
        # The marker must appear on the attrdesc line, not after the blank line.
        attrdesc_line = [l for l in result.splitlines() if '"a" is a boolean' in l][0]
        assert attrdesc_line.endswith("<endstruct>")

    def test_endstruct_appended_at_end_of_file(self) -> None:
        # The end marker goes on the last attribute line at the end of the file.
        text = '    "z" is a integer with range [0,10]'
        result = _pickles_to_sts._preprocess(text)
        assert result.endswith("<endstruct>")

    def test_non_attrdesc_line_triggers_insertion_on_previous(self) -> None:
        # A line that is not an attribute ends the structure on the line before.
        text = (
            '    "a" is a boolean with range [true,false]\n'
            'SomeOtherKeyword\n'
        )
        result = _pickles_to_sts._preprocess(text)
        lines = result.splitlines()
        assert lines[0].endswith("<endstruct>")
        assert "<endstruct>" not in lines[1]


class TestSerializeGuard:
    def test_primguard_simple(self) -> None:
        # A comparison on a parameter gives a comparison node.
        guard = PrimGuard("==", _num("5"))
        assert _serialize_guard(guard, _var("x_p")) == {"lhs": {"var": "x_p"}, "op": "==", "rhs": {"integer": 5}}

    def test_primguard_as_state(self) -> None:
        # A comparison on a state variable (Given step, "stored") keeps the variable name.
        guard = PrimGuard("==", _num("5"))
        assert _serialize_guard(guard, _var("x")) == {"lhs": {"var": "x"}, "op": "==", "rhs": {"integer": 5}}

    def test_primguard_rhs_always_param_even_when_lhs_is_stored(self) -> None:
        # A variable in the value always becomes its parameter, also when the subject is a state variable.
        guard = PrimGuard("==", VarRef("y"))
        assert _serialize_guard(guard, _var("x")) == {
            "lhs": {"var": "x"}, "op": "==", "rhs": {"var": "y_p"},
        }

    def test_primguard_with_sanitized_context(self) -> None:
        # A comparison on a subject with dashes in its ID keeps the ID.
        guard = PrimGuard(">", _num("18"))
        assert _serialize_guard(guard, _var("user-age_p")) == {"lhs": {"var": "user-age_p"}, "op": ">", "rhs": {"integer": 18}}

    def test_structguard_single_attribute(self) -> None:
        # An attribute condition uses a "project" node on the subject.
        inner = PrimGuard("!=", _num("0"))
        entry = AttrGuardEntry("lane", inner)
        guard = StructGuard([entry])
        td = StructType([AttrDesc("lane", PrimitiveType("integer", None))])
        assert _serialize_guard(guard, _var("det_p"), td, card_key="det") == {
            "lhs": {"lhs": {"var": "det_p"}, "op": "project", "rhs": {"var": "lane"}},
            "op":  "!=", "rhs": {"integer": 0},
        }

    def test_structguard_two_attributes_with_conjunction(self) -> None:
        # Two attribute conditions with AND give an "&&" node.
        e1 = AttrGuardEntry("lane",   PrimGuard("==", _num("1")))
        e2 = AttrGuardEntry("length", PrimGuard(">",  _num("2")), conj="AND")
        guard = StructGuard([e1, e2])
        td = StructType([
            AttrDesc("lane",   PrimitiveType("integer", None)),
            AttrDesc("length", PrimitiveType("integer", None)),
        ])
        result = _serialize_guard(guard, _var("obj_p"), td, card_key="obj")
        obj = {"var": "obj_p"}
        assert result == {
            "lhs": {"lhs": {"lhs": obj, "op": "project", "rhs": {"var": "lane"}},
                    "op": "==", "rhs": {"integer": 1}},
            "op":  "&&",
            "rhs": {"lhs": {"lhs": obj, "op": "project", "rhs": {"var": "length"}},
                    "op": ">", "rhs": {"integer": 2}},
        }
        assert render_guard_expr(result) == "((obj_p[lane] == 1) && (obj_p[length] > 2))"

    @pytest.mark.parametrize("quantifier, count", [
        ("exactly",  2),
        ("at_least", 2),
        ("at_most",  2),
    ])
    def test_arrayguard_count_condition(self, quantifier: str, count: int) -> None:
        # "has exactly/at least/at most N elements where each element" gives a "cardinality" node.
        inner = PrimGuard("==", _num("1"))
        guard = ArrayGuard(quantifier, count, inner)
        result = _serialize_guard(guard, _var("items_p"), _INT_ARRAY, {"items": (1, 3)}, card_key="items")
        assert result["op"] == "cardinality"
        assert result["quantifier"] == quantifier
        assert result["n"] == count
        assert result["over"] == {"var": "items_p"}
        assert result["lambda"] == "e1"

    def test_arrayguard_at_least_one_compiles_to_an_exists_node(self) -> None:
        # "has at least 1 element where each element" gives an "exists" node.
        inner = PrimGuard("==", _num("1"))
        guard = ArrayGuard("at_least", 1, inner)
        result = _serialize_guard(guard, _var("items_p"), _INT_ARRAY, {"items": (1, 3)}, card_key="items")
        assert result == {
            "op": "exists", "over": {"var": "items_p"}, "lambda": "e1",
            "expression": {"lhs": {"var": "e1"}, "op": "==", "rhs": {"integer": 1}},
        }

    def test_arrayguard_exactly_covers_only_declared_slots(self) -> None:
        # "has exactly N elements" puts the element condition on the element variable.
        inner = PrimGuard(">", _num("0"))
        guard = ArrayGuard("exactly", 2, inner)
        result = _serialize_guard(guard, _var("arr_p"), _INT_ARRAY, {"arr": (2, 2)}, card_key="arr")
        assert result["expression"] == {"lhs": {"var": "e1"}, "op": ">", "rhs": {"integer": 0}}

    def test_arrayguard_at_most_all_slots_are_conditional(self) -> None:
        # "has at most N elements" puts the element condition on the element variable.
        inner = PrimGuard("==", _num("1"))
        guard = ArrayGuard("at_most", 1, inner)
        result = _serialize_guard(guard, _var("arr_p"), _INT_ARRAY, {"arr": (1, 2)}, card_key="arr")
        assert result["expression"] == {"lhs": {"var": "e1"}, "op": "==", "rhs": {"integer": 1}}

    def test_arrayguard_all_compiles_to_a_compact_forall_node(self) -> None:
        # "has all elements where each element" gives a "forall" node.
        inner = PrimGuard("==", _num("1"))
        guard = ArrayGuard("all", None, inner)
        result = _serialize_guard(guard, _var("arr_p"), _INT_ARRAY, {"arr": (0, 3)}, card_key="arr")
        assert result == {
            "op": "forall", "over": {"var": "arr_p"}, "lambda": "e1",
            "expression": {"lhs": {"var": "e1"}, "op": "==", "rhs": {"integer": 1}},
        }

    def test_contains_is_an_elem_check(self) -> None:
        # "contains" gives an "elem" node (value in array).
        contains_guard = CollectionGuard("contains", _num("1"))
        var_card = {"numbers": (0, 3)}
        result = _serialize_guard(contains_guard, _var("numbers_p"), var_card=var_card, card_key="numbers")
        assert result == {"lhs": {"integer": 1}, "op": "elem", "rhs": {"var": "numbers_p"}}

    def test_not_contains_is_a_negated_elem_check(self) -> None:
        # "does not contain" gives "not" over an "elem" node.
        var_card = {"numbers": (0, 3)}
        not_contains_guard = CollectionGuard("not_contains", _num("1"))
        result = _serialize_guard(not_contains_guard, _var("numbers_p"), var_card=var_card, card_key="numbers")
        assert result == {
            "op": "not", "rhs": {"lhs": {"integer": 1}, "op": "elem", "rhs": {"var": "numbers_p"}},
        }

    def test_contains_only_is_an_elem_check_and_length_one(self) -> None:
        # "contains only" gives an "elem" node AND a length of 1.
        contains_only_guard = CollectionGuard("contains_only", _num("1"))
        var_card = {"numbers": (0, 3)}
        result = _serialize_guard(contains_only_guard, _var("numbers_p"), var_card=var_card, card_key="numbers")
        assert result == {
            "lhs": {"lhs": {"integer": 1}, "op": "elem", "rhs": {"var": "numbers_p"}},
            "op": "&&",
            "rhs": {"lhs": {"op": "len", "rhs": {"var": "numbers_p"}}, "op": "==", "rhs": {"integer": 1}},
        }

    def test_is_empty_checks_len_equals_zero(self) -> None:
        # "is empty" gives length == 0.
        guard = CollectionGuard("is_empty", None)
        assert _serialize_guard(guard, _var("numbers_p")) == {
            "lhs": {"op": "len", "rhs": {"var": "numbers_p"}}, "op": "==", "rhs": {"integer": 0},
        }

    def test_is_not_empty_checks_len_not_equal_zero(self) -> None:
        # "is not empty" gives length != 0.
        guard = CollectionGuard("is_not_empty", None)
        assert _serialize_guard(guard, _var("numbers_p")) == {
            "lhs": {"op": "len", "rhs": {"var": "numbers_p"}}, "op": "!=", "rhs": {"integer": 0},
        }

    def test_contains_all_ands_an_elem_check_per_domain_value(self) -> None:
        # "contains all possible elements" gives one "elem" node for each range value, with AND.
        guard = CollectionGuard("contains_all", None)
        domains = {"numbers": [1, 2, 3]}
        var_card = {"numbers": (0, 3)}
        result = _serialize_guard(guard, "numbers_p", var_card, card_key="numbers", domains=domains)
        assert result["op"] == "&&"
        rendered = render_guard_expr(result)
        for v in (1, 2, 3):
            assert f"{v} elem numbers_p" in rendered

    def test_contains_all_without_domain_raises(self) -> None:
        # "contains all possible elements" on a variable with no range gives an error.
        guard = CollectionGuard("contains_all", None)
        with pytest.raises(ConsistencyError, match="Cannot determine the domain"):
            _serialize_guard(guard, "numbers_p", {"numbers": (0, 3)}, card_key="numbers", domains={})

    def test_attr_bool_guard_true(self) -> None:
        # The boolean syntactic sugar ("obj" is "selected") gives attribute == true.
        guard = AttrBoolGuard("selected", True)
        td = StructType([AttrDesc("selected", PrimitiveType("boolean", None))])
        assert _serialize_guard(guard, _var("obj_p"), td) == {
            "lhs": {"lhs": {"var": "obj_p"}, "op": "project", "rhs": {"var": "selected"}},
            "op":  "==", "rhs": {"boolean": True},
        }

    def test_attr_bool_guard_false(self) -> None:
        # The negated boolean syntactic sugar ("obj" is not "selected") gives attribute == false.
        guard = AttrBoolGuard("selected", False)
        td = StructType([AttrDesc("selected", PrimitiveType("boolean", None))])
        assert _serialize_guard(guard, _var("obj_p"), td) == {
            "lhs": {"lhs": {"var": "obj_p"}, "op": "project", "rhs": {"var": "selected"}},
            "op":  "==", "rhs": {"boolean": False},
        }
