import json
import operator
import os
from typing import Any

_OP_TEXT: dict[str, str] = {
    '==':     'equal to',
    '!=':     'not equal to',
    '>':      'greater than',
    '<':      'lower than',
    '>=':     'greater or equal to',
    '<=':     'lower or equal to',
    'in':     'in',
    'not in': 'not in',
}

_ARITH_OPS = {
    '-': operator.sub,
    '+': operator.add,
    '*': operator.mul,
    '/': operator.truediv,
}

_CONJ_TEXT: dict[str, str] = {
    '&&': 'AND',
    '||': 'OR',
}

_QUANT_TEXT: dict[str, str] = {
    'at_least': 'at least',
    'at_most':  'at most',
    'exactly':  'exactly',
}

_CONTAINS_TEXT: dict[str, str] = {
    'contains':      'contains',
    'not_contains':  'does not contain',
    'contains_only': 'contains only',
}


def _split_top_level(node: Any) -> list[tuple[str | None, Any]]:
    """Split a guard tree into a list of top level clauses.

    Args:
        node: Guard tree or leaf node built by transformer.py.

    Returns:
        List of (conjunction, clause) pairs, left to right. Conjunction is
        None for the first pair.
    """
    if isinstance(node, dict) and node.get('op') in ('&&', '||'):
        left  = _split_top_level(node['lhs'])
        right = _split_top_level(node['rhs'])
        _, first_clause = right[0]
        right[0] = (node['op'], first_clause)
        return left + right
    return [(None, node)]


def _var_path(node: Any) -> str | None:
    """Get the variable path from a guard node.

    Args:
        node: A guard node. May be a {"var": path} reference or a literal.

    Returns:
        The variable path string, or None if node is not a variable reference.
    """
    return node['var'] if isinstance(node, dict) and 'var' in node else None


def _is_constant(var_path: str, spec: dict) -> bool:
    """Check if a variable path names a spec level constant.

    Args:
        var_path: Variable path to check.
        spec: The target STS dict.

    Returns:
        True if var_path has no parameter counterpart in spec.
    """
    return f'{var_path}_p' not in spec.get('parameters', {})


def _base_path(path: str) -> str:
    """Remove the trailing "_p" suffix from a variable path.

    Args:
        path: Variable path, with or without the "_p" suffix.

    Returns:
        The path without the "_p" suffix.
    """
    return path[:-2] if path.endswith('_p') else path


def _len_operand(node: Any) -> Any | None:
    """Get the operand of a "len" guard node.

    Args:
        node: A guard node. May be a {"op": "len", "rhs": ...} node.

    Returns:
        The operand node, or None if node is not a "len" node.
    """
    if isinstance(node, dict) and node.get('op') == 'len':
        return node.get('rhs')
    return None


def _resolve_type(node: Any, spec: dict, elem_binder: str | None = None, elem_type: dict | None = None) -> dict | None:
    """Resolve the variable definition for a variable or project node.

    Args:
        node: A guard node. May be a {"var": ...} reference or a "project" node.
        spec: The target STS dict, with parameter and location variable definitions.
        elem_binder: Name bound by the innermost array quantifier, if any.
        elem_type: Declared per-element type for elem_binder, if any.

    Returns:
        The variable definition dict, or None if node cannot be resolved.
    """
    if isinstance(node, dict) and node.get('op') == 'project':
        parent = _resolve_type(node['lhs'], spec, elem_binder, elem_type)
        if not parent:
            return None
        return parent.get('attributes', {}).get(_var_path(node['rhs']))
    path = _var_path(node)
    if path is None:
        return None
    if elem_binder is not None and path == elem_binder:
        return elem_type
    base = path[:-2] if path.endswith('_p') else path
    return spec.get('parameters', {}).get(path) or spec.get('locationVariables', {}).get(base)


def _fmt_subject(node: Any, spec: dict, elem_binder: str | None = None, elem_type: dict | None = None) -> str:
    """Get the display name for a guard clause's subject node.

    Args:
        node: A guard node. May be a {"var": ...} reference or a "project" node.
        spec: The target STS dict.
        elem_binder: Name bound by the innermost array quantifier, if any.
        elem_type: Declared per-element type for elem_binder, if any.

    Returns:
        The formatted display name.
    """
    if isinstance(node, dict) and node.get('op') == 'project':
        return _fmt_name(_var_path(node['rhs']))
    return _fmt_name(node)


