"""
Tests that build small dummy JSON examples and validate them against the
three project schemas: sts_partial, sts_composed, and test_cases.

Each *valid* test must not raise; each *invalid* test must raise
jsonschema.ValidationError.
"""
import copy
import json
from pathlib import Path

import pytest
from jsonschema import validate, ValidationError

# ---------------------------------------------------------------------------
# Schema loading
# ---------------------------------------------------------------------------

_SCHEMAS_DIR = Path(__file__).parent.parent / "schemas"


def _load(name: str) -> dict:
    return json.loads((_SCHEMAS_DIR / name).read_text())


PARTIAL_SCHEMA    = _load("sts_partial.schema.json")
COMPOSED_SCHEMA   = _load("sts_composed.schema.json")
TEST_CASES_SCHEMA = _load("test_cases.schema.json")


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _vardef(type_: str = "string", range_: list | None = None) -> dict:
    return {"type": type_, "range": range_ or ["AV", "NOT AV"]}


def _partial_sts(n: int = 1, *, initial_state: bool = True) -> dict:
    """Minimal valid partial STS for scenario number *n*."""
    return {
        "id":               f"sts_{n:03d}",
        "description":      f"{n:02d}: simple scenario",
        "initial_state":    initial_state,
        "initial_location": f"L0_{n}",
        "gate_id_type":     "string",
        "location_id_type": "string",
        "locationVariables": {
            "status": _vardef("string", ["OK", "FAIL"]),
        },
        "parameters": {
            "status_p": _vardef("string", ["OK", "FAIL"]),
        },
        "attributes": {},
        "locations":   [f"L0_{n}", f"L1_{n}", f"L2_{n}"],
        "inputGates": {
            f"In{n}_1": {"text": "set status", "parameters": ["status_p"]},
        },
        "outputGates": {
            f"Out{n}_1": {"text": "report status", "parameters": ["status_p"]},
        },
        "guards": {
            "G1": {"lhs": "status", "op": "==", "rhs": "OK"},
        },
        "assignments": {
            "A1": {"target": "status", "expression": "status_p"},
        },
        "switches": {
            "r_1": {
                "init_loc":    f"L0_{n}",
                "gate":        f"In{n}_1",
                "guard":       "G1",
                "assignments": ["A1"],
                "end_loc":     f"L1_{n}",
            },
            "r_2": {
                "init_loc":    f"L1_{n}",
                "gate":        f"Out{n}_1",
                "guard":       "G1",
                "assignments": [],
                "end_loc":     f"L2_{n}",
            },
        },
    }


def _composed_sts(num_scenarios: int = 1) -> dict:
    """Minimal valid composed STS built from *num_scenarios* partial STSs."""
    ids = ", ".join(f"sts_{i:03d}" for i in range(1, num_scenarios + 1))
    locs = ["L0_comp"] + [f"L{k}_{i}" for i in range(1, num_scenarios + 1) for k in (1, 2)]
    return {
        "id":               "sts_composed",
        "description":      f"Composition of scenarios {ids}",
        "initial_location": "L0_comp",
        "gate_id_type":     "string",
        "location_id_type": "string",
        "locationVariables": {
            "status": _vardef("string", ["OK", "FAIL"]),
        },
        "parameters": {
            "status_p": _vardef("string", ["OK", "FAIL"]),
        },
        "attributes": {},
        "locations":   locs,
        "inputGates": {
            "In1": {"text": "set status", "parameters": ["status_p"]},
        },
        "outputGates": {
            "Out1": {"text": "report status", "parameters": ["status_p"]},
        },
        "guards": {
            "G1": {"lhs": "status", "op": "==", "rhs": "OK"},
        },
        "assignments": {
            "A1": {"target": "status", "expression": "status_p"},
        },
        "switches": {
            "r_1": {
                "init_loc":    "L0_comp",
                "gate":        "In1",
                "guard":       "G1",
                "assignments": ["A1"],
                "end_loc":     "L1_1",
            },
            "r_2": {
                "init_loc":    "L1_1",
                "gate":        "Out1",
                "guard":       "G1",
                "assignments": [],
                "end_loc":     "L2_1",
            },
        },
    }


def _test_case() -> dict:
    """Minimal valid test case targeting the composed STS."""
    return {
        "initial_location": "L0_comp",
        "initial_values":   {"status": "OK"},
        "steps": [
            {"switch_id": "r_1", "values": {"status_p": "OK"}},
            {"switch_id": "r_2", "values": {}},
        ],
    }

