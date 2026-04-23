"""
Visualization exporters for STS dicts.
"""

import json
from html import escape as _he
from pathlib import Path
from typing import Any

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

class STSExporter:
    """Generates DOT and HTML visualizations of a single STS dict.

    Transitions are labeled with their gate ID only to keep the graph
    readable. Full semantics (gate text, guard expressions, assignments)
    are available in the DOT legend cluster and the HTML side panel.

    Args:
        sts: An STS dict as produced by _scenario_to_sts or
            compose_stss. Must contain at minimum the keys
            id, description, locations, switches,
            guards, inputActions, and outputActions.
    """

    def __init__(self, sts: dict[str, Any]) -> None:
        self._sts          = sts
        self._initial_loc: str       = sts["initial_location"]
        self._open_states: set[str]  = self._compute_open_states()
        self._gate_index:  dict[str, dict[str, Any]] = self._build_gate_index()
        self._guard_index: dict[str, str] = {
            gid: g["expression"] for gid, g in sts["guards"].items()
        }
        with open(Path(__file__).parent.parent / "resources" / "sts_template.html", encoding="utf-8") as f:
          self.html_template = f.read()

    def _compute_open_states(self) -> set[str]:
        """Return the set of locations that have no outgoing switches.

        Returns:
            Set of location names with no outgoing switch.
        """
        origins = {sw["init_loc"] for sw in self._sts["switches"].values()}
        return {loc for loc in self._sts["locations"] if loc not in origins}

    def _build_gate_index(self) -> dict[str, dict[str, Any]]:
        """Build a mapping from gate ID to its declaration and direction.

        Returns:
            Dict mapping each gate ID to a dict with keys
            text, parameters, and direction ("input" or
            "output").
        """
        index: dict[str, dict[str, Any]] = {}
        for gid, ix in self._sts["inputActions"].items():
            index[gid] = {
                "text":       ix["text"],
                "parameters": ix["parameters"],
                "direction":  "input",
            }
        for gid, ix in self._sts["outputActions"].items():
            index[gid] = {
                "text":       ix["text"],
                "parameters": ix["parameters"],
                "direction":  "output",
            }
        return index

    def _node_type(self, loc: str) -> str:
        """Classify a location as "initial", "open", or "normal".

        Args:
            loc: Location name to classify.

        Returns:
            One of "initial", "open", or "normal".
        """
        if loc == self._initial_loc:
            return "initial"
        if loc in self._open_states:
            return "open"
        return "normal"

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

        # Invisible entry arrow into the initial state
        lines += [
            '    __start__ [shape=point width=0.15];',
            f'    __start__ -> "{self._initial_loc}";',
            "",
        ]

        # Nodes
        _NODE_STYLES = {
            "initial": 'shape=doublecircle style=filled fillcolor="#aaddaa" color="#226622"',
            "open":    'shape=circle style="filled,dashed" fillcolor="#ffeecc" color="#cc8800"',
            "normal":  'shape=circle style=filled fillcolor="#ddeeff" color="#4488bb"',
        }
        for loc in sts["locations"]:
            attrs = _NODE_STYLES[self._node_type(loc)]
            lines.append(f'    "{loc}" [label="{_dot_attr_escape(loc)}" {attrs}];')
        lines.append("")

        # Transitions
        for sw_id, sw in sts["switches"].items():
            tooltip = _dot_attr_escape(f'{sw_id}: {sw["gate"]} [{sw["guard"]}]')
            lines.append(
                f'    "{sw["init_loc"]}" -> "{sw["end_loc"]}"'
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
        lines += ["    }", "}"]

        return "\n".join(lines)

    def to_html(self) -> str:
        """Render the STS as a self-contained interactive HTML page.

        Returns:
            A self-contained HTML string that can be opened in any browser.
        """
        sts = self._sts

        # cytoscape element list
        elements: list[dict[str, Any]] = []
        for loc in sts["locations"]:
            elements.append({
                "group": "nodes",
                "data":  {"id": loc, "label": loc, "type": self._node_type(loc)},
            })
        for sw_id, sw in sts["switches"].items():
            elements.append({
                "group": "edges",
                "data":  {
                    "id":         sw_id,
                    "source":     sw["init_loc"],
                    "target":     sw["end_loc"],
                    "label":      sw_id,
                    "switchId":   sw_id,
                    "gateId":     sw["gate"],
                    "guard":      sw["guard"],
                    "assignment": sw.get("assignment", []),
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
        for gid, expr in self._guard_index.items():
            guard_rows.append(
                f'<div class="legend-entry">'
                f'<span class="legend-id">{_he(gid)}</span>'
                f'<span class="legend-val">{_he(expr)}</span>'
                f'</div>'
            )

        title = f'{sts["id"]}: {sts["description"]}'

        return (
            self.html_template
            .replace("<<<TITLE_ESC>>>",    _he(title))
            .replace("<<<ELEMENTS>>>",     json.dumps(elements))
            .replace("<<<GATES_DATA>>>",   json.dumps(self._gate_index))
            .replace("<<<GUARDS_DATA>>>",  json.dumps(self._guard_index))
            .replace("<<<INITIAL_NODE>>>", json.dumps(self._initial_loc))
            .replace("<<<FILENAME>>>",     json.dumps(sts["id"]))
            .replace("<<<LEGEND_GATES>>>", "\n".join(gate_rows))
            .replace("<<<LEGEND_GUARDS>>>","\n".join(guard_rows))
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
