"""Storage-package naming rule, checked against the AST rather than by pattern.

Clause A (invariant, every step): no underscored name crosses a storage module boundary.
Clause B (postcondition, final step only): no public name lacks a consumer - another
storage module, a re-export in the package __init__, or an importer elsewhere in src.

Blind spot, deliberate and documented: this reads the DECLARED surface, so a public
instance attribute assigned in a method body (``self.path = path``) is invisible to it.
Connection.path is the known example. Coverage is not total.
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import sys

PACKAGE_PREFIX = "mcp_memory.storage."


def module_defs(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def resolve_target(node: ast.ImportFrom, defs: dict[str, set[str]]) -> str | None:
    """Return the storage module an ImportFrom refers to, or None if it points elsewhere."""
    module = node.module or ""
    if node.level > 0:
        candidate = module
    elif module.startswith(PACKAGE_PREFIX):
        candidate = module.removeprefix(PACKAGE_PREFIX)
    else:
        return None
    return candidate if candidate in defs else None


def check_boundary_crossings(
    trees: dict[str, ast.Module], defs: dict[str, set[str]]
) -> tuple[set[tuple[str, str]], list[str]]:
    """Walk every module and report clause A violations, tracking cross-module usage."""
    used: set[tuple[str, str]] = set()
    clause_a: list[str] = []

    for mod, tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                target = resolve_target(node, defs)
                if target is None or target == mod:
                    continue
                for alias in node.names:
                    used.add((target, alias.name))
                    if alias.name.startswith("_"):
                        clause_a.append(f"{mod}.py:{node.lineno}: imports private {alias.name!r} from {target!r}")
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                target = node.value.id
                if target not in defs or target == mod:
                    continue
                used.add((target, node.attr))
                if node.attr.startswith("_"):
                    clause_a.append(f"{mod}.py:{node.lineno}: reaches private {target}.{node.attr}")

    return used, clause_a


def record_package_exports(storage: pathlib.Path, defs: dict[str, set[str]], used: set[tuple[str, str]]) -> None:
    """Mark names re-exported by the package's __init__ as consumed.

    __init__.py is excluded from the checked module set, so without this a deliberate
    re-export looks like a public name that nobody uses.
    """
    init = storage / "__init__.py"
    if not init.is_file():
        return
    for node in ast.walk(ast.parse(init.read_text())):
        if isinstance(node, ast.ImportFrom):
            target = resolve_target(node, defs)
            if target is not None:
                used.update((target, alias.name) for alias in node.names)


def record_outside_consumers(package_root: pathlib.Path, defs: dict[str, set[str]], used: set[tuple[str, str]]) -> None:
    """Mark names the rest of the package imports from storage/ as consumed.

    A name any src consumer outside storage/ reaches must be public, and that rule wins
    over the within-package one. Tests are deliberately not consumers.
    """
    if not (package_root / "__init__.py").is_file():
        return
    storage_dir = package_root / "storage"
    for path in sorted(package_root.rglob("*.py")):
        if storage_dir in path.parents:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = (node.module or "").removeprefix("mcp_memory.")
            if module != "storage" and not module.startswith("storage."):
                continue
            submodule = module.removeprefix("storage").removeprefix(".")
            for alias in node.names:
                if submodule in defs:
                    used.add((submodule, alias.name))
                else:
                    used.update((mod, alias.name) for mod, names in defs.items() if alias.name in names)


def check_unused_public_names(defs: dict[str, set[str]], used: set[tuple[str, str]]) -> list[str]:
    """Clause B: every public name must have at least one cross-module consumer."""
    return sorted(
        f"{mod}.py: {name!r} is public but no other storage module uses it"
        for mod, names in defs.items()
        for name in names
        if not name.startswith("_") and (mod, name) not in used
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("storage", type=pathlib.Path, help="absolute path to storage/")
    parser.add_argument("--final", action="store_true", help="also run clause B (final step only)")
    args = parser.parse_args()

    storage: pathlib.Path = args.storage
    if not storage.is_dir():
        sys.stderr.write(f"FATAL: not a directory: {storage}\n")
        return 2
    paths = sorted(p for p in storage.rglob("*.py") if p.stem != "__init__")
    if not paths:
        sys.stderr.write(f"FATAL: no modules found under {storage}\n")
        return 2

    trees = {p.stem: ast.parse(p.read_text()) for p in paths}
    defs = {m: module_defs(t) for m, t in trees.items()}
    used, clause_a = check_boundary_crossings(trees, defs)

    failures = sorted(clause_a)
    label = "clause A"
    if args.final:
        label = "clauses A and B"
        record_package_exports(storage, defs, used)
        record_outside_consumers(storage.parent, defs, used)
        failures += check_unused_public_names(defs, used)

    if failures:
        sys.stdout.write("\n".join(failures) + "\n")
        sys.stderr.write(f"\n{len(failures)} violation(s)\n")
        return 1
    sys.stdout.write(f"{label}: clean ({len(paths)} modules checked)\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