class TestPartialSchemaValid:
    def test_single_sts(self) -> None:
        validate([_partial_sts(1)], PARTIAL_SCHEMA)

    def test_two_stss(self) -> None:
        validate([_partial_sts(1), _partial_sts(2, initial_state=False)], PARTIAL_SCHEMA)

    def test_boolean_variable(self) -> None:
        sts = _partial_sts(1)
        sts["locationVariables"]["enabled"] = _vardef("boolean", [True, False])
        sts["parameters"]["enabled_p"]      = _vardef("boolean", [True, False])
        validate([sts], PARTIAL_SCHEMA)

    def test_integer_variable(self) -> None:
        sts = _partial_sts(1)
        sts["locationVariables"]["count"] = _vardef("integer", [0, 1, 2])
        sts["parameters"]["count_p"]      = _vardef("integer", [0, 1, 2])
        validate([sts], PARTIAL_SCHEMA)

    def test_decimal_variable(self) -> None:
        sts = _partial_sts(1)
        sts["locationVariables"]["ratio"] = _vardef("decimal", [0.0, 0.5, 1.0])
        sts["parameters"]["ratio_p"]      = _vardef("decimal", [0.0, 0.5, 1.0])
        validate([sts], PARTIAL_SCHEMA)

    def test_struct_expanded_keys(self) -> None:
        """Array-of-struct variables are stored as expanded keys like 'arr[0].attr'."""
        sts = _partial_sts(1)
        sts["locationVariables"]["det[0].lane"]   = _vardef("integer", [1, 2, 3])
        sts["locationVariables"]["det[0].length"] = _vardef("decimal", [1.0, 3.0])
        validate([sts], PARTIAL_SCHEMA)

    def test_struct_attribute_in_attributes_map(self) -> None:
        sts = _partial_sts(1)
        sts["attributes"]["lane"] = _vardef("integer", [1, 2, 3])
        validate([sts], PARTIAL_SCHEMA)

    def test_empty_attributes_and_no_parameters(self) -> None:
        sts = _partial_sts(1)
        sts["attributes"]  = {}
        sts["parameters"]  = {}
        validate([sts], PARTIAL_SCHEMA)

    def test_guard_with_conjunction_tree(self) -> None:
        sts = _partial_sts(1)
        sts["guards"]["G2"] = {
            "lhs": {"lhs": "status", "op": "==", "rhs": "OK"},
            "op":  "&&",
            "rhs": {"lhs": "status", "op": "!=", "rhs": "FAIL"},
        }
        sts["switches"]["r_1"]["guard"] = "G2"
        validate([sts], PARTIAL_SCHEMA)

    def test_switch_with_empty_assignments(self) -> None:
        sts = _partial_sts(1)
        sts["switches"]["r_1"]["assignments"] = []
        validate([sts], PARTIAL_SCHEMA)

    def test_id_padding_variants(self) -> None:
        """IDs sts_001 through sts_999 are all valid."""
        for n in (1, 42, 999):
            sts = _partial_sts(n)
            validate([sts], PARTIAL_SCHEMA)

class TestPartialSchemaInvalid:
    def test_empty_array_rejected(self) -> None:
        with pytest.raises(ValidationError):
            validate([], PARTIAL_SCHEMA)

    def test_missing_required_field_id(self) -> None:
        sts = _partial_sts(1)
        del sts["id"]
        with pytest.raises(ValidationError):
            validate([sts], PARTIAL_SCHEMA)

    def test_missing_required_field_switches(self) -> None:
        sts = _partial_sts(1)
        del sts["switches"]
        with pytest.raises(ValidationError):
            validate([sts], PARTIAL_SCHEMA)

    def test_id_pattern_too_short(self) -> None:
        sts = _partial_sts(1)
        sts["id"] = "sts_1"         # must be sts_NNN (3 digits)
        with pytest.raises(ValidationError):
            validate([sts], PARTIAL_SCHEMA)

    def test_id_pattern_wrong_prefix(self) -> None:
        sts = _partial_sts(1)
        sts["id"] = "STS_001"
        with pytest.raises(ValidationError):
            validate([sts], PARTIAL_SCHEMA)

    def test_initial_location_pattern_wrong(self) -> None:
        sts = _partial_sts(1)
        sts["initial_location"] = "L_1"   # must be L0_<digits>
        with pytest.raises(ValidationError):
            validate([sts], PARTIAL_SCHEMA)

    def test_locations_only_one_item(self) -> None:
        sts = _partial_sts(1)
        sts["locations"] = ["L0_1"]   # minItems: 2
        with pytest.raises(ValidationError):
            validate([sts], PARTIAL_SCHEMA)

    def test_variable_type_invalid_enum(self) -> None:
        sts = _partial_sts(1)
        sts["locationVariables"]["status"]["type"] = "float"  # not in enum
        with pytest.raises(ValidationError):
            validate([sts], PARTIAL_SCHEMA)

    def test_variable_range_empty(self) -> None:
        sts = _partial_sts(1)
        sts["locationVariables"]["status"]["range"] = []  # minItems: 1
        with pytest.raises(ValidationError):
            validate([sts], PARTIAL_SCHEMA)

    def test_switch_missing_end_loc(self) -> None:
        sts = _partial_sts(1)
        del sts["switches"]["r_1"]["end_loc"]
        with pytest.raises(ValidationError):
            validate([sts], PARTIAL_SCHEMA)

    def test_gate_missing_parameters(self) -> None:
        sts = _partial_sts(1)
        del sts["inputGates"]["In1_1"]["parameters"]
        with pytest.raises(ValidationError):
            validate([sts], PARTIAL_SCHEMA)

    def test_additional_property_on_sts(self) -> None:
        sts = _partial_sts(1)
        sts["extraField"] = "not allowed"
        with pytest.raises(ValidationError):
            validate([sts], PARTIAL_SCHEMA)

    def test_additional_property_on_switch(self) -> None:
        sts = _partial_sts(1)
        sts["switches"]["r_1"]["extra"] = True
        with pytest.raises(ValidationError):
            validate([sts], PARTIAL_SCHEMA)