def _unwrap(node: Any) -> Any:
    """Get the bare scalar value from a wrapped guard literal.

    Args:
        node: A guard node. May be a wrapped literal, a reference, or a scalar.

    Returns:
        The bare scalar value, or node unchanged if it is not a wrapped literal.
    """
    if isinstance(node, dict) and len(node) == 1:
        for typename in ('string', 'integer', 'float', 'boolean'):
            if typename in node:
                return node[typename]
    return node


def _eval_expr(node: Any, state: dict) -> Any | None:
    """Resolve a guard expression node to a concrete value.

    Uses the tracked variable state to evaluate variable references and
    arithmetic operations.

    Args:
        node: A variable reference, an arithmetic node, or a literal.
        state: Current variable id to value map.

    Returns:
        The resolved value, or None if a referenced variable is not known yet.
    """
    path = _var_path(node)
    if path is not None:
        return state.get(path)
    if isinstance(node, dict) and node.get('op') in _ARITH_OPS:
        lhs = _eval_expr(node['lhs'], state)
        rhs = _eval_expr(node['rhs'], state)
        if lhs is None or rhs is None:
            return None
        return _ARITH_OPS[node['op']](lhs, rhs)
    return _unwrap(node)


def _render_clause(clause: Any, spec: dict, shortened: bool = False, state: dict | None = None) -> str:
    """Render a single guard clause as natural language text.

    Args:
        clause: A comparison, cardinality, exists, forall, subset, or contains node.
        spec: The target STS dict.
        shortened: If True, omit the leading subject name.
        state: Current variable id to value map, used to resolve arithmetic values.

    Returns:
        Natural language string for the clause. Falls back to str(clause) on
        parse failure.
    """
    if isinstance(clause, dict) and clause.get('op') == 'cardinality':
        return _render_count_clause(clause, spec, shortened=shortened)
    if isinstance(clause, dict) and clause.get('op') == 'exists':
        return _render_exists_clause(clause, spec, shortened=shortened)
    if isinstance(clause, dict) and clause.get('op') == 'forall':
        return _render_forall_clause(clause, spec, shortened=shortened)
    if isinstance(clause, dict) and clause.get('op') == 'subset':
        lhs, rhs = clause['lhs'], clause['rhs']
        text = f'is subset of {_fmt_rhs(rhs, spec=spec)}'
        return text if shortened else f'"{_fmt_subject(lhs, spec)}" {text}'
    if isinstance(clause, dict) and clause.get('op') in _CONTAINS_TEXT:
        lhs, op, rhs = clause['lhs'], clause['op'], clause['rhs']
        text = f'{_CONTAINS_TEXT[op]} {_fmt_rhs(rhs, spec=spec)}'
        return text if shortened else f'"{_fmt_subject(lhs, spec)}" {text}'
    if isinstance(clause, dict) and clause.get('op') == 'uniqueElem':
        text = 'has no duplicate elements'
        return text if shortened else f'"{_fmt_subject(clause.get("rhs"), spec)}" {text}'
    if isinstance(clause, dict) and clause.get('op') == '!':
        inner       = clause['rhs']
        len_operand = _len_operand(inner.get('lhs')) if isinstance(inner, dict) else None
        if (isinstance(inner, dict) and inner.get('op') == '==' and _unwrap(inner.get('rhs')) == 0
                and len_operand is not None):
            text = 'is not empty'
            return text if shortened else f'"{_fmt_subject(len_operand, spec)}" {text}'
        return f'not ({_render_clause(inner, spec, True)})'
    lhs_len_operand = _len_operand(clause.get('lhs')) if isinstance(clause, dict) else None
    if (isinstance(clause, dict) and clause.get('op') in _OP_TEXT and lhs_len_operand is not None):
        base = _fmt_subject(lhs_len_operand, spec)
        text = f'has length {_OP_TEXT[clause["op"]]} {_fmt_rhs(clause["rhs"], spec=spec)}'
        return text if shortened else f'"{base}" {text}'
    if not (isinstance(clause, dict) and clause.get('op') in _OP_TEXT):
        return str(clause)
    var, op, value = clause['lhs'], clause['op'], clause['rhs']
    if shortened:
        return f'{_OP_TEXT[op]} {_fmt_rhs(value, state, spec)}'
    else:
        return f'"{_fmt_subject(var, spec)}" is {_OP_TEXT[op]} {_fmt_rhs(value, state, spec)}'


