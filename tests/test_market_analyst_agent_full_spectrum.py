import asyncio
import logging
from types import SimpleNamespace

from services import market_analyst_agent as maa
from services.market_analyst_agent import _db as maa_db


def _agent(tmp_path, monkeypatch):
    monkeypatch.setattr(maa_db, "DB_PATH", tmp_path / "market_analyst.db")
    return maa


def test_record_full_spectrum_analysis_and_get_round_trip(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    suggestions = [{"id": "abc123", "config_path": "strategy.entry_threshold", "current_value": 0.5,
                    "suggested_value": 0.6, "rationale": "r", "source": "full-spectrum-analyst"}]
    analysis_id = agent.record_full_spectrum_analysis("Overall healthy.", suggestions, "claude-sonnet-5")
    fetched = agent.get_full_spectrum_analysis(analysis_id)
    assert fetched["summary"] == "Overall healthy."
    assert fetched["suggestions"] == suggestions


def test_get_full_spectrum_analysis_returns_none_for_unknown_id(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    assert agent.get_full_spectrum_analysis("does-not-exist") is None


def test_last_full_spectrum_analyzed_at_is_none_when_never_analyzed(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    assert agent.last_full_spectrum_analyzed_at() is None


def test_last_full_spectrum_analyzed_at_returns_most_recent(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    agent.record_full_spectrum_analysis("s1", [], "m", analyzed_at=1000.0)
    agent.record_full_spectrum_analysis("s2", [], "m", analyzed_at=2000.0)
    assert agent.last_full_spectrum_analyzed_at() == 2000.0


def test_recent_full_spectrum_analyses_returns_newest_first(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    agent.record_full_spectrum_analysis("s1", [], "m", analyzed_at=1000.0)
    agent.record_full_spectrum_analysis("s2", [], "m", analyzed_at=2000.0)
    rows = agent.recent_full_spectrum_analyses(limit=10)
    assert [r["summary"] for r in rows] == ["s2", "s1"]


def test_build_full_spectrum_prompt_includes_all_context_sections():
    ctx = {
        "config": {"strategy": {"entry_threshold": 0.5}},
        "trade_summary": {"total_closed": 10, "win_rate_pct": 55.0},
        "whale_track_record": {"total_signals": 100},
        "advisory_recommendations": [{"config_path": "strategy.entry_threshold"}],
        "variant_summaries": {"fp1": {"total_closed": 5}},
        "recent_applied_changes": [{"config_path": "risk.max_daily_loss_pct"}],
        "per_series_whale_breakdown": [{"series": "KXPGATOUR"}],
        "portfolio": {"bankroll": 9000},
        "rejected_candidate_gates": [{"gate_name": "entry_threshold", "hypothetical_win_rate": 40.0}],
        "regime_by_category": [{"category": "Sports", "win_rate_pct": 60.0}],
        "regime_by_hour": [{"hour_utc": 14, "win_rate_pct": 45.0}],
    }
    prompt = maa.build_full_spectrum_prompt(ctx)
    assert "entry_threshold" in prompt
    assert "55.0" in prompt
    assert "KXPGATOUR" in prompt
    assert "max_daily_loss_pct" in prompt
    assert "9000" in prompt
    # 2026-08-11 "web of expertise" audit - two previously-missing datasets
    assert "40.0" in prompt  # rejected-candidate hypothetical win rate
    assert '"Sports"' in prompt  # regime by-category
    assert '"hour_utc": 14' in prompt  # regime by-hour


def test_build_full_spectrum_prompt_handles_missing_new_sections_gracefully():
    # Real callers always pass these (main.py's _build_full_spectrum_context
    # always includes them now), but a minimal/legacy context dict must not
    # crash the prompt builder.
    prompt = maa.build_full_spectrum_prompt({})
    assert "none logged yet" in prompt
    assert "not enough categorized trades yet" in prompt
    assert "not enough trades yet" in prompt


def test_analyze_full_spectrum_returns_none_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = asyncio.run(maa.analyze_full_spectrum({}, model="claude-sonnet-5", api_key=None))
    assert result is None


def test_analyze_full_spectrum_parses_the_forced_tool_call(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    tool_block = SimpleNamespace(
        type="tool_use", name=maa._FULL_SPECTRUM_TOOL_NAME,
        input={"summary": "Overall healthy.", "suggestions": [
            {"config_path": "strategy.entry_threshold", "suggested_value": 0.6, "rationale": "r"},
        ]},
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

    result = asyncio.run(agent.analyze_full_spectrum({}, model="claude-sonnet-5", api_key="fake-key"))
    assert result["summary"] == "Overall healthy."
    assert result["suggestions"] == [{"config_path": "strategy.entry_threshold", "suggested_value": 0.6, "rationale": "r"}]
    assert seen_kwargs["tool_choice"] == {"type": "tool", "name": maa._FULL_SPECTRUM_TOOL_NAME}


def test_analyze_full_spectrum_defaults_suggestions_to_empty_list_when_absent(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    tool_block = SimpleNamespace(type="tool_use", name=maa._FULL_SPECTRUM_TOOL_NAME, input={"summary": "Fine."})
    fake_response = SimpleNamespace(content=[tool_block])

    class _FakeMessages:
        async def create(self, **kwargs):
            return fake_response

    class _FakeClient:
        def __init__(self, api_key):
            self.messages = _FakeMessages()

    import anthropic
    monkeypatch.setattr(anthropic, "AsyncAnthropic", _FakeClient)

    result = asyncio.run(agent.analyze_full_spectrum({}, model="claude-sonnet-5", api_key="fake-key"))
    assert result["suggestions"] == []


def test_analyze_full_spectrum_returns_none_when_the_call_raises(tmp_path, monkeypatch, caplog):
    agent = _agent(tmp_path, monkeypatch)

    class _FakeMessages:
        async def create(self, **kwargs):
            raise RuntimeError("full spectrum network error")

    class _FakeClient:
        def __init__(self, api_key):
            self.messages = _FakeMessages()

    import anthropic
    monkeypatch.setattr(anthropic, "AsyncAnthropic", _FakeClient)

    caplog.set_level(logging.ERROR)
    result = asyncio.run(agent.analyze_full_spectrum({}, model="claude-sonnet-5", api_key="fake-key"))
    assert result is None
    assert "full spectrum network error" in caplog.text


def test_analyze_full_spectrum_returns_none_when_model_skips_the_tool(tmp_path, monkeypatch):
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

    result = asyncio.run(agent.analyze_full_spectrum({}, model="claude-sonnet-5", api_key="fake-key"))
    assert result is None
