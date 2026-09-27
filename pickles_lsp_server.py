"""
Pickles Language Server.

Backs the pickles-vscode extension over the Language Server Protocol.
Provides:
- textDocument/documentSymbol: populates VS Code's built-in Outline view
  (and "Go to Symbol in File") with a spec's declared variables, constants,
  and each scenario's When/Then actions.
- Live diagnostics (textDocument/publishDiagnostics) on open/change/close:
  syntax errors and semantic (ConsistencyError) issues, underlined in the
  editor and listed in the Problems panel.
- textDocument/completion: suggests the next legal keyword(s) at the
  cursor, driven directly by the grammar (Lark's LALR interactive parser),
  not a hand-maintained list.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from lark.exceptions import UnexpectedInput, VisitError
from lsprotocol import types
from pygls.lsp.server import LanguageServer

from src.exceptions import ConsistencyError
from src.specvalidator import SpecValidator, check_action_parameter_consistency
from src.transformer import PicklesToSTS, SpecTransformer
from src.ast_nodes import ArrayType, PrimitiveType, StructType

_RESOURCES = Path(__file__).parent / "resources"

server = LanguageServer("pickles-language-server", "0.1.0")


def _var_type_label(typedesc) -> str:
    """Short human-readable type label for a variable/constant/attribute
    type descriptor, for the Outline entry's `detail` text."""
    if isinstance(typedesc, PrimitiveType):
        return typedesc.primtype
    if isinstance(typedesc, ArrayType):
        return "unique array" if typedesc.unique else "array"
    if isinstance(typedesc, StructType):
        return "structure"
    return "unknown"


def _find_line(lines: list[str], target: str, start: int, end: int | None = None, *, top_level: bool = False) -> int:
    """First line index in [start, end) containing `target`.

    Args:
        lines: The document, split into lines.
        target: Substring to search for.
        start: Line index to start searching from.
        end: Line index to stop searching before (exclusive); defaults to
            the end of the document.
        top_level: If True, skip indented lines.

    Returns:
        The matching line index, or `start` if nothing matched.
    """
    for i in range(start, len(lines) if end is None else end):
        line = lines[i]
        if top_level and line[:1] in (" ", "\t"):
            continue
        if target in line:
            return i
    return start


def _whole_line_range(lines: list[str], line: int) -> "types.Range":
    """A Range spanning one whole source line, 0 to its length."""
    text = lines[line] if 0 <= line < len(lines) else ""
    return types.Range(types.Position(line, 0), types.Position(line, len(text)))


def _symbol(name: str, kind: "types.SymbolKind", lines: list[str], line: int,
            *, detail: str | None = None, children: "list[types.DocumentSymbol] | None" = None) -> "types.DocumentSymbol":
    """Build a DocumentSymbol spanning one whole source line (or, with
    children, from that line through the last child's line)."""
    end_line = line
    for child in children or []:
        end_line = max(end_line, child.range.end.line)
    rng = types.Range(types.Position(line, 0), _whole_line_range(lines, end_line).end)
    return types.DocumentSymbol(
        name=name, kind=kind, range=rng, selection_range=_whole_line_range(lines, line),
        detail=detail, children=list(children) if children else None,
    )


