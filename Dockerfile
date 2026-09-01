# 3.13+ required by kalshi-python-async (see requirements.txt) - every
# version of that package past 3.2.0 needs it.
FROM python:3.13-slim
# python:3.13-slim has no git binary (docs/open-decisions.md, 2026-08-28) -
# tests/test_quality_coordination_cleanup_actions.py's fault-injection suite
# runs real git commands (tests/support/synthetic_git_repo.py) against a
# synthetic repo, which needs a real git binary to exist - the whole point of
# that suite is proving real git subprocess behavior, not mocking it. CI's
# own tests-pytest.yml already works around this same gap for its own
# python:3.13-slim-based step (`apt-get install -y -qq --no-install-recommends
# git`) - this bakes the same fix into the dev container's image instead of
# leaving it as a CI-only workaround these 6 tests can never pass locally
# against.
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
# requirements-dev.txt (pytest) is baked into this same image, not installed
# ad hoc after the fact - this is a dev-only container (no real deployment
# target exists yet, see ROADMAP.md's "Path to production" section), and the
# .claude/hooks/run_tests.py PostToolUse hook + CI both run pytest via
# `ddev exec -s fastapi` / this same image, so it has to survive a plain
# rebuild rather than depend on a manual pip install that a later
# `ddev restart`/container recreation silently wipes out.
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
