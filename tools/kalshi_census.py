"""Kalshi coupling/contract census - Kalshi Integration Phase A, Task A0
(docs/superpowers/plans/2026-08-24-kalshi-integration-phase-a.md).

Gives every later Phase A/C task a mechanical, reproducible blast radius
for the migration instead of relying on memory or ad hoc grep: where the
official `kalshi_python_async` SDK is imported directly, where a raw Kalshi
host string appears, where the current legacy wrapper classes
(`KalshiClient`/`KalshiAccountClient`/`KalshiTradeWebSocketClient`) are
imported/constructed/called, which known REST-vs-WS alias/deprecated
fields (docs/superpowers/research/2026-08-24-kalshi-integration-audit.md's
Finding D) are read directly by application code, which
tests/fixtures/kalshi/*.json fixtures point at a real local doc, and a
curated hot/cold classification for the entry points already known to sit
on (or off) the exchange-wide trade/ticker stream's hot path.

Deliberately narrow on "known field reads": generic overloaded key names
like plain "ticker" are excluded from `_KNOWN_FIELD_NAMES` on purpose -
measured on this repo at 166 production call sites, overwhelmingly
internal/non-Kalshi usage (e.g. a signal_log DB row's own "ticker"
column), so counting them here would fabricate semantic certainty this
static a scan cannot actually back up (A0's own instruction: "keep
uncertain findings informational rather than inventing semantic
certainty"). Only the specific alias/deprecated field names the audit
named as real, already-proven-buggy REST-vs-WS splits are tracked.

`hot_cold_classification` is a curated table, not something this scanner
can derive - "does this handler run on the exchange-wide stream" isn't a
static-analysis question. Each entry names a module + top-level/method
symbol; `verified` records whether that symbol still exists in the named
module at scan time, so a future rename doesn't leave a silently stale
classification.

No network access, no live application state - pure filesystem/AST
analysis, same posture as tools/project_manifest.py and
tools/quality_audit/*.
"""
from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from tools.quality_audit import source

_SDK_MODULE = "kalshi_python_async"

# Files where a direct kalshi_python_async import or a raw Kalshi host
# string is the current, deliberate architecture (services/kalshi_client.py
# and services/kalshi_account_client.py wrap the SDK; services/kalshi_trade_ws.py
# owns the WS host constants; services/http_client.py is the shared transport
# layer; tools/kalshi_public_canary.py is the scheduled/manual live canary -
# see docs/superpowers/research/2026-08-24-kalshi-integration-audit.md's
# "Current strengths to preserve" #1-3). Not an allowlist that suppresses a
# finding - every site is still recorded - just the flag future tasks (A15's
# CI boundary ratchet) need to tell "known integration seam" apart from "new
# leak outside services/kalshi/".
_APPROVED_INTEGRATION_FILES = frozenset({
    "services/kalshi_client.py",
    "services/kalshi_account_client.py",
    "services/kalshi_trade_ws.py",
    "services/http_client.py",
    "tools/kalshi_public_canary.py",
    # docs-mirror sync tooling (A2) references docs.kalshi.com - the
    # documentation host, not the trading API - as part of the same
    # documentation-authority layer the canary belongs to.
    "tools/kalshi_docs_sync.py",
})

# The integration boundary package itself (A4+) is approved by definition -
# it's the one place SDK/vendor access is *supposed* to live, so the whole
# services/kalshi/ prefix is approved rather than each new module needing
# to be enumerated here as Phase A adds them.
_APPROVED_INTEGRATION_PREFIX = "services/kalshi/"

# module dotted path -> class name, for the three legacy wrapper classes
# services/kalshi/ (A4+) is meant to replace behind a compatibility facade.
_LEGACY_WRAPPER_MODULES = {
    "services.kalshi_client": "KalshiClient",
    "services.kalshi_account_client": "KalshiAccountClient",
    "services.kalshi_trade_ws": "KalshiTradeWebSocketClient",
}
# KalshiAccountClient is excluded from the CLASS-name set since C8: the
# class survived as the boundary-owned composing connection
# (services/kalshi/account_client.py), so constructing it is final
# architecture, not legacy usage. Its old MODULE path above stays listed -
# importing services.kalshi_account_client is still a legacy signal.
_LEGACY_WRAPPER_CLASS_NAMES = frozenset(_LEGACY_WRAPPER_MODULES.values()) - {"KalshiAccountClient"}

