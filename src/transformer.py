from itertools import combinations
from lark import Transformer, Token, Lark
from pathlib import Path
import re as _re

_OP_MAP = {
    'EQUAL_TO':             '==',
    'NOT_EQUAL_TO':         '!=',
    'GREATER_THAN':         '>',
    'LOWER_THAN':           '<',
    'GREATER_OR_EQUAL_THAN': '>=',
    'LOWER_OR_EQUAL_THAN':  '<=',
}

_CONJ_MAP = {
    # English
    'AND': '&&', 'OR': '||',
    # Spanish
    'Y':   '&&', 'O':  '||',
    # Dutch
    'EN':  '&&', 'OF': '||',
}

_QUANT_MAP = {
    'AT_LEAST': 'at_least',
    'AT_MOST':  'at_most',
    'EXACTLY':  'exactly',
    'BETWEEN':  'between',
}


# Matches an *indented* attrdesc line: leading whitespace, then "attrid" is a/an/un/una/een TYPE
# Guard lines (has attributes, is equal to, AND ...) do NOT match this pattern.
_ATTRDESC_LINE = _re.compile(
    r'^\s+"[^"]+"\s+(is a |is an |es una? |is een )',
    _re.IGNORECASE,
)

_RESOURCES = Path(__file__).parent.parent / "resources"


class SpecSuite:
    def __init__(self, vardefblock, scenarios):
        self.vardefblock = vardefblock
        self.scenarios   = scenarios


class VarDefBlock:
    def __init__(self, vardefs):
        self.vardefs = vardefs   # list[VarDef]


class VarDef:
    def __init__(self, name, typedesc):
        self.name     = name
        self.typedesc = typedesc


class PrimitiveType:
    def __init__(self, primtype, range_):
        self.primtype = primtype   # "boolean" | "string" | "integer" | "decimal"
        self.range_   = range_     # list of stripped strings


class ArrayType:
    def __init__(self, cardinality, element_type):
        self.cardinality  = cardinality
        self.element_type = element_type


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


class Scenario:
    def __init__(self, description, given, when, then):
        self.description = description
        self.given       = given    # Given | None
        self.when        = when
        self.then        = then


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

    def to_expr(self, var_card=None):
        parts = []
        for entry in self.entries:
            expr = _serialize_guard(entry.guard, _format_id(entry.varid), var_card)
            if entry.conj:
                parts.append(f" {_CONJ_MAP.get(entry.conj, entry.conj)} {expr}")
            else:
                parts.append(expr)
        return "".join(parts)


class GuardEntry:
    def __init__(self, varid, guard, conj=None):
        self.varid = varid
        self.guard = guard
        self.conj  = conj   # None | "AND" | "OR" (raw language keyword)


class PrimGuard:
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


class VarRef:
    def __init__(self, varid, stored=False):
        self.varid  = varid
        # TODO: Check if additional logic around stored is needed
        self.stored = stored


class ExpValue:
    """Wrapper returned by the expvalue transformer rule so step() can identify it."""
    def __init__(self, value):
        self.value = value


def _serialize_value(v):
    """Render a guard RHS (Token / VarRef / list / Tree) as a string."""
    if isinstance(v, VarRef):
        return _format_id(v.varid)
    if isinstance(v, list):
        return str(v)
    if isinstance(v, Token):
        if v.type == 'STR_LIT':
            return str(v)
        return str(v)
    return _serialize_tree(v)


def _serialize_tree(tree):
    _bin = {'add': '+', 'sub': '-', 'mul': '*', 'div': '/'}
    if tree.data in _bin:
        l = _serialize_value(tree.children[0])
        r = _serialize_value(tree.children[1])
        return f"({l} {_bin[tree.data]} {r})"
    if tree.data == 'in_op':
        l = _serialize_value(tree.children[0])
        r = _serialize_set_expr(tree.children[1])
        return f"{l} in {r}"
    if tree.data == 'not_in_op':
        l = _serialize_value(tree.children[0])
        r = _serialize_set_expr(tree.children[1])
        return f"{l} not in {r}"
    return str(tree)


