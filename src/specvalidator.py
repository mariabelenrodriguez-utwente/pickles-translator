from .exceptions import ConsistencyError
from .transformer import (
    SpecSuite, Scenario,
    PrimitiveType, StructType, ArrayType,
    PrimGuard, StructGuard, ArrayGuard,
    GuardBlock,
)


class SpecValidator:

    def __init__(self, suite: SpecSuite, scenario: Scenario):
        self.suite    = suite
        self.scenario = scenario
        # name, type lookup for all variables
        self.variables = {
            vd.name: vd.typedesc for vd in suite.vardefblock.vardefs
        }

    def validate(self):
        self._check_scenario(self.scenario)

    def _check_scenario(self, scenario: Scenario) -> None:
        if scenario.given:
            for step in scenario.given.steps:
                if step.guardblock:
                    self._check_guardblock(step.guardblock)
        for step in scenario.when.steps:
            if step.guardblock:
                self._check_guardblock(step.guardblock)
        for step in scenario.then.steps:
            if step.guardblock:
                self._check_guardblock(step.guardblock)

    def _check_guardblock(self, guardblock: GuardBlock) -> None:
        for entry in guardblock.entries:
            self._check_guard_entry(entry)

    def _check_guard_entry(self, entry):
        varname = entry.varid

        # Variable must be declared
        if varname not in self.variables:
            raise ConsistencyError(
                f"Variable '{varname}' used in guard but never declared"
            )

        declared_type = self.variables[varname]
        guard         = entry.guard

        # Struct guard on a non-struct variable
        if isinstance(guard, StructGuard) and not isinstance(declared_type, StructType):
            raise ConsistencyError(
                f"Variable '{varname}' has type "
                f"'{type(declared_type).__name__}' but is used with a struct guard"
            )

        # Attribute names in struct guard must exist in the declaration
        if isinstance(guard, StructGuard) and isinstance(declared_type, StructType):
            declared_attrs = {a.attrid for a in declared_type.attrs}
            for ag in guard.entries:
                if ag.attrid not in declared_attrs:
                    raise ConsistencyError(
                        f"Attribute '{ag.attrid}' is not declared "
                        f"in struct '{varname}'"
                    )

        # Array guard on a non-array variable
        if isinstance(guard, ArrayGuard) and not isinstance(declared_type, ArrayType):
            raise ConsistencyError(
                f"Variable '{varname}' has type "
                f"'{type(declared_type).__name__}' but is used with an array guard"
            )

        # TODO: Add array checks
        # 1) all elements of an array must be of the same type
        # 2) array len should be consistent between variable settings and guards (e.g. a guard can't be "at least 5 elements" if in variable settings the same variable can have length up until 3)

        # TODO: Check satisfiability of guards w.r.t. range declared in variable settings.