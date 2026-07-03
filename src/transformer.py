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

    def to_tree(self, var_card=None, as_param=True):
        node = None
        for entry in self.entries:
            lhs_as_param = as_param and not entry.stored
            base         = _format_id(entry.varid)
            base_context = _leaf(base, lhs_as_param)
            expr = _serialize_guard(entry.guard, base_context, var_card, as_param, base)
            if node is None:
                node = expr
            else:
                node = {"lhs": node, "op": _CONJ_MAP.get(entry.conj, entry.conj), "rhs": expr}
        return node


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


def _fold(parts, op, identity):
    """Left-fold a list of guard tree nodes into one nested {lhs,op,rhs} tree.

    Args:
        parts: List of guard tree nodes/leaves to combine.
        op: Binary operator to join them with ("&&" or "||").
        identity: Value to return when parts is empty (True for "&&", False for "||").

    Returns:
        A single tree node/leaf.
    """
    if not parts:
        return identity
    node = parts[0]
    for p in parts[1:]:
        node = {"lhs": node, "op": op, "rhs": p}
    return node


def _leaf(context: str, as_param: bool) -> str:
    """Render a guard subject's context as a parameter or state reference.

    Args:
        context: The bare (state) variable/path, e.g. "det[0].lane".
        as_param: When True, refer to the communicated parameter (suffix
            "_p"); when False, refer to the persisted state variable as-is.

    Returns:
        The leaf string to use in a guard tree.
    """
    return f"{context}_p" if as_param else context


def _serialize_value(v, rhs_as_param: bool = True):
    """Convert a guard value (Token / VarRef / list / Tree) to a tree leaf/node.

    Args:
        v: The raw value node from the grammar.
        rhs_as_param: Whether a VarRef should resolve to the parameter
            (default, When/Then context) or the bare state variable (Given
            context). VarRef.stored is not consulted here: a guard's value
            always follows the surrounding step's context, never an
            individual "stored" marking on the subject.
    """
    if isinstance(v, VarRef):
        return _leaf(_format_id(v.varid), rhs_as_param)
    if isinstance(v, list):
        return v
    if isinstance(v, Token):
        if v.type == 'STR_LIT':
            return str(v)[1:-1]
        if v.type == 'BOOL_LIT':
            return str(v).lower() == 'true'
        s = str(v)
        return float(s) if '.' in s else int(s) if s.lstrip('-').isdigit() else s
    return _serialize_tree(v, rhs_as_param)


def _serialize_tree(tree, rhs_as_param: bool = True):
    _bin = {'add': '+', 'sub': '-', 'mul': '*', 'div': '/'}
    if tree.data in _bin:
        l = _serialize_value(tree.children[0], rhs_as_param)
        r = _serialize_value(tree.children[1], rhs_as_param)
        return {"lhs": l, "op": _bin[tree.data], "rhs": r}
    if tree.data == 'in_op':
        l = _serialize_value(tree.children[0], rhs_as_param)
        r = _serialize_set_expr(tree.children[1], rhs_as_param)
        return {"lhs": l, "op": "in", "rhs": r}
    if tree.data == 'not_in_op':
        l = _serialize_value(tree.children[0], rhs_as_param)
        r = _serialize_set_expr(tree.children[1], rhs_as_param)
        return {"lhs": l, "op": "not in", "rhs": r}
    return str(tree)


def _serialize_set_expr(tree, rhs_as_param: bool = True):
    return [_serialize_value(c, rhs_as_param) for c in tree.children]


