"""
Tests for SpecValidator class.
"""
import re

import pytest

from lark import Token

from src.exceptions import ConsistencyError
from src.ast_nodes import (
    SpecSuite, VarDefBlock, VarDef,
    PrimitiveType, RangeSpec, StructType, AttrDesc,
    ArrayType, Cardinality,
    Scenario, When, Then, Step,
    GuardBlock, GuardEntry, VarRef,
    PrimGuard, CollectionGuard, StructGuard, ArrayGuard, AttrGuardEntry, AttrBoolGuard,
)
from src.specvalidator import SpecValidator


def _prim(primtype: str = "integer", range_: "RangeSpec | None" = None) -> PrimitiveType:
    return PrimitiveType(primtype, range_ or RangeSpec("closed", ["0", "100"]))


def _struct(*attr_names: str) -> StructType:
    attrs = [AttrDesc(name, _prim()) for name in attr_names]
    return StructType(attrs)


def _unique_array(primtype: str = "integer") -> ArrayType:
    """Equivalent to "is an array of at most 10 unique elements where each element 
    is a <primtype> ..."."""
    return ArrayType(Cardinality("at_most", [10]), _prim(primtype), unique=True)


def _unique_struct_array(*attr_names: str) -> ArrayType:
    """Equivalent to "is an array of at most 10 unique elements where each element is a
    structure with attributes ..."."""
    struct_type = _struct(*attr_names)
    return ArrayType(Cardinality("at_most", [10]), struct_type, unique=True)


def _bool_struct(attrid: str = "selected") -> StructType:
    return StructType([AttrDesc(attrid, PrimitiveType("boolean", None))])


def _suite(*vardefs: VarDef) -> SpecSuite:
    return SpecSuite(VarDefBlock(list(vardefs)), [])


def _const(name: str, primtype: str = "integer", value: str = "5") -> VarDef:
    return VarDef(name, PrimitiveType(primtype, RangeSpec("set", [value])), is_constant=True)


def _suite_with_consts(vardefs: list, constdefs: list) -> SpecSuite:
    return SpecSuite(VarDefBlock(list(vardefs) + list(constdefs)), [])


def _scenario_with_when_guard(guardblock: GuardBlock) -> Scenario:
    varids = [entry.varid for entry in guardblock.entries]
    step = Step("action", varids, guardblock)
    return Scenario("test", None, When([step]), Then([Step("result", [], None)]))


def _num(v: str) -> Token:
    return Token("UNSIGNED_NUMBER", v)


def _simple_prim_guard(varid: str, op: str = "==") -> GuardBlock:
    entry = GuardEntry(varid, PrimGuard(op, _num("1")))
    return GuardBlock([entry])