# The two names services/quality_audit/api_usage.py's own inventory already
# tracks (client/account), plus the two long-lived KalshiTradeWebSocketClient
# singletons from services/app_state.py (trade_stream/index_stream) - this
# census's wrapper-call inventory intentionally covers the WS receivers too,
# which is why it isn't simply reusing that scanner's narrower set.
_WRAPPER_METHOD_RECEIVER_NAMES = frozenset({"client", "account", "trade_stream", "index_stream"})

# See module docstring: only the specific REST-vs-WS alias/deprecated field
# names docs/superpowers/research/2026-08-24-kalshi-integration-audit.md's
# Finding D named as real, already-buggy splits.
_KNOWN_FIELD_NAMES = frozenset({
    "taker_side", "taker_outcome_side", "taker_book_side",
    "market_ticker", "fill_id", "trade_id", "count_fp",
})

# Built from parts rather than one contiguous literal so this scanner's own
# detection token isn't itself an ast.Constant string containing the
# pattern it looks for (it would otherwise self-report tools/kalshi_census.py
# as a "direct host usage" site - confirmed by running this against the real
# repo before this line existed). Substring match covers both .com and demo
# .co hosts.
_KALSHI_HOST_TOKEN = "kalshi" + ".co"


def _relpath(repo_root: Path, path: Path) -> str:
    return source.relative_path(repo_root, path).replace("\\", "/")


def _is_approved(repo_root: Path, path: Path) -> bool:
    rel = _relpath(repo_root, path)
    return rel in _APPROVED_INTEGRATION_FILES or rel.startswith(_APPROVED_INTEGRATION_PREFIX)


def _site(repo_root: Path, path: Path, lineno: int, **extra) -> dict:
    entry = {"file": _relpath(repo_root, path), "line": lineno}
    entry.update(extra)
    return entry


# --- scanners -----------------------------------------------------------------


def _scan_sdk_imports(repo_root: Path) -> list[dict]:
    sites: list[dict] = []
    for path in source.iter_python_files(repo_root, include_tests=False):
        tree = source.parse_python(path)
        for node in ast.walk(tree):
            imported = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == _SDK_MODULE or alias.name.startswith(_SDK_MODULE + "."):
                        imported = alias.name
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module == _SDK_MODULE or node.module.startswith(_SDK_MODULE + "."):
                    imported = node.module
            if imported is not None:
                sites.append(_site(
                    repo_root, path, node.lineno,
                    imported=imported,
                    inside_approved_integration_file=_is_approved(repo_root, path),
                ))
    return sites


