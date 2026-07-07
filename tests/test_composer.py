"""
Tests for compose_stss and related utils.
"""
import pytest

from src.composer import _open_states, compose_stss


def _make_sts(
    sts_id: str,
    locations: list[str],
    switches: list[dict],
    *,
    initial: bool = False,
    input_gates: list[dict] | None = None,
    output_gates: list[dict] | None = None,
    guards: dict | None = None,
) -> dict:
    def _to_dict(items: list[dict]) -> dict:
        return {item["id"]: {k: v for k, v in item.items() if k != "id"}
                for item in items}

    return {
        "id":               sts_id,
        "description":      f"scenario {sts_id}",
        "initial_state":    initial,
        "gate_id_type":     "string",
        "location_id_type": "string",
        "locationVariables": [],
        "parameters":      [],
        "attributes":      [],
        "locations":       locations,
        "inputGates":      _to_dict(input_gates or []),
        "outputGates":     _to_dict(output_gates or []),
        "guards":          guards or {},
        "assignments":     {},
        "switches":        _to_dict(switches),
    }


def _simple_sts(prefix: str, initial: bool = False) -> dict:
    """A two-switch STS: L0 --In--> L1 --Out--> L2."""
    locs    = [f"L0_{prefix}", f"L1_{prefix}", f"L2_{prefix}"]
    inputs  = [{"id": f"In{prefix}_1", "text": "do action", "parameters": []}]
    outputs = [{"id": f"Out{prefix}_1", "text": "report result", "parameters": []}]
    guards  = {f"G1_{prefix}": True, f"G2_{prefix}": True}
    switches = [
        {
            "id":          "r_1",
            "init_loc":    locs[0],
            "gate":        inputs[0]["id"],
            "guard":       f"G1_{prefix}",
            "assignments": [],
            "end_loc":     locs[1],
        },
        {
            "id":          "r_2",
            "init_loc":    locs[1],
            "gate":        outputs[0]["id"],
            "guard":       f"G2_{prefix}",
            "assignments": [],
            "end_loc":     locs[2],
        },
    ]
    return _make_sts(
        f"sts_{prefix}", locs, switches,
        initial=initial,
        input_gates=inputs,
        output_gates=outputs,
        guards=guards,
    )


class TestOpenStates:
    def test_no_open_states_when_all_locs_have_outgoing(self) -> None:
        locs    = ["L0", "L1"]
        trans   = [
            {"init_loc": "L0", "end_loc": "L1"},
            {"init_loc": "L1", "end_loc": "L0"},
        ]
        assert _open_states(locs, trans) == []

    def test_sink_locations_are_open(self) -> None:
        locs  = ["L0", "L1", "L2"]
        trans = [{"init_loc": "L0", "end_loc": "L1"}]
        open_ = _open_states(locs, trans)
        assert set(open_) == {"L1", "L2"}

    def test_empty_switches_means_all_open(self) -> None:
        locs = ["A", "B"]
        assert set(_open_states(locs, [])) == {"A", "B"}


class TestComposeStss:
    def test_empty_list_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            compose_stss([])

    def test_no_initial_state_raises(self) -> None:
        sts = _simple_sts("x", initial=False)
        with pytest.raises(ValueError, match="no STS is marked as initial"):
            compose_stss([sts])

    def test_single_initial_sts_produces_composed(self) -> None:
        sts    = _simple_sts("a", initial=True)
        result = compose_stss([sts])
        assert result["id"] == "sts_composed"
        assert len(result["locations"]) >= 1

    def test_composed_switches_get_fresh_ids(self) -> None:
        sts    = _simple_sts("a", initial=True)
        result = compose_stss([sts])
        ids = list(result["switches"].keys())
        assert ids == [f"r_{i+1}" for i in range(len(ids))]

    def test_duplicate_guards_are_deduplicated(self) -> None:
        """Two STSs sharing a 'true' guard should produce one unified guard."""
        sts1 = _simple_sts("a", initial=True)
        sts2 = _simple_sts("b", initial=False)
        result = compose_stss([sts1, sts2])
        guard_values = list(result["guards"].values())
        # 'true' appears twice across the two STSs but only once in composed output
        assert guard_values.count(True) == 1

    def test_duplicate_input_gates_are_deduplicated(self) -> None:
        """Two STSs with identical inputGate text+params share one entry."""
        sts1 = _simple_sts("a", initial=True)
        sts2 = _simple_sts("b", initial=False)
        # Both use "do action" with no params -> should collapse to one entry
        result   = compose_stss([sts1, sts2])
        in_texts = [ia["text"] for ia in result["inputGates"].values()]
        assert in_texts.count("do action") == 1