def _render_count_clause(clause: dict, spec: dict, elem_binder: str | None = None,
                          elem_type: dict | None = None, shortened: bool = False) -> str:
    """Render a "cardinality" guard node as natural language text.

    Args:
        clause: A "cardinality" node with quantifier, count, and expression fields.
        spec: The target STS dict.
        elem_binder: Binder name from an enclosing quantifier, if nested.
        elem_type: Per-element type from an enclosing quantifier, if nested.
        shortened: If True, omit the leading subject name.

    Returns:
        Natural language string for the clause.
    """
    array_type    = _resolve_type(clause['over'], spec, elem_binder, elem_type)
    inner_elem_ty = array_type.get('elements') if array_type else None
    qtext  = _QUANT_TEXT.get(clause['quantifier'], clause['quantifier'])
    n      = clause['n']
    noun   = 'element' if n == 1 else 'elements'
    body   = f'has {qtext} {n} {noun} where each element ' \
             f'{_render_element(clause["expression"], spec, clause["lambda"], inner_elem_ty)}'
    return body if shortened else f'"{_fmt_subject(clause["over"], spec, elem_binder, elem_type)}" {body}'


def _render_exists_clause(clause: dict, spec: dict, elem_binder: str | None = None,
                           elem_type: dict | None = None, shortened: bool = False) -> str:
    """Render an "exists" guard node as natural language text.

    Args:
        clause: An "exists" node with over, lambda, and expression fields.
        spec: The target STS dict.
        elem_binder: Binder name from an enclosing quantifier, if nested.
        elem_type: Per-element type from an enclosing quantifier, if nested.
        shortened: If True, omit the leading subject name.

    Returns:
        Natural language string for the clause.
    """
    array_type    = _resolve_type(clause['over'], spec, elem_binder, elem_type)
    inner_elem_ty = array_type.get('elements') if array_type else None
    body = f'has at least 1 element where each element ' \
           f'{_render_element(clause["expression"], spec, clause["lambda"], inner_elem_ty)}'
    return body if shortened else f'"{_fmt_subject(clause["over"], spec, elem_binder, elem_type)}" {body}'


def _render_forall_clause(clause: dict, spec: dict, elem_binder: str | None = None,
                           elem_type: dict | None = None, shortened: bool = False) -> str:
    """Render a "forall" guard node as natural language text.

    Args:
        clause: A "forall" node with over, lambda, and expression fields.
        spec: The target STS dict.
        elem_binder: Binder name from an enclosing quantifier, if nested.
        elem_type: Per-element type from an enclosing quantifier, if nested.
        shortened: If True, omit the leading subject name.

    Returns:
        Natural language string for the clause.
    """
    array_type    = _resolve_type(clause['over'], spec, elem_binder, elem_type)
    inner_elem_ty = array_type.get('elements') if array_type else None
    body = f'has all elements where each element ' \
           f'{_render_element(clause["expression"], spec, clause["lambda"], inner_elem_ty)}'
    return body if shortened else f'"{_fmt_subject(clause["over"], spec, elem_binder, elem_type)}" {body}'


