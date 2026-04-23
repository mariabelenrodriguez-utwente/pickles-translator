# pickles-transducer

A (structured) natural language specification transducer that converts specifications in Pickles syntax into Symbolic Transition System (STS) JSON, with composition and visualization output.

## Setup

```bash
conda create --name picklestransd python=3.10
conda activate picklestransd
pip install -r requirements.txt
```

## Running

The tool has two subcommands.

**Generate STS** — process spec files and write STS JSON + visualization:

```bash
conda activate picklestransd

# All .txt files in input_files/
python pickles_transducer.py sts

# Single spec file
python pickles_transducer.py sts --spec path/to/spec.txt
```

For each input file, the following outputs are written to `output/`:

| File | Contents |
|---|---|
| `<name>.json` | One STS per scenario (partial STSs) |
| `<name>_composed.json` | Single composed STS (choice + sequential composition) |
| `<name>_composed.dot` | Graphviz DOT file for the composed STS |
| `<name>_composed.html` | Self-contained interactive HTML for the composed STS |

**Translate test cases** — convert a pre-generated test cases JSON to natural language:

```bash
python pickles_transducer.py tests \
  --sts   output/<name>_composed.json \
  --tests output/<name>_tests.json
```

## Visualization

### Interactive HTML

Open `output/<name>_composed.html` in any browser.
- Click any switch to see its gate, guard, and assignment in the side panel.
- Use **Fit** to reset the viewport and **Export PNG** to save a static image.

### DOT / Graphviz (static)

Render the DOT file to PDF or SVG with the Graphviz `dot` command:

```bash
# PDF
dot -Tpdf output/spec_composed.dot -o output/spec_composed.pdf

# SVG
dot -Tsvg output/spec_composed.dot -o output/spec_composed.svg

# PNG
dot -Tpng output/spec_composed.dot -o output/spec_composed.png
```

## Using the exporter directly

`STSExporter` can be used standalone on any STS JSON:

```python
from exporter import STSExporter

exporter = STSExporter(sts_dict)
exporter.write_dot("my_sts.dot")
exporter.write_html("my_sts.html")

dot_src  = exporter.to_dot()
html_src = exporter.to_html()
```

## Reproduce paper results

### Setup

#### Docker CLI
Make sure [Docker CLI](https://www.docker.com/products/cli/) is installed in the system.

#### Load image
To load the artifact Docker image, execute:
```
docker load --input pickles_translator.tar.gz
```

### Generate master model from Pickles specifications
File `spec_examples/detectors_spec.txt` contains the Pickles specification presented in Listing 1 in the paper. To get the master model, execute:
```
make execute-sts SPEC=spec_examples/detectors_spec.txt
```
This command should take a couple of seconds. Expected console output:
```
============================================================
Processing: detectors_spec.txt  (4 scenario(s))
============================================================
  [partial STSs]  -> output/[TIMESTAMP]_detectors_spec.json
  [composed STS]  -> output/[TIMESTAMP]_detectors_spec_composed.json
    locations : 25
    switches  : 40
    guards    : 10
  [dot]           -> output/[TIMESTAMP]_detectors_spec_composed.dot
  [html]          -> output/[TIMESTAMP]_detectors_spec_composed.html

============================================================
Done.
```
To visualize the composed STS, there are two options:
1. Open file `output/[TIMESTAMP]_detectors_spec_composed.html` in a browser: this will provide an interactive visualization of the STS where switches can be clicked to see more details
1. Execute `make sts-dot-to-png`: This will generate a .png file in `/output` with the visualization of the latest composed STS.

NOTE: Figure 3 in the paper shows a version of the same STS where non-satisfiable paths have been removed. This was done manually as the current version of the tool does not support this.

### Formal test case translation to Pickles
Execute:
```
make translate-tests TESTS=./test_examples/detectors_tests.json
```
This will generate the Pickles translation of the test cases defined in the json, based on the latest STS generated (present in `/output`). To point to a specific STS .json file:
```
make translate-tests STS=./path/to/sts.json TESTS=./test_examples/detectors_tests.json
```
In both cases, the file `[TIMESTAMP]_[STS_FILENAME]_test_cases_pickles.txt` with the test cases in Pickles syntax. Test Case 1 corresponds to the test case introduced in Listing 2 in the paper.