class TestSpecValidator:
    def test_valid_primitive_guard_passes(self) -> None:
        # An integer variable with a comparison guard is valid.
        suite    = _suite(VarDef("someInteger", _prim("integer")))
        scenario = _scenario_with_when_guard(_simple_prim_guard("someInteger"))
        SpecValidator(suite, scenario).validate()  # must not raise

    def test_undeclared_variable_raises(self) -> None:
        # A step parameter that is not declared gives an error.
        suite    = _suite(VarDef("unknownVar", _prim()))
        scenario = _scenario_with_when_guard(_simple_prim_guard("unknown"))
        validator = SpecValidator(suite, scenario)
        with pytest.raises(ConsistencyError, match=re.escape(
                "Variable 'unknown' used in step but never declared")):
            validator.validate()

    def test_undeclared_guard_variable_raises(self) -> None:
        # A guard on a variable that is not declared (and not a step parameter) gives an error.
        suite    = _suite(VarDef("someInteger", _prim()))
        step     = Step("action", [], _simple_prim_guard("unknown"))
        scenario = Scenario("test", None, When([step]), Then([Step("result", [], None)]))
        validator = SpecValidator(suite, scenario)
        with pytest.raises(ConsistencyError, match=re.escape(
                "Variable 'unknown' used in guard but never declared")):
            validator.validate()

    def test_struct_guard_on_primitive_raises(self) -> None:
        # A struct guard on an integer variable gives an error.
        suite = _suite(VarDef("someInteger", _prim("integer")))
        ag    = AttrGuardEntry("someAttribute", PrimGuard("==", _num('1')))
        gb    = GuardBlock([GuardEntry("someInteger", StructGuard([ag]))])
        scenario = _scenario_with_when_guard(gb)
        validator = SpecValidator(suite, scenario)
        with pytest.raises(ConsistencyError, match=re.escape(
                "Variable 'someInteger' has type 'PrimitiveType' but is used with a struct guard")):
            validator.validate()

    def test_unknown_attribute_in_struct_guard_raises(self) -> None:
        # A struct guard on an attribute that is not declared gives an error.
        suite = _suite(VarDef("obj", _struct("a", "b")))
        ag    = AttrGuardEntry("c", PrimGuard("==", _num("0")))
        gb    = GuardBlock([GuardEntry("obj", StructGuard([ag]))])
        scenario = _scenario_with_when_guard(gb)
        validator = SpecValidator(suite, scenario)
        with pytest.raises(ConsistencyError, match=re.escape(
                "Attribute 'c' is not declared in struct 'obj'")):
            validator.validate()

    def test_valid_struct_guard_passes(self) -> None:
        # A struct guard on a declared attribute is valid.
        suite = _suite(VarDef("obj", _struct("a", "b")))
        
        ag = AttrGuardEntry("a", PrimGuard("==", _num("1")))
        gb = GuardBlock([GuardEntry("obj", StructGuard([ag]))])
        scenario = _scenario_with_when_guard(gb)
        SpecValidator(suite, scenario).validate()  # must not raise

    def test_contains_guard_on_unique_array_passes(self) -> None:
        # "contains" on an array of unique values is valid.
        suite = _suite(VarDef("numbers", _unique_array()))
        gb = GuardBlock([GuardEntry("numbers", CollectionGuard("contains", _num("1")))])
        scenario = _scenario_with_when_guard(gb)
        SpecValidator(suite, scenario).validate()  # must not raise

    def test_not_contains_guard_on_unique_array_passes(self) -> None:
        # "does not contain" on an array of unique values is valid.
        suite = _suite(VarDef("numbers", _unique_array()))
        gb = GuardBlock([GuardEntry("numbers", CollectionGuard("not_contains", _num("1")))])
        scenario = _scenario_with_when_guard(gb)
        SpecValidator(suite, scenario).validate()  # must not raise

    def test_contains_guard_on_array_passes(self) -> None:
        # "contains" on an array (values not unique) is valid.
        card  = Cardinality("at_most", [3])
        suite = _suite(VarDef("items", ArrayType(card, _prim())))
        gb = GuardBlock([GuardEntry("items", CollectionGuard("contains", _num("1")))])
        scenario = _scenario_with_when_guard(gb)
        SpecValidator(suite, scenario).validate()  # must not raise

    @pytest.mark.parametrize("primtype", ["integer", "string", "boolean"])
    def test_contains_guard_on_primitive_raises(self, primtype: str) -> None:
        # "contains" on an integer, string or boolean variable gives an error.
        suite = _suite(VarDef("somePrimitive", PrimitiveType(primtype, None)))
        gb = GuardBlock([GuardEntry("somePrimitive", CollectionGuard("contains", _num("1")))])
        scenario = _scenario_with_when_guard(gb)
        validator = SpecValidator(suite, scenario)
        with pytest.raises(ConsistencyError, match=re.escape(
                "Variable 'somePrimitive' has type 'PrimitiveType' but is used with a CollectionGuard")):
            validator.validate()

    def test_arrayguard_on_unique_array_passes(self) -> None:
        # "has at least N elements where each element" on an array of unique values is valid.
        suite = _suite(VarDef("numbers", _unique_array()))
        elem_def = PrimGuard("==", _num("1"))
        gb = GuardBlock([GuardEntry("numbers", ArrayGuard("at_least", 1, elem_def))])
        scenario = _scenario_with_when_guard(gb)
        SpecValidator(suite, scenario).validate()  # must not raise

    def test_arrayguard_on_unique_struct_array_passes(self) -> None:
        # An element guard with attribute conditions on an array of unique structures is valid.
        suite = _suite(VarDef("filters", _unique_struct_array("a", "b")))
        ag = AttrGuardEntry("a", PrimGuard("==", _num("1")))
        elem_def = StructGuard([ag])
        gb = GuardBlock([GuardEntry("filters", ArrayGuard("at_least", 1, elem_def))])
        scenario = _scenario_with_when_guard(gb)
        SpecValidator(suite, scenario).validate()  # must not raise

    def test_arrayguard_on_primitive_raises(self) -> None:
        # An element guard on an integer variable gives an error.
        suite = _suite(VarDef("cardinality", _prim("integer")))
        elem_def = PrimGuard("==", _num("1"))
        gb = GuardBlock([GuardEntry("cardinality", ArrayGuard("at_least", 1, elem_def))])
        scenario = _scenario_with_when_guard(gb)
        validator = SpecValidator(suite, scenario)
        with pytest.raises(ConsistencyError, match=re.escape(
                "Variable 'cardinality' has type 'PrimitiveType' but is used with a ArrayGuard")):
            validator.validate()

    def test_constant_used_as_step_varid_raises(self) -> None:
        # A constant as a step parameter gives an error. A constant cannot change.
        suite = _suite_with_consts([], [_const("someConstant")])
        step = Step("action", ["someConstant"], None)
        scenario = Scenario("test", None, When([step]), Then([Step("result", [], None)]))
        validator = SpecValidator(suite, scenario)
        with pytest.raises(ConsistencyError, match=re.escape(
                "'someConstant' is a constant and cannot be used as a step variable")):
            validator.validate()

    def test_constant_as_guard_value_passes(self) -> None:
        # A constant as the value of a guard is valid.
        suite = _suite_with_consts([VarDef("cardinality", _prim("integer"))], [_const("mynum")])
        gb = GuardBlock([GuardEntry("cardinality", PrimGuard("==", VarRef("mynum")))])
        scenario = _scenario_with_when_guard(gb)
        SpecValidator(suite, scenario).validate()  # must not raise

    def test_contains_only_on_unique_array_passes(self) -> None:
        # "contains only" on an array of unique values is valid.
        suite = _suite(VarDef("numbers", _unique_array()))
        gb = GuardBlock([GuardEntry("numbers", CollectionGuard("contains_only", _num("1")))])
        scenario = _scenario_with_when_guard(gb)
        SpecValidator(suite, scenario).validate()  # must not raise

    def test_contains_all_on_primitive_raises(self) -> None:
        # "contains all possible elements" on an integer variable gives an error.
        suite = _suite(VarDef("cardinality", _prim("integer")))
        gb = GuardBlock([GuardEntry("cardinality", CollectionGuard("contains_all", None))])
        scenario = _scenario_with_when_guard(gb)
        validator = SpecValidator(suite, scenario)
        with pytest.raises(ConsistencyError, match=re.escape(
                "Variable 'cardinality' has type 'PrimitiveType' but is used with a CollectionGuard")):
            validator.validate()

    def test_is_empty_on_unique_array_passes(self) -> None:
        # "is empty" on an array of unique values is valid.
        suite = _suite(VarDef("numbers", _unique_array()))
        gb = GuardBlock([GuardEntry("numbers", CollectionGuard("is_empty", None))])
        scenario = _scenario_with_when_guard(gb)
        SpecValidator(suite, scenario).validate()  # must not raise

    def test_is_empty_on_unique_struct_array_passes(self) -> None:
        # "is empty" on an array of unique structures is valid.
        suite = _suite(VarDef("filters", _unique_struct_array("a", "b")))
        gb = GuardBlock([GuardEntry("filters", CollectionGuard("is_empty", None))])
        scenario = _scenario_with_when_guard(gb)
        SpecValidator(suite, scenario).validate()  # must not raise

    def test_is_not_empty_on_primitive_raises(self) -> None:
        # "is not empty" on an integer variable gives an error.
        suite = _suite(VarDef("cardinality", _prim("integer")))
        gb = GuardBlock([GuardEntry("cardinality", CollectionGuard("is_not_empty", None))])
        scenario = _scenario_with_when_guard(gb)
        validator = SpecValidator(suite, scenario)
        with pytest.raises(ConsistencyError, match=re.escape(
                "Variable 'cardinality' has type 'PrimitiveType' but is used with a CollectionGuard")):
            validator.validate()

    def test_attr_bool_guard_on_struct_passes(self) -> None:
        # The boolean shorthand ("obj" is "selected") on a struct is valid.
        suite = _suite(VarDef("obj", _bool_struct("selected")))
        gb = GuardBlock([GuardEntry("obj", AttrBoolGuard("selected", True))])
        scenario = _scenario_with_when_guard(gb)
        SpecValidator(suite, scenario).validate()  # must not raise

    def test_attr_bool_guard_on_non_struct_raises(self) -> None:
        # The boolean shorthand on an integer variable gives an error.
        suite = _suite(VarDef("cardinality", _prim("integer")))
        gb = GuardBlock([GuardEntry("cardinality", AttrBoolGuard("selected", True))])
        scenario = _scenario_with_when_guard(gb)
        validator = SpecValidator(suite, scenario)
        with pytest.raises(ConsistencyError, match=re.escape(
                "Variable 'cardinality' has type 'PrimitiveType' but is used with the struct-attribute boolean syntactic sugar")):
            validator.validate()

    def test_attr_bool_guard_unknown_attribute_raises(self) -> None:
        # The boolean shorthand on an attribute that is not declared gives an error.
        suite = _suite(VarDef("obj", _bool_struct("selected")))
        gb = GuardBlock([GuardEntry("obj", AttrBoolGuard("missing", True))])
        scenario = _scenario_with_when_guard(gb)
        validator = SpecValidator(suite, scenario)
        with pytest.raises(ConsistencyError, match=re.escape(
                "Attribute 'missing' is not declared in struct 'obj'")):
            validator.validate()

    def test_attr_bool_guard_non_boolean_attribute_raises(self) -> None:
        # The boolean shorthand on an attribute that is not boolean gives an error.
        suite = _suite(VarDef("obj", _struct("cardinality")))
        gb = GuardBlock([GuardEntry("obj", AttrBoolGuard("cardinality", True))])
        scenario = _scenario_with_when_guard(gb)
        validator = SpecValidator(suite, scenario)
        with pytest.raises(ConsistencyError, match=re.escape(
                "Attribute 'cardinality' of struct 'obj' is not boolean")):
            validator.validate()
