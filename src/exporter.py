"""
Visualization exporters for STS dicts.
"""

import json
import re
from html import escape as _he
from pathlib import Path
from typing import Any

from src.transformer import render_guard_expr

_GATE_RE = re.compile(r'^([?!])"([^"]+)"\s*\[(.*)\]$')
_LOC_OUTER_RE   = re.compile(r'^\("([^"]+)",(.*)\)$')
_LOC_PLAIN_RE   = re.compile(r'^"([^"]*)"$')
_LOC_PENDING_RE = re.compile(r'^pending\s+([?!]"[^"]+"\s*\[.*\])\s*->\s*"([^"]+)"$')

_COMPONENT_PALETTE = [
    "#e67e22", "#3498db", "#9b59b6", "#2ecc71",
    "#e74c3c", "#1abc9c", "#f1c40f", "#7f8c8d",
    "#6df50c", "#00a6ff", "#ffe786", "#4b6365",
    "#e74c3c", "#77C100", "#4e4317", "#222f67",
    "#ffb4e1", "#365112", "#ac711e", "#403791",
    "#b340a9", "#005d4a", "#895400", "#950ea7",
]

def _dot_attr_escape(s: str) -> str:
    """Escape a string for use as a DOT double-quoted attribute value.

    Args:
        s: Raw string to escape.

    Returns:
        Escaped string safe for placement inside DOT double quotes.
    """
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _dot_html_escape(s: str) -> str:
    """Escape a string for use inside a DOT HTML-label cell.

    Args:
        s: Raw string to escape.

    Returns:
        HTML-entity-escaped string safe for DOT HTML labels.
    """
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
    )


def _render_initial_value(value: Any) -> str:
    """Render one initialValuation entry as a display string.

    Args:
        value: A bare scalar (str, number, bool) or a guard-expr leaf/tree
            (dict or list), as used in the STS "initialValuation" map.

    Returns:
        Flat display string for the value.
    """
    if isinstance(value, (dict, list)):
        return render_guard_expr(value)
    return str(value)


def _split_top_level(s: str, sep: str = ",") -> list[str]:
    """Split a string on a separator, skipping separators nested in [] or ().

    Args:
        s: String to split.
        sep: Separator character.

    Returns:
        List of trimmed top-level chunks.
    """
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in s:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    parts.append("".join(current).strip())
    return parts


def _parse_location(loc: str) -> tuple[str, str]:
    """Parse a "(sts_id, loc_id)" location string into its two parts.

    The string may be wrapped in Left/Right. Pending markers are
    simplified to "pending <gate name> -> <target loc>".

    Args:
        loc (str): Location string, e.g. 'Left ("sts_001","L0_1")'.

    Returns:
        Tuple of (sts_id, loc_id).
    """
    if "Right" in loc:
        loc = loc.replace("Right ", "")
    if "Left" in loc:
        loc = loc.replace("Left ", "")    

    outer = _LOC_OUTER_RE.match(loc)
    sts_id, rest = outer.group(1), outer.group(2)
    plain = _LOC_PLAIN_RE.match(rest)
    if plain:
        return sts_id, plain.group(1)
    pending = _LOC_PENDING_RE.match(rest)
    if pending:
        _, gate_name, _ = _parse_gate(pending.group(1).strip())
        return sts_id, f"pending {gate_name} -> {pending.group(2)}"
    return sts_id, rest


def _pretty_location(loc: str) -> str:
    """Render a "(sts_id, loc_id)" location string as one readable line.

    Args:
        loc: Location string, e.g. '("sts_001","L0_1")'.

    Returns:
        Display string, e.g. "sts_001 / L0_1".
    """
    sts_id, loc_id = _parse_location(loc)
    return f"{sts_id} / {loc_id}"


def _component_colors(sts_ids: set[str]) -> dict[str, str]:
    """Assign a stable color to each unique sts_id, cycling the palette.

    Args:
        sts_ids: Unique sts_ids to assign colors to.

    Returns:
        Dict mapping sts_id to a hex color string.
    """
    return {
        sid: _COMPONENT_PALETTE[i % len(_COMPONENT_PALETTE)]
        for i, sid in enumerate(sorted(sts_ids))
    }


def _iter_switches(switches: list) -> Any:
    """Iterate the switches list, expanding list-valued (fan-out) slots.

    Each slot is either {} (no switch, skipped), a single switch dict, or
    a list of alternative switch dicts sharing the same slot.

    Args:
        switches: The STS "switches" list.

    Yields:
        (switch_id, switch_dict) pairs with a synthesized unique switch_id.
    """
    for i, slot in enumerate(switches):
        if not slot:
            continue
        if isinstance(slot, list):
            for j, sw in enumerate(slot):
                yield f"sw_{i}_{j}", sw
        else:
            yield f"sw_{i}", slot


