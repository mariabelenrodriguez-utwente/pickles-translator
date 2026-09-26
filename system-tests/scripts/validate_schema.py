"""Validate a JSON file against a JSON Schema file. Exits non-zero on failure."""
import json
import sys

import jsonschema


def main() -> int:
    schema_path, json_path = sys.argv[1], sys.argv[2]
    with open(schema_path) as f:
        schema = json.load(f)
    with open(json_path) as f:
        data = json.load(f)
    jsonschema.validate(data, schema)
    print(f"OK: {json_path} conforms to {schema_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
