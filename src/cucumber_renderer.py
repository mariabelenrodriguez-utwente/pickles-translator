"""Renders parsed test cases as a Cucumber .feature file via Jinja2.
"""
import os
from typing import Any

from jinja2 import Environment, FileSystemLoader

from src.tc_translator import TestCaseTranslator


def _indent(text: str, prefix: str = '    ') -> str:
    """Prefix every line of text with prefix.

    Args:
        text: Multi-line source text.
        prefix: Indentation to add to each line.

    Returns:
        The indented text.
    """
    return '\n'.join(f'{prefix}{line}' for line in text.split('\n'))


def render_cucumber(test_cases: list[dict], translator: TestCaseTranslator, template_path: str,
                     json_dir: str | None = None, keyword_map: dict[str, Any] | None = None) -> str:
    """Render parsed test cases as a Cucumber .feature file.

    If every test case shares the same initial_values, the Given block is
    rendered once into Background and each Scenario only has When/Then;
    otherwise each Scenario keeps its own Given.

    Args:
        test_cases: Test case dicts.
        translator: A TestCaseTranslator initialised with the STS spec(s)
            these test cases target.
        template_path: Path to the Jinja2 .feature template.
        json_dir: If given, a Given/When dict/list-of-dicts value is written
            to a JSON file in this directory instead of inlined.
        keyword_map: If given, maps an gate's fixed "text" to a proxy
            template (or, for a multi-variable gate, a {variable_id:
            template} map) that fully replaces that step's rendered text.

    Returns:
        The rendered .feature file text.
    """
    common_init = bool(test_cases) and all(
        tc['initial_values'] == test_cases[0]['initial_values'] for tc in test_cases
    )

    if common_init:
        background_given = _indent(translator._render_initial(test_cases[0]['initial_values'],
                                                                json_dir=json_dir, keyword_map=keyword_map))
        bodies = [_indent(translator.render_steps(tc['steps'], json_dir=json_dir, keyword_map=keyword_map,
                                                    initial_values=tc['initial_values']))
                  for tc in test_cases]
    else:
        background_given = ''
        bodies = [
            _indent('\n'.join([translator._render_initial(tc['initial_values'], json_dir=json_dir,
                                                            keyword_map=keyword_map),
                                translator.render_steps(tc['steps'], json_dir=json_dir, keyword_map=keyword_map,
                                                         initial_values=tc['initial_values'])]))
            for tc in test_cases
        ]

    scenarios = [{"id": f'Test Case {i + 1}', "body": body} for i, body in enumerate(bodies)]

    env = Environment(
        loader=FileSystemLoader(os.path.dirname(template_path)),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    template = env.get_template(os.path.basename(template_path))
    return template.render(background_given=background_given, test_cases=scenarios)