def _serialize_set_expr(tree):
    items = ", ".join(_serialize_value(c) for c in tree.children)
    return "{" + items + "}"


def _serialize_guard(guard, context, var_card=None):
    """Serialize a guard with `context` as the subject (varid or varid.attrid).

    For ArrayGuard, generates a fully verbose combinatorial expansion: one
    disjunct per valid array length, each disjunct enumerating the relevant
    combinations of element slots.
    """
    if isinstance(guard, PrimGuard):
        if guard.op == 'between':
            lo, hi = guard.value
            return f"{context} >= {_serialize_value(lo)} && {context} <= {_serialize_value(hi)}"
        return f"{context} {guard.op} {_serialize_value(guard.value)}"
    if isinstance(guard, StructGuard):
        parts = []
        for ag in guard.entries:
            nested = f"{context}.{_format_id(ag.attrid)}"
            expr   = _serialize_guard(ag.guard, nested, var_card)
            if ag.conj:
                parts.append(f" {_CONJ_MAP.get(ag.conj, ag.conj)} {expr}")
            else:
                parts.append(expr)
        return "".join(parts)
    if isinstance(guard, ArrayGuard):
        min_slots, max_slots = (var_card or {}).get(context, (guard.count, guard.count))
        n = guard.count

        def _slot_cond(i):
            return _serialize_guard(guard.element_guard, f'{context}[{i}]', var_card)

        if guard.quantifier == 'exactly':
            # For each valid length L, enumerate all C(L,N) combos where exactly
            # those N slots satisfy the element guard and the rest do not.
            length_cases = []
            for L in range(max(n, min_slots), max_slots + 1):
                all_idx = list(range(L))
                combo_parts = []
                for combo in combinations(all_idx, n):
                    satisfying     = set(combo)
                    not_satisfying = set(all_idx) - satisfying
                    pos = [f"({_slot_cond(i)})" for i in sorted(satisfying)]
                    neg = [f"!({_slot_cond(i)})" for i in sorted(not_satisfying)]
                    combo_parts.append(" && ".join(pos + neg))
                combos_expr = "(" + " || ".join(f"({p})" for p in combo_parts) + ")"
                length_cases.append(f"({context}.len() == {L} && {combos_expr})")
            return " || ".join(length_cases) if length_cases else "false"

        elif guard.quantifier == 'at_least':
            # For each valid length L >= N, enumerate all C(L,N) combos where
            # at least those N slots satisfy the element guard.
            length_cases = []
            for L in range(max(n, min_slots), max_slots + 1):
                combo_parts = [
                    " && ".join(f"({_slot_cond(i)})" for i in combo)
                    for combo in combinations(range(L), n)
                ]
                combos_expr = "(" + " || ".join(f"({p})" for p in combo_parts) + ")"
                length_cases.append(f"({context}.len() == {L} && {combos_expr})")
            return " || ".join(length_cases) if length_cases else "false"

        elif guard.quantifier == 'at_most':
            # For lengths <= N: trivially satisfied (not enough slots to exceed N).
            # For lengths > N: no C(L,N+1) combo may have all slots satisfy.
            length_cases = []
            for L in range(min_slots, max_slots + 1):
                if L <= n:
                    length_cases.append(f"({context}.len() == {L})")
                else:
                    clauses = [
                        "(" + " || ".join(f"!({_slot_cond(i)})" for i in combo) + ")"
                        for combo in combinations(range(L), n + 1)
                    ]
                    length_cases.append(
                        f"({context}.len() == {L} && {' && '.join(clauses)})"
                    )
            return " || ".join(length_cases) if length_cases else "false"

        elif guard.quantifier == 'all':
            # Every element in the array (any valid length) must satisfy the condition.
            length_cases = []
            for L in range(min_slots, max_slots + 1):
                if L == 0:
                    length_cases.append(f"({context}.len() == 0)")
                else:
                    slot_conds = " && ".join(f"({_slot_cond(i)})" for i in range(L))
                    length_cases.append(f"({context}.len() == {L} && {slot_conds})")
            return " || ".join(length_cases) if length_cases else "false"

        else:
            raise NotImplementedError(
                f"Support for {guard.quantifier} not implemented yet."
            )
    return str(guard)