def _serialize_guard(guard, context, var_card=None, rhs_as_param: bool = True, card_key: str = None):
    """Build a guard tree with `context` as the subject (varid or varid.attrid).

    For ArrayGuard, generates a fully verbose combinatorial expansion: one
    disjunct per valid array length, each disjunct enumerating the relevant
    combinations of element slots.

    Args:
        context: Display path for the subject. Any "_p" suffix is already
            baked in (once, right after the base variable name) by the
            caller -- nested struct/array suffixes are appended as-is.
        rhs_as_param: Whether a guard's value (a literal or another
            variable reference) refers to the parameter or the state
            variable. Always follows the surrounding step's context,
            regardless of whether the subject (lhs) was marked "stored".
        card_key: Bare (no "_p") path mirroring `context`, used to look up
            array cardinalities in `var_card` (keyed by bare names).
            Defaults to `context` itself.
    """
    if card_key is None:
        card_key = context
    if isinstance(guard, PrimGuard):
        if guard.op == 'between':
            lo, hi = guard.value
            return {
                "lhs": {"lhs": context, "op": ">=", "rhs": _serialize_value(lo, rhs_as_param)},
                "op":  "&&",
                "rhs": {"lhs": context, "op": "<=", "rhs": _serialize_value(hi, rhs_as_param)},
            }
        return {"lhs": context, "op": guard.op,
                "rhs": _serialize_value(guard.value, rhs_as_param)}
    if isinstance(guard, StructGuard):
        node = None
        for ag in guard.entries:
            nested     = f"{context}.{_format_id(ag.attrid)}"
            nested_key = f"{card_key}.{_format_id(ag.attrid)}"
            expr       = _serialize_guard(ag.guard, nested, var_card, rhs_as_param, nested_key)
            if node is None:
                node = expr
            else:
                node = {"lhs": node, "op": _CONJ_MAP.get(ag.conj, ag.conj), "rhs": expr}
        return node
    if isinstance(guard, ArrayGuard):
        min_slots, max_slots = (var_card or {}).get(card_key, (guard.count, guard.count))
        n = guard.count

        def _slot_cond(i):
            return _serialize_guard(guard.element_guard, f'{context}[{i}]', var_card,
                                     rhs_as_param, f'{card_key}[{i}]')

        def _len_eq(L):
            return {"lhs": f"{context}.len()", "op": "==", "rhs": L}

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
                    pos = [_slot_cond(i) for i in sorted(satisfying)]
                    neg = [{"op": "!", "rhs": _slot_cond(i)} for i in sorted(not_satisfying)]
                    combo_parts.append(_fold(pos + neg, "&&", True))
                combos_node = _fold(combo_parts, "||", False)
                length_cases.append({"lhs": _len_eq(L), "op": "&&", "rhs": combos_node})
            return _fold(length_cases, "||", False)

        elif guard.quantifier == 'at_least':
            # For each valid length L >= N, enumerate all C(L,N) combos where
            # at least those N slots satisfy the element guard.
            length_cases = []
            for L in range(max(n, min_slots), max_slots + 1):
                combo_parts = [
                    _fold([_slot_cond(i) for i in combo], "&&", True)
                    for combo in combinations(range(L), n)
                ]
                combos_node = _fold(combo_parts, "||", False)
                length_cases.append({"lhs": _len_eq(L), "op": "&&", "rhs": combos_node})
            return _fold(length_cases, "||", False)

        elif guard.quantifier == 'at_most':
            # For lengths <= N: trivially satisfied (not enough slots to exceed N).
            # For lengths > N: no C(L,N+1) combo may have all slots satisfy.
            length_cases = []
            for L in range(min_slots, max_slots + 1):
                if L <= n:
                    length_cases.append(_len_eq(L))
                else:
                    clauses = [
                        _fold([{"op": "!", "rhs": _slot_cond(i)} for i in combo], "||", False)
                        for combo in combinations(range(L), n + 1)
                    ]
                    length_cases.append({"lhs": _len_eq(L), "op": "&&", "rhs": _fold(clauses, "&&", True)})
            return _fold(length_cases, "||", False)

        elif guard.quantifier == 'all':
            # Every element in the array (any valid length) must satisfy the condition.
            length_cases = []
            for L in range(min_slots, max_slots + 1):
                if L == 0:
                    length_cases.append(_len_eq(0))
                else:
                    slot_conds = _fold([_slot_cond(i) for i in range(L)], "&&", True)
                    length_cases.append({"lhs": _len_eq(L), "op": "&&", "rhs": slot_conds})
            return _fold(length_cases, "||", False)

        else:
            raise NotImplementedError(
                f"Support for {guard.quantifier} not implemented yet."
            )
    return guard


