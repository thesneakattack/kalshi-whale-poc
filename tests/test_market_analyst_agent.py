import asyncio
import time
from types import SimpleNamespace

import pytest

from services import market_analyst_agent as maa


def _agent(tmp_path, monkeypatch):
    monkeypatch.setattr(maa, "DB_PATH", tmp_path / "market_analyst.db")
    return maa


def test_record_analysis_and_recent_round_trip(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    agent.record_analysis(
        ticker="TICK-A", series="TICK", market_price=0.6, estimated_probability=0.75,
        llm_confidence=0.8, reasoning="Rules favor yes given recent context.", model="claude-sonnet-5",
    )
    rows = agent.recent(limit=10)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "TICK-A"
    assert rows[0]["estimated_probability"] == 0.75
    assert rows[0]["resolved"] == 0
    assert agent.total_count() == 1


def test_recent_returns_newest_first(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    agent.record_analysis("TICK-A", "TICK", 0.5, 0.6, 0.7, "first", "m", analyzed_at=1000.0)
    agent.record_analysis("TICK-B", "TICK", 0.5, 0.6, 0.7, "second", "m", analyzed_at=2000.0)
    rows = agent.recent(limit=10)
    assert [r["ticker"] for r in rows] == ["TICK-B", "TICK-A"]


def test_last_analyzed_at_is_none_when_never_analyzed(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    assert agent.last_analyzed_at("TICK-A") is None


def test_last_analyzed_at_returns_most_recent(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    agent.record_analysis("TICK-A", "TICK", 0.5, 0.6, 0.7, "r1", "m", analyzed_at=1000.0)
    agent.record_analysis("TICK-A", "TICK", 0.5, 0.6, 0.7, "r2", "m", analyzed_at=2000.0)
    assert agent.last_analyzed_at("TICK-A") == 2000.0


def test_resolve_from_market_results_marks_correct_and_incorrect(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    agent.record_analysis("TICK-A", "TICK", 0.5, 0.75, 0.8, "leaned yes", "m")  # leaned yes, market said yes -> correct
    agent.record_analysis("TICK-B", "TICK", 0.5, 0.30, 0.8, "leaned no", "m")   # leaned no, market said yes -> wrong
    resolved = agent.resolve_from_market_results({"TICK-A": "yes", "TICK-B": "yes"})
    assert resolved == 2
    rows = {r["ticker"]: r for r in agent.recent(limit=10)}
    assert rows["TICK-A"]["resolved"] == 1 and rows["TICK-A"]["correct"] == 1
    assert rows["TICK-B"]["resolved"] == 1 and rows["TICK-B"]["correct"] == 0


def test_resolve_from_market_results_ignores_unresolved_and_unknown_tickers(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    agent.record_analysis("TICK-A", "TICK", 0.5, 0.75, 0.8, "r", "m")
    resolved = agent.resolve_from_market_results({"TICK-A": "", "OTHER-TICK": "yes"})
    assert resolved == 0
    assert agent.recent(limit=10)[0]["resolved"] == 0


def test_resolve_from_market_results_never_re_resolves(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    agent.record_analysis("TICK-A", "TICK", 0.5, 0.75, 0.8, "r", "m")
    agent.resolve_from_market_results({"TICK-A": "yes"})
    resolved_again = agent.resolve_from_market_results({"TICK-A": "no"})  # already resolved - shouldn't flip
    assert resolved_again == 0
    assert agent.recent(limit=10)[0]["correct"] == 1  # still the original, correct result


def test_stats_computes_hit_rate_and_brier_score(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    # Perfectly confident and correct: estimate 1.0, actual yes.
    agent.record_analysis("TICK-A", "TICK", 0.5, 1.0, 0.9, "r", "m")
    # Perfectly confident and wrong: estimate 1.0, actual no.
    agent.record_analysis("TICK-B", "TICK", 0.5, 1.0, 0.9, "r", "m")
    agent.resolve_from_market_results({"TICK-A": "yes", "TICK-B": "no"})
    result = agent.stats(days=30)
    assert result["total_analyses"] == 2
    assert result["resolved"] == 2
    assert result["hit_rate_pct"] == 50.0
    # squared errors: (1.0-1.0)^2=0 for the correct one, (1.0-0.0)^2=1 for the wrong one -> mean 0.5
    assert result["brier_score"] == pytest.approx(0.5)


def test_stats_returns_none_metrics_with_no_resolved_analyses(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    agent.record_analysis("TICK-A", "TICK", 0.5, 0.6, 0.7, "r", "m")
    result = agent.stats(days=30)
    assert result["total_analyses"] == 1
    assert result["resolved"] == 0
    assert result["hit_rate_pct"] is None
    assert result["brier_score"] is None


def test_clear_all_wipes_every_analysis(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    agent.record_analysis("TICK-A", "TICK", 0.5, 0.6, 0.7, "r", "m")
    agent.clear_all()
    assert agent.total_count() == 0


def test_recent_resolved_only_excludes_unresolved(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    agent.record_analysis("TICK-A", "TICK", 0.5, 0.6, 0.7, "r", "m")
    agent.record_analysis("TICK-B", "TICK", 0.5, 0.6, 0.7, "r", "m")
    agent.resolve_from_market_results({"TICK-A": "yes"})
    assert len(agent.recent(limit=10, resolved_only=True)) == 1
    assert agent.total_count(resolved_only=True) == 1


# ---- build_prompt (pure function) -------------------------------------------

def test_build_prompt_includes_market_title_and_rules():
    market_detail = {
        "title": "Will it rain tomorrow?", "category": "Weather",
        "rules_primary": "Resolves YES if measurable rain is recorded.",
        "yes_bid_dollars": "0.42", "volume_24h_fp": "10000", "close_time": "2026-08-10T00:00:00Z",
    }
    prompt = maa.build_prompt(market_detail, {})
    assert "Will it rain tomorrow?" in prompt
    assert "Weather" in prompt
    assert "Resolves YES if measurable rain is recorded." in prompt
    assert "0.42" in prompt


def test_build_prompt_includes_liquidity_open_interest_and_last_price():
    # Audit finding (2026-08-09): these three were already fetched into
    # market_detail by main.py's own client.get_market() call - the same
    # dict the dashboard's own detail modal reads them from - but silently
    # never reached this prompt. Raw (unslimmed) field names, matching what
    # client.get_market() actually returns.
    market_detail = {
        "title": "T", "yes_bid_dollars": "0.5",
        "last_price_dollars": "0.48", "open_interest_fp": "12345", "liquidity_dollars": "6789",
    }
    prompt = maa.build_prompt(market_detail, {})
    assert "0.48" in prompt
    assert "12345" in prompt
    assert "6789" in prompt


def test_build_prompt_handles_missing_context_gracefully():
    market_detail = {"title": "T", "yes_bid_dollars": "0.5"}
    prompt = maa.build_prompt(market_detail, {})  # no whale_track_record/advisory keys at all
    assert "no data yet" in prompt
    assert "none yet" in prompt


def test_build_prompt_includes_real_track_record_when_present():
    market_detail = {"title": "T", "yes_bid_dollars": "0.5"}
    snapshot = {"whale_track_record": {"win_rate": 62.5, "resolved": 40}}
    prompt = maa.build_prompt(market_detail, snapshot)
    assert "62.5" in prompt


def test_build_prompt_has_no_self_calibration_signal_when_own_track_record_empty():
    prompt = maa.build_prompt({"title": "T", "yes_bid_dollars": "0.5"}, {}, own_track_record=None)
    assert "No resolved estimates yet" in prompt


def test_build_prompt_includes_own_track_record_when_present():
    own_track_record = {"resolved": 12, "hit_rate_pct": 58.3, "brier_score": 0.21, "window_days": 30}
    prompt = maa.build_prompt({"title": "T", "yes_bid_dollars": "0.5"}, {}, own_track_record=own_track_record)
    assert "12 of your own past estimates have resolved" in prompt
    assert "58.3" in prompt
    assert "0.21" in prompt


# ---- analyze_market (gating + structured tool-call parsing) ----------------

def test_analyze_market_returns_none_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = asyncio.run(maa.analyze_market({"title": "T"}, {}, model="claude-sonnet-5", api_key=None))
    assert result is None


def test_analyze_market_parses_the_forced_tool_call(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    tool_block = SimpleNamespace(
        type="tool_use", name=maa._TOOL_NAME,
        input={"estimated_probability": 0.73, "confidence": 0.6, "reasoning": "Because of X and Y."},
    )
    fake_response = SimpleNamespace(content=[tool_block])
    seen_kwargs = {}

    class _FakeMessages:
        async def create(self, **kwargs):
            seen_kwargs.update(kwargs)
            return fake_response

    class _FakeClient:
        def __init__(self, api_key):
            self.messages = _FakeMessages()

    import anthropic
    monkeypatch.setattr(anthropic, "AsyncAnthropic", _FakeClient)

    result = asyncio.run(agent.analyze_market({"title": "T"}, {}, model="claude-sonnet-5", api_key="fake-key"))
    assert result == {"estimated_probability": 0.73, "confidence": 0.6, "reasoning": "Because of X and Y."}
    assert seen_kwargs["tool_choice"] == {"type": "tool", "name": maa._TOOL_NAME}


def test_analyze_market_threads_own_stats_into_the_prompt(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    # Build a real, resolved track record first so stats() has something to report.
    agent.record_analysis("TICK-A", "TICK", 0.5, 0.8, 0.7, "r", "m")
    agent.resolve_from_market_results({"TICK-A": "yes"})

    tool_block = SimpleNamespace(
        type="tool_use", name=maa._TOOL_NAME,
        input={"estimated_probability": 0.6, "confidence": 0.5, "reasoning": "r"},
    )
    fake_response = SimpleNamespace(content=[tool_block])
    seen_kwargs = {}

    class _FakeMessages:
        async def create(self, **kwargs):
            seen_kwargs.update(kwargs)
            return fake_response

    class _FakeClient:
        def __init__(self, api_key):
            self.messages = _FakeMessages()

    import anthropic
    monkeypatch.setattr(anthropic, "AsyncAnthropic", _FakeClient)

    asyncio.run(agent.analyze_market({"title": "T"}, {}, model="claude-sonnet-5", api_key="fake-key"))
    sent_prompt = seen_kwargs["messages"][0]["content"]
    assert "1 of your own past estimates have resolved" in sent_prompt
    assert "100.0" in sent_prompt  # hit_rate_pct: the one resolved estimate was correct


def test_analyze_market_clamps_out_of_range_values(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    tool_block = SimpleNamespace(
        type="tool_use", name=maa._TOOL_NAME,
        input={"estimated_probability": 1.4, "confidence": -0.2, "reasoning": "r"},
    )
    fake_response = SimpleNamespace(content=[tool_block])

    class _FakeMessages:
        async def create(self, **kwargs):
            return fake_response

    class _FakeClient:
        def __init__(self, api_key):
            self.messages = _FakeMessages()

    import anthropic
    monkeypatch.setattr(anthropic, "AsyncAnthropic", _FakeClient)

    result = asyncio.run(agent.analyze_market({"title": "T"}, {}, model="claude-sonnet-5", api_key="fake-key"))
    assert result["estimated_probability"] == 1.0
    assert result["confidence"] == 0.0


def test_analyze_market_returns_none_when_the_call_raises(tmp_path, monkeypatch, capsys):
    agent = _agent(tmp_path, monkeypatch)

    class _FakeMessages:
        async def create(self, **kwargs):
            raise RuntimeError("network error")

    class _FakeClient:
        def __init__(self, api_key):
            self.messages = _FakeMessages()

    import anthropic
    monkeypatch.setattr(anthropic, "AsyncAnthropic", _FakeClient)

    result = asyncio.run(agent.analyze_market({"title": "T"}, {}, model="claude-sonnet-5", api_key="fake-key"))
    assert result is None
    # Data-robustness audit finding (2026-08-10): this used to be a bare
    # `except Exception: return None` with the real error discarded
    # entirely and a caller message pointing at "server logs" that don't
    # exist (no logging framework exists anywhere in this app) - now at
    # least visible via stdout (ddev logs -s fastapi).
    captured = capsys.readouterr()
    assert "network error" in captured.out


def test_analyze_market_returns_none_when_model_skips_the_tool(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    fake_response = SimpleNamespace(content=[SimpleNamespace(type="text", text="I refuse.")])

    class _FakeMessages:
        async def create(self, **kwargs):
            return fake_response

    class _FakeClient:
        def __init__(self, api_key):
            self.messages = _FakeMessages()

    import anthropic
    monkeypatch.setattr(anthropic, "AsyncAnthropic", _FakeClient)

    result = asyncio.run(agent.analyze_market({"title": "T"}, {}, model="claude-sonnet-5", api_key="fake-key"))
    assert result is None