def _render_element(node: Any, spec: dict, elem_binder: str, elem_type: dict | None) -> str:
    """Render the per-element condition of a cardinality, exists, or forall node.

    Args:
        node: A comparison, conjunction, or nested quantifier node.
        spec: The target STS dict.
        elem_binder: Binder name of the enclosing quantifier.
        elem_type: Per-element type of the enclosing quantifier, if any.

    Returns:
        Natural language string for the condition.
    """
    if isinstance(node, dict) and node.get('op') in ('&&', '||'):
        conj = _CONJ_TEXT.get(node['op'], node['op'])
        return (f'{_render_element(node["lhs"], spec, elem_binder, elem_type)} {conj} '
                f'{_render_element(node["rhs"], spec, elem_binder, elem_type)}')
    if isinstance(node, dict) and node.get('op') == 'cardinality':
        return _render_count_clause(node, spec, elem_binder, elem_type)
    if isinstance(node, dict) and node.get('op') == 'exists':
        return _render_exists_clause(node, spec, elem_binder, elem_type)
    if isinstance(node, dict) and node.get('op') == 'forall':
        return _render_forall_clause(node, spec, elem_binder, elem_type)
    if isinstance(node, dict) and node.get('op') in _OP_TEXT:
        attr   = _fmt_element_attr(node['lhs'], spec, elem_binder, elem_type)
        prefix = f'"{attr}" ' if attr else ''
        return f'{prefix}is {_OP_TEXT[node["op"]]} {_fmt_value(node["rhs"])}'
    return str(node)


def _fmt_element_attr(node: Any, spec: dict, elem_binder: str, elem_type: dict | None) -> str | None:
    """Get the attribute name from an element condition's lhs node.

    Args:
        node: The lhs node of a count, exists, or forall condition.
        spec: The target STS dict.
        elem_binder: Binder name of the enclosing quantifier.
        elem_type: Per-element type of the enclosing quantifier, if any.

    Returns:
        The attribute display name, or None if node is a plain element reference.
    """
    if isinstance(node, dict) and node.get('op') == 'project':
        return _fmt_name(_var_path(node['rhs']))
    return None


def _render_guard_expr(guard_node: Any, spec: dict, state: dict | None = None) -> tuple[str, bool]:
    """Render a full guard tree as indented natural language lines.

    Drops non equality clauses when an equality clause is present at the
    top level, since the exact value already covers them.

    Args:
        guard_node: Guard tree or leaf node.
        spec: The target STS dict.
        state: Current variable id to value map, used to resolve values.

    Returns:
        Tuple of the rendered text and a flag for whether only one clause remained.
    """
    parts = _split_top_level(guard_node)
    equalities = [clause for _, clause in parts if isinstance(clause, dict) and clause.get('op') == '==']
    if equalities:
        parts = [(None if i == 0 else '&&', clause) for i, clause in enumerate(equalities)]

    lines = []
    single_clause = len(parts) == 1
    if single_clause:
        lines.append(_render_clause(parts[0][1], spec, True, state))
    else:
        for conj, clause in parts:
            prefix = f'{_CONJ_TEXT[conj]} ' if conj else ''
            lines.append(f'    {prefix}{_render_clause(clause, spec, state=state)}')
    return '\n'.join(lines), single_clause


def _clause_value(clause: Any, spec: dict) -> str:
    """Get the single value a Then guard clause compares against.

    Args:
        clause: A guard clause node.
        spec: The target STS dict.

    Returns:
        The formatted rhs value, or the clause's own shortened text if it has
        no single rhs value.
    """
    if isinstance(clause, dict) and clause.get('op') in _OP_TEXT and 'rhs' in clause:
        return _fmt_rhs(clause['rhs'], spec=spec)
    return _render_clause(clause, spec, shortened=True)


def _fmt_name(name: Any) -> str:
    """Format a variable path or reference as a display name.

    Strips the "_p" suffix and replaces hyphens with spaces.

    Args:
        name: A variable path string or a {"var": path} reference.

    Returns:
        The formatted display name.
    """
    path = _var_path(name)
    name = path if path is not None else name
    name = name[:-2] if name.endswith('_p') else name
    return name.replace('-', ' ')


def _fmt_value(value: Any, state: dict | None = None, spec: dict | None = None) -> str:
    """Render a guard value as plain text, without quotes.

    Handles scalars, variable references, lists, and arithmetic nodes.

    Args:
        value: The value to render.
        state: Current variable id to value map, used to resolve arithmetic nodes.
        spec: The target STS dict, used for arithmetic operand formatting.

    Returns:
        The formatted string.
    """
    path = _var_path(value)
    if path is not None:
        return path
    if isinstance(value, dict) and value.get('op') in _ARITH_OPS:
        resolved = _eval_expr(value, state) if state is not None else None
        if resolved is not None:
            return _fmt_value(resolved)
        return (f'{_fmt_rhs(value["lhs"], state, spec)} {value["op"]} '
                f'{_fmt_rhs(value["rhs"], state, spec)}')
    value = _unwrap(value)
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, list):
        return '{' + ', '.join(_fmt_value(v, state, spec) for v in value) + '}'
    return str(value)


