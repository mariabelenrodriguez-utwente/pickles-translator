"""
Tests for SpecValidator class.
"""
import pytest

from src.exceptions import ConsistencyError
from src.transformer import (
    SpecSuite, VarDefBlock, VarDef,
    PrimitiveType, StructType, AttrDesc,
    Scenario, When, Then, Step,
    GuardBlock, GuardEntry,
    PrimGuard, StructGuard, AttrGuardEntry,
)
from src.specvalidator import SpecValidator


def _prim(primtype: str = "integer", range_: list[str] | None = None) -> PrimitiveType:
    return PrimitiveType(primtype, range_ or ["0", "100"])


def _struct(*attr_names: str) -> StructType:
    attrs = [AttrDesc(name, _prim()) for name in attr_names]
    return StructType(attrs)


def _suite(*vardefs: VarDef) -> SpecSuite:
    return SpecSuite(VarDefBlock(list(vardefs)), [])


def _scenario_with_when_guard(guardblock: GuardBlock) -> Scenario:
    step = Step("action", ["var"], guardblock)
    return Scenario("test", None, When([step]), Then([Step("result", [], None)]))


def _simple_prim_guard(varid: str, op: str = "==") -> GuardBlock:
    from lark import Token
    entry = GuardEntry(varid, PrimGuard(op, Token("UNSIGNED_NUMBER", "1")))
    return GuardBlock([entry])


class TestSpecValidator:
    def test_valid_primitive_guard_passes(self) -> None:
        suite    = _suite(VarDef("count", _prim("integer")))
        scenario = _scenario_with_when_guard(_simple_prim_guard("count"))
        SpecValidator(suite, scenario).validate()  # must not raise

    def test_undeclared_variable_raises(self) -> None:
        suite    = _suite(VarDef("count", _prim()))
        scenario = _scenario_with_when_guard(_simple_prim_guard("unknown"))
        with pytest.raises(ConsistencyError, match="unknown"):
            SpecValidator(suite, scenario).validate()

    def test_struct_guard_on_primitive_raises(self) -> None:
        suite = _suite(VarDef("score", _prim("integer")))
        ag    = AttrGuardEntry("field", PrimGuard("==", __import__("lark").Token("UNSIGNED_NUMBER", "1")))
        gb    = GuardBlock([GuardEntry("score", StructGuard([ag]))])
        scenario = _scenario_with_when_guard(gb)
        with pytest.raises(ConsistencyError, match="struct guard"):
            SpecValidator(suite, scenario).validate()

    def test_unknown_attribute_in_struct_guard_raises(self) -> None:
        suite = _suite(VarDef("obj", _struct("a", "b")))
        ag    = AttrGuardEntry("c", PrimGuard("==", __import__("lark").Token("UNSIGNED_NUMBER", "0")))
        gb    = GuardBlock([GuardEntry("obj", StructGuard([ag]))])
        scenario = _scenario_with_when_guard(gb)
        with pytest.raises(ConsistencyError, match="not declared"):
            SpecValidator(suite, scenario).validate()

    def test_valid_struct_guard_passes(self) -> None:
        suite = _suite(VarDef("obj", _struct("a", "b")))
        from lark import Token
        ag = AttrGuardEntry("a", PrimGuard("==", Token("UNSIGNED_NUMBER", "1")))
        gb = GuardBlock([GuardEntry("obj", StructGuard([ag]))])
        scenario = _scenario_with_when_guard(gb)
        SpecValidator(suite, scenario).validate()  # must not raise
