# 3.13+ required by kalshi-python-async (see requirements.txt) - every
# version of that package past 3.2.0 needs it.
FROM python:3.13-slim
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