class TestComposedSchemaValid:
    def test_single_scenario_composition(self) -> None:
        validate(_composed_sts(1), COMPOSED_SCHEMA)

    def test_three_scenario_composition(self) -> None:
        validate(_composed_sts(3), COMPOSED_SCHEMA)

    def test_empty_attributes(self) -> None:
        comp = _composed_sts(1)
        comp["attributes"] = {}
        validate(comp, COMPOSED_SCHEMA)

    def test_struct_expanded_location_variables(self) -> None:
        comp = _composed_sts(1)
        comp["locationVariables"]["det[0].lane"]   = _vardef("integer", [1, 2])
        comp["locationVariables"]["det[0].length"] = _vardef("decimal", [1.0, 3.0])
        validate(comp, COMPOSED_SCHEMA)

    def test_compound_location_name(self) -> None:
        """Sequential composition produces 'La_x_Lb_y' location IDs."""
        comp = _composed_sts(1)
        comp["locations"].append("L2_1_L0_2")
        validate(comp, COMPOSED_SCHEMA)

    def test_guard_with_negation(self) -> None:
        comp = _composed_sts(1)
        comp["guards"]["G2"] = {"op": "!", "rhs": {"lhs": "status", "op": "==", "rhs": "OK"}}
        validate(comp, COMPOSED_SCHEMA)

    def test_switch_with_multiple_assignments(self) -> None:
        comp = _composed_sts(1)
        comp["locationVariables"]["count"] = _vardef("integer", [0, 1])
        comp["parameters"]["count_p"]      = _vardef("integer", [0, 1])
        comp["assignments"]["A2"] = {"target": "count", "expression": "count_p"}
        comp["switches"]["r_1"]["assignments"].append("A2")
        validate(comp, COMPOSED_SCHEMA)


class TestComposedSchemaInvalid:
    def test_id_not_sts_composed(self) -> None:
        comp = _composed_sts(1)
        comp["id"] = "sts_001"   # const must be "sts_composed"
        with pytest.raises(ValidationError):
            validate(comp, COMPOSED_SCHEMA)

    def test_initial_location_not_l0_comp(self) -> None:
        comp = _composed_sts(1)
        comp["initial_location"] = "L0_1"  # const must be "L0_comp"
        with pytest.raises(ValidationError):
            validate(comp, COMPOSED_SCHEMA)

    def test_description_pattern_wrong(self) -> None:
        comp = _composed_sts(1)
        comp["description"] = "some free text"
        with pytest.raises(ValidationError):
            validate(comp, COMPOSED_SCHEMA)

    def test_description_with_short_id(self) -> None:
        comp = _composed_sts(1)
        comp["description"] = "Composition of scenarios sts_1"  # needs 3 digits
        with pytest.raises(ValidationError):
            validate(comp, COMPOSED_SCHEMA)

    def test_missing_required_field_guards(self) -> None:
        comp = _composed_sts(1)
        del comp["guards"]
        with pytest.raises(ValidationError):
            validate(comp, COMPOSED_SCHEMA)

    def test_locations_empty(self) -> None:
        comp = _composed_sts(1)
        comp["locations"] = []   # minItems: 1
        with pytest.raises(ValidationError):
            validate(comp, COMPOSED_SCHEMA)

    def test_additional_property_rejected(self) -> None:
        comp = _composed_sts(1)
        comp["source_files"] = ["spec.txt"]
        with pytest.raises(ValidationError):
            validate(comp, COMPOSED_SCHEMA)

    def test_variable_type_wrong(self) -> None:
        comp = _composed_sts(1)
        comp["locationVariables"]["status"]["type"] = 42  # must be a string
        with pytest.raises(ValidationError):
            validate(comp, COMPOSED_SCHEMA)


