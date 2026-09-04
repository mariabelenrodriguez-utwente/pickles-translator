from lark import Tree

from .exceptions import ConsistencyError
from .ast_nodes import (
    SpecSuite, Scenario,
    PrimitiveType, StructType, ArrayType,
    PrimGuard, CollectionGuard, StructGuard, ArrayGuard, AttrBoolGuard, LengthGuard,
    GuardBlock, VarRef,
)


class SpecValidator:

    def __init__(self, suite: SpecSuite, scenario: Scenario):
        self.suite    = suite
        self.scenario = scenario
        # name, type lookup for all variables incl. constants
        self.variables = {
            vd.name: vd.typedesc for vd in suite.vardefblock.vardefs
        }
        self.constants = {
            vd.name: vd for vd in suite.vardefblock.vardefs if vd.is_constant
        }

    def validate(self):
        self._check_scenario(self.scenario)

    def _check_scenario(self, scenario: Scenario) -> None:
        steps = []
        if scenario.given:
            steps.extend(scenario.given.steps)
        steps.extend(scenario.when.steps)
        steps.extend(scenario.then.steps)
        for step in steps:
            self._check_step_varids(step)
            if step.guardblock:
                self._check_guardblock(step.guardblock)

    def _check_step_varids(self, step) -> None:
        """
        Check that all variables referenced in a step were declared 
        in Variable Settings.
        """
        for v in step.varids:
            if v in self.constants:
                raise ConsistencyError(
                    f"'{v}' is a constant and cannot be used as a step variable "
                    "(constants can be guarded, never assigned)"
                )
            if v not in self.variables:
                raise ConsistencyError(
                    f"Variable '{v}' used in step but never declared"
                )

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

        # Array guard on a variable that's not an array
        if type(guard) in [ArrayGuard, CollectionGuard, LengthGuard] and not isinstance(declared_type, ArrayType):
            raise ConsistencyError(
                f"Variable '{varname}' has type "
                f"'{type(declared_type).__name__}' but is used with a {type(guard)}"
            )

        # "is subset of" against a right-hand side that isn't an array
        if isinstance(guard, CollectionGuard) and guard.op == 'subset':
            if isinstance(guard.value, VarRef):
                rhs_type = self.variables.get(guard.value.varid)
                if rhs_type is not None and not isinstance(rhs_type, ArrayType):
                    raise ConsistencyError(
                        f"'{guard.value.varid}' has type '{type(rhs_type).__name__}' but is used as "
                        "the right-hand side of a 'subset' guard."
                    )

        # Struct-attribute boolean shorthand ("struct" is [not] "attr")
        if isinstance(guard, AttrBoolGuard):
            if not isinstance(declared_type, StructType):
                raise ConsistencyError(
                    f"Variable '{varname}' has type "
                    f"'{type(declared_type).__name__}' but is used with the "
                    f"struct-attribute boolean shorthand (\"is [not] '{guard.attrid}'\")"
                )
            attr = next((a for a in declared_type.attrs if a.attrid == guard.attrid), None)
            if attr is None:
                raise ConsistencyError(
                    f"Attribute '{guard.attrid}' is not declared in struct '{varname}'"
                )
            if not (isinstance(attr.typedesc, PrimitiveType) and attr.typedesc.primtype == 'boolean'):
                raise ConsistencyError(
                    f"Attribute '{guard.attrid}' of struct '{varname}' is not boolean, "
                    "so it can't be used with the \"is [not] 'attr'\" shorthand"
                )

        # Any variable referenced within the guard's value(s) must be declared too
        self._check_guard_value_declared(guard)


    def _check_guard_value_declared(self, guard) -> None:
        """Any variable referenced by a guard's value must itself be a 
        declared variable.
        """
        if isinstance(guard, (PrimGuard, CollectionGuard)):
            if guard.op in ('in', 'not_in') and isinstance(guard.value, Tree):
                # The rhs of "in"/"not in" (see SpecTransformer.inguard) is
                # either an inline {...} literal (nothing to check) or a
                # named reference
                children = guard.value.children
                if len(children) == 1 and isinstance(children[0], VarRef):
                    varid = children[0].varid
                    if varid not in self.variables:
                        raise ConsistencyError(
                            f"Variable '{varid}' referenced in guard value but never declared"
                        )
                return
            values = guard.value if isinstance(guard.value, tuple) else (guard.value,)
            for v in values:
                if isinstance(v, VarRef) and v.varid not in self.variables:
                    raise ConsistencyError(
                        f"Variable '{v.varid}' referenced in guard value but never declared"
                    )
        elif isinstance(guard, StructGuard):
            for entry in guard.entries:
                self._check_guard_value_declared(entry.guard)
        elif isinstance(guard, ArrayGuard):
            self._check_guard_value_declared(guard.element_guard)


def check_action_parameter_consistency(suite: SpecSuite) -> list[str]:
    """Check whether every When/Then step action (across every scenario in
    the suite) is always used with the same set of parameter variables.

    Non-blocking: returns a list of warning messages instead of raising.
    Parameter order doesn't matter: "do X" with 'a','b' is the same as
    with 'b','a'. 
    """
    seen: dict[str, set[str]] = {}
    warnings: list[str] = []
    for scenario in suite.scenarios:
        steps = list(scenario.when.steps) + list(scenario.then.steps)  # given are excluded
        for step in steps:
            params = set(step.varids)
            prior = seen.get(step.action)
            if prior is None:
                seen[step.action] = params
            elif prior != params:
                warnings.append(
                    f"Action '{step.action}' is used with different parameters: "
                    f"{sorted(prior)} vs {sorted(params)}"
                )
    return warnings