def render_guard_expr(node) -> str:
    """Render a guard tree/leaf (as built by _serialize_guard) as a flat string.

    Used only for human-readable display (DOT/HTML legends); the JSON output
    itself keeps the nested tree form.

    Args:
        node: A leaf (str | int | float | bool | list) or a nested
            {"lhs","op","rhs"} / {"op","rhs"} dict.

    Returns:
        Flat expression string equivalent to the tree.
    """
    if isinstance(node, bool):
        return 'true' if node else 'false'
    if isinstance(node, (int, float)):
        return str(node)
    if isinstance(node, list):
        return '{' + ', '.join(render_guard_expr(n) for n in node) + '}'
    if isinstance(node, str):
        return node
    if 'lhs' not in node:
        return f"!({render_guard_expr(node['rhs'])})"
    lhs, rhs, op = render_guard_expr(node['lhs']), render_guard_expr(node['rhs']), node['op']
    return f"({lhs} {op} {rhs})" if op in ('&&', '||') else f"{lhs} {op} {rhs}"


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
        op_result, exp, sugar_ref = None, None, None
        for c in children:
            if isinstance(c, tuple):
                op_result = c
            elif isinstance(c, ExpValue):
                exp = c
            elif isinstance(c, VarRef):
                sugar_ref = c
                varids.append(c.varid.strip())
            elif isinstance(c, str) and not isinstance(c, Token):
                if action is None:
                    action = c.strip()
                else:
                    varids.append(c.strip())
            elif isinstance(c, GuardBlock):
                gb = c
        if op_result is not None and exp is not None and sugar_ref is not None:
            if op_result[0] == 'between':
                pguard = PrimGuard('between', (op_result[1], exp.value))
            else:
                pguard = PrimGuard(op_result[0], exp.value)
            gb = GuardBlock([GuardEntry(sugar_ref.varid, pguard, stored=sugar_ref.stored)])
        return Step(action or "", varids, gb)

    def steptext(self, children):
        return str(children[0]).strip()

    def guardblock(self, children):
        # Build a flat sequence of typed items, ignoring structural tokens
        items = []
        for c in children:
            if isinstance(c, Token) and c.type == 'CONJOP':
                items.append(('conj', str(c)))
            elif isinstance(c, VarRef):
                items.append(('varref', c))
            elif isinstance(c, (PrimGuard, ArrayGuard, StructGuard)):
                items.append(('guard', c))

        entries, conj, i = [], None, 0
        while i < len(items):
            if items[i][0] == 'conj':
                conj = items[i][1]
                i += 1
                continue
            varref = items[i][1]
            guard  = items[i + 1][1]
            entries.append(GuardEntry(varref.varid, guard, conj, stored=varref.stored))
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
        location_vars_full = {lv["id"]: {k: v for k, v in lv.items() if k != "id"} for lv in _lv_list}

        parameters_full = {
            lv_id + "_p": dict(lv_data)
            for lv_id, lv_data in location_vars_full.items()
        }

        # The JSON interface has no "range" property: a parameter's range is
        # instead enforced as a guard on every switch that uses it.
        location_vars = {vid: {k: v for k, v in vd.items() if k != "range"}
                          for vid, vd in location_vars_full.items()}
        parameters    = {pid: {k: v for k, v in vd.items() if k != "range"}
                          for pid, vd in parameters_full.items()}

        def _range_targets(td) -> list[tuple[str, str, list]]:
            """Recursively list every primitive leaf reachable from a type
            descriptor, as (path_suffix, type, range) relative to the base
            variable -- e.g. for a struct with attrs a/b: [(".a", ...), (".b", ...)];
            for an array of such structs: [("[0].a", ...), ("[0].b", ...), ("[1].a", ...), ...].
            """
            if isinstance(td, PrimitiveType):
                return [("", td.primtype, self._interp_range(td.range_, td.primtype))]
            if isinstance(td, StructType):
                return [
                    (f".{_format_id(attr.attrid)}{suf}", t, r)
                    for attr in td.attrs
                    for suf, t, r in _range_targets(attr.typedesc)
                ]
            if isinstance(td, ArrayType):
                return [
                    (f"[{i}]{suf}", t, r)
                    for i in range(self._max_cardinality(td.cardinality))
                    for suf, t, r in _range_targets(td.element_type)
                ]
            return []

        range_targets_by_var = {_format_id(vd.name): _range_targets(vd.typedesc) for vd in vardefs}

        def _pinned_to_literal(step: Step, base: str, suffix: str) -> bool:
            """True if step's own guardblock already constrains this exact
            path to a single literal value via '==' (making a range guard
            redundant). Only top-level subjects (suffix "") and one level of
            struct attribute (suffix ".attr") are checked; array element
            conditions are never considered "pinned".
            """
            if not step.guardblock or "[" in suffix:
                return False
            for entry in step.guardblock.entries:
                if entry.varid != base or entry.stored:
                    continue
                if suffix == "":
                    if (isinstance(entry.guard, PrimGuard) and entry.guard.op == '=='
                            and isinstance(entry.guard.value, Token)):
                        return True
                elif isinstance(entry.guard, StructGuard):
                    attr_name = suffix[1:]
                    for ag in entry.guard.entries:
                        if (_format_id(ag.attrid) == attr_name and isinstance(ag.guard, PrimGuard)
                                and ag.guard.op == '==' and isinstance(ag.guard.value, Token)):
                            return True
            return False

        def _range_nodes_for_var(step: Step, v: str) -> list:
            """Build range-restriction guard nodes for every primitive leaf
            of step variable v (e.g. both "pepe_p.a" and "pepe_p.b" for a
            struct, skipping booleans and any leaf already pinned to a literal).
            """
            base  = _format_id(v)
            nodes = []
            for suffix, ptype, rng in range_targets_by_var.get(base, []):
                if ptype == "boolean" or not rng or _pinned_to_literal(step, base, suffix):
                    continue
                path = f"{base}_p{suffix}"
                if ptype in ("integer", "decimal") and len(rng) == 2:
                    lo, hi = rng
                    nodes.append({
                        "lhs": {"lhs": path, "op": ">=", "rhs": lo},
                        "op":  "&&",
                        "rhs": {"lhs": path, "op": "<=", "rhs": hi},
                    })
                else:
                    nodes.append({"lhs": path, "op": "in", "rhs": list(rng)})
            return nodes

        attributes: dict = {}

        initial_state = (
            scenario.given is not None
            and any(s.action == "INITCOND" for s in scenario.given.steps)
        )

        when_steps = scenario.when.steps   # list[Step]
        then_steps = scenario.then.steps   # list[Step]
        M, N       = len(when_steps), len(then_steps)

        # Locations: L0 … L(M+N)
        locations = [f"L{i}_{sts_id}" for i in range(M + N + 1)]

        # inputGates: one per When step
        input_gates = {
            f"In{sts_id}_{i+1}": {"text": s.action,
                                   "parameters": [_format_id(v) + "_p" for v in s.varids]}
            for i, s in enumerate(when_steps)
        }

        # outputGates: one per Then step
        output_gates = {
            f"Out{sts_id}_{j+1}": {"text": s.action,
                                    "parameters": [_format_id(v) + "_p" for v in s.varids]}
            for j, s in enumerate(then_steps)
        }

        guards: dict[str, object] = {}
        g_idx  = 1

        # Given steps: each step with a guardblock contributes one guard entry.
        # These are AND-ed into the first When switch's guard.
        given_guard_ids = []
        if scenario.given:
            for step in scenario.given.steps:
                if step.guardblock:
                    gid = f"G{g_idx}"
                    given_guard_ids.append(gid)
                    guards[gid] = step.guardblock.to_tree(var_card, as_param=False)
                    g_idx += 1

        when_guard_ids = []
        for step in when_steps:
            gid = f"G{g_idx}"
            when_guard_ids.append(gid)
            guards[gid] = step.guardblock.to_tree(var_card) if step.guardblock else True
            g_idx += 1

        then_guard_ids = []
        for step in then_steps:
            gid = f"G{g_idx}"
            then_guard_ids.append(gid)
            guards[gid] = step.guardblock.to_tree(var_card) if step.guardblock else True
            g_idx += 1

        def _single_guard_id(guard_ids: list[str], extra_nodes: list | None = None) -> str:
            """Collapse guard IDs plus ad-hoc guard tree nodes into the single
            ID a switch may reference, AND-ing everything together.
            """
            nonlocal g_idx
            parts = [guards[gid] for gid in guard_ids] + (extra_nodes or [])
            if len(parts) == 1 and len(guard_ids) == 1:
                return guard_ids[0]
            node = parts[0] if parts else True
            for p in parts[1:]:
                node = {"lhs": node, "op": "&&", "rhs": p}
            merged_gid = f"G{g_idx}"; g_idx += 1
            guards[merged_gid] = node
            return merged_gid

        assignments: dict[str, dict] = {}
        assign_sig_to_id: dict[tuple, str] = {}
        a_idx = 1

        def _assignment_ids(varids: list[str]) -> list[str]:
            nonlocal a_idx
            ids = []
            for v in varids:
                sig = (_format_id(v), _format_id(v) + "_p")
                if sig not in assign_sig_to_id:
                    aid = f"A{a_idx}"; a_idx += 1
                    assign_sig_to_id[sig] = aid
                    assignments[aid] = {"target": sig[0], "expression": sig[1]}
                ids.append(assign_sig_to_id[sig])
            return ids

        _switches_list = []

        for i, step in enumerate(when_steps):
            guard_ids   = given_guard_ids + when_guard_ids[i:i+1] if i == 0 else when_guard_ids[i:i+1]
            range_nodes = [n for v in step.varids for n in _range_nodes_for_var(step, v)]
            _switches_list.append({
                "init_loc":    f"L{i}_{sts_id}",
                "gate":        f"In{sts_id}_{i+1}",
                "guard":       _single_guard_id(guard_ids, range_nodes),
                "assignments": _assignment_ids(step.varids),
                "end_loc":     f"L{i+1}_{sts_id}",
            })

        for j, step in enumerate(then_steps):
            range_nodes = [n for v in step.varids for n in _range_nodes_for_var(step, v)]
            _switches_list.append({
                "init_loc":    f"L{M+j}_{sts_id}",
                "gate":        f"Out{sts_id}_{j+1}",
                "guard":       _single_guard_id(then_guard_ids[j:j+1], range_nodes),
                "assignments": _assignment_ids(step.varids),
                "end_loc":     f"L{M+j+1}_{sts_id}",
            })

        switches = {f"r_{idx + 1}": sw for idx, sw in enumerate(_switches_list)}

        return {
            "id":                f"sts_{sts_id:03d}",
            "description":       scenario.description,
            "initial_state":     initial_state,
            "initial_location":  locations[0],
            "gate_id_type":      "string",
            "location_id_type":  "string",
            "locationVariables": location_vars,
            "parameters":        parameters,
            "attributes":        attributes,
            "locations":         locations,
            "inputGates":        input_gates,
            "outputGates":       output_gates,
            "guards":            guards,
            "assignments":       assignments,
            "switches":          switches,
        }
