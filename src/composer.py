"""
STS composition.
"""
import json


def _build_dedup_tables(sts_list: list[dict]) -> tuple:
    """
    Scan every STS and build global unification tables.

    Each switch (switch) has exactly one gate field referencing either an
    inputGate or an outputGate declaration, and exactly one guard field
    referencing a single guard ID. Both kinds of gate share the same
    gate_map per STS (old gate id -> unified gate id) since their ID
    namespaces are disjoint (In* vs Out*).

    Args:
        sts_list: List of STS dicts to deduplicate across.

    Returns:
        A tuple of:
            unified_guards      : dict  unified guard id -> guard tree/leaf
            unified_inputs       : dict  unified input gate id -> gate dict
            unified_outputs      : dict  unified output gate id -> gate dict
            unified_assignments  : dict  unified assignment id -> assignment dict
            guard_maps           : list[dict]  old guard id -> unified id  (one per STS)
            gate_maps            : list[dict]  old gate id  -> unified id  (one per STS,
                                    covers both input and output gates)
            assignment_maps      : list[dict]  old assignment id -> unified id (one per STS)
    """
    tree_to_gid  = {}   # json(tree)     -> unified guard id
    insig_to_id  = {}   # (text, params) -> unified input-gate id
    outsig_to_id = {}   # (text, params) -> unified output-gate id
    asig_to_id   = {}   # (target, expression) -> unified assignment id

    unified_guards:      dict[str, object] = {}
    unified_inputs:      dict[str, dict]   = {}
    unified_outputs:     dict[str, dict]   = {}
    unified_assignments: dict[str, dict]   = {}

    guard_maps      = []
    gate_maps       = []
    assignment_maps = []

    g_ctr = in_ctr = out_ctr = a_ctr = 1

    for sts in sts_list:
        g_map    = {}
        gate_map = {}
        a_map    = {}

        for old_gid, tree in sts["guards"].items():
            key = json.dumps(tree, sort_keys=True)
            if key not in tree_to_gid:
                new_id = f"G{g_ctr}"; g_ctr += 1
                tree_to_gid[key] = new_id
                unified_guards[new_id] = tree
            g_map[old_gid] = tree_to_gid[key]
        guard_maps.append(g_map)

        for old_id, ix in sts["inputGates"].items():
            sig = (ix["text"], tuple(ix["parameters"]))
            if sig not in insig_to_id:
                new_id = f"In{in_ctr}"; in_ctr += 1
                insig_to_id[sig] = new_id
                unified_inputs[new_id] = {"text": ix["text"], "parameters": ix["parameters"]}
            gate_map[old_id] = insig_to_id[sig]

        for old_id, ix in sts["outputGates"].items():
            sig = (ix["text"], tuple(ix["parameters"]))
            if sig not in outsig_to_id:
                new_id = f"Out{out_ctr}"; out_ctr += 1
                outsig_to_id[sig] = new_id
                unified_outputs[new_id] = {"text": ix["text"], "parameters": ix["parameters"]}
            gate_map[old_id] = outsig_to_id[sig]
        gate_maps.append(gate_map)

        for old_id, a in sts["assignments"].items():
            sig = (a["target"], json.dumps(a["expression"], sort_keys=True))
            if sig not in asig_to_id:
                new_id = f"A{a_ctr}"; a_ctr += 1
                asig_to_id[sig] = new_id
                unified_assignments[new_id] = {"target": a["target"], "expression": a["expression"]}
            a_map[old_id] = asig_to_id[sig]
        assignment_maps.append(a_map)

    return (unified_guards, unified_inputs, unified_outputs, unified_assignments,
            guard_maps, gate_maps, assignment_maps)


def _rewrite_switch(
    tr: dict,
    l0_old: str,
    l0_new: str,
    loc_rename: dict[str, str],
    g_map: dict[str, str],
    gate_map: dict[str, str],
    a_map: dict[str, str],
) -> dict:
    """
    Return a copy of switch dict tr with all IDs and locations rewritten.

    Args:
        tr: Original switch dict.
        l0_old: The STS's original initial location name.
        l0_new: The shared initial location it should be remapped to.
        loc_rename: Mapping for any non-initial old location name -> new name.
        g_map: Old guard ID -> unified guard ID. Pass {} if already unified.
        gate_map: Old gate ID -> unified gate ID (covers both input and output).
        a_map: Old assignment ID -> unified assignment ID. Pass {} if already unified.

    Returns:
        New switch dict with all IDs replaced.
    """
    def remap(loc: str) -> str:
        if loc == l0_old:
            return l0_new
        return loc_rename.get(loc, loc)

    result = dict(tr)
    result["init_loc"] = remap(result["init_loc"])
    result["end_loc"]  = remap(result["end_loc"])

    if g_map:
        result["guard"] = g_map.get(result["guard"], result["guard"])
    if gate_map and "gate" in result:
        result["gate"] = gate_map.get(result["gate"], result["gate"])
    if a_map:
        result["assignments"] = [a_map.get(aid, aid) for aid in result["assignments"]]

    return result


