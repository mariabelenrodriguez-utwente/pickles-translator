from decimal import Decimal, InvalidOperation
from itertools import count
from lark import Transformer, Token, Tree, Lark
from pathlib import Path

import logging
import re as _re

from . import ast_nodes as AST

_logger = logging.getLogger(__name__)

from .exceptions import ConsistencyError

_OP_MAP = {
    'EQUAL_TO':             '==',
    'NOT_EQUAL_TO':         '!=',
    'GREATER_THAN':         '>',
    'LOWER_THAN':           '<',
    'GREATER_OR_EQUAL_THAN': '>=',
    'LOWER_OR_EQUAL_THAN':  '<=',
    'SUBSET_OF':            'subset',
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

_ARRAY_ONLY_OPS = {
    'contains', 'not_contains', 'contains_only', 'contains_all',
    'is_empty', 'is_not_empty', 'subset',
}

# render_guard_expr's text for a "cardinality" guard node's `quantifier` field.
_QUANTIFIER_TEXT = {
    'at_least': 'at least',
    'at_most':  'at most',
    'exactly':  'exactly',
}

_PRIMTYPE_MAP = {
    'BOOLEAN_TYPE': 'boolean',
    'STRING_TYPE':  'string',
    'INTEGER_TYPE': 'integer',
    'DECIMAL_TYPE': 'float',
    'BOOLEAN_TYPE_PL': 'boolean',
    'STRING_TYPE_PL':  'string',
    'INTEGER_TYPE_PL': 'integer',
    'DECIMAL_TYPE_PL': 'float',
}

_TRUE_LITERALS  = {'true', 'verdadero', 'waar'}
_FALSE_LITERALS = {'false', 'falso', 'onwaar'}

_NEGATION_WORDS = {'not', 'no', 'niet'}


def _ends_with_negation(steptext: str) -> bool:
    words = steptext.strip().split()
    return bool(words) and words[-1].lower() in _NEGATION_WORDS

_ATTRDESC_LINE = _re.compile(
    r'^\s+"[^"]+"\s+(is a |is an |es una? |is een )',
    _re.IGNORECASE,
)

# Strips '#' line comments: everything from '#' to end of line.
_COMMENT = _re.compile(r'#[^\n]*')

# Matches the start of a "AST.Documentation:" section, in any supported language.
_DOC_START = _re.compile(r'^\s*(AST.Documentation|Documentación|Documentatie):')

# Matches the start of a scenario section keyword, in any supported language;
# used to know where a multi-line AST.Documentation section ends.
_SECTION_START = _re.compile(
    r'^\s*(AST.Given|Dado|Gegeven|AST.When|Cuando|Wanneer|AST.Then|Entonces|Dan|AST.Scenario|Escenario)\b'
)

_RESOURCES = Path(__file__).parent.parent / "resources"


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


def _var_or_param(context: str, as_param: bool) -> str:
    """Render a guard subject's context as a parameter or state reference.

    Args:
        context: The location variable.
        as_param: When True, refer to the parameter else refer to the 
            location variable as-is.

    Returns:
        The variable identifier string to use in a guard tree.
    """
    return f"{context}_p" if as_param else context


def _var(path: str) -> dict:
    """Wrap a variable/parameter path as a guardExpr variable-reference leaf,
    so it's never mistaken for a string literal (which stays a bare string).
    """
    return {"var": path}


def _len(node: dict) -> dict:
    """Wrap in a unary "len" node"""
    return {"op": "len", "rhs": node}


def _unique(node: dict) -> dict:
    """Wrap in a unary "uniqueElem" node"""
    return {"op": "uniqueElem", "rhs": node}


def _attr_lookup(struct_td: AST.StructType, attrid: str) -> AST.AttrDesc:
    """Resolve a struct attribute's declared name to its own AST.AttrDesc."""
    formatted = _format_id(attrid)
    for attr in struct_td.attrs:
        if _format_id(attr.attrid) == formatted:
            return attr
    raise ConsistencyError(f"Attribute '{attrid}' is not declared in the struct.")

def _wrap(v):
    """Wrap a static scalar with its datatype name."""
    if isinstance(v, bool):
        return {"boolean": v}
    if isinstance(v, int):
        return {"integer": v}
    if isinstance(v, float):
        return {"float": v}
    if isinstance(v, str):
        return {"string": v}
    return v

def _serialize_list(v):
    return [_wrap(x) for x in v]

def _serialize_token(v):
    """Serialize a grammar-leaf Token. STR_LIT and BOOL_LIT have their own rule.
    Every other atom token (UNSIGNED_NUMBER, SIGNED_NUMBER) is a number.
    """
    if v.type == 'STR_LIT':
        return _wrap(str(v)[1:-1])
    if v.type == 'BOOL_LIT':
        return _wrap(str(v).lower() in _TRUE_LITERALS)
    return _serialize_sfi(v)

def _serialize_sfi(v):
    """Parse a string/int/float value as a number, string as fallback.

    Note: "+" sign and exponent notation (e.g. "1e5") parse correctly here,
    but are not verified or tested anywhere else in the pipeline.
    """
    s = str(v)
    try:
        n = int(s)
    except ValueError:
        try:
            n = float(s)
        except ValueError:
            n = s
    return _wrap(n)

SERIALIZATION_METHODS = {
    bool: _wrap,
    list: _serialize_list,
    Token: _serialize_token,
    str: _serialize_sfi,
    int: _serialize_sfi,
    float: _serialize_sfi
}

def _serialize_value(v, rhs_as_param: bool = True, constant_ids: set = None):
    """Convert a guard value (Token / AST.VarRef / list / Tree) to a tree leaf/node.

    Args:
        v: The raw value node from the grammar.
        rhs_as_param: Whether a VarRef should resolve to the parameter 
            or the location variable.
        constant_ids: {formatted_name} of declared constants.
    """
    if isinstance(v, AST.VarRef):
        key = _format_id(v.varid)
        if constant_ids and key in constant_ids:
            return _var(key)
        return _var(_var_or_param(key, rhs_as_param))
    elif type(v) in SERIALIZATION_METHODS:
        return SERIALIZATION_METHODS.get(type(v))(v)
    return _serialize_tree(v, rhs_as_param, constant_ids)


def _serialize_tree(tree, rhs_as_param: bool = True, constant_ids: set = None):
    _bin = {'add': '+', 'sub': '-', 'mul': '*', 'div': '/'}
    if tree.data in _bin:
        l = _serialize_value(tree.children[0], rhs_as_param, constant_ids)
        r = _serialize_value(tree.children[1], rhs_as_param, constant_ids)
        return {"lhs": l, "op": _bin[tree.data], "rhs": r}
    if tree.data == 'in_op':
        l = _serialize_value(tree.children[0], rhs_as_param, constant_ids)
        r = _serialize_set_expr(tree.children[1], rhs_as_param, constant_ids)
        return {"lhs": l, "op": "in", "rhs": r}
    if tree.data == 'not_in_op':
        l = _serialize_value(tree.children[0], rhs_as_param, constant_ids)
        r = _serialize_set_expr(tree.children[1], rhs_as_param, constant_ids)
        return {"lhs": l, "op": "not in", "rhs": r}
    return str(tree)


def _serialize_set_expr(tree, rhs_as_param: bool = True, constant_ids: set = None):
    """The rhs of "in"/"not in": either an inline {...} literal or a reference 
    to a declared array variable.
    """
    if len(tree.children) == 1 and isinstance(tree.children[0], AST.VarRef):
        return _var(_format_id(tree.children[0].varid))
    return [_serialize_value(c, rhs_as_param, constant_ids) for c in tree.children]


def _serialize_guard(guard, subject, subject_td, var_card=None, rhs_as_param: bool = True, card_key: str = None,
                      constant_ids: set = None, domains: dict = None, elem_depth: int = 0):
    """Build a guardExpr tree/leaf for `guard`, with `subject` as its lhs.

    Args:
        guard: Guard AST node (PrimGuard, CollectionGuard, LengthGuard, StructGuard,
            ArrayGuard, or AttrBoolGuard).
        subject: guardExpr node for the subject (variable reference, quantifier binder,
            or "project" node for a struct attribute).
        subject_td: Declared type of `subject`.
        var_card: {formatted_name: (min, max)} cardinality for array variables.
        rhs_as_param: Whether a guard's value refers to the parameter or the state variable.
        card_key: Bare dotted-name path mirroring `subject`, used to look up
            var_card/domains (both keyed by bare top-level names).
        constant_ids: {formatted_name} of declared constants.
        domains: {formatted_name: [typed values]} for "contains all possible elements".
        elem_depth: Number of array quantifiers already opened, to name each new
            "lambda" binder uniquely ("e<elem_depth + 1>").

    Returns:
        guardExpr tree/leaf dict.

    Raises:
        ConsistencyError: "contains all possible elements" on a variable with no
            resolvable domain.
        NotImplementedError: an AST.ArrayGuard quantifier other than exactly/at_least/
            at_most/all.
    """
    if isinstance(guard, AST.PrimGuard):
        return _serialize_prim_guard(guard, subject, rhs_as_param, constant_ids)
    if isinstance(guard, AST.CollectionGuard):
        return _serialize_collection_guard(guard, subject, rhs_as_param, card_key, constant_ids, domains)
    if isinstance(guard, AST.LengthGuard):
        return _serialize_length_guard(guard, subject, rhs_as_param, constant_ids)
    if isinstance(guard, AST.AttrBoolGuard):
        return _serialize_attrbool_guard(guard, subject, subject_td)
    if isinstance(guard, AST.StructGuard):
        return _serialize_struct_guard(guard, subject, subject_td, var_card, rhs_as_param, card_key,
                                        constant_ids, domains, elem_depth)
    if isinstance(guard, AST.ArrayGuard):
        return _serialize_array_guard(guard, subject, subject_td, var_card, rhs_as_param, card_key,
                                       constant_ids, domains, elem_depth)
    return guard


def _serialize_prim_guard(guard: "AST.PrimGuard", subject, rhs_as_param: bool, constant_ids: set) -> dict:
    """Build a guardExpr node for a scalar comparison guard (==, !=, <, <=, >, >=, between, in, not_in).

    Args:
        guard: PrimGuard to serialize.
        subject: guardExpr node for the subject.
        rhs_as_param: Whether the guard's value refers to the parameter or the state variable.
        constant_ids: {formatted_name} of declared constants.

    Returns:
        guardExpr tree/leaf dict.
    """
    if guard.op == 'between':
        lo, hi = guard.value
        return {
            "lhs": {"lhs": subject, "op": ">=", "rhs": _serialize_value(lo, rhs_as_param, constant_ids)},
            "op":  "&&",
            "rhs": {"lhs": subject, "op": "<=", "rhs": _serialize_value(hi, rhs_as_param, constant_ids)},
        }
    if guard.op in ('in', 'not_in'):
        rhs = _serialize_set_expr(guard.value, rhs_as_param, constant_ids)
        return {"lhs": subject, "op": guard.op, "rhs": rhs}
    return {"lhs": subject, "op": guard.op,
            "rhs": _serialize_value(guard.value, rhs_as_param, constant_ids)}


def _serialize_collection_guard(guard: "AST.CollectionGuard", subject, rhs_as_param: bool, card_key: str,
                                 constant_ids: set, domains: dict) -> dict:
    """Build a guardExpr node for an array-only guard (contains*, is_empty*, subset).

    Args:
        guard: CollectionGuard to serialize.
        subject: guardExpr node for the subject.
        rhs_as_param: Whether the guard's value refers to the parameter or the state variable.
        card_key: Bare dotted-name path mirroring `subject`, used to look up `domains`.
        constant_ids: {formatted_name} of declared constants.
        domains: {formatted_name: [typed values]} for "contains all possible elements".

    Returns:
        guardExpr tree/leaf dict.

    Raises:
        ConsistencyError: "contains all possible elements" on a variable with no
            resolvable domain.
    """
    if guard.op in ('contains', 'not_contains', 'contains_only'):
        return {"lhs": subject, "op": guard.op,
                "rhs": _serialize_value(guard.value, rhs_as_param, constant_ids)}
    if guard.op == 'contains_all':
        # "X contains all possible elements"
        values = (domains or {}).get(card_key)
        if not values:
            raise ConsistencyError(
                f"Cannot determine the domain for 'contains all possible "
                f"elements' on '{card_key}'; a range is needed"
            )
        checks = [{"lhs": subject, "op": "contains", "rhs": _wrap(v)} for v in values]
        return _fold(checks, "&&", True)
    if guard.op in ('is_empty', 'is_not_empty'):
        return {"lhs": _len(subject), "op": "==" if guard.op == "empty" else "!=", "rhs": _wrap(0)}
    # 'subset' falls here
    return {"lhs": subject, "op": guard.op,
            "rhs": _serialize_value(guard.value, rhs_as_param, constant_ids)}


def _serialize_length_guard(guard: "AST.LengthGuard", subject, rhs_as_param: bool, constant_ids: set) -> dict:
    """Build a guardExpr node for a "has length ..." guard, comparing the subject's own length.

    Args:
        guard: LengthGuard to serialize.
        subject: guardExpr node for the array/set subject (its length is computed from it).
        rhs_as_param: Whether the guard's value refers to the parameter or the state variable.
        constant_ids: {formatted_name} of declared constants.

    Returns:
        guardExpr tree/leaf dict.
    """
    length = _len(subject)
    if guard.op == 'between':
        lo, hi = guard.value
        return {
            "lhs": {"lhs": length, "op": ">=", "rhs": _serialize_value(lo, rhs_as_param, constant_ids)},
            "op":  "&&",
            "rhs": {"lhs": length, "op": "<=", "rhs": _serialize_value(hi, rhs_as_param, constant_ids)},
        }
    return {"lhs": length, "op": guard.op,
            "rhs": _serialize_value(guard.value, rhs_as_param, constant_ids)}


def _serialize_attrbool_guard(guard: "AST.AttrBoolGuard", subject, subject_td) -> dict:
    """Build a guardExpr node for the "struct is [not] 'attr'" boolean-attribute shorthand.

    Args:
        guard: AttrBoolGuard to serialize.
        subject: guardExpr node for the struct subject.
        subject_td: Struct's declared type (StructType), for the attribute lookup.

    Returns:
        guardExpr tree/leaf dict.
    """
    _attr_lookup(subject_td, guard.attrid)
    proj = {"lhs": subject, "op": "project", "rhs": {"var": _format_id(guard.attrid)}}
    return {"lhs": proj, "op": "==", "rhs": _wrap(guard.value)}


def _serialize_struct_guard(guard: "AST.StructGuard", subject, subject_td, var_card, rhs_as_param: bool,
                             card_key: str, constant_ids: set, domains: dict, elem_depth: int) -> dict:
    """Build a guardExpr node for a struct guard, AND/OR-folding its per-attribute entries.

    Args:
        guard: StructGuard to serialize.
        subject: guardExpr node for the struct subject.
        subject_td: Struct's declared type (StructType), for attribute lookups.
        var_card, rhs_as_param, constant_ids, domains, elem_depth: forwarded unchanged to
            each attribute's own _serialize_guard call.
        card_key: Bare dotted-name path mirroring `subject`; each attribute appends its
            own name to build its nested card_key.

    Returns:
        guardExpr tree/leaf dict.
    """
    node = None
    for ag in guard.entries:
        attr       = _attr_lookup(subject_td, ag.attrid)
        proj       = {"lhs": subject, "op": "project", "rhs": {"var": _format_id(ag.attrid)}}
        nested_key = f"{card_key}.{_format_id(ag.attrid)}"
        expr       = _serialize_guard(ag.guard, proj, attr.typedesc, var_card, rhs_as_param,
                                       nested_key, constant_ids, domains, elem_depth)
        if node is None:
            node = expr
        else:
            node = {"lhs": node, "op": _CONJ_MAP.get(ag.conj, ag.conj), "rhs": expr}
    return node


def _serialize_array_guard(guard: "AST.ArrayGuard", subject, subject_td, var_card, rhs_as_param: bool,
                            card_key: str, constant_ids: set, domains: dict, elem_depth: int) -> dict:
    """Build a guardExpr node for a quantified array guard (exists/cardinality/forall).

    Args:
        guard: ArrayGuard to serialize.
        subject: guardExpr node for the array subject.
        subject_td: Array's declared type (ArrayType), for its element type.
        var_card, rhs_as_param, card_key, constant_ids, domains: forwarded to the element
            guard's own _serialize_guard call.
        elem_depth: Number of array quantifiers already opened; names this one's own
            "lambda" binder uniquely.

    Returns:
        guardExpr tree/leaf dict.

    Raises:
        NotImplementedError: guard.quantifier is none of exactly/at_least/at_most/all.
    """
    if guard.quantifier not in ('exactly', 'at_least', 'at_most', 'all'):
        raise NotImplementedError(f"Support for {guard.quantifier} not implemented yet.")

    n         = guard.count
    elem_name = f"e{elem_depth + 1}"
    elem_node = _var(elem_name)
    elem_td   = subject_td.element_type
    slot_cond = _serialize_guard(guard.element_guard, elem_node, elem_td, var_card,
                                  rhs_as_param, card_key, constant_ids, domains, elem_depth + 1)

    if guard.quantifier == 'at_least' and n == 1:
        return {"op": "exists", "over": subject, "lambda": elem_name, "expression": slot_cond}
    if guard.quantifier in ('exactly', 'at_least', 'at_most'):
        return {
            "op":         "cardinality",
            "quantifier": guard.quantifier,
            "n":          n,
            "over":       subject,
            "lambda":    elem_name,
            "expression": slot_cond,
        }
    return {"op": "forall", "over": subject, "lambda": elem_name, "expression": slot_cond}


def _guardblock_to_tree(guardblock: "AST.GuardBlock", var_card=None, as_param=True,
                         constant_ids=None, domains=None, type_by_name=None) -> dict:
    """Build a guard tree from a GuardBlock's entries, AND/OR-folded per their conjunctions.

    Free function (not a GuardBlock method) so ast_nodes.py stays a pure data module with
    no import back onto transformer.py's serialization logic (_serialize_guard, _var, ...).

    Args:
        guardblock: Parsed AST.GuardBlock to serialize.
        var_card: {formatted_name: (min_cardinality, max_cardinality)} for array variables.
        as_param: Whether a non-stored subject refers to the parameter ("_p") or the bare
            location variable.
        constant_ids: {formatted_name} of declared constants.
        domains: {formatted_name: [typed values]} for "contains all possible elements".
        type_by_name: {formatted_name: typedesc} for every declared variable/constant.

    Returns:
        guardExpr tree/leaf for the whole guard block.
    """
    node = None
    for entry in guardblock.entries:
        base         = _format_id(entry.varid)
        is_const     = bool(constant_ids and base in constant_ids)
        # A constant has no parameter counterpart
        lhs_as_param = False if is_const else (as_param and not entry.stored)
        subject      = _var(_var_or_param(base, lhs_as_param))
        subject_td   = (type_by_name or {}).get(base)
        expr = _serialize_guard(entry.guard, subject, subject_td, var_card, as_param, base, constant_ids, domains)
        if node is None:
            node = expr
        else:
            node = {"lhs": node, "op": _CONJ_MAP.get(entry.conj, entry.conj), "rhs": expr}
    return node


def render_guard_expr(node) -> str:
    """Render a guard tree/leaf (as built by _serialize_guard) as a flat string.

    Used only for human-readable display (DOT/HTML legends).

    Args:
        node: A wrapped literal leaf ({"string"|"integer"|"float"|"boolean":
            value}), a list of such leaves/nodes, a variable reference
            ({"var": path}), or a nested {"lhs","op","rhs"} / {"op","rhs"}
            dict.

    Returns:
        Flat expression string equivalent to the tree.
    """
    if isinstance(node, list):
        return '{' + ', '.join(render_guard_expr(n) for n in node) + '}'
    if 'var' in node:
        return node['var']
    for typename in ('string', 'integer', 'float', 'boolean'):
        if typename in node:
            v = node[typename]
            return str(v)
    if node.get('op') == 'count':
        qtext = _QUANTIFIER_TEXT.get(node['quantifier'], node['quantifier'])
        return (f"{qtext} {node['n']} of {render_guard_expr(node['over'])} "
                f"(as {node['element']}) match ({render_guard_expr(node['expression'])})")
    if node.get('op') in ['exists', 'forall']:
        return (f"{node.get('op')} {node['element']} in {render_guard_expr(node['over'])} "
                f"the expression ({render_guard_expr(node['expression'])}) is satisfied.")
    if node.get('op') == '!':
        return f"!({render_guard_expr(node['rhs'])})"
    if node.get('op') == 'len':
        return f"{render_guard_expr(node['rhs'])}.len"
    if node.get('op') == 'uniqueElem':
        return f"{render_guard_expr(node['rhs'])} has all unique elements"
    if node.get('op') == 'project':
        return f"{render_guard_expr(node['lhs'])}[{render_guard_expr(node['rhs'])}]"
    lhs, rhs, op = render_guard_expr(node['lhs']), render_guard_expr(node['rhs']), node['op']
    return f"({lhs} {op} {rhs})"


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
        vdb       = next(c for c in children if isinstance(c, AST.VarDefBlock))
        scenarios = [c for c in children if isinstance(c, AST.Scenario)]
        return AST.SpecSuite(vdb, scenarios)

    def vardefblock(self, children):
        vardefs = [c for c in children if isinstance(c, AST.VarDef)]
        return AST.VarDefBlock(vardefs)

    def vardef(self, children):
        name = _plain_strs(children)[0].strip()
        td   = next(c for c in children
                    if isinstance(c, (AST.PrimitiveType, AST.ArrayType, AST.StructType)))
        iv   = next((c for c in children if isinstance(c, AST.InitialValue)), None)
        return AST.VarDef(name, td, initial_value=iv)

    def initval(self, children):
        value = next(
            c for c in children
            if isinstance(c, AST.RangeSpec)
            or (isinstance(c, Token) and c.type != 'AND_INITIAL_VALUE')
        )
        return AST.InitialValue(value, is_array=isinstance(value, AST.RangeSpec))

    def constdef(self, children):
        name = _plain_strs(children)[0].strip()
        td   = next(c for c in children if isinstance(c, (AST.PrimitiveType, AST.ArrayType)))
        return AST.VarDef(name, td, is_constant=True)

    def constdesc(self, children):
        return children[0]

    def primconst(self, children):
        """A scalar constant's typedesc.
        """
        primtype = _plain_strs(children)[0]
        value    = next(c for c in children if isinstance(c, Token) and c.type != 'ISTHE')
        raw = str(value)
        if value.type == 'STR_LIT':
            # Remove quotes
            raw = raw[1:-1]
        return AST.PrimitiveType(primtype, AST.RangeSpec("set", [raw]))

    def arrayconst(self, children):
        """An array constant's typedesc.
        """
        primtype = _plain_strs(children)[0]
        range_   = next(c for c in children if isinstance(c, AST.RangeSpec))
        if range_.kind != "set":
            raise ConsistencyError(
                f"A constant array must use a discrete {{...}} value list, not a "
                f"'{range_.kind}' range."
            )
        element_type = AST.PrimitiveType(primtype, range_)
        cardinality  = AST.Cardinality("exactly", [len(range_.values)])
        return AST.ArrayType(cardinality, element_type, unique=True)

    def typedesc(self, children):
        return children[0]

    def primitive(self, children):
        primtype    = _plain_strs(children)[0]
        range_      = next((c for c in children if isinstance(c, AST.RangeSpec)), None)
        resolution_ = _tokens_of(children, "UNSIGNED_NUMBER")
        if resolution_:
            if range_ is None or range_.kind not in ("open", "closed"):
                raise ConsistencyError(
                    "'resolution' requires an open or closed numeric range "
                    "(e.g. 'with range (1.0,3.0) and resolution 0.5')."
                )
            if primtype != "float":
                raise ConsistencyError(
                    f"'resolution' is only valid for float variables, not '{primtype}'."
                )
            range_ = AST.RangeSpec(range_.kind, range_.values, str(resolution_[0]))
        return AST.PrimitiveType(primtype, range_)

    def primtype(self, children):
        return _PRIMTYPE_MAP[children[0].type]

    def primtype_pl(self, children):
        return _PRIMTYPE_MAP[children[0].type]

    def _make_range(self, kind, children):
        values = [v.strip() for v in str(children[0]).split(",")]
        if kind in ("open", "closed") and len(values) != 2:
            raise ConsistencyError(
                f"Range {'(' if kind == 'open' else '['}{children[0]}"
                f"{')' if kind == 'open' else ']'} must have exactly two "
                "values (lower and upper bound); use { } for a set of "
                "allowed values instead."
            )
        return AST.RangeSpec(kind, values)

    def range_open(self, children):
        return self._make_range("open", children)

    def range_closed(self, children):
        return self._make_range("closed", children)

    def range_set(self, children):
        return self._make_range("set", children)

    def array(self, children):
        card   = next(c for c in children if isinstance(c, AST.Cardinality))
        etype  = next(c for c in children
                      if isinstance(c, (AST.PrimitiveType, AST.ArrayType, AST.StructType)))
        unique = bool(_tokens_of(children, 'UNIQUE'))
        return AST.ArrayType(card, etype, unique)

    def cardinality(self, children):
        # children[0] is AT_MOST / EXACTLY / BETWEEN token
        kind = _QUANT_MAP[children[0].type]
        nums = _tokens_of(children, 'UNSIGNED_NUMBER')
        return AST.Cardinality(kind, [float(str(n)) for n in nums])

    def struct(self, children):
        return next(c for c in children if isinstance(c, AST.StructType))

    def struct_body(self, children):
        return AST.StructType([c for c in children if isinstance(c, AST.AttrDesc)])

    def attrdesc(self, children):
        attrid = _plain_strs(children)[0].strip()
        td     = next(c for c in children
                      if isinstance(c, (AST.PrimitiveType, AST.ArrayType, AST.StructType)))
        return AST.AttrDesc(attrid, td)

    def attrid(self, children):
        return str(children[0]).strip()

    def varid(self, children):
        return str(children[0]).strip()

    def scenario(self, children):
        desc  = next((c for c in children if isinstance(c, str)
                      and not isinstance(c, (Token, AST.Documentation))), "")
        doc   = next((c for c in children if isinstance(c, AST.Documentation)), "")
        given = next((c for c in children if isinstance(c, AST.Given)), None)
        when  = next(c for c in children if isinstance(c, AST.When))
        then  = next(c for c in children if isinstance(c, AST.Then))
        return AST.Scenario(desc.strip(), given, when, then, documentation=doc.strip())

    def scenariodescription(self, children):
        return str(children[0]).strip()

    def documentation(self, children):
        return AST.Documentation(str(children[-1]).strip())

    def given(self, children):
        steps = []
        for c in children:
            if isinstance(c, Token) and c.type == 'INITCOND':
                steps.append(AST.Step("INITCOND", [], None))
            elif isinstance(c, AST.Step):
                steps.append(c)
        return AST.Given(steps)

    def when(self, children):
        return AST.When([c for c in children if isinstance(c, AST.Step)])

    def then(self, children):
        return AST.Then([c for c in children if isinstance(c, AST.Step)])

    def andstep(self, children):
        for c in children:
            if isinstance(c, Token) and c.type == 'INITCOND':
                return AST.Step("INITCOND", [], None)
            if isinstance(c, AST.Step):
                return c
        return AST.Step("", [], None)

    def step(self, children):
        action, varids, gb = None, [], None
        op_result, exp, sugar_ref = None, None, None
        inop_result, inop_set = None, None
        novalue_result = None   # emptyop / containsallop results: no expvalue at all
        attrbool = None         # AttrBoolAST.StepSugar, if this step matched "varref IS [not] varref"
        for c in children:
            if isinstance(c, tuple):
                if c[0] in ('in', 'not_in'):
                    inop_result = c
                elif c[0] in ('is_empty', 'is_not_empty', 'contains_all'):
                    novalue_result = c
                else:
                    op_result = c
            elif isinstance(c, AST.ExpValue):
                exp = c
            elif isinstance(c, Tree) and c.data == 'set_expr':
                inop_set = c
            elif isinstance(c, AST.AttrBoolStepSugar):
                attrbool = c
            elif isinstance(c, AST.VarRef):
                sugar_ref = c
                varids.append(c.varid.strip())
            elif isinstance(c, str) and not isinstance(c, Token):
                if action is None:
                    action = c.strip()
                else:
                    varids.append(c.strip())
            elif isinstance(c, AST.GuardBlock):
                gb = c
        if op_result is not None and exp is not None and sugar_ref is not None:
            if op_result[0] == 'between':
                pguard = AST.PrimGuard('between', (op_result[1], exp.value))
            elif op_result[0] in _ARRAY_ONLY_OPS:
                pguard = AST.CollectionGuard(op_result[0], exp.value)
            else:
                pguard = AST.PrimGuard(op_result[0], exp.value)
            gb = AST.GuardBlock([AST.GuardEntry(sugar_ref.varid, pguard, stored=sugar_ref.stored)])
        elif inop_result is not None and inop_set is not None and sugar_ref is not None:
            pguard = AST.PrimGuard(inop_result[0], inop_set)
            gb = AST.GuardBlock([AST.GuardEntry(sugar_ref.varid, pguard, stored=sugar_ref.stored)])
        elif novalue_result is not None and sugar_ref is not None:
            # is_empty / is_not_empty / contains_all
            pguard = AST.CollectionGuard(novalue_result[0], None)
            gb = AST.GuardBlock([AST.GuardEntry(sugar_ref.varid, pguard, stored=sugar_ref.stored)])
        elif attrbool is not None:
            pguard = AST.AttrBoolGuard(attrbool.attrid, attrbool.value)
            gb = AST.GuardBlock([AST.GuardEntry(attrbool.subj.varid, pguard, stored=attrbool.subj.stored)])
            varids.append(attrbool.subj.varid.strip())
        elif sugar_ref is not None and op_result is None and exp is None and gb is None:
            # Syntactic sugar for booleans
            pguard = AST.PrimGuard('==', not _ends_with_negation(action or ""))
            gb = AST.GuardBlock([AST.GuardEntry(sugar_ref.varid, pguard, stored=sugar_ref.stored)])
        return AST.Step(action or "", varids, gb)

    def steptext(self, children):
        return str(children[0]).strip()

    def guardblock(self, children):
        # Build a flat sequence of typed items, ignoring structural tokens
        items = []
        for c in children:
            if isinstance(c, Token) and c.type == 'CONJOP':
                items.append(('conj', str(c)))
            elif isinstance(c, AST.VarRef):
                items.append(('varref', c))
            elif isinstance(c, (AST.PrimGuard, AST.CollectionGuard, AST.ArrayGuard, AST.StructGuard, AST.LengthGuard)):
                items.append(('guard', c))

        entries, conj, i = [], None, 0
        while i < len(items):
            if items[i][0] == 'conj':
                conj = items[i][1]
                i += 1
                continue
            varref = items[i][1]
            guard  = items[i + 1][1]
            entries.append(AST.GuardEntry(varref.varid, guard, conj, stored=varref.stored))
            conj = None
            i += 2
        return AST.GuardBlock(entries)

    def guard(self, children):
        return next(c for c in children
                    if isinstance(c, (AST.PrimGuard, AST.CollectionGuard, AST.ArrayGuard, AST.StructGuard, AST.LengthGuard)))

    def primguard(self, children):
        op_result = next(c for c in children if isinstance(c, tuple))
        exp = next(c for c in children if isinstance(c, AST.ExpValue))
        if op_result[0] == 'between':
            return AST.PrimGuard('between', (op_result[1], exp.value))
        if op_result[0] in _ARRAY_ONLY_OPS:
            return AST.CollectionGuard(op_result[0], exp.value)
        return AST.PrimGuard(op_result[0], exp.value)

    def lengthguard(self, children):
        op_result = next(c for c in children if isinstance(c, tuple))
        exp = next(c for c in children if isinstance(c, AST.ExpValue))
        if op_result[0] == 'between':
            return AST.LengthGuard('between', (op_result[1], exp.value))
        return AST.LengthGuard(op_result[0], exp.value)

    def op(self, children):
        tok = children[0]
        if tok.type == 'BETWEEN':
            return ('between', children[1])
        return (_OP_MAP[tok.type], None)

    def containsguard(self, children):
        op_result = next(c for c in children if isinstance(c, tuple))
        exp = next((c for c in children if isinstance(c, AST.ExpValue)), None)
        return AST.CollectionGuard(op_result[0], exp.value if exp is not None else None)

    def contains_op(self, children):
        return ('contains',)

    def not_contains_op(self, children):
        return ('not_contains',)

    def contains_only_op(self, children):
        return ('contains_only',)

    def contains_all_op(self, children):
        return ('contains_all',)

    def inguard(self, children):
        op_result = next(c for c in children if isinstance(c, tuple))
        set_expr_tree = next(c for c in children if isinstance(c, Tree) and c.data == 'set_expr')
        return AST.PrimGuard(op_result[0], set_expr_tree)

    def in_membership(self, children):
        return ('in',)

    def not_in_membership(self, children):
        return ('not_in',)

    def emptyguard(self, children):
        op_result = next(c for c in children if isinstance(c, tuple))
        return AST.CollectionGuard(op_result[0], None)

    def is_empty_op(self, children):
        return ('is_empty',)

    def is_not_empty_op(self, children):
        return ('is_not_empty',)

    def structguard(self, children):
        entries, conj = [], None
        for c in children:
            if isinstance(c, Token) and c.type == 'CONJOP':
                conj = str(c)
            elif isinstance(c, AST.AttrGuardEntry):
                entries.append(AST.AttrGuardEntry(c.attrid, c.guard, conj))
                conj = None
        return AST.StructGuard(entries)

    def attrguard(self, children):
        attrid = _plain_strs(children)[0].strip()
        guard  = next(c for c in children
                      if isinstance(c, (AST.PrimGuard, AST.CollectionGuard, AST.ArrayGuard, AST.StructGuard, AST.LengthGuard)))
        return AST.AttrGuardEntry(attrid, guard)

    def arrayguard(self, children):
        count_tokens = _tokens_of(children, 'UNSIGNED_NUMBER')
        guard        = next(c for c in children
                            if isinstance(c, (AST.PrimGuard, AST.CollectionGuard, AST.ArrayGuard, AST.StructGuard, AST.LengthGuard)))
        if not count_tokens:
            return AST.ArrayGuard('all', None, guard)
        quantifier = _plain_strs(children)[0]
        count      = int(float(str(count_tokens[0])))
        return AST.ArrayGuard(quantifier, count, guard)

    def quantifier(self, children):
        # children: [Token('HAS'), Token('AT_LEAST' | 'AT_MOST' | 'EXACTLY')]
        return _QUANT_MAP[children[1].type]

    def expvalue(self, children):
        return AST.ExpValue(children[0])

    def varref(self, children):
        stored = bool(_tokens_of(children, 'STORED'))
        varid  = _plain_strs(children)[0].strip()
        return AST.VarRef(varid, stored)

    def attrboolstep_true(self, children):
        refs = [c for c in children if isinstance(c, AST.VarRef)]
        return AST.AttrBoolStepSugar(refs[0], refs[1].varid, True)

    def attrboolstep_false(self, children):
        refs = [c for c in children if isinstance(c, AST.VarRef)]
        return AST.AttrBoolStepSugar(refs[0], refs[1].varid, False)


class PicklesToSTS:
    def __init__(self, spec_suite: AST.SpecSuite = None):
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
        from src.specvalidator import SpecValidator, check_action_parameter_consistency  # local import avoids circular dep
        suite    = SpecTransformer().transform(tree)
        domains  = self._resolve_domains(suite.vardefblock.vardefs)
        var_info = self._resolve_vardef_context(suite.vardefblock.vardefs)
        for msg in check_action_parameter_consistency(suite):
            _logger.warning(msg)
        gate_ids = self._assign_gate_ids(suite)
        result = []
        sid    = start_id

        for scenario in suite.scenarios:
            _logger.info("Parsing scenario '%s'...", scenario.description)
            SpecValidator(suite, scenario).validate()
            result.append(self.scenario_to_sts(scenario, var_info, sid, domains, gate_ids))
            sid += 1

        return result, sid

    def _resolve_vardef_context(self, vardefs: list) -> dict:
        """Precompute every piece of per-suite data scenario_to_sts derives from vardefs alone.

        vardefblock is the same object for every scenario in a suite, so this is computed
        once per suite (by tree_to_sts) instead of once per scenario.

        Args:
            vardefs: Suite's declared variables/constants (AST.VarDefBlock.vardefs).

        Returns:
            Dict with keys "constant_ids", "initial_valuation", "var_card", "unique_vars",
            "type_by_name", "range_targets_by_var", "location_vars", "parameters".
        """
        constant_ids = {_format_id(vd.name) for vd in vardefs if vd.is_constant}

        # var_card: {formatted_name: (min_cardinality, max_cardinality)} for array variables
        var_card = {
            _format_id(vd.name): (
                self._min_cardinality(vd.typedesc.cardinality),
                self._max_cardinality(vd.typedesc.cardinality),
            )
            for vd in vardefs if isinstance(vd.typedesc, AST.ArrayType)
        }

        # unique_vars: formatted names of array variables declared "unique"
        unique_vars = {
            _format_id(vd.name) for vd in vardefs
            if isinstance(vd.typedesc, AST.ArrayType) and vd.typedesc.unique
        }

        # type_by_name: {formatted_name: typedesc} for every declared variable/constant
        type_by_name = {_format_id(vd.name): vd.typedesc for vd in vardefs}

        range_targets_by_var = {_format_id(vd.name): self._range_targets(vd.typedesc) for vd in vardefs}

        _lv_list = []
        for vd in vardefs:
            if isinstance(vd.typedesc, AST.ArrayType):
                _lv_list.append(self._array_location_var(vd.name, vd.typedesc))
            elif isinstance(vd.typedesc, AST.StructType):
                _lv_list.append(self._struct_location_var(vd.name, vd.typedesc))
            else:
                _lv_list.append(self._typedesc_to_location_var(vd.name, vd.typedesc))
        location_vars_full = {lv["id"]: {k: v for k, v in lv.items() if k != "id"} for lv in _lv_list}

        parameters_full = {
            lv_id + "_p": dict(lv_data)
            for lv_id, lv_data in location_vars_full.items()
            if lv_id not in constant_ids
        }

        # The JSON interface has no "range" property: a parameter's range is
        # instead enforced as a guard on every switch that uses it.
        location_vars = {vid: {k: v for k, v in vd.items() if k != "range"}
                          for vid, vd in location_vars_full.items()}
        parameters    = {pid: {k: v for k, v in vd.items() if k != "range"}
                          for pid, vd in parameters_full.items()}

        return {
            "constant_ids":         constant_ids,
            "initial_valuation":    self._resolve_initial_valuation(vardefs),
            "var_card":             var_card,
            "unique_vars":          unique_vars,
            "type_by_name":         type_by_name,
            "range_targets_by_var": range_targets_by_var,
            "location_vars":        location_vars,
            "parameters":           parameters,
        }

    def _assign_gate_ids(self, suite: AST.SpecSuite) -> dict:
        """Assign a gate ID to every distinct gate, parameters pair
        across the whole suite.

        Returns:
            {(kind, action, frozenset(parameter ids)): gate_id}, kind is
            "In" or "Out".
        """
        ids      = {}
        counters = {"In": 1, "Out": 1}
        for scenario in suite.scenarios:
            for kind, steps in (("In", scenario.when.steps), ("Out", scenario.then.steps)):
                for step in steps:
                    params = frozenset(_format_id(v) + "_p" for v in step.varids)
                    key    = (kind, step.action, params)
                    if key not in ids:
                        ids[key] = f"{kind}{counters[kind]}"
                        counters[kind] += 1
        return ids

    def _merge_documentation(self, text: str) -> str:
        """
        Collapse a multi-line Documentation section into a single line.

        Args:
            text: Raw (comment-stripped) spec file content.

        Returns:
            Text with every AST.Documentation section's body on a single line.
        """
        lines  = text.split('\n')
        result = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if _DOC_START.match(line):
                merged = line
                i += 1
                while i < len(lines) and lines[i].strip() and not _SECTION_START.match(lines[i]):
                    merged += ' ' + lines[i].strip()
                    i += 1
                result.append(merged)
            else:
                result.append(line)
                i += 1
        return '\n'.join(result)

    def _preprocess(self, text: str) -> str:
        """
        Strip '#' line comments, collapse multi-line "AST.Documentation:"
        sections to one line each, then append <endstruct> to the last
        attrdesc line of every struct body so the LALR parser can
        unambiguously detect when the struct ends.

        Args:
            text: Raw spec file content.

        Returns:
            Preprocessed text with comments removed, AST.Documentation sections
            collapsed, and <endstruct> markers inserted.
        """
        text  = _COMMENT.sub('', text)
        text  = self._merge_documentation(text)
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

    def _min_cardinality(self, card: AST.Cardinality) -> int:
        """Return the lower bound of an array cardinality as an int.

        Args:
            card: AST.Cardinality object.

        Returns:
            Integer lower bound (0 for at_most, the fixed n for exactly, min for between).
        """
        if card.kind == 'between':
            return int(card.values[0])
        if card.kind == 'exactly':
            return int(card.values[0])
        return 0  # at_most: minimum is 0

    def _max_cardinality(self, card: AST.Cardinality) -> int:
        """Return the upper bound of an array cardinality as an int.

        Args:
            card: AST.Cardinality object ('between' has [min, max]; others [n]).

        Returns:
            Integer upper bound.
        """
        return int(card.values[-1])

    def _typedesc_to_variable_definition(self, td) -> dict:
        """Recursively build the type portion of a variableDefinition.

        Args:
            td: Type descriptor (AST.PrimitiveType, AST.ArrayType, or AST.StructType).

        Returns:
            The "type"-shape dict, ready to merge with an "id" (and, for a
            top-level primitive, a "range") for JSON serialisation.
        """
        if isinstance(td, AST.PrimitiveType):
            return {"type": td.primtype}
        if isinstance(td, AST.ArrayType):
            return {
                "type":      "array",
                "unique":    td.unique,
                "minLength": self._min_cardinality(td.cardinality),
                "maxLength": self._max_cardinality(td.cardinality),
                "elements":  self._typedesc_to_variable_definition(td.element_type),
            }
        if isinstance(td, AST.StructType):
            return {
                "type": "structure",
                "attributes": {
                    _format_id(attr.attrid): self._typedesc_to_variable_definition(attr.typedesc)
                    for attr in td.attrs
                },
            }
        return {"type": "unknown"}

    def _array_location_var(self, name: str, array_td: "AST.ArrayType") -> dict:
        """
        Build a location variable dict for an array.

        Args:
            name: Variable name as declared in the spec.
            array_td: The AST.ArrayType descriptor for the variable.

        Returns:
            Location-variable dict ready for JSON serialisation.
        """
        return {"id": _format_id(name), **self._typedesc_to_variable_definition(array_td)}

    def _struct_location_var(self, name: str, struct_td: "AST.StructType") -> dict:
        """Build a single location variable dict for a struct.

        Args:
            name: Variable name as declared in the spec.
            struct_td: The AST.StructType descriptor.

        Returns:
            Location variable dict.
        """
        return {"id": _format_id(name), **self._typedesc_to_variable_definition(struct_td)}

    def _interp_range(self, range_strs: list[str], primtype: str) -> list:
        """Convert a list of string range values to Python-typed values for JSON.

        Args:
            range_strs: Raw string values from the parsed range expression.
            primtype: One of "integer", "float", "boolean", or "string".

        Returns:
            List of appropriately typed Python values.
        """
        result = []
        for v in range_strs:
            v = v.strip()
            try:
                if primtype == 'integer':
                    result.append(int(v))
                elif primtype == 'float':
                    result.append(float(v))
                elif primtype == 'boolean':
                    if v.lower() not in _TRUE_LITERALS and v.lower() not in _FALSE_LITERALS:
                        raise ConsistencyError(f"Invalid boolean value in range: {v}")
                    result.append(v.lower() in _TRUE_LITERALS)
                elif primtype == 'string':
                    result.append(v)
                else:
                    raise ConsistencyError(f"Invalid primitive type: {primtype} for range values; expected 'integer', 'float', or 'boolean'.")
            except ValueError:
                raise ConsistencyError(f"Invalid {primtype} value in range: {v}")
        return result

    def _interp_range_spec(self, range_: "AST.RangeSpec | None", primtype: str) -> "AST.RangeSpec | None":
        """Convert a parsed AST.RangeSpec's raw string values to typed values.

        Args:
            range_: The parsed AST.RangeSpec (kind + raw string values), or
                None if no range was declared.
            primtype: One of "integer", "float", "boolean", or "string".

        Returns:
            A AST.RangeSpec with typed values, or None if no range was declared.
        """
        if range_ is None:
            return None
        typed_values = self._interp_range(range_.values, primtype)
        if range_.resolution is not None:
            lo, hi = typed_values
            discretized = self._discretize_range(
                lo, hi, range_.resolution, inclusive=(range_.kind == "closed")
            )
            return AST.RangeSpec("set", discretized)
        return AST.RangeSpec(range_.kind, typed_values)

    def _discretize_range(self, lo: float, hi: float, resolution_str: str, inclusive: bool) -> list[float]:
        """Turn a continuous [lo,hi] bound pair into a finite grid of values.

        Uses Decimal arithmetic while stepping so results don't drift off
        the grid from float rounding (e.g. 1.0 + 3*0.1 != 1.3 in float).

        Args:
            lo: Lower bound (already typed as float).
            hi: Upper bound (already typed as float).
            resolution_str: Raw "and resolution N" value, as parsed.
            inclusive: True for a "closed" range (endpoints included),
                False for "open" (endpoints excluded).

        Returns:
            Sorted list of float values spaced by the resolution, from lo
            to hi (inclusive/exclusive per `inclusive`).

        Raises:
            ConsistencyError: If the resolution isn't a positive number, or
                no values fall within (lo, hi) at that resolution.
        """
        try:
            resolution = Decimal(resolution_str)
        except InvalidOperation:
            raise ConsistencyError(f"Invalid resolution value: {resolution_str}")
        if resolution <= 0:
            raise ConsistencyError(f"resolution must be a positive number, got {resolution_str}")

        lo_d, hi_d = Decimal(str(lo)), Decimal(str(hi))
        n_steps = int((hi_d - lo_d) / resolution)
        values  = []
        for i in range(n_steps + 1):
            v = lo_d + i * resolution
            if inclusive:
                if lo_d <= v <= hi_d:
                    values.append(float(v))
            else:
                if lo_d < v < hi_d:
                    values.append(float(v))

        if not values:
            raise ConsistencyError(
                f"range ({lo},{hi}) with resolution {resolution_str} produces no values."
            )
        return values

    def _resolve_domains(self, vardefs: list) -> dict:
        """{formatted_name: [typed values]} for every array variable whose
        element type is a primitive with a declared range.

        Args:
            vardefs: list[AST.VarDef] from the suite's AST.VarDefBlock.

        Returns:
            Dict from _format_id(name) to the variable's list of typed
            domain values. Only includes variables where this is computable
            (a primitive element type with a declared range).
        """
        domains = {}
        for vd in vardefs:
            td = vd.typedesc
            if not isinstance(td, AST.ArrayType):
                continue
            elem = td.element_type
            if isinstance(elem, AST.PrimitiveType) and elem.range_ is not None:
                typed_range = self._interp_range_spec(elem.range_, elem.primtype)
                domains[_format_id(vd.name)] = list(typed_range.values)
        return domains

    def _value_in_range(self, value, rng: "AST.RangeSpec") -> bool:
        """True if a typed value satisfies its RangeSpec, False otherwise."""
        if rng.kind == "set":
            return value in rng.values
        lo, hi = rng.values
        return lo <= value <= hi if rng.kind == "closed" else lo < value < hi

    def _resolve_initial_valuation(self, vardefs: list) -> dict:
        """{formatted_name: wrapped literal} for every declared variable
        with an "and initial value ..." suffix.

        Args:
            vardefs: list[AST.VarDef] from the suite's AST.VarDefBlock.

        Returns:
            Dict from _format_id(name) to a wrapped literal.
        """
        valuation = {}
        for vd in vardefs:
            td = vd.typedesc

            if vd.is_constant:
                if isinstance(td, AST.ArrayType):
                    elem   = td.element_type
                    values = self._interp_range_spec(elem.range_, elem.primtype).values
                    valuation[_format_id(vd.name)] = values
                else:
                    value = self._interp_range_spec(td.range_, td.primtype).values[0]
                    valuation[_format_id(vd.name)] = value
                continue

            iv = vd.initial_value
            if iv is None:
                continue

            if isinstance(td, AST.StructType):
                raise ConsistencyError(
                    f"'{vd.name}' is a structure; initial values are not "
                    "supported for structures."
                )

            if isinstance(td, AST.ArrayType):
                if not iv.is_array:
                    raise ConsistencyError(
                        f"'{vd.name}' is an array/set, so its initial value "
                        "must be a discrete {...} list, not a single value."
                    )
                elem = td.element_type
                if not isinstance(elem, AST.PrimitiveType):
                    raise ConsistencyError(
                        f"'{vd.name}': initial values are only supported for "
                        "arrays of primitive elements."
                    )
                values = self._interp_range(iv.raw.values, elem.primtype)
                lo, hi = self._min_cardinality(td.cardinality), self._max_cardinality(td.cardinality)
                if not (lo <= len(values) <= hi):
                    raise ConsistencyError(
                        f"'{vd.name}': initial value has {len(values)} element(s), "
                        f"outside its declared cardinality [{lo},{hi}]."
                    )
                if td.unique and len(set(values)) != len(values):
                    raise ConsistencyError(
                        f"'{vd.name}': initial value has duplicate elements, "
                        "but the array is declared unique."
                    )
                elem_range = self._interp_range_spec(elem.range_, elem.primtype)
                if elem_range is not None:
                    for v in values:
                        if not self._value_in_range(v, elem_range):
                            raise ConsistencyError(
                                f"'{vd.name}': initial value element {v!r} is "
                                "outside its declared element range."
                            )
                valuation[_format_id(vd.name)] = values
                continue

            # AST.PrimitiveType
            if iv.is_array:
                raise ConsistencyError(
                    f"'{vd.name}' is a single value, so its initial value "
                    "must be a single value too, not a discrete {...} list."
                )
            raw = str(iv.raw)
            if isinstance(iv.raw, Token) and iv.raw.type == 'STR_LIT':
                raw = raw[1:-1]
            value = self._interp_range([raw], td.primtype)[0]
            rng   = self._interp_range_spec(td.range_, td.primtype)
            if rng is not None and not self._value_in_range(value, rng):
                raise ConsistencyError(
                    f"'{vd.name}': initial value {value!r} is outside its "
                    "declared range."
                )
            valuation[_format_id(vd.name)] = value
        return valuation

    def _typedesc_to_location_var(self, name: str, td) -> dict:
        """Convert a plain-variable declaration to a location-variable dict.

        Args:
            name: Variable name as declared in the spec.
            td: Type descriptor (AST.PrimitiveType).

        Returns:
            Location-variable dict for JSON serialisation.
        """
        sid = _format_id(name)
        if isinstance(td, AST.PrimitiveType):
            return {
                "id":    sid,
                "type":  td.primtype,
                "range": self._interp_range_spec(td.range_, td.primtype),
            }
        return {"id": sid, "type": "unknown"}

    def _range_targets(self, td, path: tuple = ()) -> list:
        """List every primitive leaf reachable from a type descriptor, as (path, type, range).

        Args:
            td: Type descriptor to walk (AST.PrimitiveType, AST.StructType, or AST.ArrayType).
            path: Steps from the base variable to the current node; each is ("attr", attrid)
                or ("elem",). Callers use the default; recursive calls extend it.

        Returns:
            List of (path, primtype, AST.RangeSpec | None) tuples, one per primitive leaf.
        """
        if isinstance(td, AST.PrimitiveType):
            return [(path, td.primtype, self._interp_range_spec(td.range_, td.primtype))]
        if isinstance(td, AST.StructType):
            return [
                leaf
                for attr in td.attrs
                for leaf in self._range_targets(attr.typedesc, path + (("attr", attr.attrid),))
            ]
        if isinstance(td, AST.ArrayType):
            return self._range_targets(td.element_type, path + (("elem",),))
        return []

    def _path_suffix(self, path: tuple) -> str:
        """Build a leaf path's display suffix, e.g. ".a.b".

        Args:
            path: Leaf path as built by _range_targets.

        Returns:
            Dotted attribute suffix, or "" if path has no "attr" steps.
        """
        return "".join(f".{_format_id(step[1])}" for step in path if step[0] == "attr")

    def _path_has_array(self, path: tuple) -> bool:
        """Check whether a leaf path descends through at least one array element.

        Args:
            path: Leaf path as built by _range_targets.

        Returns:
            True if any step in path is an array-element descent.
        """
        return any(step[0] == "elem" for step in path)

    def _range_node_for_var_or_param(self, path: tuple, td, node, rng: "AST.RangeSpec",
                                      depth: int = 0) -> dict:
        """Build the guard node for one range-restriction leaf, nesting a "forall" per array step.

        Args:
            path: Remaining leaf path to walk.
            td: Type descriptor at the current node.
            node: guardExpr node for the current subject.
            rng: Range bound to enforce once the leaf is reached.
            depth: Number of "forall" quantifiers already opened; names each new one uniquely.

        Returns:
            guardExpr node for the range check, wrapped in "project"/"forall" per path step.
        """
        if not path:
            if rng.kind == "set":
                return {"lhs": node, "op": "in", "rhs": [_wrap(x) for x in rng.values]}
            lo, hi = rng.values
            lo_op = ">=" if rng.kind == "closed" else ">"
            hi_op = "<=" if rng.kind == "closed" else "<"
            return {
                "lhs": {"lhs": node, "op": lo_op, "rhs": _wrap(lo)},
                "op":  "&&",
                "rhs": {"lhs": node, "op": hi_op, "rhs": _wrap(hi)},
            }
        step, rest = path[0], path[1:]
        if step[0] == "attr":
            _, attrid = step
            attr_td = next(a.typedesc for a in td.attrs if _format_id(a.attrid) == _format_id(attrid))
            proj    = {"lhs": node, "op": "project", "rhs": {"var": _format_id(attrid)}}
            return self._range_node_for_var_or_param(rest, attr_td, proj, rng, depth)
        elem_name = f"e{depth + 1}"
        elem_node = _var(elem_name)
        inner     = self._range_node_for_var_or_param(rest, td.element_type, elem_node, rng, depth + 1)
        return {"op": "forall", "over": node, "lambda": elem_name, "expression": inner}

    def _pinned_to_literal(self, step: "AST.Step", base: str, suffix: str, in_array: bool) -> bool:
        """Check if a step's own guard already pins this exact leaf to a literal via "==".

        Args:
            step: Step whose own guardblock is checked.
            base: Formatted (bare) subject variable name.
            suffix: Leaf's dotted attribute suffix.

        Returns:
            True if a range guard for this leaf would be redundant.
        """
        if not step.guardblock or in_array:
            return False
        for entry in step.guardblock.entries:
            if _format_id(entry.varid) != base or entry.stored:
                continue
            if suffix == "":
                if (isinstance(entry.guard, AST.PrimGuard) and entry.guard.op == '=='
                        and isinstance(entry.guard.value, Token)):
                    return True
            elif isinstance(entry.guard, AST.StructGuard):
                attr_name = suffix[1:]
                for ag in entry.guard.entries:
                    if (_format_id(ag.attrid) == attr_name and isinstance(ag.guard, AST.PrimGuard)
                            and ag.guard.op == '==' and isinstance(ag.guard.value, Token)):
                        return True
        return False

    def _range_nodes(self, base: str, path_prefix: str, subject_td, range_targets_by_var: dict,
                      pinned=None) -> list:
        """Build (name, node) pairs for a range-restriction guard on every primitive leaf of a variable.

        Args:
            base: Formatted variable name.
            path_prefix: Bare path for the top-level guard subject, e.g. "{base}_p".
            subject_td: base's own declared type descriptor.
            range_targets_by_var: {base: _range_targets(typedesc)} precomputed for the scenario.
            pinned: Optional (suffix, in_array) -> bool, true when the leaf is already
                pinned to a literal elsewhere and the guard would be redundant.

        Returns:
            List of (guard name, guardExpr node) pairs, one per unpinned primitive leaf with a range.
        """
        nodes = []
        for path, ptype, rng in range_targets_by_var.get(base, []):
            suffix   = self._path_suffix(path)
            in_array = self._path_has_array(path)
            if ptype == "boolean" or rng is None or (pinned and pinned(suffix, in_array)):
                continue
            node = self._range_node_for_var_or_param(path, subject_td, _var(path_prefix), rng)
            nodes.append((f"G_{base}{suffix}", node))
        return nodes

    def _range_nodes_for_var(self, step: "AST.Step", v: str, range_targets_by_var: dict,
                              type_by_name: dict) -> list:
        """Build a step variable's range-restriction guard nodes, skipping any the step itself pins.

        Args:
            step: Step the variable is assigned in.
            v: Variable name as declared in the spec.
            range_targets_by_var: {base: _range_targets(typedesc)} precomputed for the scenario.
            type_by_name: {base: typedesc} for every declared variable/constant.

        Returns:
            List of (guard name, guardExpr node) pairs for v's range restrictions.
        """
        base = _format_id(v)
        return self._range_nodes(
            base, f"{base}_p", type_by_name[base], range_targets_by_var,
            pinned=lambda suffix, in_array: self._pinned_to_literal(step, base, suffix, in_array),
        )

    def _length_pinned_to_literal(self, step: "AST.Step", base: str) -> bool:
        """Check if a step's own guard already asserts an exact literal length for this array/set.

        Args:
            step: Step whose own guardblock is checked.
            base: Formatted (bare) array/set variable name.

        Returns:
            True if an auto-injected cardinality-bound guard would be redundant.
        """
        if not step.guardblock:
            return False
        return any(
            _format_id(entry.varid) == base and not entry.stored
            and isinstance(entry.guard, AST.LengthGuard) and entry.guard.op == '=='
            for entry in step.guardblock.entries
        )

    def _cardinality_nodes(self, base: str, path_prefix: str, var_card: dict,
                            pinned: bool = False) -> list:
        """Build a len guard node asserting an array/set variable's own declared cardinality bound.

        Args:
            base: Formatted (bare) variable name.
            path_prefix: Bare path for the guard subject, e.g. "{base}_p".
            var_card: {base: (min_cardinality, max_cardinality)} for array/set variables.
            pinned: True if the length is already pinned to an exact literal elsewhere.

        Returns:
            [(guard name, guardExpr node)], or [] if base isn't an array/set, or pinned is True.
        """
        if base not in var_card or pinned:
            return []
        lo, hi = var_card[base]
        length = _len(_var(path_prefix))
        name = f"G_{base}.len"
        if lo == hi:
            return [(name, {"lhs": length, "op": "==", "rhs": _wrap(lo)})]
        return [(name, {
            "lhs": {"lhs": length, "op": ">=", "rhs": _wrap(lo)},
            "op":  "&&",
            "rhs": {"lhs": length, "op": "<=", "rhs": _wrap(hi)},
        })]

    def _cardinality_nodes_for_var(self, step: "AST.Step", v: str, var_card: dict) -> list:
        """Build a step variable's cardinality-bound guard node, skipped if the step pins its length.

        Args:
            step: Step the variable is assigned in.
            v: Variable name as declared in the spec.
            var_card: {base: (min_cardinality, max_cardinality)} for array/set variables.

        Returns:
            [(guard name, guardExpr node)], or [] if not applicable.
        """
        base = _format_id(v)
        return self._cardinality_nodes(base, f"{base}_p", var_card, self._length_pinned_to_literal(step, base))

    def _uniqueness_nodes(self, base: str, path_prefix: str, unique_vars: set) -> list:
        """Build a "uniqueElem" guard node for an array variable declared "unique".

        Args:
            base: Formatted (bare) variable name.
            path_prefix: Bare path for the guard subject, e.g. "{base}_p".
            unique_vars: Formatted names of array variables declared "unique".

        Returns:
            [(guard name, guardExpr node)], or [] if base isn't declared "unique".
        """
        if base not in unique_vars:
            return []
        return [(f"G_{base}.unique", _unique(_var(path_prefix)))]

    def _uniqueness_nodes_for_var(self, v: str, unique_vars: set) -> list:
        """Build a step variable's uniqueness guard node.

        Args:
            v: Variable name as declared in the spec.
            unique_vars: Formatted names of array variables declared "unique".

        Returns:
            [(guard name, guardExpr node)], or [] if not applicable.
        """
        base = _format_id(v)
        return self._uniqueness_nodes(base, f"{base}_p", unique_vars)

    def _gate_id(self, counters: dict, gate_ids: dict, sts_id: int, kind: str, step: "AST.Step") -> str:
        """Look up or allocate a gate ID for a step's (action, parameters) signature.

        Args:
            counters: {"In"|"Out": next local index}, mutated in place for a fresh ID.
            gate_ids: Suite-wide {(kind, action, frozenset(params)): gate_id} to check first.
            sts_id: This scenario's unique ID, used to make a fresh ID scenario-local.
            kind: "In" (AST.When step) or "Out" (AST.Then step).
            step: Step being gated.

        Returns:
            The shared gate ID for this signature, or a freshly allocated one.
        """
        params = frozenset(_format_id(v) + "_p" for v in step.varids)
        key    = (kind, step.action, params)
        if key in gate_ids:
            return gate_ids[key]
        gid = f"{kind}{sts_id}_{counters[kind]}"
        counters[kind] += 1
        return gid

    def _switch_guard_ref(self, guards: dict, g_idx: count, guard_ids: list,
                           extra_nodes: list = None) -> list[str]:
        """Combine a step's own guard ID(s) with auto-injected guard nodes into one switch guard ref.

        Args:
            guards: Scenario's {guard id: guardExpr node} map, extended in place with extra_nodes.
            g_idx: Shared counter for a fresh "true" guard ID when guard_ids is empty.
            guard_ids: The step's own guard ID(s), already present in guards.
            extra_nodes: Auto-injected (name, node) pairs (range/cardinality/uniqueness), if any.

        Returns:
            The guard IDs to AND together, always as a list (even a single one).
        """
        ids = list(guard_ids)
        for name, node in (extra_nodes or []):
            guards[name] = node
            ids.append(name)
        if not ids:
            gid = f"G{next(g_idx)}"
            guards[gid] = _wrap(True)
            return [gid]
        return ids

    def _assignment_ids(self, assignments: dict, assign_sig_to_id: dict, a_idx: count,
                         varids: list) -> list:
        """Look up or allocate assignment IDs for a step's variables, reusing one per (var, param) pair.

        Args:
            assignments: Scenario's {assignment id: {"target", "expression"}} map, extended in place.
            assign_sig_to_id: {(var, param): assignment id} cache, extended in place.
            a_idx: Shared counter for fresh assignment IDs.
            varids: Variable names assigned by the step.

        Returns:
            List of assignment IDs, one per variable in varids.
        """
        ids = []
        for v in varids:
            sig = (_format_id(v), _format_id(v) + "_p")
            if sig not in assign_sig_to_id:
                aid = f"A{next(a_idx)}"
                assign_sig_to_id[sig] = aid
                assignments[aid] = {"target": sig[0], "expression": _var(sig[1])}
            ids.append(assign_sig_to_id[sig])
        return ids

    def scenario_to_sts(self, scenario: AST.Scenario, var_info: dict, sts_id: int,
                         domains: dict = None, gate_ids: dict = None) -> dict:
        """Convert a single scenario to an STS dict, given its suite's precomputed variable data.

        Args:
            scenario: Parsed AST.Scenario object.
            var_info: Suite-wide data derived from vardefs alone, shared across every
                scenario in the file.
            sts_id: Unique ID for this STS.
            domains: {formatted_name: [typed values]} for declared array/set.
            gate_ids: {(kind, action, frozenset(params)): gate_id} the same
                action+parameters pair gets the same gate ID
                in every scenario it appears in.

        Returns:
            STS dict ready for JSON serialisation.
        """
        gate_ids = gate_ids if gate_ids is not None else {}

        constant_ids         = var_info["constant_ids"]
        initial_valuation    = var_info["initial_valuation"]
        var_card             = var_info["var_card"]
        unique_vars          = var_info["unique_vars"]
        type_by_name         = var_info["type_by_name"]
        range_targets_by_var = var_info["range_targets_by_var"]
        location_vars        = var_info["location_vars"]
        parameters           = var_info["parameters"]

        initial_state = (
            scenario.given is not None
            and any(s.action == "INITCOND" for s in scenario.given.steps)
        )

        when_steps = scenario.when.steps   # list[AST.Step]
        then_steps = scenario.then.steps   # list[AST.Step]
        M, N       = len(when_steps), len(then_steps)

        # Locations: L0 … L(M+N)
        locations = [f"L{i}_{sts_id}" for i in range(M + N + 1)]

        # Gate IDs: the same action+parameters (interaction pair) across the whole
        # suite, not just this scenario
        gate_counters = {"In": 1, "Out": 1}

        # inputGates: one per distinct When-step
        when_gate_ids = [self._gate_id(gate_counters, gate_ids, sts_id, "In", s) for s in when_steps]
        input_gates   = {
            gid: {"text": s.action, "parameters": [_format_id(v) + "_p" for v in s.varids]}
            for gid, s in zip(when_gate_ids, when_steps)
        }

        # outputGates: one per distinct Then-step
        then_gate_ids = [self._gate_id(gate_counters, gate_ids, sts_id, "Out", s) for s in then_steps]
        output_gates  = {
            gid: {"text": s.action, "parameters": [_format_id(v) + "_p" for v in s.varids]}
            for gid, s in zip(then_gate_ids, then_steps)
        }

        guards: dict[str, object] = {}
        g_idx  = count(1)

        # AST.Given steps: each step with a guardblock contributes one guard entry.
        # These are AND-ed into the first AST.When switch's guard.
        given_guard_ids = []
        if scenario.given:
            for step in scenario.given.steps:
                if step.guardblock:
                    gid = f"G{next(g_idx)}"
                    given_guard_ids.append(gid)
                    guards[gid] = _guardblock_to_tree(step.guardblock, var_card, as_param=False, constant_ids=constant_ids, domains=domains, type_by_name=type_by_name)

        assignments: dict[str, dict] = {}
        assign_sig_to_id: dict[tuple, str] = {}
        a_idx = count(1)
        _switches_list = []
        all_guard_ids = []

        for i, step in enumerate(when_steps + then_steps):
            gid = f"G{next(g_idx)}"
            all_guard_ids.append(gid)
            guards[gid] = _guardblock_to_tree(step.guardblock, var_card, constant_ids=constant_ids, domains=domains, type_by_name=type_by_name) if step.guardblock else _wrap(True)
            
            guard_ids = given_guard_ids + [gid] if step == when_steps[0] else [gid]
            range_nodes = [n for v in step.varids
                           for n in self._range_nodes_for_var(step, v, range_targets_by_var, type_by_name)
                           + self._cardinality_nodes_for_var(step, v, var_card)
                           + self._uniqueness_nodes_for_var(v, unique_vars)]
            in_out = "In" if step in when_steps else "Out"
            _switches_list.append({
                "init_loc":    f"L{i}_{sts_id}",
                "gate":        self._gate_id(gate_counters, gate_ids, sts_id, in_out, step),
                "guard":       self._switch_guard_ref(guards, g_idx, guard_ids, range_nodes),
                "assignments": self._assignment_ids(assignments, assign_sig_to_id, a_idx, step.varids),
                "end_loc":     f"L{i+1}_{sts_id}",
            })

        switches = {f"r_{idx + 1}": sw for idx, sw in enumerate(_switches_list)}

        return {
            "id":                f"sts_{sts_id:03d}",
            "description":       scenario.description,
            "documentation":     scenario.documentation,
            "initial_state":     initial_state,
            "initial_location":  locations[0],
            "gate_id_type":      "string",
            "location_id_type":  "string",
            "locationVariables": location_vars,
            "parameters":        parameters,
            "initialValuation":  initial_valuation,
            "locations":         locations,
            "inputGates":        input_gates,
            "outputGates":       output_gates,
            "guards":            guards,
            "assignments":       assignments,
            "switches":          switches,
        }