def _parse_gate(gate_str: str) -> tuple[str, str, list[str]]:
    """Parse an inline gate string into direction, name, and parameters.

    Args:
        gate_str: Inline gate declaration, e.g. '?"In1" [foo_p:Int]'
            (input, leading "?") or '!"Out1" []' (output, leading "!").

    Returns:
        Tuple of ("input" or "output", gate name, list of "name:type"
        parameter strings; empty list if no parameters).
    """
    m = _GATE_RE.match(gate_str)
    direction = "input" if m.group(1) == "?" else "output"
    params = _split_top_level(m.group(3)) if m.group(3).strip() else []
    return direction, m.group(2), params

class STSExporter:
    """Generates DOT and HTML visualizations of a single STS dict.

    Args:
        sts: A single STS dict. Must contain at minimum the keys id,
            initial_location (list of location IDs), initialValuation,
            locations, and switches.
        originals: Optional list of pre-composition STS dicts (one per
            scenario, each with its own guards/inputGates/outputGates/
            assignments), keyed internally by their own "id".
    """

    def __init__(self, sts: dict[str, Any], originals: list[dict[str, Any]] | None = None) -> None:
        self._sts          = sts
        self._initial_locs: list[str] = sts["initial_location"]
        self._initial_valuation: dict[str, Any] = sts["initialValuation"]
        self._originals_by_id: dict[str, dict[str, Any]] = {o["id"]: o for o in originals or []}
        self._open_states: set[str]  = self._compute_open_states()
        self._gate_index:  dict[str, dict[str, Any]] = self._build_gate_index()
        self._guard_index: dict[str, str] = self._build_guard_index()
        self._assignment_index: dict[str, str] = self._build_assignment_index()
        with open(Path(__file__).parent.parent / "resources" / "sts_template.html", encoding="utf-8") as f:
          self.html_template = f.read()

    def _compute_open_states(self) -> set[str]:
        """Return the set of locations that have no outgoing switches.

        Returns:
            Set of location names with no outgoing switch.
        """
        origins = {sw["init_loc"] for _, sw in _iter_switches(self._sts["switches"])}
        return {loc for loc in self._sts["locations"] if loc not in origins}

    def _build_gate_index(self) -> dict[str, dict[str, Any]]:
        """Build a mapping from gate name to its declaration and direction.

        Returns:
            Dict mapping each gate name to a dict with keys
            text, parameters, and direction ("input" or
            "output").
        """
        index: dict[str, dict[str, Any]] = {}
        for _, sw in _iter_switches(self._sts["switches"]):
            direction, name, params = _parse_gate(sw["gate"])
            if name in index:
                continue
            own_sts_id, _ = _parse_location(sw["init_loc"])
            text = self._lookup_gate_text(own_sts_id, direction, name) or name
            index[name] = {"text": text, "parameters": params, "direction": direction}
        return index

    def _lookup_gate_text(self, own_sts_id: str, direction: str, name: str) -> str | None:
        """Look up a gate's natural-language text in the original STSs.

        Args:
            own_sts_id: sts_id of the switch's own scenario, tried first.
            direction: "input" or "output", selects the registry to check.
            name: Gate name to look up.

        Returns:
            The gate's "text", or None if not found in any original.
        """
        registry_key = "inputGates" if direction == "input" else "outputGates"
        candidates = [self._originals_by_id[own_sts_id]] if own_sts_id in self._originals_by_id else []
        candidates += list(self._originals_by_id.values())
        for original in candidates:
            gate = original.get(registry_key, {}).get(name)
            if gate is not None:
                return gate.get("text")
        return None

    def _lookup_registry_item(self, own_sts_id: str, registry_key: str, item_id: str) -> Any | None:
        """Look up an item by ID in the original STSs' registries.

        Args:
            own_sts_id: sts_id of the switch's own scenario, tried first.
            registry_key: "guards" or "assignments".
            item_id: ID to look up within that registry.

        Returns:
            The registry entry, or None if not found in any original.
        """
        candidates = [self._originals_by_id[own_sts_id]] if own_sts_id in self._originals_by_id else []
        candidates += list(self._originals_by_id.values())
        for original in candidates:
            item = original.get(registry_key, {}).get(item_id)
            if item is not None:
                return item
        return None

    def _build_guard_index(self) -> dict[str, str]:
        """Resolve guard IDs referenced by switches into rendered strings.

        Returns:
            Dict mapping each resolved guard ID to its rendered expression
            string. IDs not found in any original are omitted.
        """
        index: dict[str, str] = {}
        if not self._originals_by_id:
            return index
        for _, sw in _iter_switches(self._sts["switches"]):
            own_sts_id, _ = _parse_location(sw["init_loc"])
            for gid in sw.get("guard", []):
                if gid in index:
                    continue
                tree = self._lookup_registry_item(own_sts_id, "guards", gid)
                if tree is not None:
                    index[gid] = render_guard_expr(tree)
        return index

    def _build_assignment_index(self) -> dict[str, str]:
        """Resolve assignment IDs referenced by switches into "target := expr" strings.

        Returns:
            Dict mapping each resolved assignment ID to its display string.
            IDs not found in any original are omitted.
        """
        index: dict[str, str] = {}
        if not self._originals_by_id:
            return index
        for _, sw in _iter_switches(self._sts["switches"]):
            aid_list = sw.get("assignments")
            for aid in aid_list:
                if not aid or aid in index:
                    continue
                own_sts_id, _ = _parse_location(sw["init_loc"])
                assignment = self._lookup_registry_item(own_sts_id, "assignments", aid)
                if assignment is not None:
                    index[aid] = f'{assignment["target"]} := {render_guard_expr(assignment["expression"])}'
        return index

    def to_dot(self) -> str:
        """Render the STS as a Graphviz DOT string.

        Each switch is labeled with its gate ID only. A cluster_legend
        subgraph lists the full gate declarations and guard expressions.

        Returns:
            A DOT-format string ready to pass to dot -Tpdf or similar.
        """
        sts   = self._sts
        lines = [f'digraph "{_dot_attr_escape(sts["id"])}" {{']
        lines += [
            "    rankdir=LR;",
            "    node [fontname=monospace fontsize=11];",
            "    edge [fontname=monospace fontsize=10];",
            "",
        ]

        # Invisible entry arrow(s) into the initial state(s)
        lines.append('    __start__ [shape=point width=0.15];')
        for loc in self._initial_locs:
            lines.append(f'    __start__ -> "{_dot_attr_escape(loc)}";')
        lines.append("")

        # Nodes
        
        for loc in sts["locations"]:
            node_style = f'shape=doublecircle style=filled fillcolor="#ffffff" color="{_component_colors(loc)}"'
            lines.append(f'    "{_dot_attr_escape(loc)}" [label="{_dot_attr_escape(_pretty_location(loc))}" {node_style}];')
        lines.append("")

        # Transitions
        for sw_id, sw in _iter_switches(sts["switches"]):
            _, gate_name, _ = _parse_gate(sw["gate"])
            guard_str = " && ".join(sw.get("guard", []))
            tooltip = _dot_attr_escape(f'{sw_id}: {gate_name} [{guard_str}]')
            lines.append(
                f'    "{_dot_attr_escape(sw["init_loc"])}" -> "{_dot_attr_escape(sw["end_loc"])}"'
                f' [label="{tooltip}" id="{sw_id}" tooltip="{tooltip}"];'
            )
        lines.append("")

        # Legend cluster
        lines += [
            "    subgraph cluster_legend {",
            '        label="Legend"; style=filled; fillcolor="#f5f5f5"; color="#aaaaaa";',
        ]
        rows = [
            '<TR><TD COLSPAN="2" BGCOLOR="#dddddd"><B>Gates</B></TD></TR>',
        ]
        for gid, gdata in self._gate_index.items():
            direction = "in" if gdata["direction"] == "input" else "out"
            params    = ", ".join(gdata["parameters"]) or "\u2014"
            text_cell = _dot_html_escape(f'{gdata["text"]} [{params}]')
            rows.append(
                f'<TR><TD ALIGN="LEFT"><B>{gid}</B> ({direction})</TD>'
                f'<TD ALIGN="LEFT">{text_cell}</TD></TR>'
            )
        table = (
            '<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="3">\n'
            + "".join(f"        {r}\n" for r in rows)
            + "        </TABLE>"
        )
        lines.append(f"        legend [shape=none label=<{table}>];")

        valuation_rows = [
            '<TR><TD COLSPAN="2" BGCOLOR="#dddddd"><B>Initial Valuation</B></TD></TR>',
        ]
        for var, value in self._initial_valuation.items():
            valuation_rows.append(
                f'<TR><TD ALIGN="LEFT"><B>{_dot_html_escape(var)}</B></TD>'
                f'<TD ALIGN="LEFT">{_dot_html_escape(_render_initial_value(value))}</TD></TR>'
            )
        valuation_table = (
            '<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="3">\n'
            + "".join(f"        {r}\n" for r in valuation_rows)
            + "        </TABLE>"
        )
        lines.append(f"        initial_valuation [shape=none label=<{valuation_table}>];")
        lines += ["    }", "}"]

        return "\n".join(lines)

    def to_html(self) -> str:
        """Render the STS as a self-contained interactive HTML page.

        Returns:
            A self-contained HTML string that can be opened in any browser.
        """
        sts = self._sts
        component_colors = _component_colors({_parse_location(loc)[0] for loc in sts["locations"]})
        elements: list[dict[str, Any]] = []
        for loc in sts["locations"]:
            sts_id, _ = _parse_location(loc)
            elements.append({
                "group": "nodes",
                "data":  {
                    "id":             loc,
                    "label":          _pretty_location(loc),
                    "stsId":          sts_id,
                    "componentColor": component_colors[sts_id],
                },
            })
        for sw_id, sw in _iter_switches(sts["switches"]):
            _, gate_name, _ = _parse_gate(sw["gate"])
            assignment_list = sw.get("assignments")
            elements.append({
                "group": "edges",
                "data":  {
                    "id":          sw_id,
                    "source":      sw["init_loc"],
                    "target":      sw["end_loc"],
                    "label":       sw_id,
                    "switchId":    sw_id,
                    "gateId":      gate_name,
                    "guard":       sw.get("guard", []),
                    "assignments": [self._assignment_index.get(aid, aid) for aid in assignment_list] if assignment_list else [],
                },
            })

        # Legend HTML fragments
        gate_rows: list[str] = []
        for gid, gdata in self._gate_index.items():
            dir_class = "dir-in" if gdata["direction"] == "input" else "dir-out"
            dir_arrow = "\u2193" if gdata["direction"] == "input" else "\u2191"
            params    = ", ".join(gdata["parameters"]) or "\u2014"
            gate_rows.append(
                f'<div class="legend-entry">'
                f'<span class="legend-id">{_he(gid)}</span>'
                f'<span class="legend-val">'
                f'<span class="{dir_class}">{dir_arrow}</span> '
                f'{_he(gdata["text"])} [{_he(params)}]'
                f'</span></div>'
            )

        guard_rows: list[str] = []
        for gid, rendered in self._guard_index.items():
            guard_rows.append(
                f'<div class="legend-entry">'
                f'<span class="legend-id">{_he(gid)}</span>'
                f'<span class="legend-val">{_he(rendered)}</span>'
                f'</div>'
            )

        component_rows = [
            f'<div class="node-key">'
            f'<span class="ndot" style="background:{color};border-color:{color}"></span> {_he(sid)}'
            f'</div>'
            for sid, color in sorted(component_colors.items())
        ]

        valuation_rows: list[str] = []
        for var, value in self._initial_valuation.items():
            valuation_rows.append(
                f'<div class="legend-entry">'
                f'<span class="legend-id">{_he(var)}</span>'
                f'<span class="legend-val">{_he(_render_initial_value(value))}</span>'
                f'</div>'
            )

        title = sts["id"]

        return (
            self.html_template
            .replace("<<<TITLE_ESC>>>",    _he(title))
            .replace("<<<ELEMENTS>>>",     json.dumps(elements))
            .replace("<<<GATES_DATA>>>",   json.dumps(self._gate_index))
            .replace("<<<GUARDS_DATA>>>",  json.dumps(self._guard_index))
            .replace("<<<INITIAL_NODES>>>", json.dumps(self._initial_locs))
            .replace("<<<FILENAME>>>",     json.dumps(sts["id"]))
            .replace("<<<LEGEND_GATES>>>", "\n".join(gate_rows))
            .replace("<<<LEGEND_GUARDS>>>","\n".join(guard_rows))
            .replace("<<<LEGEND_INITIAL_VALUATION>>>", "\n".join(valuation_rows))
            .replace("<<<LEGEND_COMPONENTS>>>", "\n".join(component_rows))
        )


    def write_dot(self, path: str | Path) -> None:
        """Write the DOT rendering to a file.

        Args:
            path: Destination file path. Created or overwritten.
        """
        Path(path).write_text(self.to_dot(), encoding="utf-8")

    def write_html(self, path: str | Path) -> None:
        """Write the interactive HTML rendering to a file.

        Args:
            path: Destination file path. Created or overwritten.
        """
        Path(path).write_text(self.to_html(), encoding="utf-8")
