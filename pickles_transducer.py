import argparse
from datetime import datetime
import json
import os

from src.transformer import PicklesToSTS
from src.composer import compose_stss
from src.exporter import STSExporter
from src.tc_translator import TestCaseTranslator

def cmd_generate_sts(args: argparse.Namespace) -> None:
    """Process .txt spec(s) and write STS JSON + exports."""
    pickles = PicklesToSTS()
    parser  = pickles.load_parser(lang="en")
    sts_id  = 1
    if args.spec:
        spec_files = [args.spec]
    else:
        spec_files = [
            f"input_files/{fn}"
            for fn in sorted(os.listdir("input_files"))
            if fn.endswith(".txt")
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

        partial_path = f"output/{ts}_{basename}.json"
        with open(partial_path, "w") as out:
            json.dump(sts_list, out, indent=4)
        print(f"  [partial STSs]  -> {partial_path}")

        composed      = compose_stss(sts_list)
        composed_path = f"output/{ts}_{basename}_composed.json"
        with open(composed_path, "w") as out:
            json.dump(composed, out, indent=4)
        print(f"  [composed STS]  -> {composed_path}")
        print(f"    locations : {len(composed['locations'])}")
        print(f"    switches  : {len(composed['switches'])}")
        print(f"    guards    : {len(composed['guards'])}")

        exporter  = STSExporter(composed)
        dot_path  = f"output/{ts}_{basename}_composed.dot"
        html_path = f"output/{ts}_{basename}_composed.html"
        exporter.write_dot(dot_path)
        exporter.write_html(html_path)
        print(f"  [dot]           -> {dot_path}")
        print(f"  [html]          -> {html_path}")

    print(f"\n{'='*60}")
    print("Done.")


def cmd_translate_tests(args: argparse.Namespace) -> None:
    """Translate pre-generated test cases JSON to natural language (Pickles format)."""
    with open(args.sts) as f:
        composed = json.load(f)
    with open(args.tests) as f:
        test_cases = json.load(f)

    ts       = datetime.now().strftime("%Y%m%dT%H%M%S")
    basename = os.path.splitext(os.path.basename(args.sts))[0]
    nl_path  = f"output/{ts}_{basename}_test_cases_pickles.txt"

    translator = TestCaseTranslator(composed)
    translator.translate(test_cases, nl_path)
    print(f"Translated {len(test_cases)} test cases to Pickles format -> {nl_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Pickles transducer")
    sub = ap.add_subparsers(dest="command", required=True)

    p_sts = sub.add_parser("sts", help="Generate STS from .txt spec(s)")
    p_sts.add_argument("--spec", default=None, metavar="SPEC_TXT",
                       help="Path to a single spec file (default: all files in input_files/)")

    p_tests = sub.add_parser("tests", help="Translate test cases JSON to natural language")
    p_tests.add_argument("--sts",   required=True, metavar="STS_JSON",   help="Path to composed STS JSON")
    p_tests.add_argument("--tests", required=True, metavar="TESTS_JSON", help="Path to test cases JSON")

    args = ap.parse_args()
    if args.command == "sts":
        cmd_generate_sts(args)
    else:
        cmd_translate_tests(args)
