# Pickles

Pickles is a tool for writing system specifications in structured, natural-language scenarios in Pickles, a DSL that extends Gherkin and its Given/When/Then structure, and turning them into a single visual model of your system's behavior. From that model you can generate test cases, and translate formal test cases back into plain-language descriptions. This tool is based on the paper [PICKLES: a Natural Language Framework for Requirement Specification and Model-Based Testing](https://www.jot.fm/issues/issue_2026_03/a25.pdf) authored by María Belén Rodríguez and Petra van den Bos.

See also:
- [Writing specification scenarios](pickles-scenarios.md)
- [Translating back test cases](pickles-tc.md)

## Setup
Create a virtual environment for Python (recommended) and install dependencies.

```bash
conda create --name pickles python=3.10
conda activate pickles
pip install -r requirements.pickles
```

## Quickstart

1. Write a spec file (see [Writing scenarios](pickles-scenarios.md)) and save it under `input_files/` with a `.pickles` extension.
2. Generate the model:

    ```bash
    conda activate pickles

    # process every .pickles file in input_files/
    python pickles_transducer.py sts

    # or target a single file
    python pickles_transducer.py sts --spec input_files/my_spec.pickles
    ```

3. Check `output/` for the results:

    | File | Contents |
    |---|---|
    | `<name>.json` | One model per scenario |
    | `<name>_composed.json` | All scenarios combined into a single model |
    | `<name>_composed.dot` | Graphviz source for the combined model |
    | `<name>_composed.html` | Interactive visualization of the combined model |

4. Open `<name>_composed.html` in a browser to explore the model. Use **Fit** to reset the view and **Export PNG** to save an image.

   To render a static image instead, use Graphviz on the `.dot` file:

    ```bash
    dot -Tpng output/my_spec_composed.dot -o output/my_spec_composed.png
    ```

### Editor support

A VS Code extension for `.pickles` syntax highlighting is available in `pickles-vscode/`. Install it via **TODO**