def _fmt_rhs(value: Any, state: dict | None = None, spec: dict | None = None) -> str:
    """Render a guard clause's rhs value as display text.

    A reference to a mutable variable is quoted as a name. A reference to
    a constant is resolved to its concrete value.

    Args:
        value: The rhs value to render.
        state: Current variable id to value map, used to resolve constants.
        spec: The target STS dict, needed to detect constant references.

    Returns:
        The formatted string.
    """
    path = _var_path(value)
    if path is not None:
        if spec is not None and _is_constant(path, spec):
            const_value = state.get(path) if state is not None else None
            if const_value is None:
                const_value = spec.get('initialValuation', {}).get(path)
            if const_value is not None:
                return _fmt_value(const_value)
        return f'"{_fmt_name(value)}"'
    return _fmt_value(value, state, spec)


def _fmt_dict(d: dict) -> str:
    """Render an attribute dict as a formatted key-value string.

    Args:
        d: The attribute dict to render.

    Returns:
        String in the form '{"key": val, ...}'.
    """
    parts = [f'"{_fmt_name(k)}": {_fmt_value(v)}' for k, v in d.items()]
    return '{' + ', '.join(parts) + '}'


def _collapse_consecutive_keywords(blocks: list[str]) -> list[str]:
    """Replace repeated step keywords with "And".

    Rewrites a step's leading "When" or "Then" to "And" when the step
    right before it starts with the same keyword.

    Args:
        blocks: List of rendered step strings, in order.

    Returns:
        List of step strings with repeated keywords collapsed.
    """
    result = []
    last_keyword = None
    for block in blocks:
        first_line, sep, rest = block.partition('\n')
        keyword, kw_sep, remainder = first_line.partition(' ')
        if keyword in ('When', 'Then'):
            if keyword == last_keyword:
                first_line = f'And{kw_sep}{remainder}'
            else:
                last_keyword = keyword
        result.append(f'{first_line}{sep}{rest}')
    return result


def _struct_attrs(param_type: Any) -> list[str] | None:
    """Get the declared attribute names of a structure typed parameter.

    Args:
        param_type: A parameter type dict. May describe a structure or an
            array of structures.

    Returns:
        List of attribute names, or None if param_type is not struct shaped.
    """
    if not isinstance(param_type, dict):
        return None
    if param_type.get('type') == 'structure':
        return list(param_type.get('attributes', {}).keys())
    if param_type.get('type') == 'array':
        elements = param_type.get('elements')
        if isinstance(elements, dict) and elements.get('type') == 'structure':
            return list(elements.get('attributes', {}).keys())
    return None


def _struct_row(elem: Any, attrs: list[str]) -> list[str]:
    """Build one Cucumber table row for a struct element.

    Args:
        elem: A struct element, as a dict or a positional list of values.
        attrs: Attribute names to align the row to.

    Returns:
        List of formatted cell strings, one per entry in attrs.
    """
    if isinstance(elem, dict):
        return [_fmt_value(elem.get(a)) for a in attrs]
    values = elem if isinstance(elem, list) else [elem]
    return [_fmt_value(values[i]) if i < len(values) else '' for i in range(len(attrs))]


def _render_struct_table(attrs: list[str], rows: list[list[str]]) -> list[str]:
    """Build aligned Cucumber data table lines.

    Args:
        attrs: Column header names.
        rows: Table rows, each a list of already formatted cell strings.

    Returns:
        List of formatted table lines, header first.
    """
    widths = [len(a) for a in attrs]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def _line(cells: list[str]) -> str:
        return '  | ' + ' | '.join(cell.ljust(widths[i]) for i, cell in enumerate(cells)) + ' |'

    return [_line(attrs)] + [_line(row) for row in rows]


