"""Parses lists of Lattest test cases into TestCaseTranslator test cases.
"""
from typing import Any

from src.tc_translator import _unwrap, _var_path


def _split_top(s: str) -> list[str]:
    """Split s on top-level commas, respecting (), [], {} nesting and "..." strings.

    Args:
        s: Source text to split.

    Returns:
        List of trimmed top-level segments.
    """
    parts: list[str] = []
    depth = 0
    buf: list[str] = []
    in_str = False
    for c in s:
        if in_str:
            buf.append(c)
            if c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            buf.append(c)
        elif c in '([{':
            depth += 1
            buf.append(c)
        elif c in ')]}':
            depth -= 1
            buf.append(c)
        elif c == ',' and depth == 0:
            parts.append(''.join(buf).strip())
            buf = []
        else:
            buf.append(c)
    if buf:
        parts.append(''.join(buf).strip())
    return parts


def _parse_literal(s: str) -> Any:
    """Parse a Haskell-style literal: string, bool, number, list, or tuple.

    Args:
        s: Source text, e.g. '"AV"', 'False', '2.5', '[(1,1.6)]'.

    Returns:
        Python equivalent. Tuples of 2+ elements become lists; a 1-tuple
        unwraps to its single element.
    """
    s = s.strip()
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1]
    if s == 'True':
        return True
    if s == 'False':
        return False
    if s.startswith('[') and s.endswith(']'):
        inner = s[1:-1].strip()
        return [_parse_literal(p) for p in _split_top(inner)] if inner else []
    if s.startswith('(') and s.endswith(')'):
        inner = s[1:-1].strip()
        if not inner:
            return []
        parts = _split_top(inner)
        return _parse_literal(parts[0]) if len(parts) == 1 else [_parse_literal(p) for p in parts]
    try:
        return float(s) if ('.' in s or 'e' in s.lower()) else int(s)
    except ValueError:
        return s


def _const_value(text: str) -> Any:
    """Extract constValue from one 'Some (Constant {constType=.., constValue=..})' entry.

    Args:
        text: One value-list entry from the trace.

    Returns:
        The parsed constValue.

    Raises:
        ValueError: If text isn't a recognised Some/Constant wrapper, or has
            no constValue field.
    """
    text = text.strip()
    if not (text.startswith('Some') and '{' in text and text.endswith(')')):
        raise ValueError(f"unrecognised trace value: {text!r}")
    body = text[text.index('{') + 1: text.rindex('}')]
    for field in _split_top(body):
        key, _, val = field.partition('=')
        if key.strip() == 'constValue':
            return _parse_literal(val.strip())
    raise ValueError(f"no constValue field in: {text!r}")


def _parse_values_list(bracket_text: str) -> list[Any]:
    """Parse a '[Some (Constant {...}), ...]' value list into constValues.

    Args:
        bracket_text: Text including the surrounding '[' ']'.

    Returns:
        List of parsed constValues, in source order.
    """
    inner = bracket_text[1:-1].strip()
    return [_const_value(e) for e in _split_top(inner)] if inner else []


def _strip_either(text: str) -> str:
    """Remove Haskell 'Left'/'Right' wrappers, e.g. 'Left ("sts_001",...)' -> '("sts_001",...)'.

    Args:
        text: Source text. May have no wrapper.

    Returns:
        The text without the wrappers.
    """
    while text.startswith(('Left ', 'Right ')):
        text = text.split(' ', 1)[1].strip()
        if text.startswith('(') and text.endswith(')') and len(_split_top(text[1:-1])) == 1:
            text = text[1:-1].strip()
    return text


def _parse_input_event(event: str) -> tuple[str, str]:
    """Split a '?"<gate>"[...]' event into (gate, bracket_text).

    Args:
        event: One top-level trace event starting with '?"'.

    Returns:
        (gate, bracket_text)
    """
    end  = event.index('"', 2)
    gate = event[2:end]
    return gate, event[end + 1:].strip()


def _parse_output_event(event: str) -> dict:
    """Parse a '!("<gate>",Only,[values],((sts_id,<text> -> <loc>),{state}))' event.

    end_loc is the text after '->'. It is None if there is no '->'.

    Args:
        event: One top-level trace event starting with '!('.

    Returns:
        {"gate", "sts_id", "end_loc", "value", "values"}
    """
    parts        = _split_top(event[2:-1])
    gate         = _parse_literal(parts[0])
    values       = _parse_values_list(parts[2])
    value        = values[0] if values else None
    tail_parts   = _split_top(parts[3][1:-1])
    loc_pair     = _split_top(_strip_either(tail_parts[0])[1:-1])
    sts_id       = _parse_literal(loc_pair[0])
    _, arrow, loc = loc_pair[1].rpartition('->')
    end_loc      = _parse_literal(loc) if arrow else None
    return {"gate": gate, "sts_id": sts_id, "end_loc": end_loc, "value": value, "values": values}


def _guard_checks_value(guard_ids: list[str], guard_idx: dict, param_ids: list[str], value: Any) -> bool:
    """True if any of guard_ids' trees compares one of param_ids against value.

    Args:
        guard_ids: The switch's own 'guard' list (component IDs, ANDed).
        guard_idx: The STS's 'guards' map.
        param_ids: The output gate's declared parameter IDs.
        value: The trace's observed constValue for this output.

    Returns:
        True if an '=='/'in' leaf on one of param_ids matches value.
    """
    def walk(node: Any) -> bool:
        if not isinstance(node, dict):
            return False
        op = node.get('op')
        if op in ('&&', '||'):
            return walk(node.get('lhs')) or walk(node.get('rhs'))
        if op in ('==', 'in') and _var_path(node.get('lhs')) in param_ids:
            rhs = node.get('rhs')
            if op == '==':
                return _unwrap(rhs) == value
            return isinstance(rhs, list) and value in [_unwrap(v) for v in rhs]
        return False

    return any(walk(guard_idx.get(gid)) for gid in guard_ids)


