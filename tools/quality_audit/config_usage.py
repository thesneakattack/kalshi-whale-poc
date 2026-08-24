"""Config-usage scanner for the Quality Control Plane's static audit CLI
(docs/superpowers/plans/2026-08-24-quality-control-plane.md, Task 5). Flags
a config/settings.yaml leaf that no non-test source file appears to read
via a direct `cfg["a"]["b"]` or `cfg.get("a", ...).get("b", ...)` chain
(or a mix of the two) rooted at a variable literally named `cfg` - the
codebase's overwhelmingly dominant convention for the loaded config dict
(`cfg = config_store.get()`, `def _maybe_foo(cfg: dict)`, etc.).

v1 only follows a single unbroken chain expression: it does NOT track
values read through an intermediate variable
(`backup_cfg = cfg.get("backup") or {}`, then `backup_cfg.get("enabled")`
elsewhere - a genuinely common pattern in this codebase, e.g.
services/backup/backup.py's `_maybe_run_backup`). That means a real,
live-read leaf can still be reported here as "unread" - expected and
accepted per this scanner's severity: warning/medium, never a default CI
failure (.claude/rules/quality-capabilities.md: "heuristic static-analysis
findings are not hard CI failures until their confidence is demonstrated").
Findings are raw material for human triage into baseline.json, not an
automatic verdict that a setting is dead.
"""
from __future__ import annotations

import ast
from pathlib import Path

import yaml

from services.quality.models import QualityFinding
from tools.quality_audit import source

_ROOT_VAR_NAME = "cfg"


def _flatten_leaves(data: dict, prefix: str = "") -> dict[str, object]:
    leaves: dict[str, object] = {}
    for key, value in data.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            leaves.update(_flatten_leaves(value, prefix=f"{path}."))
        else:
            leaves[path] = value
    return leaves


def load_config_leaves(settings_path: Path) -> dict[str, object]:
    data = yaml.safe_load(Path(settings_path).read_text()) or {}
    return _flatten_leaves(data)


def _chain_keys(node: ast.expr) -> tuple[str, list[str]] | None:
    """Walks a subscript/.get() chain from outermost to base, returning
    (base_name, [key1, key2, ...]) in root-to-leaf order, or None if the
    chain doesn't bottom out at a bare Name."""
    keys: list[str] = []
    current = node
    while True:
        if isinstance(current, ast.Subscript):
            key = current.slice
            if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                return None
            keys.append(key.value)
            current = current.value
            continue
        if (
            isinstance(current, ast.Call)
            and isinstance(current.func, ast.Attribute)
            and current.func.attr == "get"
            and current.args
            and isinstance(current.args[0], ast.Constant)
            and isinstance(current.args[0].value, str)
        ):
            keys.append(current.args[0].value)
            current = current.func.value
            continue
        break
    if isinstance(current, ast.Name):
        keys.reverse()
        return current.id, keys
    return None


def _referenced_leaf_paths(repo_root: Path) -> set[str]:
    referenced: set[str] = set()
    for path in source.iter_python_files(repo_root, include_tests=False):
        tree = source.parse_python(path)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Subscript, ast.Call)):
                continue
            result = _chain_keys(node)
            if result is None:
                continue
            base_name, keys = result
            if base_name == _ROOT_VAR_NAME and keys:
                referenced.add(".".join(keys))
    return referenced


def scan_config_usage(repo_root: Path) -> list[QualityFinding]:
    repo_root = Path(repo_root)
    settings_path = repo_root / "config" / "settings.yaml"
    if not settings_path.exists():
        return []

    leaves = load_config_leaves(settings_path)
    referenced = _referenced_leaf_paths(repo_root)

    findings: list[QualityFinding] = []
    for leaf_path in sorted(leaves):
        if leaf_path in referenced:
            continue
        findings.append(
            QualityFinding(
                finding_id=f"config-unread:{leaf_path}",
                check="config-usage",
                severity="warning",
                confidence="medium",
                source="ci",
                scope=leaf_path,
                summary=f"config/settings.yaml's {leaf_path} has no direct cfg[...]/.get(...) read found in source",
                evidence={"path": "config/settings.yaml"},
                remediation=(
                    "confirm this setting is read (possibly via an intermediate variable, which this "
                    "v1 scanner can't follow) or remove it if genuinely unused"
                ),
            )
        )
    return findings
