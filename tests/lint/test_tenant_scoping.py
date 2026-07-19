import ast
import pytest
import src.db.models as models_module
from sqlalchemy import inspect as sa_inspect
from pathlib import Path


def tenant_scoped_model_names() -> set[str]:
    scoped = set()

    for name, obj in vars(models_module).items():
        if not isinstance(obj, type):
            continue
        if not issubclass(obj, models_module.Base) or obj is models_module.Base:
            continue

        mapper = sa_inspect(obj)
        if "tenant_id" in mapper.columns.keys():
            scoped.add(name)

    return scoped


TENANT_SCOPED_MODELS = tenant_scoped_model_names()


def is_query_call(call: ast.Call) -> str | None:
    func = call.func

    if isinstance(func, ast.Name):
        func_name = func.id
    elif isinstance(func, ast.Attribute):
        func_name = func.attr
    else:
        return None

    if func_name in {"select", "query", "get"}:
        return func_name

    return None


def referenced_model_names(node: ast.AST) -> set[str]:
    return {
        n.id
        for n in ast.walk(node)
        if isinstance(n, ast.Name) and n.id in TENANT_SCOPED_MODELS
    }


def size(node: ast.AST) -> int:
    return sum(1 for _ in ast.walk(node))


def enclosing_statement(tree: ast.AST, target: ast.AST) -> ast.stmt:
    best = None

    for node in ast.walk(tree):
        if not isinstance(node, ast.stmt):
            continue
        if any(n is target for n in ast.walk(node)):
            if best is None or size(node) < size(best):
                best = node

    return best or target


def find_violations(path: Path) -> list[str]:
    source = path.read_text()
    tree = ast.parse(source, filename=str(path))

    violations = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        query_type = is_query_call(node)
        if query_type is None:
            continue

        if not node.args:
            continue

        models = set()
        for arg in node.args:
            models |= referenced_model_names(arg)
        if not models:
            continue

        if query_type == "get":
            violations.append(
                f"{path}:{node.lineno}: session.get({', '.join(models)}, ...) "
                f"cannot be tenant-scoped"
            )
            continue

        stmt = enclosing_statement(tree, node)
        stmt_source = ast.get_source_segment(source, stmt) or ""

        if "tenant_id" not in stmt_source:
            violations.append(
                f"{path}:{node.lineno}: query on {', '.join(models)} "
                f"has no tenant_id filter:\n    {stmt_source.strip()}"
            )

    return violations


SRC_ROOT = Path(__file__).resolve().parents[2] / "src"


def python_files() -> list[Path]:
    return sorted(p for p in SRC_ROOT.rglob("*.py") if p.name != "__init__.py")


@pytest.mark.parametrize(
    "path", python_files(), ids=lambda p: str(p.relative_to(SRC_ROOT))
)
def test_no_unscoped_tenant_queries(path: Path):
    violations = find_violations(path)
    assert not violations, "\n\n".join(violations)