def _resolve_output_switch(out: dict, spec: dict) -> str:
    """Find the switch corresponding to an output in a trace.

    Args:
        out: {"gate", "sts_id", "end_loc", "value"} from _parse_output_event.
        spec: The STS dict named by out['sts_id'].

    Returns:
        The matching switch_id.

    Raises:
        ValueError: If no switch, or more than one, matches.
    """
    switches   = spec.get('switches', {})
    guard_idx  = spec.get('guards', {})
    param_ids  = spec.get('outputGates', {}).get(out['gate'], {}).get('parameters', [])
    candidates = [swid for swid, sw in switches.items()
                  if sw.get('gate') == out['gate'] and sw.get('end_loc') == out['end_loc']]
    matches    = [swid for swid in candidates
                  if _guard_checks_value(switches[swid].get('guard', []), guard_idx, param_ids, out['value'])]

    if len(matches) == 1:
        return matches[0]
    if not matches and len(candidates) == 1:
        return candidates[0]
    raise ValueError(
        f"cannot resolve switch for gate={out['gate']!r} end_loc={out['end_loc']!r} "
        f"value={out['value']!r} in {spec.get('id')}: {len(matches)} guard match(es), "
        f"{len(candidates)} gate/location candidate(s)"
    )


def _interpret_struct(value: Any, param_type: dict | None) -> Any:
    """Convert a trace's positional struct-array element (e.g. (1, "hi")) into an attribute dict.

    Args:
        value: The raw parsed constValue for this parameter.
        param_type: The parameter's declared shape (spec['parameters'][pid]),
            or None if unknown.

    Returns:
        value unchanged, unless param_type is a structure-typed array, in
        which case each positional element becomes an attribute dict.
    """
    if not (isinstance(param_type, dict) and param_type.get('type') == 'array'):
        return value
    elements = param_type.get('elements') or {}
    if elements.get('type') != 'structure' or not isinstance(value, list):
        return value
    attrs = list(elements.get('attributes', {}).keys())
    return [dict(zip(attrs, elem)) if isinstance(elem, list) else elem for elem in value]


def parse_events(trace: str) -> list[str]:
    """Split a '[event, event, ...]' trace string into its top-level events.

    Args:
        trace: Full trace string, including the outer '[' ']'.

    Returns:
        List of event source strings, in trace order.
    """
    trace = trace.strip()
    inner = trace[1:-1].strip()
    return _split_top(inner) if inner else []


def parse_trace(trace: str, specs: list[dict]) -> dict | None:
    """Parse one flat-list execution trace into a TestCaseTranslator test case.

    Args:
        trace: Trace string.
        specs: STS dicts (one per scenario).

    Returns:
        dict: {"initial_values", "steps"}
    """
    trace = trace.strip()
    if trace == 'Nothing':
        return None
    if trace.startswith('Just '):
        trace = trace[len('Just '):].strip()

    input_gates: dict[str, dict] = {}
    param_types: dict[str, dict] = {}
    for spec in specs:
        input_gates.update(spec.get('inputGates', {}))
        param_types.update(spec.get('parameters', {}))
    specs_by_id = {spec['id']: spec for spec in specs}

    steps: list[dict] = []
    for event in parse_events(trace):
        if event.startswith('?"'):
            gate, bracket_text = _parse_input_event(event)
            if gate.startswith('check_'):
                continue
            values: dict[str, Any] = {}
            if bracket_text:
                param_ids  = input_gates.get(gate, {}).get('parameters', [])
                raw_values = _parse_values_list(bracket_text)
                values     = {
                    pid: _interpret_struct(val, param_types.get(pid))
                    for pid, val in zip(param_ids, raw_values)
                }
            steps.append({"gate": gate, "values": values})
        elif event.startswith('!('):
            out       = _parse_output_event(event)
            spec      = specs_by_id[out['sts_id']]
            switch_id = _resolve_output_switch(out, spec)
            param_ids = spec.get('outputGates', {}).get(out['gate'], {}).get('parameters', [])
            values    = {
                pid: _interpret_struct(val, param_types.get(pid))
                for pid, val in zip(param_ids, out['values'])
            }
            steps.append({"switch_id": switch_id, "sts_id": out['sts_id'], "values": values})
        else:
            raise ValueError(f"unrecognised trace event: {event!r}")

    initial_values = specs[0].get('initialValuation', {}) if specs else {}
    return {"initial_values": initial_values, "steps": steps}


def parse_traces_file(path: str, specs: list[dict]) -> list[dict]:
    """Parse every trace in a file (one trace per line) into test cases.

    Args:
        path: Text file with one trace per line.
        specs: STS dicts the traces' sts_ids reference.

    Returns:
        List of test case dicts.
    """
    with open(path) as f:
        lines = [line.strip() for line in f if line.strip()]
    test_cases = [parse_trace(line, specs) for line in lines]
    return [tc for tc in test_cases if tc is not None]