def _format_id(s: str) -> str:
    """Replace spaces with middle dashes so IDs are valid in JSON and expressions."""
    return s.replace(' ', '-')


def _plain_strs(children):
    """Non-Token strings from children (rule results that are str)."""
    return [c for c in children if isinstance(c, str) and not isinstance(c, Token)]


def _tokens_of(children, *types):
    """Tokens matching any of the given type names."""
    return [c for c in children if isinstance(c, Token) and c.type in types]


class SpecTransformer(Transformer):

    def specsuite(self, children):
        vdb       = next(c for c in children if isinstance(c, VarDefBlock))
        scenarios = [c for c in children if isinstance(c, Scenario)]
        return SpecSuite(vdb, scenarios)

    def vardefblock(self, children):
        return VarDefBlock([c for c in children if isinstance(c, VarDef)])

    def vardef(self, children):
        name = _plain_strs(children)[0].strip()
        td   = next(c for c in children
                    if isinstance(c, (PrimitiveType, ArrayType, StructType)))
        return VarDef(name, td)

    def typedesc(self, children):
        return children[0]

    def primitive(self, children):
        primtype = _plain_strs(children)[0]
        range_   = next(c for c in children if isinstance(c, list))
        return PrimitiveType(primtype, range_)

    def primtype(self, children):
        return str(children[0])

    def range(self, children):
        return [v.strip() for v in str(children[0]).split(",")]

    def array(self, children):
        card  = next(c for c in children if isinstance(c, Cardinality))
        etype = next(c for c in children
                     if isinstance(c, (PrimitiveType, ArrayType, StructType)))
        return ArrayType(card, etype)

    def cardinality(self, children):
        # children[0] is AT_MOST / EXACTLY / BETWEEN token
        kind = _QUANT_MAP[children[0].type]
        nums = _tokens_of(children, 'UNSIGNED_NUMBER')
        return Cardinality(kind, [float(str(n)) for n in nums])

    def struct(self, children):
        return StructType([c for c in children if isinstance(c, AttrDesc)])

    def attrdesc(self, children):
        attrid = _plain_strs(children)[0].strip()
        td     = next(c for c in children
                      if isinstance(c, (PrimitiveType, ArrayType, StructType)))
        return AttrDesc(attrid, td)

    def attrid(self, children):
        return str(children[0]).strip()

    def varid(self, children):
        return str(children[0]).strip()

    def scenario(self, children):
        desc  = next((c for c in children
                      if isinstance(c, str) and not isinstance(c, Token)), "")
        given = next((c for c in children if isinstance(c, Given)), None)
        when  = next(c for c in children if isinstance(c, When))
        then  = next(c for c in children if isinstance(c, Then))
        return Scenario(desc.strip(), given, when, then)

    def scenariodescription(self, children):
        return str(children[0]).strip()

    def given(self, children):
        steps = []
        for c in children:
            if isinstance(c, Token) and c.type == 'INITCOND':
                steps.append(Step("INITCOND", [], None))
            elif isinstance(c, Step):
                steps.append(c)
        return Given(steps)

    def when(self, children):
        return When([c for c in children if isinstance(c, Step)])

    def then(self, children):
        return Then([c for c in children if isinstance(c, Step)])

    def andstep(self, children):
        for c in children:
            if isinstance(c, Token) and c.type == 'INITCOND':
                return Step("INITCOND", [], None)
            if isinstance(c, Step):
                return c
        return Step("", [], None)

    def step(self, children):
        action, varids, gb = None, [], None
        op_result, exp = None, None
        for c in children:
            if isinstance(c, tuple):
                op_result = c
            elif isinstance(c, ExpValue):
                exp = c
            elif isinstance(c, str) and not isinstance(c, Token):
                if action is None:
                    action = c.strip()
                else:
                    varids.append(c.strip())
            elif isinstance(c, GuardBlock):
                gb = c
        if op_result is not None and exp is not None and varids:
            varid = varids[-1]
            if op_result[0] == 'between':
                pguard = PrimGuard('between', (op_result[1], exp.value))
            else:
                pguard = PrimGuard(op_result[0], exp.value)
            gb = GuardBlock([GuardEntry(varid, pguard)])
        return Step(action or "", varids, gb)

    def steptext(self, children):
        return str(children[0]).strip()

    def guardblock(self, children):
        # Build a flat sequence of typed items, ignoring structural tokens
        items = []
        for c in children:
            if isinstance(c, Token) and c.type == 'CONJOP':
                items.append(('conj', str(c)))
            elif isinstance(c, str) and not isinstance(c, Token):
                items.append(('varid', c.strip()))
            elif isinstance(c, (PrimGuard, ArrayGuard, StructGuard)):
                items.append(('guard', c))

        entries, conj, i = [], None, 0
        while i < len(items):
            if items[i][0] == 'conj':
                conj = items[i][1]
                i += 1
                continue
            varid = items[i][1]
            guard = items[i + 1][1]
            entries.append(GuardEntry(varid, guard, conj))
            conj = None
            i += 2
        return GuardBlock(entries)

    def guard(self, children):
        return next(c for c in children
                    if isinstance(c, (PrimGuard, ArrayGuard, StructGuard)))

    def primguard(self, children):
        op_result = next(c for c in children if isinstance(c, tuple))
        exp = next(c for c in children if isinstance(c, ExpValue))
        if op_result[0] == 'between':
            return PrimGuard('between', (op_result[1], exp.value))
        return PrimGuard(op_result[0], exp.value)

    def op(self, children):
        tok = children[0]
        if tok.type == 'BETWEEN':
            return ('between', children[1])
        return (_OP_MAP[tok.type],)

    def structguard(self, children):
        entries, conj = [], None
        for c in children:
            if isinstance(c, Token) and c.type == 'CONJOP':
                conj = str(c)
            elif isinstance(c, AttrGuardEntry):
                entries.append(AttrGuardEntry(c.attrid, c.guard, conj))
                conj = None
        return StructGuard(entries)

    def attrguard(self, children):
        attrid = _plain_strs(children)[0].strip()
        guard  = next(c for c in children
                      if isinstance(c, (PrimGuard, ArrayGuard, StructGuard)))
        return AttrGuardEntry(attrid, guard)

    def arrayguard(self, children):
        count_tokens = _tokens_of(children, 'UNSIGNED_NUMBER')
        guard        = next(c for c in children
                            if isinstance(c, (PrimGuard, ArrayGuard, StructGuard)))
        if not count_tokens:
            return ArrayGuard('all', None, guard)
        quantifier = _plain_strs(children)[0]
        count      = int(float(str(count_tokens[0])))
        return ArrayGuard(quantifier, count, guard)

    def quantifier(self, children):
        # children: [Token('HAS'), Token('AT_LEAST' | 'AT_MOST' | 'EXACTLY')]
        return _QUANT_MAP[children[1].type]

    def expvalue(self, children):
        return ExpValue(children[0])

    def varref(self, children):
        stored = bool(_tokens_of(children, 'STORED'))
        varid  = _plain_strs(children)[0].strip()
        return VarRef(varid, stored)