def _choice_compose(
    sts_entries: list[tuple[dict, dict, dict, dict]],
    shared_l0: str,
) -> tuple[list[str], list[dict]]:
    """
    Merge a list of STSs into a single automaton with one shared initial location.

    Each STS's original initial location is remapped to shared_l0; all
    other locations are kept as-is.

    Args:
        sts_entries: List of (sts_dict, g_map, gate_map, a_map) with the per-STS
                     old-to-unified ID translations.
        shared_l0: Name to use as the single shared initial location.

    Returns:
        A locations, switches tuple with unified IDs throughout.
    """
    locations   = [shared_l0]
    switches = []

    for sts, g_map, gate_map, a_map in sts_entries:
        l0 = sts["locations"][0]

        for loc in sts["locations"]:
            if loc != l0:
                locations.append(loc)

        for tr in sts["switches"].values():
            switches.append(
                _rewrite_switch(tr, l0, shared_l0, {}, g_map, gate_map, a_map)
            )

    return locations, switches


def _open_states(locations: list[str], switches: list[dict]) -> list[str]:
    """Return locations that have no outgoing switches.

    Args:
        locations: All location names in the automaton.
        switches: All switch dicts (each has an init_loc key).

    Returns:
        Subset of locations with no outgoing switch.
    """
    origins = {tr["init_loc"] for tr in switches}
    return [loc for loc in locations if loc not in origins]


def compose_stss(sts_list: list[dict]) -> dict:
    """
    Compose a list of partial STS dicts into a single composed STS dict.

    1. Pair each STS with its per-STS guard/gate/assignment maps
    2. Choice-compose the initial STSs only  -> C_init
    3. Choice-compose all STSs               -> C_all
    4. Sequentially compose C_init > C_all > C_all

    NOTE: Guards are kept as-is, therefore the resulting system may be
    non-deterministic.

    Args:
        sts_list: Non-empty list of STS dicts, at least one marked as initial.

    Returns:
        dict: A single composed STS dict.

    Raises:
        ValueError: If sts_list is empty or contains no initial STS.
    """
    if not sts_list:
        raise ValueError("compose_stss requires at least one STS")

    (unified_guards, unified_inputs, unified_outputs, unified_assignments,
     guard_maps, gate_maps, assignment_maps) = _build_dedup_tables(sts_list)

    # Pair each STS with its maps so subsets can be passed without index mismatch.
    all_entries     = list(zip(sts_list, guard_maps, gate_maps, assignment_maps))
    initial_entries = [(s, gm, tm, am) for s, gm, tm, am in all_entries
                       if s.get("initial_state")]

    if not initial_entries:
        raise ValueError("compose_stss: no STS is marked as initial.")

    c_init_locs, c_init_trs = _choice_compose(initial_entries, "L0_comp")
    c_all_locs,  c_all_trs  = _choice_compose(all_entries,     "L0_all")

    c_all_open  = set(_open_states(c_all_locs, c_all_trs))
    open_states = _open_states(c_init_locs, c_init_trs)
    final_locs  = list(c_init_locs)
    final_trs   = list(c_init_trs)

    for open_state in open_states:
        loc_rename = {}
        for loc in c_all_locs:
            if loc == "L0_all":
                continue
            if loc in c_all_open:
                loc_rename[loc] = open_state
            else:
                new_name = f"{open_state}_{loc}"
                loc_rename[loc] = new_name
                final_locs.append(new_name)

        for tr in c_all_trs:
            final_trs.append(
                _rewrite_switch(tr, "L0_all", open_state, loc_rename, {}, {}, {})
            )

    scenario_ids = ", ".join(s["id"] for s in sts_list)

    return {
        "id":                "sts_composed",
        "description":       f"Composition of scenarios {scenario_ids}",
        "initial_location":  final_locs[0],
        "gate_id_type":      sts_list[0]["gate_id_type"],
        "location_id_type":  sts_list[0]["location_id_type"],
        "locationVariables": sts_list[0]["locationVariables"],
        "parameters":        sts_list[0]["parameters"],
        "attributes":        sts_list[0]["attributes"],
        "locations":         final_locs,
        "inputGates":        unified_inputs,
        "outputGates":       unified_outputs,
        "guards":            unified_guards,
        "assignments":       unified_assignments,
        "switches":          {f"r_{idx + 1}": tr for idx, tr in enumerate(final_trs)},
    }
