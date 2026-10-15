"""Minimal F821-style check: names used but never bound (catches deleted assignments)."""
import ast, builtins, sys
from pathlib import Path

failures = []
for path in sorted(Path("skills/openharmony-ci-orchestrator/scripts").glob("*.py")):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bound = set(dir(builtins)) | {"__name__", "__file__", "annotations", "self", "cls"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                bound.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = node.args
                for a in [*args.args, *args.posonlyargs, *args.kwonlyargs]:
                    bound.add(a.arg)
                if args.vararg: bound.add(args.vararg.arg)
                if args.kwarg: bound.add(args.kwarg.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            pass
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
    # comprehension / for targets
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.AsyncFor)):
            for n in ast.walk(node.target):
                if isinstance(n, ast.Name): bound.add(n.id)
        if isinstance(node, ast.withitem) and node.optional_vars is not None:
            for n in ast.walk(node.optional_vars):
                if isinstance(n, ast.Name): bound.add(n.id)
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    missing = sorted(used - bound)
    if missing:
        failures.append(f"{path.name}: possibly-undefined {missing}")
print("\n".join(failures) if failures else "NAMES OK")
sys.exit(1 if failures else 0)