class PicklesToSTS:
    def __init__(self, spec_suite: SpecSuite = None):
        self.spec_suite = spec_suite

    def tree_to_sts(self, tree, start_id: int) -> tuple[list, int]:
        """
        Transform, validate, and convert a parsed spec tree to a list of STS dicts.

        Args:
            tree: Lark parse tree produced by the spec parser.
            start_id: First integer ID to assign to scenarios in this file.

        Returns:
            A tuple (sts_list, next_available_id).  Each scenario produces one
            STS entry with a globally unique id.

        Raises:
            ConsistencyError: on the first semantic validation failure.
        """
        from src.specvalidator import SpecValidator  # local import avoids circular dep
        suite  = SpecTransformer().transform(tree)
        result = []
        sid    = start_id

        for scenario in suite.scenarios:
            SpecValidator(suite, scenario).validate()
            result.append(self.scenario_to_sts(scenario, suite.vardefblock, sid))
            sid += 1

        return result, sid

    def _preprocess(self, text: str) -> str:
        """
        Append <endstruct> to the last attrdesc line of every struct body
        so the LALR parser can unambiguously detect when the struct ends.

        Only lines that look like "attrid" is a/an TYPE ... (indented) are
        treated as attrdesc lines; guard-block lines such as
        "var" has attributes such that: or AND "x" is equal to 5 are
        not affected.

        Args:
            text: Raw spec file content.

        Returns:
            Preprocessed text with <endstruct> markers inserted.
        """
        lines = text.split('\n')
        result = []
        in_struct_body = False

        # TODO: Optimize
        for line in lines:
            is_blank    = not line.strip()
            is_attrdesc = bool(_ATTRDESC_LINE.match(line))

            if in_struct_body and not is_blank and not is_attrdesc:
                result[-1] += '<endstruct>'
                in_struct_body = False

            result.append(line)

            if is_attrdesc:
                in_struct_body = True

        if in_struct_body:
            result[-1] += '<endstruct>'

        return '\n'.join(result)

    def load_parser(self, lang: str = "en") -> Lark:
        """
        Load the Lark parser with the appropriate token definitions for the given language.

        Args:
            lang: Language code (e.g., "en", "es", "nl").

        Returns:
            A Lark parser instance ready to parse spec suites in the specified language.
        """
        grammar_path = str(_RESOURCES / "spec_suite_core.lark")
        token_path   = str(_RESOURCES / "tokens" / lang)
        return Lark.open(
            grammar_path,
            import_paths=[token_path],
            parser="lalr",
            start="specsuite",
        )

    def _min_cardinality(self, card: Cardinality) -> int:
        """Return the lower bound of an array cardinality as an int.

        Args:
            card: Cardinality object.

        Returns:
            Integer lower bound (0 for at_most, the fixed n for exactly, min for between).
        """
        if card.kind == 'between':
            return int(card.values[0])
        if card.kind == 'exactly':
            return int(card.values[0])
        return 0  # at_most: minimum is 0

    def _max_cardinality(self, card: Cardinality) -> int:
        """Return the upper bound of an array cardinality as an int.

        Args:
            card: Cardinality object ('between' has [min, max]; others [n]).

        Returns:
            Integer upper bound.
        """
        return int(card.values[-1])

    def _expand_array_vars(self, name: str, array_td: ArrayType) -> list[dict]:
        """
        Expand an ArrayType into a flat list of location variable dicts.

        Args:
            name: Variable name as declared in the spec.
            array_td: The ArrayType descriptor for the variable.

        Returns:
            List of location-variable dicts ready for JSON serialisation.
        """
        max_card = self._max_cardinality(array_td.cardinality)
        sid      = _format_id(name)
        result   = []

        elem = array_td.element_type
        for i in range(max_card):
            prefix = f"{sid}[{i}]"
            if isinstance(elem, PrimitiveType):
                result.append({
                    "id":    prefix,
                    "type":  elem.primtype,
                    "range": self._interp_range(elem.range_, elem.primtype),
                })
            elif isinstance(elem, StructType):
                for attr in elem.attrs:
                    td    = attr.typedesc
                    entry = {"id": f"{prefix}.{_format_id(attr.attrid)}"}
                    if isinstance(td, PrimitiveType):
                        entry["type"]  = td.primtype
                        entry["range"] = self._interp_range(td.range_, td.primtype)
                    else:
                        entry["type"] = "unknown"
                    result.append(entry)
            else:
                result.append({"id": prefix, "type": "unknown"})
        return result

    def _interp_range(self, range_strs: list[str], primtype: str) -> list:
        """Convert a list of string range values to Python-typed values for JSON.

        Args:
            range_strs: Raw string values from the parsed range expression.
            primtype: One of "integer", "decimal", "boolean",
                or "string".

        Returns:
            List of appropriately typed Python values.
        """
        result = []
        for v in range_strs:
            v = v.strip()
            if primtype == 'integer':
                try:
                    result.append(int(v))
                except ValueError:
                    result.append(v)
            elif primtype == 'decimal':
                try:
                    result.append(float(v))
                except ValueError:
                    result.append(v)
            elif primtype == 'boolean':
                result.append(v.lower() == 'true')
            else:
                result.append(v)
        return result

    def _typedesc_to_location_var(self, name: str, td) -> dict:
        """Convert a variable declaration to a location-variable dict.

        Args:
            name: Variable name as declared in the spec.
            td: Type descriptor (PrimitiveType, StructType, or
                ArrayType).

        Returns:
            Location-variable dict for JSON serialisation.
        """
        sid = _format_id(name)
        if isinstance(td, StructType):
            return {
                "id":         sid,
                "type":       "structure",
                "attributes": [_format_id(a.attrid) for a in td.attrs],
            }
        if isinstance(td, PrimitiveType):
            return {
                "id":    sid,
                "type":  td.primtype,
                "range": self._interp_range(td.range_, td.primtype),
            }
        if isinstance(td, ArrayType):
            return {"id": sid, "type": "array"}
        return {"id": sid, "type": "unknown"}

    def _attrdesc_to_json(self, attr: AttrDesc) -> dict:
        """Serialise a struct attribute descriptor to a JSON-ready dict.

        Args:
            attr: AttrDesc from the parsed struct definition.

        Returns:
            Dict with keys id, type, and optionally range.
        """
        td = attr.typedesc
        if isinstance(td, PrimitiveType):
            return {
                "id":    _format_id(attr.attrid),
                "type":  td.primtype,
                "range": self._interp_range(td.range_, td.primtype),
            }
        return {"id": _format_id(attr.attrid), "type": "unknown"}

    def scenario_to_sts(self, scenario: Scenario, vardefblock: VarDefBlock, sts_id: int) -> dict:
        """Convert a single scenario and its variable block to an STS dict.

        Args:
            scenario: Parsed Scenario object.
            vardefblock: Variable declarations shared across scenarios in the file.
            sts_id: Unique integer ID for this STS.

        Returns:
            STS dict ready for JSON serialisation.
        """
        vardefs = vardefblock.vardefs

        # var_card: {formatted_name: (min_cardinality, max_cardinality)} for array variables
        var_card = {
            _format_id(vd.name): (
                self._min_cardinality(vd.typedesc.cardinality),
                self._max_cardinality(vd.typedesc.cardinality),
            )
            for vd in vardefs if isinstance(vd.typedesc, ArrayType)
        }

        # locationVariables: arrays are expanded into count + per-slot variables
        _lv_list = []
        for vd in vardefs:
            if isinstance(vd.typedesc, ArrayType):
                _lv_list.extend(self._expand_array_vars(vd.name, vd.typedesc))
            else:
                _lv_list.append(self._typedesc_to_location_var(vd.name, vd.typedesc))
        location_vars = {lv["id"]: {k: v for k, v in lv.items() if k != "id"} for lv in _lv_list}

        parameters = {
            lv_id + "_p": dict(lv_data)
            for lv_id, lv_data in location_vars.items()
        }

        # Flattened struct attribute definitions (plain structs + array-of-struct elements)
        _attr_list = []
        for vd in vardefs:
            if isinstance(vd.typedesc, StructType):
                for attr in vd.typedesc.attrs:
                    _attr_list.append(self._attrdesc_to_json(attr))
            elif isinstance(vd.typedesc, ArrayType):
                elem = vd.typedesc.element_type
                if isinstance(elem, StructType):
                    for attr in elem.attrs:
                        _attr_list.append(self._attrdesc_to_json(attr))
        attributes = {a["id"]: {k: v for k, v in a.items() if k != "id"} for a in _attr_list}

        initial_state = (
            scenario.given is not None
            and any(s.action == "INITCOND" for s in scenario.given.steps)
        )

        when_steps = scenario.when.steps   # list[Step]
        then_steps = scenario.then.steps   # list[Step]
        M, N       = len(when_steps), len(then_steps)

        # Locations: L0 … L(M+N)
        locations = [f"L{i}_{sts_id}" for i in range(M + N + 1)]

        # inputActions: one per When step
        input_switches = {
            f"In{sts_id}_{i+1}": {"text": s.action,
                                   "parameters": [_format_id(v) + "_p" for v in s.varids]}
            for i, s in enumerate(when_steps)
        }

        # outputActions: one per Then step
        output_switches = {
            f"Out{sts_id}_{j+1}": {"text": s.action,
                                    "parameters": [_format_id(v) + "_p" for v in s.varids]}
            for j, s in enumerate(then_steps)
        }

        guards: dict[str, dict] = {}
        g_idx  = 1

        # Given steps: each step with a guardblock contributes one guard entry.
        # These are AND-ed into the first When switch's guard.
        given_guard_ids = []
        if scenario.given:
            for step in scenario.given.steps:
                if step.guardblock:
                    gid = f"G{g_idx}"
                    given_guard_ids.append(gid)
                    guards[gid] = {"expression": step.guardblock.to_expr(var_card)}
                    g_idx += 1

        when_guard_ids = []
        for step in when_steps:
            gid = f"G{g_idx}"
            when_guard_ids.append(gid)
            expr = step.guardblock.to_expr(var_card) if step.guardblock else "true"
            guards[gid] = {"expression": expr}
            g_idx += 1

        then_guard_ids = []
        for step in then_steps:
            gid = f"G{g_idx}"
            then_guard_ids.append(gid)
            expr = step.guardblock.to_expr(var_card) if step.guardblock else "true"
            guards[gid] = {"expression": expr}
            g_idx += 1

        _switches_list = []

        for i, step in enumerate(when_steps):
            guard_ids  = when_guard_ids[i:i+1]
            if i == 0:
                guard_ids = given_guard_ids + guard_ids
            assignment = [{"variable": _format_id(v), "expression": _format_id(v) + "_p"}
                          for v in step.varids]
            _switches_list.append({
                "init_loc":   f"L{i}_{sts_id}",
                "gate":       f"In{sts_id}_{i+1}",
                "guard":      " && ".join(guard_ids) if guard_ids else "true",
                "assignment": assignment,
                "end_loc":    f"L{i+1}_{sts_id}",
            })

        for j, step in enumerate(then_steps):
            assignment = [{"variable": _format_id(v), "expression": _format_id(v) + "_p"}
                          for v in step.varids]
            _switches_list.append({
                "init_loc":   f"L{M+j}_{sts_id}",
                "gate":       f"Out{sts_id}_{j+1}",
                "guard":      then_guard_ids[j],
                "assignment": assignment,
                "end_loc":    f"L{M+j+1}_{sts_id}",
            })

        switches = {f"r_{idx + 1}": sw for idx, sw in enumerate(_switches_list)}

        return {
            "id":                f"sts_{sts_id:03d}",
            "description":       scenario.description,
            "initial_state":     initial_state,
            "initial_location":  locations[0],
            "locationVariables": location_vars,
            "parameters":        parameters,
            "attributes":        attributes,
            "locations":         locations,
            "inputActions":      input_switches,
            "outputActions":     output_switches,
            "guards":            guards,
            "switches":          switches,
        }