def _scan_direct_host_usage(repo_root: Path) -> list[dict]:
    sites: list[dict] = []
    for path in source.iter_python_files(repo_root, include_tests=False):
        tree = source.parse_python(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and _KALSHI_HOST_TOKEN in node.value.lower():
                sites.append(_site(
                    repo_root, path, node.lineno,
                    snippet=node.value[:120],
                    inside_approved_integration_file=_is_approved(repo_root, path),
                ))
    return sites


def _scan_legacy_wrapper(repo_root: Path) -> dict:
    imports: list[dict] = []
    constructions: list[dict] = []
    for path in source.iter_python_files(repo_root, include_tests=False):
        tree = source.parse_python(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in _LEGACY_WRAPPER_MODULES:
                expected = _LEGACY_WRAPPER_MODULES[node.module]
                for alias in node.names:
                    if alias.name == expected:
                        imports.append(_site(repo_root, path, node.lineno, module=node.module, symbol=alias.name))
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in _LEGACY_WRAPPER_CLASS_NAMES
            ):
                constructions.append(_site(repo_root, path, node.lineno, symbol=node.func.id))
    return {"imports": imports, "constructions": constructions}


def _scan_wrapper_method_calls(repo_root: Path) -> dict[str, list[str]]:
    sites: dict[str, list[str]] = defaultdict(list)
    for path in source.iter_python_files(repo_root, include_tests=False):
        tree = source.parse_python(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            receiver = node.func.value
            if not isinstance(receiver, ast.Name) or receiver.id not in _WRAPPER_METHOD_RECEIVER_NAMES:
                continue
            key = f"{receiver.id}.{node.func.attr}"
            sites[key].append(f"{_relpath(repo_root, path)}:{node.lineno}")
    return dict(sorted(sites.items()))


def _string_key(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _scan_known_field_reads(repo_root: Path) -> dict[str, list[str]]:
    sites: dict[str, list[str]] = defaultdict(list)
    for path in source.iter_python_files(repo_root, include_tests=False):
        tree = source.parse_python(path)
        for node in ast.walk(tree):
            field = None
            if isinstance(node, ast.Subscript):
                field = _string_key(node.slice)
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and node.args
            ):
                field = _string_key(node.args[0])
            if field in _KNOWN_FIELD_NAMES:
                sites[field].append(f"{_relpath(repo_root, path)}:{node.lineno}")
    return dict(sorted(sites.items()))


def _scan_fixtures(repo_root: Path) -> list[dict]:
    """tests/fixtures/kalshi/*.json's own convention (see e.g. market.json,
    public_trade_no_deprecated_side.json): _meta.source_doc is sometimes a
    single path and sometimes a comma-separated list of paths when a
    fixture draws fields from more than one doc page. Split on comma and
    require every referenced path to exist - a partial reference is still
    a real doc-authority gap."""
    fixtures_dir = repo_root / "tests" / "fixtures" / "kalshi"
    entries: list[dict] = []
    if not fixtures_dir.is_dir():
        return entries
    for path in sorted(fixtures_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            entries.append({
                "file": _relpath(repo_root, path), "source_doc": None,
                "source_docs": [], "source_doc_exists": False,
            })
            continue
        source_doc = (data.get("_meta") or {}).get("source_doc")
        source_docs = [part.strip() for part in source_doc.split(",")] if source_doc else []
        exists = bool(source_docs) and all((repo_root / doc).exists() for doc in source_docs)
        entries.append({
            "file": _relpath(repo_root, path), "source_doc": source_doc,
            "source_docs": source_docs, "source_doc_exists": exists,
        })
    return entries


# --- hot/cold classification ----------------------------------------------------

# See module docstring for why this is curated rather than derived. Grounded
# directly against current HEAD's real symbol names, not invented - each
# module/symbol pair was confirmed to exist via grep before being added here.
DEFAULT_HOT_COLD_TABLE: tuple[dict, ...] = (
    {
        # Moved behind the boundary at A11 (services/kalshi/websocket.py);
        # services.kalshi_trade_ws remains only a subclass facade.
        "module": "services.kalshi.websocket", "symbol": "KalshiStreamGateway._handle_message",
        "classification": "hot",
        "reason": "Dispatches every message on the exchange-wide WS connection (trade_stream_exchange_wide: true).",
    },
    {
        "module": "services.whale_stream.whale_stream_handlers", "symbol": "_process_stream_trade",
        "classification": "hot", "reason": "Runs per public trade on the exchange-wide stream.",
    },
    {
        "module": "services.whale_stream.whale_stream_handlers", "symbol": "_process_stream_ticker",
        "classification": "hot", "reason": "Runs per ticker update on the exchange-wide stream.",
    },
    {
        "module": "services.whale_stream.whale_stream_handlers", "symbol": "_process_stream_fill",
        "classification": "cold",
        "reason": "Only fires on a real account fill; kalshi_account.trading_enabled is false by default.",
    },
    {
        "module": "services.whale_stream.whale_stream_handlers", "symbol": "_process_stream_position",
        "classification": "cold",
        "reason": "Only fires on a real account position update; kalshi_account.trading_enabled is false by default.",
    },
    {
        "module": "services.whale_stream.whale_stream_handlers", "symbol": "_process_stream_lifecycle",
        "classification": "cold", "reason": "Per-market lifecycle transitions are infrequent relative to trade volume.",
    },
    {
        "module": "services.series_watcher", "symbol": "record_trade",
        "classification": "hot", "reason": "Called from _process_stream_trade for every captured trade.",
    },
    {
        "module": "services.series_watcher", "symbol": "record_book",
        "classification": "hot", "reason": "Called from _process_stream_ticker for every captured ticker update.",
    },
    {
        # Moved behind the boundary at A13; the provider's _taker_side is
        # now a same-object alias of this function, so this is the symbol
        # that actually runs per trade.
        "module": "services.kalshi.contracts.trade", "symbol": "resolve_taker_outcome_side",
        "classification": "hot", "reason": "Called per trade to classify direction on the whale-detection path.",
    },
    {
        "module": "services.market_watch.catalog_scan", "symbol": "_maybe_scan_catalog_batch",
        "classification": "cold", "reason": "Runs on the periodic poll_interval_sec catalog-scan scheduler, not the WS stream.",
    },
    {
        "module": "services.market_watch.discovery_cache", "symbol": "_maybe_refresh_discovery_cache",
        "classification": "cold", "reason": "Runs on a periodic cache-refresh scheduler, not the WS stream.",
    },
)


def _symbol_exists(repo_root: Path, module: str, symbol: str) -> bool:
    file_path = repo_root / (module.replace(".", "/") + ".py")
    if not file_path.exists():
        return False
    tree = source.parse_python(file_path)
    target = symbol.rsplit(".", 1)[-1]
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == target
        for node in ast.walk(tree)
    )


def _classify_hot_cold(repo_root: Path, table: tuple[dict, ...]) -> list[dict]:
    return [
        {**entry, "verified": _symbol_exists(repo_root, entry["module"], entry["symbol"])}
        for entry in table
    ]


# --- git provenance (best-effort, never required) --------------------------------


def _git_head(repo_root: Path) -> str | None:
    if not (repo_root / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


# --- assembly -------------------------------------------------------------------


def build_census(repo_root: Path, hot_cold_table: tuple[dict, ...] = DEFAULT_HOT_COLD_TABLE) -> dict:
    repo_root = Path(repo_root)

    sdk_imports = _scan_sdk_imports(repo_root)
    direct_host_usage = _scan_direct_host_usage(repo_root)
    legacy_wrapper = _scan_legacy_wrapper(repo_root)
    wrapper_method_calls = _scan_wrapper_method_calls(repo_root)
    known_field_reads = _scan_known_field_reads(repo_root)
    fixtures = _scan_fixtures(repo_root)
    hot_cold_classification = _classify_hot_cold(repo_root, hot_cold_table)

    direct_access_count = sum(
        1 for site in (sdk_imports + direct_host_usage) if not site["inside_approved_integration_file"]
    )
    legacy_caller_count = len({site["file"] for site in legacy_wrapper["imports"]})

    return {
        "schema_version": 1,
        "generated_from_head": _git_head(repo_root),
        "definitions": {
            "direct_access_count": (
                "Count of sdk_imports + direct_host_usage sites where "
                "inside_approved_integration_file is false - i.e. genuine boundary leaks "
                "outside the files the current architecture already treats as the Kalshi seam."
            ),
            "legacy_caller_count": (
                "Count of distinct production files importing KalshiClient/"
                "KalshiAccountClient/KalshiTradeWebSocketClient from their current legacy "
                "module locations - the Phase A compatibility-facade caller count "
                "(design spec's 'Compatibility facades' rule 1: captured at Phase A start)."
            ),
            "approved_integration_files": sorted(_APPROVED_INTEGRATION_FILES),
        },
        "sdk_imports": sdk_imports,
        "direct_host_usage": direct_host_usage,
        "legacy_wrapper": legacy_wrapper,
        "wrapper_method_calls": wrapper_method_calls,
        "known_field_reads": known_field_reads,
        "fixtures": fixtures,
        "hot_cold_classification": hot_cold_classification,
        "counts": {
            "sdk_import_sites": len(sdk_imports),
            "direct_host_usage_sites": len(direct_host_usage),
            "legacy_wrapper_import_sites": len(legacy_wrapper["imports"]),
            "legacy_wrapper_construction_sites": len(legacy_wrapper["constructions"]),
            "wrapper_method_call_sites": sum(len(v) for v in wrapper_method_calls.values()),
            "known_field_read_sites": sum(len(v) for v in known_field_reads.values()),
            "fixtures_total": len(fixtures),
            "fixtures_missing_source_doc": sum(1 for f in fixtures if not f["source_doc_exists"]),
            "direct_access_count": direct_access_count,
            "legacy_caller_count": legacy_caller_count,
        },
    }


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m tools.kalshi_census")
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--json-out", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    repo_root = args.repo_root.resolve()
    census = build_census(repo_root)

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(census, indent=2) + "\n")
        print(f"kalshi-census: wrote {args.json_out}")
    else:
        print(json.dumps(census, indent=2))

    print(
        f"kalshi-census: legacy_caller_count={census['counts']['legacy_caller_count']} "
        f"direct_access_count={census['counts']['direct_access_count']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
