from typing import NamedTuple

class SpecSuite:
    def __init__(self, vardefblock, scenarios):
        self.vardefblock = vardefblock
        self.scenarios   = scenarios


class VarDefBlock:
    def __init__(self, vardefs):
        self.vardefs = vardefs   # list[VarDef]


class VarDef:
    def __init__(self, name, typedesc, is_constant=False, initial_value=None):
        self.name          = name
        self.typedesc      = typedesc
        self.is_constant   = is_constant
        self.initial_value = initial_value


class InitialValue:
    def __init__(self, raw, is_array):
        self.raw      = raw       # Token (scalar) or RangeSpec (array), unresolved
        self.is_array = is_array


class RangeSpec(NamedTuple):
    """A parsed "with range ..." clause.

    kind: "open" (parentheses, exclusive bounds), "closed" (square
        brackets, inclusive bounds), or "set" (curly braces, discrete
        allowed values).
    values: the two bound values for "open"/"closed", or the full list
        of allowed values for "set".
    resolution: for a plain float variable's "open"/"closed" range only,
        an optional "and resolution N" grid step.
    """
    kind:       str
    values:     list
    resolution: str | None = None


class PrimitiveType:
    def __init__(self, primtype, range_):
        self.primtype = primtype   # "boolean" | "string" | "integer" | "float"
        self.range_   = range_     # RangeSpec | None


class ArrayType:
    def __init__(self, cardinality, element_type, unique=False):
        self.cardinality  = cardinality
        self.element_type = element_type
        self.unique       = unique   # True disallows duplicate elements (see the "uniqueElem" guard)


class Cardinality:
    def __init__(self, kind, values):
        self.kind   = kind    # "at_most" | "exactly" | "between"
        self.values = values  # [n] or [min, max]


class StructType:
    def __init__(self, attrs):
        self.attrs = attrs   # list[AttrDesc]


class AttrDesc:
    def __init__(self, attrid, typedesc):
        self.attrid   = attrid
        self.typedesc = typedesc


class Documentation(str):
    """A scenario's optional documentation text."""


class Scenario:
    def __init__(self, description, given, when, then, documentation=""):
        self.description   = description
        self.given          = given    # Given | None
        self.when           = when
        self.then           = then
        self.documentation  = documentation


class Step:
    """One step within a Given/When/Then/And block."""
    def __init__(self, action, varids, guardblock):
        self.action     = action        # str; "INITCOND" for bare initial-state steps
        self.varids     = varids        # list[str], may be empty
        self.guardblock = guardblock    # GuardBlock | None  (None → guard is "true")


class Given:
    def __init__(self, steps):
        self.steps = steps   # list[Step]


class When:
    def __init__(self, steps):
        self.steps = steps   # list[Step]


class Then:
    def __init__(self, steps):
        self.steps = steps   # list[Step]


class GuardBlock:
    def __init__(self, entries):
        self.entries = entries   # list[GuardEntry]


class GuardEntry:
    def __init__(self, varid, guard, conj=None, stored=False):
        self.varid  = varid
        self.guard  = guard
        self.conj   = conj
        self.stored = stored


class PrimGuard:
    def __init__(self, op, value):
        self.op    = op
        self.value = value


class CollectionGuard:
    def __init__(self, op, value):
        self.op    = op
        self.value = value


class LengthGuard:
    def __init__(self, op, value):
        self.op    = op
        self.value = value


class StructGuard:
    def __init__(self, entries):
        self.entries = entries   # list[AttrGuardEntry]


class ArrayGuard:
    def __init__(self, quantifier, count, element_guard):
        self.quantifier    = quantifier
        self.count         = count
        self.element_guard = element_guard


class AttrGuardEntry:
    def __init__(self, attrid, guard, conj=None):
        self.attrid = attrid
        self.guard  = guard
        self.conj   = conj


class AttrBoolGuard:
    """Sugar for a single boolean struct attribute"""
    def __init__(self, attrid, value):
        self.attrid = attrid
        self.value  = value   # True / False


class AttrBoolStepSugar:
    def __init__(self, subj, attrid, value):
        self.subj   = subj   # VarRef -- the struct variable
        self.attrid = attrid
        self.value  = value  # True / False


class VarRef:
    def __init__(self, varid, stored=False):
        self.varid  = varid
        self.stored = stored


class ExpValue:
    def __init__(self, value):
        self.value = value