class TestTestCasesSchemaValid:
    def test_single_test_case(self) -> None:
        validate([_test_case()], TEST_CASES_SCHEMA)

    def test_multiple_test_cases(self) -> None:
        tc2 = copy.deepcopy(_test_case())
        tc2["initial_values"]["status"] = "FAIL"
        validate([_test_case(), tc2], TEST_CASES_SCHEMA)

    def test_numeric_initial_value(self) -> None:
        tc = _test_case()
        tc["initial_values"]["count"] = 3
        validate([tc], TEST_CASES_SCHEMA)

    def test_boolean_initial_value(self) -> None:
        tc = _test_case()
        tc["initial_values"]["enabled"] = True
        validate([tc], TEST_CASES_SCHEMA)

    def test_array_initial_value_primitives(self) -> None:
        tc = _test_case()
        tc["initial_values"]["lanes"] = [1, 2, 3]
        validate([tc], TEST_CASES_SCHEMA)

    def test_array_initial_value_structs(self) -> None:
        tc = _test_case()
        tc["initial_values"]["detectors"] = [
            {"lane": 1, "length-position": 1.5},
            {"lane": 2, "length-position": 2.0},
        ]
        validate([tc], TEST_CASES_SCHEMA)

    def test_step_switch_id_high_number(self) -> None:
        tc = _test_case()
        tc["steps"][0]["switch_id"] = "r_99"
        validate([tc], TEST_CASES_SCHEMA)

    def test_step_values_empty_object(self) -> None:
        """Actions with no parameters use an empty values dict."""
        tc = copy.deepcopy(_test_case())
        for step in tc["steps"]:
            step["values"] = {}
        validate([tc], TEST_CASES_SCHEMA)


class TestTestCasesSchemaInvalid:
    def test_empty_array_rejected(self) -> None:
        with pytest.raises(ValidationError):
            validate([], TEST_CASES_SCHEMA)

    def test_missing_initial_location(self) -> None:
        tc = _test_case()
        del tc["initial_location"]
        with pytest.raises(ValidationError):
            validate([tc], TEST_CASES_SCHEMA)

    def test_missing_initial_values(self) -> None:
        tc = _test_case()
        del tc["initial_values"]
        with pytest.raises(ValidationError):
            validate([tc], TEST_CASES_SCHEMA)

    def test_missing_steps(self) -> None:
        tc = _test_case()
        del tc["steps"]
        with pytest.raises(ValidationError):
            validate([tc], TEST_CASES_SCHEMA)

    def test_steps_empty_array(self) -> None:
        tc = _test_case()
        tc["steps"] = []   # minItems: 1
        with pytest.raises(ValidationError):
            validate([tc], TEST_CASES_SCHEMA)

    def test_switch_id_wrong_pattern(self) -> None:
        tc = copy.deepcopy(_test_case())
        tc["steps"][0]["switch_id"] = "switch_1"   # must be r_<digits>
        with pytest.raises(ValidationError):
            validate([tc], TEST_CASES_SCHEMA)

    def test_switch_id_missing_number(self) -> None:
        tc = copy.deepcopy(_test_case())
        tc["steps"][0]["switch_id"] = "r_"   # digits required after underscore
        with pytest.raises(ValidationError):
            validate([tc], TEST_CASES_SCHEMA)

    def test_step_missing_values(self) -> None:
        tc = copy.deepcopy(_test_case())
        del tc["steps"][0]["values"]
        with pytest.raises(ValidationError):
            validate([tc], TEST_CASES_SCHEMA)

    def test_additional_property_on_test_case(self) -> None:
        tc = _test_case()
        tc["label"] = "extra"
        with pytest.raises(ValidationError):
            validate([tc], TEST_CASES_SCHEMA)

    def test_additional_property_on_step(self) -> None:
        tc = copy.deepcopy(_test_case())
        tc["steps"][0]["note"] = "not allowed"
        with pytest.raises(ValidationError):
            validate([tc], TEST_CASES_SCHEMA)