def _is_json_value(val: Any) -> bool:
    """Check if a value should be written to a JSON file instead of inlined.

    Args:
        val: The value to check.

    Returns:
        True for a dict, or a non-empty list of only dicts.
    """
    if isinstance(val, dict):
        return True
    return isinstance(val, list) and bool(val) and all(isinstance(e, dict) for e in val)


class TestCaseTranslator:
    """Translates structured test case dicts to natural language text."""

    def __init__(self, spec: dict | list[dict]) -> None:
        """Initialize the translator with the target STS specification.

        Args:
            spec: A single STS dict, or a list of STS dicts, one per
                scenario. inputGates/outputGates are shared across scenarios.
        """
        specs = spec if isinstance(spec, list) else [spec]
        self._specs      = {s['id']: s for s in specs if 'id' in s}
        self._spec        = specs[0] if specs else {}
        self._switch_idx  = self._spec.get('switches',  {})
        self._guard_idx   = self._spec.get('guards', {})
        self._input_idx   = {}
        self._output_idx  = {}
        self._param_idx   = {}
        for s in specs:
            self._input_idx.update(s.get('inputGates', {}))
            self._output_idx.update(s.get('outputGates', {}))
            self._param_idx.update(s.get('parameters', {}))
        self._json_counters: dict[str, int] = {}

    def translate(self, test_cases: list[dict], output_path: str) -> str:
        """Translate test cases to natural language and write them to a file.

        Args:
            test_cases: List of test case dicts from TestGenerator.
            output_path: Destination path for the .pickles file.

        Returns:
            The full natural language text that was written to disk.
        """
        blocks = [self._render_test_case(i + 1, tc) for i, tc in enumerate(test_cases)]
        text   = '\n\n'.join(blocks)
        with open(output_path, 'w') as f:
            f.write(text)
        return text

    def _render_test_case(self, number: int, tc: dict) -> str:
        """Render one test case dict as a natural language block.

        Args:
            number: 1-based test case index, used in the header line.
            tc: Test case dict with initial_values and steps.

        Returns:
            Multi-line natural language string for this test case.
        """
        lines = [f'Test Case {number}:',
                 self._render_initial(tc['initial_values']),
                 self.render_steps(tc['steps'], initial_values=tc['initial_values'])]
        return '\n'.join(lines)

    def render_steps(self, steps: list[dict], json_dir: str | None = None,
                      keyword_map: dict[str, Any] | None = None,
                      initial_values: dict | None = None) -> str:
        """Render a test case's steps as When and Then lines.

        Does not render the Given block. Callers add it separately.

        Args:
            steps: List of step dicts for this test case.
            json_dir: Directory to write dict or list-of-dict values as
                JSON files. None keeps inline rendering.
            keyword_map: Maps an action's text to a proxy template string.
                None keeps the default natural language rendering.
            initial_values: Starting variable id to value map, used to
                track state across steps. None disables value tracking.

        Returns:
            Multi-line natural language string, one entry per step.
        """
        state  = dict(initial_values) if initial_values is not None else None
        blocks = [self._render_step(step, json_dir, keyword_map, state) for step in steps]
        return '\n'.join(_collapse_consecutive_keywords(blocks))

    def _render_initial(self, values: dict, json_dir: str | None = None,
                         keyword_map: dict[str, str] | None = None) -> str:
        """Render the initial values block.

        Args:
            values: Dict of variable id to value.
            json_dir: Directory to write dict or list-of-dict values as
                JSON files. None keeps inline rendering.
            keyword_map: Maps the reserved "__given__" key to a custom
                header text. Ignored when json_dir is None.

        Returns:
            Multi-line string starting with the Given header.
        """
        if json_dir is None:
            lines = ['Given the system is initialized with values:']
            for vid, val in values.items():
                name = _fmt_name(vid)
                if isinstance(val, list):
                    lines.append(f'    "{name}":')
                    for i, elem in enumerate(val, 1):
                        entry = _fmt_dict(elem) if isinstance(elem, dict) else _fmt_value(elem)
                        lines.append(f'        {i}: {entry}')
                else:
                    lines.append(f'    "{name}": {_fmt_value(val)}')
            return '\n'.join(lines)

        header = (keyword_map or {}).get('__given__', 'the system is initialized with values')
        rows = []
        for vid, val in values.items():
            name = _fmt_name(vid)
            cell = self._write_json_value(name, val, json_dir) if _is_json_value(val) else _fmt_value(val)
            rows.append((name, cell))
        widths = [max(len(row[col]) for row in rows) for col in (0, 1)]
        table = [
            '    | ' + ' | '.join(cell.ljust(w) for cell, w in zip(row, widths)) + ' |'
            for row in rows
        ]
        return '\n'.join([f'Given {header}:', *table])

    def _render_step(self, step: dict, json_dir: str | None = None,
                      keyword_map: dict[str, Any] | None = None, state: dict | None = None) -> str:
        """Dispatch a step dict to the input or output renderer.

        Args:
            step: A step dict. Either an input step with "gate", or an
                output step with "switch_id".
            json_dir: Forwarded to _render_input.
            keyword_map: Forwarded to _render_input and _render_output.
            state: Forwarded to _render_output.

        Returns:
            Natural language string for this step.
        """
        if 'gate' in step:
            return self._render_input(step['gate'], step.get('values', {}), json_dir, keyword_map)

        spec       = self._specs.get(step.get('sts_id'), self._spec)
        switch_idx = spec.get('switches', {})
        sw         = switch_idx.get(step['switch_id'], {})
        gate       = sw.get('gate', '')
        if gate in self._input_idx:
            return self._render_input(gate, step.get('values', {}), json_dir, keyword_map)
        return self._render_output(gate, sw.get('guard', []), spec, keyword_map, state)

    def _write_json_value(self, base_name: str, val: Any, json_dir: str) -> str:
        """Write a dict or list-of-dict parameter value to a JSON file.

        Args:
            base_name: Parameter's display name.
            val: The dict or list-of-dict value to write.
            json_dir: Directory to write into. Created if missing.

        Returns:
            The written file's basename.
        """
        os.makedirs(json_dir, exist_ok=True)
        safe_name = base_name.replace(' ', '_').replace('-', '_')
        count = self._json_counters.get(safe_name, 0) + 1
        self._json_counters[safe_name] = count
        filename = f'{safe_name}_{count:03d}.json'
        with open(os.path.join(json_dir, filename), 'w') as f:
            json.dump(val, f, indent=2)
        return filename

    def _render_input(self, gate: str, inputs: dict, json_dir: str | None = None,
                       keyword_map: dict[str, str] | None = None) -> str:
        """Render an input step as a When clause.

        Args:
            gate: Action gate ID, for example "In1".
            inputs: Dict of parameter id to value for this step.
            json_dir: Directory to write struct or JSON-shaped values into.
                None keeps the inline numbered-list rendering.
            keyword_map: Maps the action's text to a proxy template string
                that replaces the step's rendered text.

        Returns:
            Natural language string starting with "When".
        """
        action = self._input_idx.get(gate, {})
        text   = action.get('text', gate)
        params = action.get('parameters', [])

        template = (keyword_map or {}).get(text)
        if template is not None:
            values = []
            for pid in params:
                val = inputs.get(pid)
                if val is None:
                    continue
                base = _fmt_name(pid)
                if json_dir is not None and _is_json_value(val):
                    values.append(self._write_json_value(base, val, json_dir))
                elif isinstance(val, list):
                    values.extend(_fmt_value(elem) for elem in val)
                else:
                    values.append(_fmt_value(val))
            return f'When {template.format(*values)}'

        if not params or not inputs:
            return f'When {text}'

        lines: list[str] = []
        for pid in params:
            val = inputs.get(pid)
            if val is None:
                continue
            base = _fmt_name(pid)

            if json_dir is not None:
                attrs = _struct_attrs(self._param_idx.get(pid))
                if attrs:
                    if isinstance(val, list) and val and all(isinstance(e, dict) for e in val):
                        rows = [_struct_row(e, attrs) for e in val]
                    else:
                        rows = [_struct_row(val, attrs)]
                    lines.append(f'When {text} "{base}" with values:' if not lines
                                 else f'    "{base}" with values:')
                    lines.extend(_render_struct_table(attrs, rows))
                    continue
                if _is_json_value(val):
                    filename = self._write_json_value(base, val, json_dir)
                    entry = f'"{base}" with values as "{filename}"'
                    lines.append(f'When {text} {entry}' if not lines else f'    {entry}')
                    continue

            if not lines:
                lines.append(f'When {text} "{base}" with values:')
            lines.append(f'    "{base}":')
            if isinstance(val, list):
                for i, elem in enumerate(val, 1):
                    entry = _fmt_dict(elem) if isinstance(elem, dict) else _fmt_value(elem)
                    lines.append(f'        {i}: {entry}')
            else:
                lines.append(f'    {_fmt_value(val)}')

        return '\n'.join(lines) if lines else f'When {text}'

    def _resolve_guard(self, guard_ids: list[str], guard_idx: dict | None = None) -> Any:
        """Resolve a switch's guard reference to one combined guard tree.

        Args:
            guard_ids: Guard IDs to AND together.
            guard_idx: The scenario's guards map to resolve them against.
                Defaults to the first or only spec's map.

        Returns:
            The combined guard tree, or None if no guard IDs resolve.
        """
        guard_idx = guard_idx if guard_idx is not None else self._guard_idx
        nodes = [guard_idx[gid] for gid in guard_ids if gid in guard_idx]
        if not nodes:
            return None
        node = nodes[0]
        for n in nodes[1:]:
            node = {"lhs": node, "op": "&&", "rhs": n}
        return node

    def _render_output(self, gate: str, guard_ids: list[str], spec: dict | None = None,
                        keyword_map: dict[str, Any] | None = None, state: dict | None = None) -> str:
        """Render an output step as a Then clause with its guard condition.

        Args:
            gate: Action gate ID, for example "Out1".
            guard_ids: Guard IDs to AND together for this step.
            spec: The scenario's STS dict to resolve the guard against.
                Defaults to the first or only spec.
            keyword_map: Maps the action's text to a proxy template, or to
                a per-variable map of templates, for the guard's clauses.
            state: Running variable id to value map for this test case.
                Updated in place with each rendered equality clause.

        Returns:
            Natural language string starting with "Then".
        """
        spec       = spec if spec is not None else self._spec
        action     = self._output_idx.get(gate, {})
        text       = action.get('text', gate)
        params     = action.get('parameters', [])
        guard_expr = self._resolve_guard(guard_ids, spec.get('guards', {}))
        parts      = _split_top_level(guard_expr) if guard_expr is not None else []

        updates = {}
        for _, clause in parts:
            if not isinstance(clause, dict) or clause.get('op') != '==':
                continue
            var_path = _var_path(clause.get('lhs'))
            if var_path is None:
                continue
            value = _eval_expr(clause['rhs'], state) if state is not None else None
            if value is not None:
                updates[_base_path(var_path)] = value

        template = (keyword_map or {}).get(text)
        result = None
        if isinstance(template, dict):
            lines = []
            for _, clause in parts:
                if not isinstance(clause, dict) or clause.get('op') != '==':
                    continue
                var_path = _var_path(clause.get('lhs'))
                base_path = _base_path(var_path) if var_path is not None else None
                if base_path is None or base_path not in template:
                    continue
                value   = updates.get(base_path)
                display = _fmt_value(value) if value is not None else _clause_value(clause, spec)
                keyword = 'Then' if not lines else 'And'
                lines.append(f'{keyword} {template[base_path].format(display)}')
            if lines:
                result = '\n'.join(lines)
        elif template is not None:
            values = [_clause_value(clause, spec) for _, clause in parts]
            result = f'Then {template.format(*values)}'

        if result is None:
            if not params or guard_expr is None:
                result = f'Then {text}'
            else:
                param_base = _fmt_name(params[0])
                rendered_guard, single_clause = _render_guard_expr(guard_expr, spec, state)
                if single_clause:
                    result = f'Then {text} "{param_base}" {rendered_guard}'
                else:
                    result = '\n'.join([f'Then {text} "{param_base}" such that:', rendered_guard])

        if state is not None:
            state.update(updates)
        return result
