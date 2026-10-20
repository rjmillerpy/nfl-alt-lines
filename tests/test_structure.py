"""Cross-cutting checks for CODE_QUALITY_RULES.md rules that ruff and mypy can't express.

Rules 1 and 56 (function, method, class and module size), 6/42 (nesting), 24 (nested loops),
15/56 (module size) and 48/52 (every source file has a mirrored test file).
"""

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Each package and the folder that mirrors its tests (the private model package is only in the private repo).
PACKAGES = {PROJECT_ROOT / "nfl_alt_lines": PROJECT_ROOT / "tests"}
MAX_MODULE_LINES = 400
MAX_CLASS_LINES = 200
MAX_FUNCTION_LINES = 50
MAX_METHOD_LINES = 30
MAX_NESTING = 3
MAX_LOOP_NESTING = 2

SOURCE_FILES = sorted(
    path for package in PACKAGES if package.is_dir() for path in package.rglob("*.py") if path.name != "__init__.py"
)
BLOCKS = (ast.If, ast.For, ast.While, ast.With, ast.Try, ast.Match)
LOOPS = (ast.For, ast.While, ast.comprehension)
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _relative_name(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))


def _parse(source: Path) -> ast.Module:
    return ast.parse(source.read_text(), filename=str(source))


Definition = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef


def _length(node: Definition) -> int:
    """Lines from the definition to its last line, decorators and docstring included."""
    first = min([node.lineno, *(decorator.lineno for decorator in node.decorator_list)])
    last = node.end_lineno if node.end_lineno is not None else node.lineno
    return last - first + 1


def _functions(tree: ast.Module) -> Iterator[tuple[ast.FunctionDef | ast.AsyncFunctionDef, bool]]:
    """Every function with whether it is a method (defined directly in a class body)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            yield from ((child, True) for child in node.body if isinstance(child, FUNCTIONS))
    methods = {id(child) for node in ast.walk(tree) if isinstance(node, ast.ClassDef) for child in node.body}
    yield from ((node, False) for node in ast.walk(tree) if isinstance(node, FUNCTIONS) and id(node) not in methods)


def _depth(node: ast.AST, kinds: tuple[type, ...]) -> int:
    """Deepest chain of `kinds` nested inside `node`, not counting nested function definitions."""
    deepest = 0
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (*FUNCTIONS, ast.ClassDef, ast.Lambda)):
            continue
        inner = _depth(child, kinds)
        is_kind = isinstance(child, kinds) and not (isinstance(child, ast.If) and _is_elif(node, child))
        deepest = max(deepest, inner + 1 if is_kind else inner)
    return deepest


def _is_elif(parent: ast.AST, child: ast.If) -> bool:
    return isinstance(parent, ast.If) and parent.orelse == [child]


@pytest.mark.parametrize("source", SOURCE_FILES, ids=_relative_name)
def test_source_file_has_mirrored_test(source: Path) -> None:
    package = next(package for package in PACKAGES if source.is_relative_to(package))
    relative = source.relative_to(package)
    expected = PACKAGES[package] / relative.parent / f"test_{relative.name}"
    assert expected.is_file(), f"missing {_relative_name(expected)}"


@pytest.mark.parametrize("source", SOURCE_FILES, ids=_relative_name)
def test_module_stays_under_line_limit(source: Path) -> None:
    assert len(source.read_text().splitlines()) <= MAX_MODULE_LINES


@pytest.mark.parametrize("source", SOURCE_FILES, ids=_relative_name)
def test_classes_stay_under_line_limit(source: Path) -> None:
    classes = [node for node in ast.walk(_parse(source)) if isinstance(node, ast.ClassDef)]
    too_long = [f"{node.name} ({_length(node)} lines)" for node in classes if _length(node) > MAX_CLASS_LINES]
    assert not too_long


@pytest.mark.parametrize("source", SOURCE_FILES, ids=_relative_name)
def test_functions_stay_small(source: Path) -> None:
    too_long = [
        f"{node.name} ({_length(node)} lines)"
        for node, is_method in _functions(_parse(source))
        if _length(node) > (MAX_METHOD_LINES if is_method else MAX_FUNCTION_LINES)
    ]
    assert not too_long


@pytest.mark.parametrize("source", SOURCE_FILES, ids=_relative_name)
def test_nesting_stays_shallow(source: Path) -> None:
    functions = [node for node, _ in _functions(_parse(source))]
    too_deep = [node.name for node in functions if _depth(node, BLOCKS) > MAX_NESTING]
    too_many_loops = [node.name for node in functions if _depth(node, LOOPS) > MAX_LOOP_NESTING]
    assert not too_deep, "more than 3 nested blocks"
    assert not too_many_loops, "more than 2 nested loops"


def test_package_markers_stay_empty() -> None:
    """__init__.py files are exempt from the mirror rule only while they hold no code."""
    markers = [marker for package in PACKAGES if package.is_dir() for marker in package.rglob("__init__.py")]
    assert markers and all(marker.read_text().strip() == "" for marker in markers)
