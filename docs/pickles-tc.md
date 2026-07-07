# Pickles Test Cases

A test case is a starting state plus an ordered sequence of steps through the model you generated with `sts`. Pickles can translate a set of test cases (as JSON) into plain-language descriptions, in the same Given/When/Then style as your specs.

## The test cases file

Test cases are a JSON array, one object per test case. The expected format is in `schemas/test_cases.schema.json`:

```json
[
  {
    "initial_location": "L0_comp",
    "initial_values": {
      "availability": "AV",
      "enabledness": true,
      "critical-section-lane": 1
    },
    "steps": [
      { "switch_id": "r_5", "values": { "faulty-detectors_p": [ { "lane": 1, "length-position": 2.0 } ] } },
      { "switch_id": "r_6", "values": {} }
    ]
  }
]
```

- `initial_location` is always `"L0_comp"`.
- `initial_values` gives a starting value for every variable. Variable names match your spec, with spaces replaced by middle-dashes (e.g. `"water level"` → `"water-level"`).
- `steps` is the path taken through the model: each step executes an input or output switch (`switch_id`) and supplies any values it needs for its parameters (`values`) in case of inputs.

## Translating test cases

```bash
python pickles_transducer.py tests --sts output/<name>_composed.json --tests path/to/test_cases.json
```

This writes `output/<timestamp>_<name>_test_cases_pickles.txt`, e.g.:

```
Test Case 1:
Given the system is initialized with values:
    "availability": AV
    "enabledness": true
When the controller detects "faulty detectors" with values:
    "faulty detectors":
        1: {"lane": 1, "length position": 2.0}
Then the user interface displays "availability" equal to NOT AV
```
