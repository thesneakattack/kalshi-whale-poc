import asyncio
import logging
from types import SimpleNamespace

from services import market_analyst_agent as maa
from services.market_analyst_agent import _db as maa_db


def _agent(tmp_path, monkeypatch):
    monkeypatch.setattr(maa_db, "DB_PATH", tmp_path / "market_analyst.db")
    return maa


def test_record_series_analysis_and_get_round_trip(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    suggestions = [{"id": "abc123", "config_path": "strategy.excluded_series", "current_value": [],
                    "suggested_value": ["KXTICK"], "rationale": "r", "series": "KXTICK", "source": "series-analyst"}]
    analysis_id = agent.record_series_analysis("KXTICK", "Looks weak.", suggestions, "claude-sonnet-5")
    fetched = agent.get_series_analysis(analysis_id)
    assert fetched["series"] == "KXTICK"
    assert fetched["summary"] == "Looks weak."
    assert fetched["suggestions"] == suggestions


def test_get_series_analysis_returns_none_for_unknown_id(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    assert agent.get_series_analysis("does-not-exist") is None


def test_last_series_analyzed_at_is_none_when_never_analyzed(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    assert agent.last_series_analyzed_at("KXTICK") is None


def test_last_series_analyzed_at_returns_most_recent(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    agent.record_series_analysis("KXTICK", "s1", [], "m", analyzed_at=1000.0)
    agent.record_series_analysis("KXTICK", "s2", [], "m", analyzed_at=2000.0)
    assert agent.last_series_analyzed_at("KXTICK") == 2000.0


def test_recent_series_analyses_returns_newest_first(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    agent.record_series_analysis("KXTICK-A", "s1", [], "m", analyzed_at=1000.0)
    agent.record_series_analysis("KXTICK-B", "s2", [], "m", analyzed_at=2000.0)
    rows = agent.recent_series_analyses(limit=10)
    assert [r["series"] for r in rows] == ["KXTICK-B", "KXTICK-A"]


def test_build_series_prompt_includes_series_and_context():
    ctx = {
        "series": "KXPGATOUR", "currently_excluded": False, "min_contracts_override": None,
        "whale_stats": {"total_signals": 10, "win_rate": 60.0},
        "trade_summary": {"total_closed": 5, "win_rate_pct": 40.0},
        "evaluator_status": {"status": "approved", "trades_observed": 30, "strike_count": 0},
    }
    prompt = maa.build_series_prompt("KXPGATOUR", ctx)
    assert "KXPGATOUR" in prompt
    assert "status=approved" in prompt
    assert "60.0" in prompt  # whale win rate
    assert "40.0" in prompt  # trade win rate


def test_build_series_prompt_handles_never_evaluated_series():
    ctx = {
        "series": "KXNEW", "currently_excluded": False, "min_contracts_override": None,
        "whale_stats": {}, "trade_summary": {}, "evaluator_status": None,
    }
    prompt = maa.build_series_prompt("KXNEW", ctx)
    assert "never evaluated" in prompt
    assert "no whale signals logged yet" in prompt
    assert "no closed trades yet" in prompt


def test_analyze_series_returns_none_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = asyncio.run(maa.analyze_series({"series": "KXTICK"}, model="claude-sonnet-5", api_key=None))
    assert result is None


def test_analyze_series_parses_the_forced_tool_call(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    tool_block = SimpleNamespace(
        type="tool_use", name=maa._SERIES_TOOL_NAME,
        input={"summary": "Looks fine.", "suggestions": [{"action": "exclude", "rationale": "weak whale accuracy"}]},
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

    result = asyncio.run(agent.analyze_series({"series": "KXTICK"}, model="claude-sonnet-5", api_key="fake-key"))
    assert result["summary"] == "Looks fine."
    assert result["suggestions"] == [{"action": "exclude", "rationale": "weak whale accuracy"}]
    assert seen_kwargs["tool_choice"] == {"type": "tool", "name": maa._SERIES_TOOL_NAME}


def test_analyze_series_defaults_suggestions_to_empty_list_when_absent(tmp_path, monkeypatch):
    agent = _agent(tmp_path, monkeypatch)
    tool_block = SimpleNamespace(type="tool_use", name=maa._SERIES_TOOL_NAME, input={"summary": "Fine."})
    fake_response = SimpleNamespace(content=[tool_block])

    class _FakeMessages:
        async def create(self, **kwargs):
            return fake_response

    class _FakeClient:
        def __init__(self, api_key):
            self.messages = _FakeMessages()

    import anthropic
    monkeypatch.setattr(anthropic, "AsyncAnthropic", _FakeClient)

    result = asyncio.run(agent.analyze_series({"series": "KXTICK"}, model="claude-sonnet-5", api_key="fake-key"))
    assert result["suggestions"] == []


def test_analyze_series_returns_none_when_the_call_raises(tmp_path, monkeypatch, caplog):
    agent = _agent(tmp_path, monkeypatch)

    class _FakeMessages:
        async def create(self, **kwargs):
            raise RuntimeError("series network error")

    class _FakeClient:
        def __init__(self, api_key):
            self.messages = _FakeMessages()

    import anthropic
    monkeypatch.setattr(anthropic, "AsyncAnthropic", _FakeClient)

    caplog.set_level(logging.ERROR)
    result = asyncio.run(agent.analyze_series({"series": "KXTICK"}, model="claude-sonnet-5", api_key="fake-key"))
    assert result is None
    assert "series network error" in caplog.text


def test_analyze_series_returns_none_when_model_skips_the_tool(tmp_path, monkeypatch):
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

    result = asyncio.run(agent.analyze_series({"series": "KXTICK"}, model="claude-sonnet-5", api_key="fake-key"))
    assert result is None
