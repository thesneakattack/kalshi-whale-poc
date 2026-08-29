"""The MCP/plugin surface is config, not code - so nothing else proves it stays true.

CLAUDE.md pins GitNexus to an exact version because an in-place upgrade under a
running MCP server breaks every query until sessions restart (2026-08-28). That
pin only means something if the server the harness actually launches carries it.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIN = re.compile(r"gitnexus@(\d+\.\d+\.\d+)")


def _mcp() -> dict:
    return json.loads((ROOT / ".mcp.json").read_text())


def test_gitnexus_mcp_server_is_declared_at_the_pinned_version():
    server = _mcp()["mcpServers"]["gitnexus"]
    spec = " ".join(server.get("args", []))
    assert "@latest" not in spec, "an in-place upgrade is what corrupted the index on 2026-08-28"
    found = PIN.search(spec)
    assert found, f"gitnexus must be launched at an exact version, got: {spec}"


def test_the_mcp_pin_matches_the_pin_claude_md_states():
    """Two places state the version; they drift silently if nothing compares them."""
    declared = PIN.search(" ".join(_mcp()["mcpServers"]["gitnexus"]["args"])).group(1)
    documented = {m.group(1) for m in PIN.finditer((ROOT / "CLAUDE.md").read_text())}
    assert documented == {declared}, f"CLAUDE.md pins {documented}, .mcp.json launches {declared}"


def test_no_mcp_server_carries_an_inline_credential():
    """Project MCP config is committed - a token here would be a leaked secret."""
    for name, server in _mcp()["mcpServers"].items():
        blob = json.dumps(server)
        for marker in ("Bearer ", "ghp_", "github_pat_", "sk-"):
            assert marker not in blob, f"{name} carries what looks like an inline credential"


def test_enabled_plugins_are_all_actually_reachable():
    """CLAUDE.md records 42crunch and second-opinion as blocked (no account); an
    enabled-but-dead plugin advertises skills that always fail when invoked."""
    settings = json.loads((ROOT / ".claude/settings.json").read_text())
    enabled = set(settings.get("enabledPlugins", {}))
    blocked = {p for p in enabled if p.split("@")[0] in {"second-opinion", "42crunch-api-security-testing"}}
    assert not blocked, f"enabled but unreachable (no account): {sorted(blocked)}"


def test_every_enabled_plugin_has_a_known_marketplace():
    settings = json.loads((ROOT / ".claude/settings.json").read_text())
    known = set(settings.get("extraKnownMarketplaces", {})) | {"claude-plugins-official"}
    for plugin in settings.get("enabledPlugins", {}):
        assert plugin.split("@")[-1] in known, f"{plugin} has no marketplace entry"
