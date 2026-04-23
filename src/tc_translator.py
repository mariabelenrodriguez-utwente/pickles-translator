import re
from typing import Any

_OP_TEXT: dict[str, str] = {
    '==': 'equal to',
    '!=': 'not equal to',
    '>':  'greater than',
    '<':  'lower than',
    '>=': 'greater or equal to',
    '<=': 'lower or equal to',
}

_CONJ_TEXT: dict[str, str] = {
    '&&': 'AND',
    '||': 'OR',
}


def _expand_guard(guard_str: str, guard_idx: dict[str, str]) -> str:
    """Expand guard IDs (e.g. 'G1 && G2') to their full expressions.

    Args:
        guard_str: Guard reference string from a switch.
        guard_idx: Mapping of guard ID to expression.

    Returns:
        Parenthesised conjunction of the referenced expressions.
    """
    parts = [guard_idx[gid] for gid in re.findall(r'G\d+', guard_str) if gid in guard_idx]
    return ' && '.join(f'({p})' for p in parts)


def _split_top_level(expr: str) -> list[tuple[str | None, str]]:
    """Split a guard expression on top-level && / || operators.

    Args:
        expr: Guard expression string, possibly containing nested parens.

    Returns:
        List of (conjunction_op_or_None, clause_string) pairs.
    """
    parts: list[tuple[str | None, str]] = []
    depth, buf, last_op = 0, [], None
    i = 0
    while i < len(expr):
        ch = expr[i]
        if ch == '(':
            depth += 1
            buf.append(ch)
        elif ch == ')':
            depth -= 1
            buf.append(ch)
        elif depth == 0 and expr[i:i + 2] in ('&&', '||'):
            parts.append((last_op, ''.join(buf).strip()))
            last_op = expr[i:i + 2]
            buf = []
            i += 2
            continue
        else:
            buf.append(ch)
        i += 1
    if buf:
        parts.append((last_op, ''.join(buf).strip()))
    return parts


def _render_clause(clause: str, shortened: bool = False) -> str:
    """Render a single comparison clause as a natural language string.

    Args:
        clause: A comparison expression like ``availability == 'PART AV'``.
        shortened: Whether to use a shortened format.

    Returns:
        Natural language string, e.g. ``"availability" is equal to PART AV``.
        Returns the raw clause on parse failure.
    """
    clause = clause.strip().strip('()')
    m = re.match(r'^([\w\-\[\]\.]+)\s*(==|!=|>=|<=|>|<)\s*(.+)$', clause.strip())
    if not m:
        return clause
    var, op, value = m.group(1).strip(), m.group(2), m.group(3).strip()
    if value.startswith("'") and value.endswith("'"):
        value = value[1:-1]
    if shortened:
        return f'{_OP_TEXT[op]} {_fmt_value(value)}'
    else:
        return f'"{_fmt_name(var)}" is {_OP_TEXT[op]} {value}'


def _render_guard_expr(guard_expr: str) -> tuple[str, bool]:
    """Render a full guard expression as indented natural language lines.

    Args:
        guard_expr: Expanded guard expression string.

    Returns:
        Multi-line string with each clause indented by four spaces.
    """
    parts = _split_top_level(guard_expr)
    lines = []
    single_clause = len(parts) == 1
    if len(parts) > 1:
        for conj, clause in parts:
            prefix = f'{_CONJ_TEXT[conj]} ' if conj else ''
            lines.append(f'    {prefix}{_render_clause(clause)}')
    else:
        lines.append(f'{_render_clause(guard_expr, True)}')
    return '\n'.join(lines), single_clause


def _fmt_name(name: str) -> str:
    """Strip _p suffix and replace hyphens with spaces."""
    name = name[:-2] if name.endswith('_p') else name
    return name.replace('-', ' ')


def _fmt_value(value: Any) -> str:
    """Render a scalar value as a string without surrounding quotes."""
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return str(value)