def build_document_symbols(text: str) -> "list[types.DocumentSymbol]":
    """Parse `text` and build the Outline tree.
    """
    lines = text.splitlines()
    try:
        pickles = PicklesToSTS()
        parser  = pickles.load_parser(lang="en")
        tree    = parser.parse(pickles._preprocess(text))
        suite   = SpecTransformer().transform(tree)
    except Exception:
        return []

    symbols: list[types.DocumentSymbol] = []

    scenario_lines  = [i for i, l in enumerate(lines) if l.strip().startswith("Scenario")]
    vardefblock_end = scenario_lines[0] if scenario_lines else len(lines)

    # Variables and constants are two separate groups
    var_cursor = 0
    var_symbols = []
    for vd in suite.vardefblock.vardefs:
        if vd.is_constant:
            continue
        line = _find_line(lines, f'"{vd.name}"', var_cursor, vardefblock_end, top_level=True)
        var_cursor = max(var_cursor, line)
        var_symbols.append(_symbol(vd.name, types.SymbolKind.Variable, lines, line,
                                    detail=_var_type_label(vd.typedesc)))
    if var_symbols:
        symbols.append(_symbol("Variables", types.SymbolKind.Namespace, lines, 0, children=var_symbols))

    const_cursor = 0
    const_symbols = []
    for vd in suite.vardefblock.vardefs:
        if not vd.is_constant:
            continue
        line = _find_line(lines, f'"{vd.name}"', const_cursor, vardefblock_end, top_level=True)
        const_cursor = max(const_cursor, line)
        const_symbols.append(_symbol(vd.name, types.SymbolKind.Constant, lines, line,
                                      detail=_var_type_label(vd.typedesc)))
    if const_symbols:
        symbols.append(_symbol("Constants", types.SymbolKind.Namespace, lines, 0, children=const_symbols))

    action_cursor = 0
    action_symbols = []
    seen_actions: set[str] = set()
    for scenario in suite.scenarios:
        for step in scenario.when.steps + scenario.then.steps:
            if step.action in seen_actions:
                continue
            seen_actions.add(step.action)
            aline = _find_line(lines, step.action, action_cursor)
            action_cursor = max(action_cursor, aline)
            action_symbols.append(_symbol(step.action, types.SymbolKind.Method, lines, aline))
    if action_symbols:
        symbols.append(_symbol("Actions", types.SymbolKind.Namespace, lines, 0, children=action_symbols))

    scenario_symbols = [
        _symbol(scenario.description or "Scenario", types.SymbolKind.Class, lines, sline)
        for scenario, sline in zip(suite.scenarios, scenario_lines)
    ]
    if scenario_symbols:
        symbols.append(_symbol("Scenarios", types.SymbolKind.Namespace, lines, 0, children=scenario_symbols))

    return symbols


@server.feature(types.TEXT_DOCUMENT_DOCUMENT_SYMBOL)
def document_symbol(ls: LanguageServer, params: types.DocumentSymbolParams) -> "list[types.DocumentSymbol]":
    doc = ls.workspace.get_text_document(params.text_document.uri)
    return build_document_symbols(doc.source)


_QUOTED    = re.compile(r"'([^']*)'")
_BRACKETED = re.compile(r"[(\[{][^()\[\]{}]*[)\]}]")


def _search_line(lines: list[str], target: str, start: int, end: int) -> int | None:
    """First line index in [start, end) containing `target`, or None."""
    for i in range(start, end):
        if target in lines[i]:
            return i
    return None


def _locate(lines: list[str], message: str, start: int, end: int) -> int:
    """Best-effort source line for a ConsistencyError.
    """
    for candidate in _QUOTED.findall(message):
        line = _search_line(lines, f'"{candidate}"', start, end)
        if line is not None:
            return line
    for candidate in _BRACKETED.findall(message):
        line = _search_line(lines, candidate, start, end)
        if line is not None:
            return line
    return start


def _diagnostic(lines: list[str], line: int, message: str,
                 severity: "types.DiagnosticSeverity" = None) -> "types.Diagnostic":
    return types.Diagnostic(
        range=_whole_line_range(lines, line),
        message=message,
        severity=severity or types.DiagnosticSeverity.Error,
        source="pickles",
    )


def build_diagnostics(text: str) -> "list[types.Diagnostic]":
    lines   = text.splitlines()
    pickles = PicklesToSTS()
    parser  = pickles.load_parser(lang="en")

    try:
        tree = parser.parse(pickles._preprocess(text))
    except UnexpectedInput as e:
        line = max((e.line or 1) - 1, 0)
        return [_diagnostic(lines, line, str(e).splitlines()[0])]

    scenario_lines  = [i for i, l in enumerate(lines) if l.strip().startswith("Scenario")]
    vardefblock_end = scenario_lines[0] if scenario_lines else len(lines)

    try:
        suite = SpecTransformer().transform(tree)
    except (ConsistencyError, VisitError) as e:
        err  = e.orig_exc if isinstance(e, VisitError) else e
        line = _locate(lines, str(err), 0, vardefblock_end)
        return [_diagnostic(lines, line, str(err))]

    diagnostics = []
    for msg in check_action_parameter_consistency(suite):
        m    = re.match(r"^Action '(.+)' is used with different parameters", msg)
        line = _search_line(lines, m.group(1), 0, len(lines)) if m else None
        if line is None:
            line = _locate(lines, msg, 0, len(lines))
        # Non-blocking
        diagnostics.append(_diagnostic(lines, line, msg, types.DiagnosticSeverity.Warning))
    for scenario, sstart, send in zip(
        suite.scenarios, scenario_lines, scenario_lines[1:] + [len(lines)]
    ):
        try:
            SpecValidator(suite, scenario).validate()
        except ConsistencyError as e:
            line = _locate(lines, str(e), sstart, send)
            diagnostics.append(_diagnostic(lines, line, str(e)))
    return diagnostics


