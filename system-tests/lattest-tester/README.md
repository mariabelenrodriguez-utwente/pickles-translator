# Lattest tester

Model-based testing with lattest-lib.

## Building the Docker image
Succesive versions of the same tester should be distinguished by tag version:
```
docker build -t lattest-tester:x.x.x .
```

## Execution

The tester reads an STS list JSON (from the translator `sts` mode). It composes the STSs, writes the composed STS and writes the test traces (one test case on each line).

Arguments (all required, in this order):

| Position | Description |
|----------|-------------|
| 1 `SPEC_JSON` | STS list JSON from the translator `sts` mode. |
| 2 `COMPOSED_JSON` | Output: composed STS JSON. |
| 3 `TRACES_TXT` | Output: test traces. |
| 4 `N_TESTS` | Number of test cases. |
| 5 `N_STEPS` | Maximum number of steps in each test case. |

This is meant to be executed by the Robot system tests. To execute the Docker container manually run:

```bash
docker run --rm -v "$PWD/output:/data" lattest-tester:0.2.0 \
  /data/<timestamp>_coffee_machine_spec.json \
  /data/coffee_machine_composed.json \
  /data/coffee_machine_traces.txt \
  3 10
```

TODO: Report coverage

## Lattest Dependency Details
This project depends on the lattest library. To ensure reproducible builds, we pin to a specific commit. This is configured in stack.yaml using a git-based extra-dep:
```
extra-deps:
  - git: https://github.com/ramon-janssen/lattest.git
    commit: e8dc81d899393749c5701cbb403878aa925d6d47
    subdirs:
      - lattest-lib
  - sbv-14.7  # lattest-lib needs this sbv version
```
To update to a newer version of lattest we must:
- Update the commit hash in stack.yaml
- Rebuild with `stack clean` && `stack build`