def _fmt_dict(d: dict) -> str:
    """Render an attribute dict as {key: val, ...} with formatted keys."""
    parts = [f'"{_fmt_name(k)}": {_fmt_value(v)}' for k, v in d.items()]
    return '{' + ', '.join(parts) + '}'


class TestCaseTranslator:
    """Translates structured test case dicts back to natural language."""

    def __init__(self, spec: dict) -> None:
        """Initialise with the composed STS specification.

        Args:
            spec: Composed STS dict as produced by compose_stss.
        """
        self._spec        = spec
        self._switch_idx  = spec.get('switches',      {})
        self._input_idx   = spec.get('inputActions',  {})
        self._output_idx  = spec.get('outputActions', {})
        self._guard_idx   = {gid: g['expression'] for gid, g in spec.get('guards', {}).items()}

    def translate(self, test_cases: list[dict], output_path: str) -> str:
        """Translate test cases to natural language and write to a file.

        Args:
            test_cases: List of test case dicts from TestGenerator.
            output_path: Destination path for the .txt file.

        Returns:
            The full natural language text that was written to disk.
        """
        blocks = [self._render_test_case(i + 1, tc) for i, tc in enumerate(test_cases)]
        text   = '\n\n'.join(blocks)
        with open(output_path, 'w') as f:
            f.write(text)
        return text

    def _render_test_case(self, number: int, tc: dict) -> str:
        """Render a single test case dict as a natural language block.

        Args:
            number: 1-based test case index used in the header line.
            tc: Test case dict with initial_values and steps.

        Returns:
            Multi-line natural language string for this test case.
        """
        lines = [f'Test Case {number}:',
                 self._render_initial(tc['initial_values'])]
        for step in tc['steps']:
            lines.append(self._render_step(step))
        return '\n'.join(lines)

    def _render_initial(self, values: dict) -> str:
        """Render the initial values block.

        Args:
            values: Dict of variable id → value from initial_values.

        Returns:
            Multi-line string starting with the Given header.
        """
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

    def _render_step(self, step: dict) -> str:
        """Dispatch to input or output renderer.

        Args:
            step: Step dict with switch_id and values.

        Returns:
            Natural language string for this step.
        """
        sw    = self._switch_idx.get(step['switch_id'], {})
        gate  = sw.get('gate', '')
        if gate in self._input_idx:
            return self._render_input(gate, step.get('values', {}))
        return self._render_output(gate, sw.get('guard', ''))

    def _render_input(self, gate: str, inputs: dict) -> str:
        """Render an input step as a When clause.

        Args:
            gate: Action gate ID (e.g. ``In1``).
            inputs: Dict of parameter id → value for this step.

        Returns:
            Natural language string starting with ``When``.
        """
        action = self._input_idx.get(gate, {})
        text   = action.get('text', gate)
        params = action.get('parameters', [])

        if not params or not inputs:
            return f'When {text}'

        lines: list[str] = []
        for pid in params:
            val = inputs.get(pid)
            if val is None:
                continue
            base = _fmt_name(pid)
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

    def _render_output(self, gate: str, guard_str: str) -> str:
        """Render an output step as a Then clause with oracle condition.

        Args:
            gate: Action gate ID (e.g. ``Out1``).
            guard_str: Raw guard reference string from the switch (e.g. ``G3``).

        Returns:
            Natural language string starting with ``Then``.
        """
        action     = self._output_idx.get(gate, {})
        text       = action.get('text', gate)
        params     = action.get('parameters', [])
        guard_expr = _expand_guard(guard_str, self._guard_idx)

        if not params or not guard_expr:
            return f'Then {text}'

        param_base = _fmt_name(params[0])
        rendered_guard, single_clause = _render_guard_expr(guard_expr)
        if single_clause:
            return '\n'.join([
                f'Then {text} "{param_base}" {rendered_guard}',
            ])
        else:
            return '\n'.join([
                f'Then {text} "{param_base}" such that:',
                rendered_guard,
            ])
