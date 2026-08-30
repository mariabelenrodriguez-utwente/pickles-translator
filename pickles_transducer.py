import argparse
from datetime import datetime
import json
import logging
import os

from src.transformer import PicklesToSTS
from src.tc_translator import TestCaseTranslator

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
    """Translate pre-generated test cases JSON to natural language (Pickles format)."""
    with open(args.sts) as f:
        sts = json.load(f)
    with open(args.tests) as f:
        test_cases = json.load(f)

    basename = os.path.splitext(os.path.basename(args.sts))[0]
    nl_path  = f"output/{ts}_{basename}_test_cases.pickles"

    translator = TestCaseTranslator(sts)
    translator.translate(test_cases, nl_path)
    print(f"Translated {len(test_cases)} test cases to Pickles format -> {nl_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Pickles transducer")
    sub = ap.add_subparsers(dest="command", required=True)

    p_sts = sub.add_parser("sts", help="Generate STS from .pickles spec(s)")
    p_sts.add_argument("--spec", default=None, metavar="SPEC_TXT",
                       help="Path to a single spec file (default: all files in input_files/)")

    p_tests = sub.add_parser("tests", help="Translate test cases JSON to natural language")
    p_tests.add_argument("--sts",   required=True, metavar="STS_JSON",   help="Path to the STS JSON the test cases target")
    p_tests.add_argument("--tests", required=True, metavar="TESTS_JSON", help="Path to test cases JSON")

    args = ap.parse_args()
    _configure_logging()
    if args.command == "sts":
        cmd_generate_sts(args)
    else:
        cmd_translate_tests(args)
