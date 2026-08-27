from tools.quality_coordination import fetch_app_report


def _fake_getter(responses):
    def getter(url, timeout):
        if url not in responses:
            raise AssertionError(f"unexpected url {url}")
        value = responses[url]
        if isinstance(value, Exception):
            raise value
        return value
    return getter


def test_fetch_app_report_returns_all_three_on_success():
    getter = _fake_getter({
        "http://fastapi:8000/api/quality/summary": {"status": "ok"},
        "http://fastapi:8000/api/health/pipeline": {"schedulers": []},
        "http://fastapi:8000/api/health/faults": {"faults": []},
    })

    report = fetch_app_report("http://fastapi:8000", getter=getter)

    assert report == {
        "quality_summary": {"status": "ok"},
        "health_pipeline": {"schedulers": []},
        "health_faults": {"faults": []},
    }


def test_fetch_app_report_degrades_one_failed_call_to_none():
    getter = _fake_getter({
        "http://fastapi:8000/api/quality/summary": {"status": "ok"},
        "http://fastapi:8000/api/health/pipeline": ConnectionError("refused"),
        "http://fastapi:8000/api/health/faults": {"faults": []},
    })

    report = fetch_app_report("http://fastapi:8000", getter=getter)

    assert report["quality_summary"] == {"status": "ok"}
    assert report["health_pipeline"] is None
    assert report["health_faults"] == {"faults": []}


def test_fetch_app_report_never_raises_when_everything_fails():
    getter = _fake_getter({
        "http://fastapi:8000/api/quality/summary": RuntimeError("x"),
        "http://fastapi:8000/api/health/pipeline": RuntimeError("x"),
        "http://fastapi:8000/api/health/faults": RuntimeError("x"),
    })

    report = fetch_app_report("http://fastapi:8000", getter=getter)

    assert report == {"quality_summary": None, "health_pipeline": None, "health_faults": None}