def _publish_diagnostics(ls: LanguageServer, uri: str) -> None:
    doc = ls.workspace.get_text_document(uri)
    try:
        diagnostics = build_diagnostics(doc.source)
    except Exception:
        diagnostics = []
    ls.text_document_publish_diagnostics(
        types.PublishDiagnosticsParams(uri=uri, diagnostics=diagnostics)
    )


@server.feature(types.TEXT_DOCUMENT_DID_OPEN)
def did_open(ls: LanguageServer, params: types.DidOpenTextDocumentParams) -> None:
    _publish_diagnostics(ls, params.text_document.uri)


@server.feature(types.TEXT_DOCUMENT_DID_CHANGE)
def did_change(ls: LanguageServer, params: types.DidChangeTextDocumentParams) -> None:
    _publish_diagnostics(ls, params.text_document.uri)


@server.feature(types.TEXT_DOCUMENT_DID_CLOSE)
def did_close(ls: LanguageServer, params: types.DidCloseTextDocumentParams) -> None:
    ls.text_document_publish_diagnostics(
        types.PublishDiagnosticsParams(uri=params.text_document.uri, diagnostics=[])
    )


_TOKEN_LINE = re.compile(r'^([A-Z][A-Z0-9_]*)\s*:\s*(.+)$')
_STR_LIT    = re.compile(r'^"((?:[^"\\]|\\.)*)"$')

# Tokens that are real grammar terminals but never meant to be typed by a user
_EXCLUDED_TERMINALS = {"STRUCT_END"}

_literal_tokens_cache: dict[str, dict[str, list[str]]] = {}


def _literal_tokens(lang: str) -> dict[str, list[str]]:
    if lang not in _literal_tokens_cache:
        text   = (_RESOURCES / "tokens" / lang / "tokens.lark").read_text()
        result = {}
        for line in text.splitlines():
            m = _TOKEN_LINE.match(line.strip())
            if not m:
                continue
            name, rhs = m.groups()
            literals = []
            for alt in rhs.split("|"):
                lm = _STR_LIT.match(alt.strip())
                if lm is None:
                    literals = None
                    break
                literals.append(lm.group(1).replace('\\"', '"'))
            if literals:
                result[name] = literals
        _literal_tokens_cache[lang] = result
    return _literal_tokens_cache[lang]


def build_completions(text: str, line: int, character: int, lang: str = "en") -> "list[types.CompletionItem]":
    """Suggest the next possible keyword(s) at the given cursor position.
    """
    prefix  = text[:_offset_at(text, line, character)]
    pickles = PicklesToSTS()
    parser  = pickles.load_parser(lang=lang)

    try:
        preprocessed = pickles._preprocess(prefix)
        ip = parser.parse_interactive(preprocessed)
        try:
            for _ in ip.iter_parse():
                pass
        except UnexpectedInput:
            pass
        accepted = ip.accepts()
    except Exception:
        return []

    tokens = _literal_tokens(lang)
    items: list[types.CompletionItem] = []
    seen: set[str] = set()
    for term in accepted:
        if term in _EXCLUDED_TERMINALS:
            continue
        for literal in tokens.get(term, []):
            if literal in seen:
                continue
            seen.add(literal)
            items.append(types.CompletionItem(
                label=literal.strip(), insert_text=literal,
                kind=types.CompletionItemKind.Keyword,
            ))
    return items


def _offset_at(text: str, line: int, character: int) -> int:
    lines = text.splitlines(keepends=True)
    return sum(len(l) for l in lines[:line]) + character


@server.feature(
    types.TEXT_DOCUMENT_COMPLETION,
    types.CompletionOptions(trigger_characters=[" "]),
)
def completion(ls: LanguageServer, params: types.CompletionParams) -> "list[types.CompletionItem]":
    doc = ls.workspace.get_text_document(params.text_document.uri)
    return build_completions(doc.source, params.position.line, params.position.character)


if __name__ == "__main__":
    server.start_io()
