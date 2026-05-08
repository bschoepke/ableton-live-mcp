from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import shutil
import sys
import types
from pathlib import Path
from typing import Any

from ableton_paths import remote_scripts_dir


DEFAULT_REMOTE_SCRIPT = "Ableton_Live_MCP"


def _resource_root() -> Path | None:
    script = Path(__file__).resolve().parent / DEFAULT_REMOTE_SCRIPT
    return script.parent if script.is_dir() else None


def _source_root() -> Path | None:
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / DEFAULT_REMOTE_SCRIPT
    return script.parent if script.is_dir() else None


def remote_script_root() -> Path:
    for root in (_resource_root(), _source_root()):
        if root is not None:
            return root
    raise FileNotFoundError("Could not find packaged Ableton Remote Scripts")


def default_install_dir() -> Path:
    return remote_scripts_dir()


def install_remote_script(name: str = DEFAULT_REMOTE_SCRIPT, target_dir: Path | None = None, force: bool = False) -> Path:
    if name != DEFAULT_REMOTE_SCRIPT:
        raise ValueError("Unknown Remote Script %r. Expected: %s" % (name, DEFAULT_REMOTE_SCRIPT))
    source = remote_script_root() / name
    if not source.is_dir():
        raise FileNotFoundError("Remote Script %s is not available at %s" % (name, source))
    target_base = target_dir or default_install_dir()
    target = target_base / name
    if target.exists():
        if not force:
            raise FileExistsError("%s already exists; pass --force to replace it" % target)
        if target.is_symlink() or target.is_file():
            target.unlink()
        else:
            shutil.rmtree(str(target))
    target_base.mkdir(parents=True, exist_ok=True)
    shutil.copytree(str(source), str(target), ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
    return target


def remote_script_status(name: str = DEFAULT_REMOTE_SCRIPT, target_dir: Path | None = None) -> dict[str, Any]:
    if name != DEFAULT_REMOTE_SCRIPT:
        raise ValueError("Unknown Remote Script %r. Expected: %s" % (name, DEFAULT_REMOTE_SCRIPT))
    source = remote_script_root() / name
    target = (target_dir or default_install_dir()) / name
    source_hashes = _file_hashes(source)
    target_hashes = _file_hashes(target) if target.is_dir() else {}
    missing = sorted(path for path in source_hashes if path not in target_hashes)
    mismatched = sorted(path for path, digest in source_hashes.items() if target_hashes.get(path) not in (None, digest))
    current = bool(target.is_dir()) and not missing and not mismatched
    return {
        "name": name,
        "source": str(source),
        "target": str(target),
        "installed": target.is_dir(),
        "current": current,
        "files_checked": len(source_hashes),
        "source_bridge_sha256": source_hashes.get("bridge.py"),
        "target_bridge_sha256": target_hashes.get("bridge.py"),
        "source_runtime_version": _runtime_version(source / "bridge.py"),
        "target_runtime_version": _runtime_version(target / "bridge.py") if target.is_dir() else "",
        "source_runtime_code_sha256": _runtime_code_fingerprint(source / "bridge.py"),
        "target_runtime_code_sha256": _runtime_code_fingerprint(target / "bridge.py") if target.is_dir() else "",
        "missing": missing,
        "mismatched": mismatched,
    }


def _file_hashes(root: Path) -> dict[str, str]:
    if not root.is_dir():
        return {}
    hashes = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or _ignored_file(path):
            continue
        relative = path.relative_to(root).as_posix()
        hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def _ignored_file(path: Path) -> bool:
    return "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}


def _runtime_version(path: Path) -> str:
    try:
        source = path.read_text(encoding="utf-8")
    except Exception:
        return ""
    match = re.search(r"^REMOTE_SCRIPT_RUNTIME_VERSION\s*=\s*['\"]([^'\"]+)['\"]", source, re.M)
    return match.group(1) if match else ""


def _runtime_code_fingerprint(path: Path) -> str:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        compiled = compile(source, str(path), "exec", dont_inherit=True)
    except Exception:
        return ""
    payload = {
        "constants": _source_constant_signatures(tree),
        "functions": _source_function_signatures(compiled, _top_level_function_names(tree)),
        "methods": _source_method_signatures(compiled, tree, "AbletonLiveMCP"),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _source_constant_signatures(tree: ast.Module) -> list[tuple[str, str]]:
    items = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id.isupper():
                try:
                    value = ast.literal_eval(node.value)
                except Exception:
                    try:
                        value = eval(compile(ast.Expression(node.value), "<runtime-constant>", "eval", dont_inherit=True), {"__builtins__": {}}, {})
                    except Exception:
                        continue
                if isinstance(value, (str, int, float, bool, tuple, list)):
                    items.append((target.id, repr(value)))
    return sorted(items)


def _top_level_function_names(tree: ast.Module) -> set[str]:
    return {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}


def _source_function_signatures(code: types.CodeType, names: set[str]) -> list[tuple[str, dict]]:
    items = []
    for child in _child_code_objects(code):
        if child.co_name in names:
            items.append((child.co_name, _code_signature(child)))
    return sorted(items)


def _source_method_signatures(code: types.CodeType, tree: ast.Module, class_name: str) -> list[tuple[str, dict]]:
    method_names = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            method_names = {item.name for item in node.body if isinstance(item, ast.FunctionDef)}
            break
    class_code = next((child for child in _child_code_objects(code) if child.co_name == class_name), None)
    if class_code is None:
        return []
    return _source_function_signatures(class_code, method_names)


def _child_code_objects(code: types.CodeType) -> list[types.CodeType]:
    return [value for value in code.co_consts if isinstance(value, types.CodeType)]


def _code_signature(code: types.CodeType) -> dict[str, Any]:
    return {
        "argcount": code.co_argcount,
        "code": hashlib.sha256(code.co_code).hexdigest(),
        "consts": [_constant_signature(value) for value in code.co_consts],
        "flags": code.co_flags,
        "names": list(code.co_names),
        "varnames": list(code.co_varnames),
    }


def _constant_signature(value: Any) -> Any:
    if isinstance(value, types.CodeType):
        return _code_signature(value)
    return repr(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install the Ableton Live MCP Remote Script into the Ableton User Library.")
    parser.add_argument("--name", choices=(DEFAULT_REMOTE_SCRIPT,), default=DEFAULT_REMOTE_SCRIPT, help="Remote Script package to install. Default: %(default)s")
    parser.add_argument("--target-dir", type=Path, default=default_install_dir(), help="Ableton Remote Scripts directory. Default: %(default)s")
    parser.add_argument("--force", action="store_true", help="Replace an existing installed Remote Script directory.")
    parser.add_argument("--update", action="store_true", help="Install if missing or stale; leave a current install untouched.")
    parser.add_argument("--list", action="store_true", help="List packaged Remote Scripts and exit.")
    args = parser.parse_args(argv)

    if args.list:
        print(DEFAULT_REMOTE_SCRIPT)
        return 0

    try:
        if args.update:
            status = remote_script_status(args.name, args.target_dir)
            if status["current"]:
                print("Remote Script already current: %s" % status["target"])
                return 0
            target = install_remote_script(args.name, args.target_dir, force=True)
        else:
            target = install_remote_script(args.name, args.target_dir, args.force)
    except Exception as exc:
        print("Remote Script install failed: %s" % exc, file=sys.stderr)
        return 1
    print("Installed %s" % target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
