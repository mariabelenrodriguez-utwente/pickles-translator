import argparse
from datetime import datetime
import json
import logging
import os

from src.transformer import PicklesToSTS
from src.tc_translator import TestCaseTranslator
from src.trace_parser import parse_traces_file
from src.cucumber_renderer import render_cucumber
from src.exporter import STSExporter

ts       = datetime.now().strftime("%Y%m%dT%H%M%S")
LOG_PATH = f"output/pickles_{ts}.log"

def _configure_logging() -> None:
    """Route logs to a file"""
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    logging.basicConfig(
        filename=LOG_PATH,
        filemode="a",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def cmd_generate_sts(args: argparse.Namespace) -> None:
    """Process .pickles spec(s) and write a JSON array of STS definitions."""
    pickles = PicklesToSTS()
    parser  = pickles.load_parser(lang="en")
    sts_id  = 1
    if args.spec:
        spec_files = [args.spec]
    else:
        spec_files = [
            f"input_files/{fn}"
            for fn in sorted(os.listdir("input_files"))
            if fn.endswith(".pickles")
        ]
    for filepath in spec_files:
        filename = os.path.basename(filepath)
        with open(filepath) as f:
            text = f.read()
        tree = parser.parse(pickles._preprocess(text))
        sts_list, sts_id = pickles.tree_to_sts(tree, start_id=sts_id)
        basename = os.path.splitext(filename)[0]
        ts       = datetime.now().strftime("%Y%m%dT%H%M%S")

        print(f"\n{'='*60}")
        print(f"Processing: {filename}  ({len(sts_list)} scenario(s))")
        print(f"{'='*60}")

        sts_path = f"output/{ts}_{basename}.json"
        with open(sts_path, "w") as out:
            json.dump(sts_list, out, indent=4)
        print(f"  [STSs]  -> {sts_path}")

    print(f"\n{'='*60}")
    print("Done.")


def cmd_translate_tests(args: argparse.Namespace) -> None:
    """Translate test cases (JSON or trace strings) to Pickles or Cucumber format."""
    with open(args.sts) as f:
        specs = json.load(f)

    if args.json:
        with open(args.json) as f:
            test_cases = json.load(f)
    else:
        test_cases = parse_traces_file(args.trace, specs)

    basename   = os.path.splitext(os.path.basename(args.sts))[0]
    translator = TestCaseTranslator(specs)

    if args.cucumber_template:
        feature_name = f"{ts}_{basename}_test_cases"
        feature_path = f"output/{feature_name}.feature"
        json_dir     = f"output/{feature_name}"
        text = render_cucumber(test_cases, translator, args.cucumber_template, json_dir=json_dir)
        with open(feature_path, "w") as f:
            f.write(text)
        print(f"Translated {len(test_cases)} test cases to Cucumber format -> {feature_path}")

        if args.keyword_map:
            with open(args.keyword_map) as f:
                keyword_map = json.load(f)
            proxy_translator = TestCaseTranslator(specs)
            proxy_path = f"output/{feature_name}_proxy.feature"
            proxy_text = render_cucumber(test_cases, proxy_translator, args.cucumber_template,
                                          json_dir=json_dir, keyword_map=keyword_map)
            with open(proxy_path, "w") as f:
                f.write(proxy_text)
            print(f"Wired proxy Cucumber format -> {proxy_path}")
    else:
        nl_path = f"output/{ts}_{basename}_test_cases.pickles"
        translator.translate(test_cases, nl_path)
        print(f"Translated {len(test_cases)} test cases to Pickles format -> {nl_path}")


def cmd_visualize(args: argparse.Namespace) -> None:
    """Render an STS JSON dict as a DOT and/or HTML visualization."""
    with open(args.sts) as f:
        sts = json.load(f)

    originals = None
    if args.originals:
        with open(args.originals) as f:
            originals = json.load(f)

    basename = os.path.splitext(os.path.basename(args.sts))[0]
    exporter = STSExporter(sts, originals=originals)

    if args.format in ("dot", "both"):
        dot_path = f"output/{ts}_{basename}.dot"
        exporter.write_dot(dot_path)
        print(f"  [DOT]  -> {dot_path}")
    if args.format in ("html", "both"):
        html_path = f"output/{ts}_{basename}.html"
        exporter.write_html(html_path)
        print(f"  [HTML] -> {html_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Pickles transducer")
    sub = ap.add_subparsers(dest="command", required=True)

    p_sts = sub.add_parser("sts", help="Generate STS from .pickles spec(s)")
    p_sts.add_argument("--spec", default=None, metavar="SPEC_TXT",
                       help="Path to a single spec file (default: all files in input_files/)")

    p_tests = sub.add_parser("tests", help="Translate test cases (JSON or traces) to natural language")
    p_tests.add_argument("--sts", required=True, metavar="STS_JSON", help="Path to the STS JSON the test cases target")
    p_tests_in = p_tests.add_mutually_exclusive_group(required=True)
    p_tests_in.add_argument("--json",  metavar="TESTS_JSON", help="Path to a test cases JSON file")
    p_tests_in.add_argument("--trace", metavar="TRACE_TXT",  help="Path to a file with one trace string per line")
    p_tests.add_argument("--cucumber-template", default=None, metavar="TEMPLATE_TXT",
                          help="Path to a Jinja2 Cucumber .feature template. If given, writes a "
                               ".feature file instead of the default .pickles text output.")
    p_tests.add_argument("--keyword-map", default=None, metavar="MAP_JSON",
                          help="Path to a JSON file mapping pickles gate text to a keyword mapping from Pickles "
                               "keywords to custom step definitions. Requires --cucumber-template.")

    p_viz = sub.add_parser("visualize", help="Render an STS JSON dict as DOT/HTML visualizations")
    p_viz.add_argument("--sts", required=True, metavar="STS_JSON", help="Path to the STS JSON dict to visualize")
    p_viz.add_argument("--format", choices=["dot", "html", "both"], default="both",
                        help="Output format to write (default: both)")
    p_viz.add_argument("--originals", default=None, metavar="ORIGINALS_JSON",
                        help="Path to a JSON array of STS dicts (as produced by 'sts'). If given, gate text and guard/assignment "
                             "expressions are resolved based on this file, instead of shown as raw IDs.")

    args = ap.parse_args()
    _configure_logging()
    if args.command == "sts":
        cmd_generate_sts(args)
    elif args.command == "tests":
        cmd_translate_tests(args)
    else:
        cmd_visualize(args)
