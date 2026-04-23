"""
STS composition.
"""
import re


def _rewrite_guard_str(guard_str: str, old_to_new: dict[str, str]) -> str:
    """
    Replace guard IDs in a guard expression string using whole-token matching.
    Sorted by length (longest first) to prevent 'G1' matching inside 'G10'.

    Args:
        guard_str: Guard expression string containing old guard IDs.
        old_to_new: Mapping from old guard ID to unified guard ID.

    Returns:
        Guard expression with all old IDs replaced by their unified equivalents.
    """
    for old_id in sorted(old_to_new, key=len, reverse=True):
        guard_str = re.sub(r'\b' + re.escape(old_id) + r'\b',
                           old_to_new[old_id], guard_str)
    return guard_str


def _build_dedup_tables(sts_list: list[dict]) -> tuple:
    """
    Scan every STS and build global unification tables.

    Each switch (switch) has exactly one gate field referencing either an
    inputAction or an outputAction declaration. Both kinds share the same
    gate_map per STS (old gate id -> unified gate id) since their ID
    namespaces are disjoint (In* vs Out*).

    Args:
        sts_list: List of STS dicts to deduplicate across.

    Returns:
        A tuple of:
            unified_guards  : list of {"id", "expression"}
            unified_inputs  : list of {"id", "text", "parameters"}
            unified_outputs : list of {"id", "text", "parameters"}
            guard_maps      : list[dict]  old guard id -> unified id  (one per STS)
            gate_maps       : list[dict]  old gate id  -> unified id  (one per STS,
                              covers both input and output gates)
    """
    expr_to_gid  = {}   # expression     -> unified guard id
    insig_to_id  = {}   # (text, params) -> unified input-gate id
    outsig_to_id = {}   # (text, params) -> unified output-gate id

    unified_guards:  dict[str, dict] = {}
    unified_inputs:  dict[str, dict] = {}
    unified_outputs: dict[str, dict] = {}

    guard_maps = []
    gate_maps  = []

    g_ctr = in_ctr = out_ctr = 1

    for sts in sts_list:
        g_map    = {}
        gate_map = {}

        for old_gid, g in sts["guards"].items():
            expr = g["expression"]
            if expr not in expr_to_gid:
                new_id = f"G{g_ctr}"; g_ctr += 1
                expr_to_gid[expr] = new_id
                unified_guards[new_id] = {"expression": expr}
            g_map[old_gid] = expr_to_gid[expr]
        guard_maps.append(g_map)

        for old_id, ix in sts["inputActions"].items():
            sig = (ix["text"], tuple(ix["parameters"]))
            if sig not in insig_to_id:
                new_id = f"In{in_ctr}"; in_ctr += 1
                insig_to_id[sig] = new_id
                unified_inputs[new_id] = {"text": ix["text"], "parameters": ix["parameters"]}
            gate_map[old_id] = insig_to_id[sig]

        for old_id, ix in sts["outputActions"].items():
            sig = (ix["text"], tuple(ix["parameters"]))
            if sig not in outsig_to_id:
                new_id = f"Out{out_ctr}"; out_ctr += 1
                outsig_to_id[sig] = new_id
                unified_outputs[new_id] = {"text": ix["text"], "parameters": ix["parameters"]}
            gate_map[old_id] = outsig_to_id[sig]

        gate_maps.append(gate_map)

    return (unified_guards, unified_inputs, unified_outputs,
            guard_maps, gate_maps)


def _rewrite_switch(
    tr: dict,
    l0_old: str,
    l0_new: str,
    loc_rename: dict[str, str],
    g_map: dict[str, str],
    gate_map: dict[str, str],
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
        result["guard"] = _rewrite_guard_str(result["guard"], g_map)
    if gate_map and "gate" in result:
        result["gate"] = gate_map.get(result["gate"], result["gate"])

    return result


def _choice_compose(
    sts_entries: list[tuple[dict, dict, dict]],
    shared_l0: str,
) -> tuple[list[str], list[dict]]:
    """
    Merge a list of STSs into a single automaton with one shared initial location.

    Each STS's original initial location is remapped to shared_l0; all
    other locations are kept as-is.

    Args:
        sts_entries: List of (sts_dict, g_map, gate_map) with the per-STS
                     old-to-unified ID translations.
        shared_l0: Name to use as the single shared initial location.

    Returns:
        A locations, switches tuple with unified IDs throughout.
    """
    locations   = [shared_l0]
    switches = []

    for sts, g_map, gate_map in sts_entries:
        l0 = sts["locations"][0]

        for loc in sts["locations"]:
            if loc != l0:
                locations.append(loc)

        for tr in sts["switches"].values():
            switches.append(
                _rewrite_switch(tr, l0, shared_l0, {}, g_map, gate_map)
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

    1. Pair each STS with its per-STS guard/gate maps
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

    (unified_guards, unified_inputs, unified_outputs,
     guard_maps, gate_maps) = _build_dedup_tables(sts_list)

    # Pair each STS with its maps so subsets can be passed without index mismatch.
    all_entries     = list(zip(sts_list, guard_maps, gate_maps))
    initial_entries = [(s, gm, tm) for s, gm, tm in all_entries
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
                _rewrite_switch(tr, "L0_all", open_state, loc_rename, {}, {})
            )

    scenario_ids = ", ".join(s["id"] for s in sts_list)

    return {
        "id":                "sts_composed",
        "description":       f"Composition of scenarios {scenario_ids}",
        "initial_state":     True,
        "initial_location":  final_locs[0],
        "locationVariables": sts_list[0]["locationVariables"],
        "parameters":        sts_list[0]["parameters"],
        "attributes":        sts_list[0]["attributes"],
        "locations":         final_locs,
        "inputActions":      unified_inputs,
        "outputActions":     unified_outputs,
        "guards":            unified_guards,
        "switches":          {f"r_{idx + 1}": tr for idx, tr in enumerate(final_trs)},
    }
