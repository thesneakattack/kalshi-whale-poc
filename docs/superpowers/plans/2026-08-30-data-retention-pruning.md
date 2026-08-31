# Data Retention Pruning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `data/backups/`'s unbounded growth (292GB as of 2026-08-30, versus ~30GB in `data/` itself) by splitting the backup mechanism into two independently-cadenced tiers, and close the one real retention gap found in `data/*.db` (`market_history.db`'s `snapshots` table, 1.16GB, no prune mechanism).

**Architecture:** `services/backup/backup.py`'s single undifferentiated snapshot cycle becomes two: a "regular" tier (small, account-critical files, unchanged 6h/14-count cadence) and a "large" tier (the files CLAUDE.md's "accumulated history is a first-class asset" rule keeps growing forever — `series_watcher.db`, `candidate_log.db`, `market_history.db` — on a longer, separately-configured cadence into a separate snapshot directory). Both tiers reuse the existing `sqlite3.Connection.backup()` snapshot mechanism and `backup_runs` history table (new `tier` column), just with different file selection, interval, retention, and destination. Separately, `market_history.py` gets a `prune()` function mirroring `index_feed.ingestion.prune()`/`observability.prune()`'s existing shape, wired into the same hourly sweep those two already use.

**Tech Stack:** Python 3.13, sqlite3 stdlib, FastAPI, pytest.

**Spec:** This plan's own Design Summary below — derived from live investigation in this session (2026-08-30): `data/*.db` and `data/backups/` sizes measured directly, `services/backup/backup.py`, `services/backup/README.md`, `services/market_history.py`, `services/index_feed/ingestion.py`, `services/storage_health/README.md`, `main.py`'s `_maybe_prune_capture_stores`/`_SCHEDULER_TRIGGERS`/`_scheduler_loop`, `services/app_state.py`, `tests/test_backup.py`, `tests/test_market_history.py`, and `tests/support/runtime_isolation.py` all read directly — no field, schema, or wiring guessed. No separate spec doc exists; this section is it.

## Design Summary (the spec)

**Measured facts (2026-08-30, this session):**
- `data/backups/`: 292GB across 13 real snapshots + 3 empty/failed dirs, each real snapshot 18–27GB and growing (`retention_count: 14` in `config/settings.yaml`).
- Root cause, read directly from `services/backup/backup.py`: every 6h, `run_backup_cycle` does a full `sqlite3.Connection.backup()` of *every* `data/*.db` file into a new timestamped directory, then prunes to the newest 14 directories. `series_watcher.db` (24.4GB, `raw_trades` table) is deliberately never pruned at the row level (`services/series_watcher.py:411-417`'s own docstring, CLAUDE.md's "accumulated history is a first-class asset" rule) — so every one of the 14 retained snapshots carries its own full copy of that same ever-growing file.
- `candidate_log.db` (2.8GB, `rejection_events` table, 6.2M+ rows) is likewise deliberately unbounded (`services/candidate_log.py:350`'s own docstring: "undeduped, no retention" — it's the earmarked future input for cost-banded gate calibration). Neither this nor `series_watcher.db` may be pruned at the row level by this plan.
- `market_history.db` (1.16GB, `snapshots` table) has **no** `prune()` function, unlike its siblings `index_feed.ingestion.prune()` (`services/index_feed/ingestion.py:287-300`, wired into `main.py:163`) and `observability.prune()` (`services/observability/observability.py:119-123`, wired into `main.py:166`). Both of those already run from `main.py`'s `_maybe_prune_capture_stores` (`main.py:153-166`), an hourly sweep. `market_history.py`'s own docstring calls its snapshot log "rolling," and its only readers (`momentum()`, `volatility()`, `compute_hypothetical_trades()`) use lookback windows no longer than 86400s (24h, `market_history.py:319`'s default `lookback_windows_sec`) — this is a genuine gap, not a protected asset.
- `index_feed.db` (463MB) and `observability.db` (224MB) are already retention-bounded and healthy — out of scope.

**Decisions this plan bakes in as defaults (flagged for confirmation at plan review — see the closing message of this turn):**
- `market_history.retention_hours`: 168 (7 days) — matches `series_watcher`/`index_feed`'s existing default, comfortably above the 24h max lookback actually used.
- `backup.large_files`: `["series_watcher.db", "candidate_log.db", "market_history.db"]`.
- `backup.large_file_interval_sec`: 86400 (24h).
- `backup.large_file_retention_count`: 4 (~4 days).
- New snapshot destination: `data/backups_large/` (a *sibling* of `data/backups/`, not nested inside it — nesting would make `_prune_old_snapshots`' directory-name sort for the regular tier treat the large-tier subdirectory as just another (always-newest-sorting) snapshot, corrupting both tiers' pruning and status counts).

**What this plan explicitly does NOT do:**
- Does not delete or prune any row in `series_watcher.db`'s `raw_trades` or `candidate_log.db`'s `rejection_events` — that would violate CLAUDE.md's data-plane HARD RULE.
- Does not change `backup.interval_sec`/`retention_count` (the regular tier's existing cadence) — those still protect the small, account-critical files (`paper_broker.db`, `risk_state.db`, `accounts.db`, etc.) exactly as today.
- Does not extend `backup_overdue_finding` (`services/storage_health/storage_health.py:260`) or `GET /api/quality/summary` to know about the large tier — those keep checking only the regular tier's recency (the tier that actually matters for "is my critical backup fresh" alerting). Large-tier staleness surfaces only via `GET /api/backup/status`'s new `large_tier` key. Noted here as a deliberate scope boundary, not a silent gap.
- Does not run `VACUUM` on `market_history.db` after pruning — freed pages stay allocated until a separate, deliberately-scheduled `VACUUM` runs (SQLite doesn't reclaim space from `DELETE` automatically). Out of scope for this plan; flagged as a follow-up.

## Global Constraints

- Every new/changed SQLite schema change is additive-only (`CREATE TABLE IF NOT EXISTS` + `_add_column_if_missing`), per CLAUDE.md's persistence idiom — never a destructive migration.
- Every test that touches a `services/*` persistence module monkeypatches its `DB_PATH`/`DATA_DIR`/`BACKUP_DIR` constant to a tmp path — never a real `data/*.db` file, per CLAUDE.md's `data/*.db` safety invariant.
- `config/settings.yaml` is live-reloadable and live-tuned by the running dashboard; any edit to it uses the `config-field-edit` skill's snapshot/reset/re-apply steps so the commit contains only the schema change, not whatever live tuning currently sits in the working file (there is already one uncommitted modification to this file on the current branch — check `git diff config/settings.yaml` before Task 3's config edit and account for it in step 1's snapshot).
- This is implementation work per `.claude/rules/branching-and-ci.md` — do not commit to `main`; use the current initiative branch if already on one, otherwise create `feat/data-retention-pruning` (or work in a worktree per `superpowers:using-git-worktrees` if the primary checkout is occupied — check `ListAgents`/`orient.sh` first).
- Every step's commands run inside the app's dev environment per CLAUDE.md's "Dev workflow" section (`ddev exec -s fastapi <cmd>` for anything that must run inside the container; plain `pytest` from the host works for these files since they need no live services).

---

### Task 1: `market_history.prune()` — close the retention gap

**Files:**
- Modify: `services/market_history.py:303-307` (insert after `outcome_count()`, before `clear_all()`)
- Modify: `main.py:153-166` (`_maybe_prune_capture_stores`)
- Modify: `config/settings.yaml` (new `market_history:` section)
- Test: `tests/test_market_history.py`

**Interfaces:**
- Produces: `market_history.prune(retention_hours: float = 168.0, now: float | None = None) -> dict` — same signature shape as `index_feed.ingestion.prune`/`observability.prune`, returns `{"snapshots_deleted": int, "cutoff": float}` on success or `{"snapshots_deleted": 0, "error": str}` on failure.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_market_history.py` (uses the file's existing `_mh(tmp_path, monkeypatch)` fixture helper, same pattern as every other test in the file):

```python
def test_prune_deletes_snapshots_older_than_the_retention_window(tmp_path, monkeypatch):
    _mh(tmp_path, monkeypatch)
    mh.record_snapshots(
        [{"ticker": "TICK-A", "yes_price": 0.5, "spread": 0.02, "volume_24h": 1000, "time_to_close_sec": 3600}],
        timestamp=1000.0,
    )
    mh.record_snapshots(
        [{"ticker": "TICK-A", "yes_price": 0.6, "spread": 0.02, "volume_24h": 1000, "time_to_close_sec": 3600}],
        timestamp=500_000.0,
    )

    result = mh.prune(retention_hours=1.0, now=500_010.0)  # cutoff = 500_010 - 3600 = 496_410

    assert result["snapshots_deleted"] == 1
    assert mh.snapshot_count() == 1
    with mh._connect(tmp_path / "market_history.db") as conn:
        remaining_ts = conn.execute("SELECT timestamp FROM snapshots").fetchone()[0]
    assert remaining_ts == 500_000.0


def test_prune_does_not_touch_outcomes(tmp_path, monkeypatch):
    _mh(tmp_path, monkeypatch)
    mh.record_outcome("TICK-A", "yes", resolved_at=1000.0)

    mh.prune(retention_hours=1.0, now=500_010.0)

    assert mh.outcome_count() == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_market_history.py -k test_prune -v`
Expected: FAIL with `AttributeError: module 'services.market_history' has no attribute 'prune'`

- [ ] **Step 3: Implement `market_history.prune()`**

Insert into `services/market_history.py` at line 307 (between `outcome_count()` and `clear_all()`):

```python
def prune(retention_hours: float = 168.0, now: float | None = None) -> dict:
    """Drop price/spread snapshots older than the retention window. This
    table's only readers - momentum()/volatility() (short, caller-supplied
    lookback_sec windows) and compute_hypothetical_trades() (longest
    configured lookback_windows_sec entry: 86400s/24h) - never look further
    back than a day; the 168h/7d default leaves a wide margin above that.
    outcomes is deliberately not pruned here: one row per settled ticker
    (PRIMARY KEY ticker, see _connect's schema), so it can't grow unbounded
    the way a per-tick snapshot table does - same reasoning series_watcher.
    prune() gives for leaving raw_trades alone, just for a table that's
    naturally bounded instead of a deliberately-unbounded one."""
    now = now if now is not None else time.time()
    cutoff = now - retention_hours * 3600
    try:
        with _connect(DB_PATH) as conn:
            cur = conn.execute("DELETE FROM snapshots WHERE timestamp < ?", (cutoff,))
            return {"snapshots_deleted": cur.rowcount, "cutoff": cutoff}
    except Exception as exc:
        fault_log.record("market_history", "prune", exc)
        return {"snapshots_deleted": 0, "error": str(exc)}
```

(`fault_log` is already imported at the top of `services/market_history.py` — no new import needed.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_market_history.py -k test_prune -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Wire into the hourly capture-store prune sweep**

Modify `main.py:153-166` (`_maybe_prune_capture_stores`) from:

```python
def _maybe_prune_capture_stores(cfg: dict, now: float) -> None:
    """Hourly retention sweep across the sampled capture stores. Bounded
    and idempotent; each store's own prune() is non-raising and logs to
    fault_log on failure."""
    global _last_capture_prune_at
    if now - _last_capture_prune_at < 3600:
        return
    _last_capture_prune_at = now
    hours = float((cfg.get("series_watcher") or {}).get("retention_hours", 168))
    series_watcher.prune(retention_hours=hours, now=now)
    index_feed.prune(retention_hours=hours, now=now)
    game_state.prune(retention_hours=hours, now=now)
    obs_hours = float((cfg.get("observability") or {}).get("retention_hours", 336))
    observability.prune(retention_hours=obs_hours, now=now)
```

to:

```python
def _maybe_prune_capture_stores(cfg: dict, now: float) -> None:
    """Hourly retention sweep across the sampled capture stores. Bounded
    and idempotent; each store's own prune() is non-raising and logs to
    fault_log on failure."""
    global _last_capture_prune_at
    if now - _last_capture_prune_at < 3600:
        return
    _last_capture_prune_at = now
    hours = float((cfg.get("series_watcher") or {}).get("retention_hours", 168))
    series_watcher.prune(retention_hours=hours, now=now)
    index_feed.prune(retention_hours=hours, now=now)
    game_state.prune(retention_hours=hours, now=now)
    obs_hours = float((cfg.get("observability") or {}).get("retention_hours", 336))
    observability.prune(retention_hours=obs_hours, now=now)
    mh_hours = float((cfg.get("market_history") or {}).get("retention_hours", 168))
    market_history.prune(retention_hours=mh_hours, now=now)
```

(`market_history` is already imported at `main.py:39` — no new import needed.)

- [ ] **Step 6: Add the config key via the config-field-edit skill**

Follow `.claude/skills/config-field-edit/SKILL.md` exactly (this repo's own procedure for schema edits to a live-tuned file):

```bash
cp config/settings.yaml /tmp/settings.yaml.bak
git show HEAD:config/settings.yaml > config/settings.yaml
```

Then edit the freshly-reset `config/settings.yaml`, adding a `market_history:` section. Insert it near the other retention-bearing sections (e.g. directly after the `observability:` block, before `research:`):

```yaml
market_history:
  retention_hours: 168  # 7d - momentum()/volatility()/compute_hypothetical_trades()'s
  # longest lookback is 86400s/24h (services/market_history.py's own default
  # lookback_windows_sec); this leaves a wide margin above that. Added
  # 2026-08-30 to close the one retention gap found in data/*.db - see
  # services/market_history.py's prune() docstring.
```

```bash
git add config/settings.yaml
git commit -m "chore: add market_history.retention_hours config key"
cp /tmp/settings.yaml.bak config/settings.yaml
```

Re-apply the same `market_history:` block (same key, same position, same value) to this just-restored file, then verify:

```bash
git diff config/settings.yaml
```

Expected: only the live-tuned values differ from the new `HEAD` — no lines related to the `market_history:` block itself (it should appear identically in both, i.e. no diff on those lines).

- [ ] **Step 7: Manual verification against a real snapshot shape**

Run: `python -c "
from services import market_history as mh
import tempfile, pathlib
mh.DB_PATH = pathlib.Path(tempfile.mkdtemp()) / 'market_history.db'
mh.record_snapshots([{'ticker': 'T', 'yes_price': 0.5, 'spread': 0.01, 'volume_24h': 1, 'time_to_close_sec': 1}], timestamp=1.0)
print(mh.prune(retention_hours=0.0, now=1000.0))
print('remaining:', mh.snapshot_count())
"`
Expected: `{'snapshots_deleted': 1, 'cutoff': 1000.0}` then `remaining: 0`

- [ ] **Step 8: Commit**

```bash
git add services/market_history.py main.py tests/test_market_history.py
git commit -m "feat: prune market_history.db snapshots on the existing hourly capture-store sweep"
```

---

### Task 2: `backup.py` — two-tier snapshot mechanism (core)

**Files:**
- Modify: `services/backup/backup.py` (whole-file changes below)
- Test: `tests/test_backup.py`

**Interfaces:**
- Consumes: nothing new from other tasks.
- Produces: `run_backup_cycle(retention_count, now=None, *, tier="regular", exclude=frozenset(), only=None, snapshot_root=None) -> dict` (return dict now includes `"tier"`); `_prune_old_snapshots(retention_count, snapshot_root=None) -> list[str]`; `_data_db_files(*, exclude=frozenset(), only=None) -> list[Path]`; `recent(limit=20, tier="regular") -> list[dict]`; `latest(tier="regular") -> dict | None`; `_run_backup_background(retention_count, *, tier="regular", exclude=frozenset(), only=None, snapshot_root=None, state_key="backup") -> None` (coroutine); `_maybe_run_backup(cfg) -> None` (unchanged signature, now excludes `large_files`); `_maybe_run_large_backup(cfg) -> None` (new); module constants `LARGE_BACKUP_DIR: Path`, `_DEFAULT_LARGE_FILE_INTERVAL_SEC = 86400`, `_DEFAULT_LARGE_FILE_RETENTION_COUNT = 4`, `_DEFAULT_LARGE_FILES = frozenset({"series_watcher.db", "candidate_log.db", "market_history.db"})`. These are consumed by Task 3 (`main.py`, `services/app_state.py`) and Task 4 (`services/backup/routes.py`, `services/backup/__init__.py`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_backup.py`, after the existing `test_prune_old_snapshots_keeps_only_retention_count` test:

```python
# --- two-tier file selection --------------------------------------------

def test_run_backup_cycle_exclude_skips_named_files():
    _make_real_sqlite_file(backup.DATA_DIR / "paper_broker.db")
    _make_real_sqlite_file(backup.DATA_DIR / "series_watcher.db")

    result = backup.run_backup_cycle(
        retention_count=5, now=1000.0, tier="regular", exclude=frozenset({"series_watcher.db"}),
    )

    assert result["files_ok"] == 1
    assert result["tier"] == "regular"
    snapshot_dir = backup.BACKUP_DIR / result["snapshot"]
    assert (snapshot_dir / "paper_broker.db").exists()
    assert not (snapshot_dir / "series_watcher.db").exists()


def test_run_backup_cycle_only_backs_up_named_files():
    _make_real_sqlite_file(backup.DATA_DIR / "paper_broker.db")
    _make_real_sqlite_file(backup.DATA_DIR / "series_watcher.db")

    result = backup.run_backup_cycle(
        retention_count=5, now=1000.0, tier="large",
        only=frozenset({"series_watcher.db"}), snapshot_root=backup.LARGE_BACKUP_DIR,
    )

    assert result["files_ok"] == 1
    assert result["tier"] == "large"
    snapshot_dir = backup.LARGE_BACKUP_DIR / result["snapshot"]
    assert (snapshot_dir / "series_watcher.db").exists()
    assert not (snapshot_dir / "paper_broker.db").exists()


def test_prune_old_snapshots_respects_snapshot_root():
    _make_real_sqlite_file(backup.DATA_DIR / "series_watcher.db")
    for i in range(4):
        backup.run_backup_cycle(
            retention_count=2, now=1000.0 + i, tier="large",
            only=frozenset({"series_watcher.db"}), snapshot_root=backup.LARGE_BACKUP_DIR,
        )

    assert len(list(backup.LARGE_BACKUP_DIR.iterdir())) == 2
    assert not backup.BACKUP_DIR.exists() or len(list(backup.BACKUP_DIR.iterdir())) == 0


def test_recent_and_latest_filter_by_tier():
    _make_real_sqlite_file(backup.DATA_DIR / "paper_broker.db")
    _make_real_sqlite_file(backup.DATA_DIR / "series_watcher.db")
    backup.run_backup_cycle(retention_count=5, now=1000.0, tier="regular",
                             exclude=frozenset({"series_watcher.db"}))
    backup.run_backup_cycle(retention_count=5, now=2000.0, tier="large",
                             only=frozenset({"series_watcher.db"}), snapshot_root=backup.LARGE_BACKUP_DIR)

    regular = backup.latest(tier="regular")
    large = backup.latest(tier="large")

    assert regular["tier"] == "regular"
    assert regular["started_at"] == 1000.0
    assert large["tier"] == "large"
    assert large["started_at"] == 2000.0


def test_recent_regular_tier_includes_pre_migration_rows_with_null_tier():
    # Simulates a backup_runs row written before the `tier` column existed -
    # recent()/latest() must still count it as "regular" so a fresh deploy
    # of this change doesn't misread historical recency as "overdue."
    _make_real_sqlite_file(backup.DATA_DIR / "paper_broker.db")
    with backup._connect() as conn:
        conn.execute(
            "INSERT INTO backup_runs "
            "(started_at, finished_at, snapshot_name, files_ok, files_failed, total_bytes, pruned_count, error, tier) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (500.0, 501.0, "pre-migration-snapshot", 1, 0, 100, 0, None),
        )

    last = backup.latest(tier="regular")

    assert last["snapshot_name"] == "pre-migration-snapshot"


# --- large tier's own _maybe_run_large_backup ----------------------------

_LARGE_CFG = {"backup": {"enabled": True, "large_file_interval_sec": 21600,
                          "large_files": ["series_watcher.db"]}}


async def _call_maybe_run_large_backup(cfg):
    backup._maybe_run_large_backup(cfg)
    task = state["backup_large"].get("task")
    if task is not None:
        await task


def test_maybe_run_large_backup_fires_on_a_genuinely_fresh_install():
    _make_real_sqlite_file(backup.DATA_DIR / "series_watcher.db")
    assert backup.latest(tier="large") is None
    assert state["backup_large"]["last_started_at"] == 0.0

    asyncio.run(_call_maybe_run_large_backup(_LARGE_CFG))

    assert len(backup.recent(limit=10, tier="large")) == 1
    assert state["backup_large"]["running"] is False


def test_maybe_run_large_backup_only_writes_files_in_large_files_config():
    _make_real_sqlite_file(backup.DATA_DIR / "series_watcher.db")
    _make_real_sqlite_file(backup.DATA_DIR / "paper_broker.db")

    asyncio.run(_call_maybe_run_large_backup(_LARGE_CFG))

    last = backup.latest(tier="large")
    snapshot_dir = backup.LARGE_BACKUP_DIR / last["snapshot_name"]
    assert (snapshot_dir / "series_watcher.db").exists()
    assert not (snapshot_dir / "paper_broker.db").exists()


def test_maybe_run_backup_still_excludes_large_files_by_default():
    _make_real_sqlite_file(backup.DATA_DIR / "paper_broker.db")
    _make_real_sqlite_file(backup.DATA_DIR / "series_watcher.db")
    cfg = {"backup": {"enabled": True, "interval_sec": 21600,
                       "large_files": ["series_watcher.db"]}}

    asyncio.run(_call_maybe_run_backup(cfg))

    last = backup.latest(tier="regular")
    snapshot_dir = backup.BACKUP_DIR / last["snapshot_name"]
    assert (snapshot_dir / "paper_broker.db").exists()
    assert not (snapshot_dir / "series_watcher.db").exists()
```

Also update the existing `_isolated` fixture (`tests/test_backup.py:59-67`) to redirect the two new module constants, and seed the new `state["backup_large"]` entry — from:

```python
@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(backup, "DATA_DIR", data_dir)
    monkeypatch.setattr(backup, "BACKUP_DIR", data_dir / "backups")
    monkeypatch.setattr(backup, "DB_PATH", data_dir / "backup_log.db")
    state["backup"] = {"running": False, "last_started_at": 0.0, "task": None}
    yield
```

to:

```python
@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(backup, "DATA_DIR", data_dir)
    monkeypatch.setattr(backup, "BACKUP_DIR", data_dir / "backups")
    monkeypatch.setattr(backup, "LARGE_BACKUP_DIR", data_dir / "backups_large")
    monkeypatch.setattr(backup, "DB_PATH", data_dir / "backup_log.db")
    state["backup"] = {"running": False, "last_started_at": 0.0, "task": None}
    state["backup_large"] = {"running": False, "last_started_at": 0.0, "task": None}
    yield
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_backup.py -v`
Expected: FAIL — `run_backup_cycle() got an unexpected keyword argument 'tier'` (and similar) on the new tests; existing tests still pass.

- [ ] **Step 3: Implement the two-tier mechanism**

Replace `services/backup/backup.py`'s constants block (lines 49-65) with:

```python
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
BACKUP_DIR = DATA_DIR / "backups"
# A *sibling* of BACKUP_DIR, not BACKUP_DIR / "large" - nesting would make
# _prune_old_snapshots' directory-name sort for the regular tier treat this
# subdirectory as just another snapshot (and, since "large" sorts after
# every UTC-timestamp name lexicographically, always the "newest" one),
# corrupting both tiers' pruning and GET /api/backup/status's counts.
LARGE_BACKUP_DIR = DATA_DIR / "backups_large"
DB_PATH = DATA_DIR / "backup_log.db"

_DEFAULT_INTERVAL_SEC = 21600  # 6h - frequent enough that a lost disk never
# costs more than a few hours of real trade/signal history.
_DEFAULT_RETENTION_COUNT = 14  # ~3.5 days at the default 6h cadence. Safe to
# keep unchanged now that the large tier below carries series_watcher.db/
# candidate_log.db/market_history.db separately - this tier's own files are
# all single-digit-MB, so 14 of them costs nothing like the pre-tiering
# snapshot did (2026-08-30: 292GB across 13 snapshots once series_watcher.db
# alone reached 24GB, because every snapshot carried a full copy of it).

_DEFAULT_LARGE_FILE_INTERVAL_SEC = 86400  # 24h - deliberately longer than
# the regular tier: these files are already durable, continuously-
# accumulating append logs (CLAUDE.md's "accumulated history is a
# first-class asset" rule), so losing a few hours of them to a backup gap
# costs nothing like losing live trading state would.
_DEFAULT_LARGE_FILE_RETENTION_COUNT = 4  # ~4 days at that cadence.
_DEFAULT_LARGE_FILES = frozenset({"series_watcher.db", "candidate_log.db", "market_history.db"})
# The three data/*.db files with no row-level retention by design (measured
# 2026-08-30: 24.4GB, 2.8GB, and - once Task 1 of this plan ships - a
# capped market_history.db respectively). A full 6h/14-count snapshot of
# these dominated data/backups/ before this split existed.
```

Insert a shared `_add_column_if_missing` helper (same idiom as `services/candidate_log.py:67-73`/`services/title_cache.py`) right before `_connect`:

```python
def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, coltype: str):
    # Same idiom as services/candidate_log.py/services/title_cache.py -
    # CREATE TABLE IF NOT EXISTS alone doesn't add a column to an existing
    # table with existing rows.
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
```

Modify `_connect` (lines 68-88) to add the `tier` column:

```python
def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS backup_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at REAL NOT NULL,
            finished_at REAL NOT NULL,
            snapshot_name TEXT NOT NULL,
            files_ok INTEGER NOT NULL,
            files_failed INTEGER NOT NULL,
            total_bytes INTEGER NOT NULL,
            pruned_count INTEGER NOT NULL,
            error TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_backup_runs_started_at ON backup_runs (started_at)")
    # tier (2026-08-30): which of the two independent backup cadences a run
    # belongs to - "regular" (small, account-critical files) or "large"
    # (the permanently-growing history files). NULL on rows written before
    # this column existed; recent()/latest() treat NULL as "regular" since
    # that's the cadence those historical runs actually ran on.
    _add_column_if_missing(conn, "backup_runs", "tier", "TEXT")
    return conn
```

Replace `_data_db_files` (lines 91-100) with:

```python
def _data_db_files(*, exclude: frozenset[str] = frozenset(), only: frozenset[str] | None = None) -> list[Path]:
    """Every data/*.db file at call time, backup_log.db included (its own
    run history is worth keeping too) - deliberately a fresh glob every
    call, not a cached list, so a newly-added persistence module's file is
    picked up automatically with no registration step. Only matches files
    directly in data/ (glob's * doesn't cross the data/backups/ or
    data/backups_large/ boundary), so this can never recurse into its own
    prior output.

    exclude/only (2026-08-30) select this call's tier: the regular tier
    passes exclude=large_files to skip the permanently-growing files; the
    large tier passes only=large_files to back up nothing else. Passing
    both is never done by this module's own callers - only is checked
    first and, if given, exclude is ignored entirely."""
    if not DATA_DIR.exists():
        return []
    files = sorted(DATA_DIR.glob("*.db"))
    if only is not None:
        return [f for f in files if f.name in only]
    return [f for f in files if f.name not in exclude]
```

Replace `_prune_old_snapshots` (lines 120-135) with:

```python
def _prune_old_snapshots(retention_count: int, snapshot_root: Path | None = None) -> list[str]:
    """Deletes whole snapshot directories beyond retention_count, oldest
    first (snapshot names are UTC timestamps formatted to sort
    lexicographically in chronological order, so a plain name sort is
    enough - no need to parse them back into datetimes). retention_count
    <= 0 disables pruning entirely (an explicit opt-out, not a footgun -
    matches this app's other "0/None means off" config conventions).

    snapshot_root (2026-08-30) lets the large tier prune its own
    LARGE_BACKUP_DIR independently of the regular tier's BACKUP_DIR -
    defaults to BACKUP_DIR so every pre-tiering call site keeps working
    unchanged."""
    root = snapshot_root if snapshot_root is not None else BACKUP_DIR
    if retention_count <= 0 or not root.exists():
        return []
    snapshots = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name)
    to_prune = snapshots[:-retention_count] if len(snapshots) > retention_count else []
    pruned_names = []
    for p in to_prune:
        shutil.rmtree(p, ignore_errors=True)
        pruned_names.append(p.name)
    return pruned_names
```

Replace `run_backup_cycle` (lines 138-183) with:

```python
def run_backup_cycle(
    retention_count: int = _DEFAULT_RETENTION_COUNT, now: float | None = None, *,
    tier: str = "regular", exclude: frozenset[str] = frozenset(), only: frozenset[str] | None = None,
    snapshot_root: Path | None = None,
) -> dict:
    """The full cycle for one tier: snapshot the tier's own file selection
    (exclude/only, see _data_db_files), prune that tier's own old snapshots
    (snapshot_root, see _prune_old_snapshots), record the run tagged with
    tier. Synchronous and blocking by design - sqlite3's backup() is a
    blocking call with no async variant, so the async wiring below
    (_run_backup_background) is what keeps this off the event loop, not
    this function itself. That also makes this directly callable from a
    plain script/cron with no asyncio involved at all.

    One bad file never aborts the whole run (fault_log records it and the
    cycle continues) - the same "degrade honestly, keep going" idiom the
    rest of this app already applies to REST fetches; a paper broker DB
    that's momentarily locked shouldn't cost the signal log its backup
    too."""
    root = snapshot_root if snapshot_root is not None else BACKUP_DIR
    started_at = now if now is not None else time.time()
    snapshot_name = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(started_at))
    snapshot_dir = root / snapshot_name
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    files_ok, files_failed, total_bytes = 0, 0, 0
    errors: list[str] = []
    for src in _data_db_files(exclude=exclude, only=only):
        try:
            total_bytes += _backup_one_file(src, snapshot_dir)
            files_ok += 1
        except Exception as exc:
            files_failed += 1
            errors.append(f"{src.name}: {type(exc).__name__}: {exc}")
            fault_log.record("backup", "backup_one_file", exc, context=src.name)

    pruned = _prune_old_snapshots(retention_count, snapshot_root=root)
    finished_at = time.time()

    with _connect() as conn:
        conn.execute(
            "INSERT INTO backup_runs "
            "(started_at, finished_at, snapshot_name, files_ok, files_failed, total_bytes, pruned_count, error, tier) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (started_at, finished_at, snapshot_name, files_ok, files_failed, total_bytes, len(pruned),
             "; ".join(errors) if errors else None, tier),
        )

    return {
        "snapshot": snapshot_name, "tier": tier, "files_ok": files_ok, "files_failed": files_failed,
        "total_bytes": total_bytes, "pruned": pruned, "duration_sec": round(finished_at - started_at, 3),
        "errors": errors,
    }
```

Replace `recent`/`latest` (lines 186-198) with:

```python
def recent(limit: int = 20, tier: str = "regular") -> list[dict]:
    cols = ["id", "started_at", "finished_at", "snapshot_name", "files_ok", "files_failed",
            "total_bytes", "pruned_count", "error", "tier"]
    # Rows written before the tier column existed have tier IS NULL - treat
    # those as "regular" (the only cadence that existed then), so a fresh
    # deploy of this change doesn't misread real historical recency as
    # "overdue." Only the regular-tier query needs this; "large" never had
    # pre-migration rows.
    where = "WHERE tier = ? OR tier IS NULL" if tier == "regular" else "WHERE tier = ?"
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM backup_runs {where} ORDER BY started_at DESC LIMIT ?",
            (tier, limit),
        ).fetchall()
    return [dict(zip(cols, r)) for r in rows]


def latest(tier: str = "regular") -> dict | None:
    rows = recent(limit=1, tier=tier)
    return rows[0] if rows else None
```

Replace `_run_backup_background` (lines 201-213) with:

```python
async def _run_backup_background(
    retention_count: int, *, tier: str = "regular", exclude: frozenset[str] = frozenset(),
    only: frozenset[str] | None = None, snapshot_root: Path | None = None, state_key: str = "backup",
) -> None:
    """Background-task wrapper, same split as catalog_scan._scan_catalog_
    batch_background/discovery_cache._refresh_discovery_cache_background -
    owns releasing the "running" flag regardless of outcome via finally.
    asyncio.to_thread is what actually keeps run_backup_cycle's blocking
    sqlite3.backup() calls off the event loop; without it, backing up
    several MB-scale files would stall the trading loop's own tick timing
    for the duration.

    state_key (2026-08-30) picks which of state["backup"]/state["backup_large"]
    this run's "running" flag belongs to, so the two tiers' overlap guards
    never share state."""
    backup_state = state[state_key]
    try:
        await asyncio.to_thread(
            run_backup_cycle, retention_count, tier=tier, exclude=exclude, only=only, snapshot_root=snapshot_root,
        )
    finally:
        backup_state["running"] = False
```

Replace `_maybe_run_backup` (lines 216-259) with:

```python
def _maybe_run_backup(cfg: dict) -> None:
    """Kicks off _run_backup_background as an independent background task
    if a backup is due and none is already running - never awaited by the
    calling tick, same fire-and-forget shape as _maybe_scan_catalog_batch/
    _maybe_refresh_discovery_cache. Synchronous save for one lazy, one-time-
    per-process-lifetime DB read below - this doesn't otherwise do I/O of
    its own.

    This is the "regular" tier only (small, account-critical files) -
    excludes cfg's backup.large_files, which _maybe_run_large_backup below
    backs up on its own, longer cadence. See this module's own docstring
    and _DEFAULT_LARGE_FILES for why: those files (series_watcher.db,
    candidate_log.db, market_history.db) grow forever by design, and every
    snapshot on this tier's 6h/14-count cadence used to carry a full copy
    of them (292GB measured 2026-08-30 before this split).

    Real bug found and fixed live 2026-08-23, same "module quality" pass as
    the rest of this session: state["backup"]["last_started_at"] is pure
    in-memory state (services/app_state.py's default, 0.0), so it resets to
    "never" on every process restart - not just a real reboot, but every
    single `uvicorn --reload` reload this dev environment does routinely on
    any .py edit (including test files, per CLAUDE.md's dev-workflow notes).
    Each reset made the "due" check below fire an immediate full backup
    (every data/*.db file, ~9.4GB, ~40-50s in a background thread) regardless
    of how recently one had actually completed - confirmed live: 37 runs in
    4.4h against a configured 6h interval_sec, 28 of 36 gaps under 200s. Each
    one's real disk I/O measurably contended with the live trading loop's own
    SQLite reads/writes on the same disk - tick_phase_timings.market_fetch
    was observed at 6-8s (vs. a 6s configured poll_interval_sec) during this
    window. Fixed by seeding from the already-persisted backup_runs history
    (this function's own record of every run, via backup_log.db) on cold
    start instead of trusting in-memory state alone - a restart no longer
    means immediately overdue - just unknown, so go check what actually
    happened before deciding."""
    backup_cfg = cfg.get("backup") or {}
    if not backup_cfg.get("enabled", True):
        return
    backup_state = state["backup"]
    interval = backup_cfg.get("interval_sec", _DEFAULT_INTERVAL_SEC)
    now_ts = time.time()
    if backup_state["last_started_at"] == 0.0:
        last = latest(tier="regular")
        backup_state["last_started_at"] = last["started_at"] if last else 0.0
    due = now_ts - backup_state["last_started_at"] > interval
    if due and not backup_state["running"]:
        backup_state["running"] = True
        backup_state["last_started_at"] = now_ts
        retention_count = backup_cfg.get("retention_count", _DEFAULT_RETENTION_COUNT)
        large_files = frozenset(backup_cfg.get("large_files", _DEFAULT_LARGE_FILES))
        backup_state["task"] = task_supervisor.supervise(
            lambda: _run_backup_background(retention_count, tier="regular", exclude=large_files),
            component="backup", operation="run",
        )


def _maybe_run_large_backup(cfg: dict) -> None:
    """Second, independent backup tier for the files CLAUDE.md's
    "accumulated history is a first-class asset" rule keeps growing forever
    (series_watcher.db's raw_trades, candidate_log.db's rejection_events,
    market_history.db's snapshots) - see _DEFAULT_LARGE_FILES and this
    module's own docstring for why these can't share the regular tier's
    6h/14-count cadence without every snapshot ballooning in lockstep with
    the source file (292GB measured 2026-08-30 across 13 regular snapshots
    once series_watcher.db alone reached 24GB). A longer interval and
    shorter retention here trades disaster-recovery freshness for these
    specific files against disk - an explicit, configured choice
    (backup.large_file_interval_sec/large_file_retention_count), not a
    silent one; the regular tier's own cadence for the small,
    account-critical files (_maybe_run_backup) is untouched.

    Same due()/overlap-guard/cold-start-reseed shape as _maybe_run_backup,
    against its own state["backup_large"] and its own tier="large" history
    (latest(tier="large")) so a restart doesn't misread the regular tier's
    recency as this tier's."""
    backup_cfg = cfg.get("backup") or {}
    if not backup_cfg.get("enabled", True):
        return
    backup_state = state["backup_large"]
    interval = backup_cfg.get("large_file_interval_sec", _DEFAULT_LARGE_FILE_INTERVAL_SEC)
    now_ts = time.time()
    if backup_state["last_started_at"] == 0.0:
        last = latest(tier="large")
        backup_state["last_started_at"] = last["started_at"] if last else 0.0
    due = now_ts - backup_state["last_started_at"] > interval
    if due and not backup_state["running"]:
        backup_state["running"] = True
        backup_state["last_started_at"] = now_ts
        retention_count = backup_cfg.get("large_file_retention_count", _DEFAULT_LARGE_FILE_RETENTION_COUNT)
        large_files = frozenset(backup_cfg.get("large_files", _DEFAULT_LARGE_FILES))
        backup_state["task"] = task_supervisor.supervise(
            lambda: _run_backup_background(
                retention_count, tier="large", only=large_files,
                snapshot_root=LARGE_BACKUP_DIR, state_key="backup_large",
            ),
            component="backup", operation="run_large",
        )
```

Replace the `if __name__ == "__main__":` block (lines 262-269) with:

```python
if __name__ == "__main__":
    import json

    from services.config.config_store import config_store

    _cfg = (config_store.get().get("backup") or {})
    _large_files = frozenset(_cfg.get("large_files", _DEFAULT_LARGE_FILES))
    regular_result = run_backup_cycle(
        _cfg.get("retention_count", _DEFAULT_RETENTION_COUNT), tier="regular", exclude=_large_files,
    )
    large_result = run_backup_cycle(
        _cfg.get("large_file_retention_count", _DEFAULT_LARGE_FILE_RETENTION_COUNT),
        tier="large", only=_large_files, snapshot_root=LARGE_BACKUP_DIR,
    )
    print(json.dumps({"regular": regular_result, "large": large_result}, indent=2))
```

(Both tiers now run from the standalone CLI path too — a real deployment's cron/systemd timer needs both, per the module's own docstring on why this path exists independently of the app process's uptime.)

Also add one line to the top of the module docstring (right after the existing first paragraph, before "Uses sqlite3.Connection.backup()...") noting the split:

```
Two independent tiers as of 2026-08-30 (see _DEFAULT_LARGE_FILES): a
"regular" tier for small, account-critical files on the original 6h/14-
count cadence, and a "large" tier for the files that grow forever by
design, on a longer, separately-configured cadence into their own
LARGE_BACKUP_DIR - see _maybe_run_large_backup's own docstring for why.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_backup.py -v`
Expected: PASS (all tests, old and new)

- [ ] **Step 5: Commit**

```bash
git add services/backup/backup.py tests/test_backup.py
git commit -m "feat: split backup.py into a regular and large-file snapshot tier"
```

---

### Task 3: Wire the large tier into the app

**Files:**
- Modify: `services/app_state.py:277`
- Modify: `services/backup/__init__.py`
- Modify: `main.py` (import block + `_SCHEDULER_TRIGGERS`)
- Modify: `config/settings.yaml` (`backup:` section)
- Modify: `.gitignore`
- Test: `tests/test_main_scheduler_loops.py`

**Interfaces:**
- Consumes: `backup._maybe_run_large_backup`, `backup.LARGE_BACKUP_DIR` (Task 2).
- Produces: `state["backup_large"]` app-state entry; `_SCHEDULER_TRIGGERS` includes `("backup_large", _maybe_run_large_backup)`, consumed by Task 4's route work only insofar as it reads `state["backup_large"]`.

- [ ] **Step 1: Add the new app-state entry**

Modify `services/app_state.py:277` from:

```python
    "backup": {"running": False, "last_started_at": 0.0, "task": None},
```

to:

```python
    "backup": {"running": False, "last_started_at": 0.0, "task": None},
    "backup_large": {"running": False, "last_started_at": 0.0, "task": None},
```

- [ ] **Step 2: Re-export the new trigger from the package**

Modify `services/backup/__init__.py` from:

```python
from services.backup.backup import (  # noqa: F401
    _data_db_files, _DEFAULT_INTERVAL_SEC, _DEFAULT_RETENTION_COUNT, _maybe_run_backup,
    _prune_old_snapshots, _run_backup_background, BACKUP_DIR, DATA_DIR, latest, recent,
    run_backup_cycle,
)
```

to:

```python
from services.backup.backup import (  # noqa: F401
    _data_db_files, _DEFAULT_INTERVAL_SEC, _DEFAULT_LARGE_FILE_INTERVAL_SEC,
    _DEFAULT_LARGE_FILE_RETENTION_COUNT, _DEFAULT_LARGE_FILES, _DEFAULT_RETENTION_COUNT,
    _maybe_run_backup, _maybe_run_large_backup, _prune_old_snapshots, _run_backup_background,
    BACKUP_DIR, DATA_DIR, LARGE_BACKUP_DIR, latest, recent, run_backup_cycle,
)
```

- [ ] **Step 3: Write the failing test for scheduler registration**

Add to `tests/test_main_scheduler_loops.py`, near the existing `test_scheduler_loop_invokes_the_trigger_with_live_config_each_interval` test:

```python
def test_backup_large_trigger_is_registered_in_scheduler_triggers():
    from main import _SCHEDULER_TRIGGERS
    from services.backup import _maybe_run_large_backup

    names_to_triggers = dict(_SCHEDULER_TRIGGERS)
    assert names_to_triggers["backup_large"] is _maybe_run_large_backup
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `pytest tests/test_main_scheduler_loops.py -k backup_large -v`
Expected: FAIL with `KeyError: 'backup_large'` (`_maybe_run_large_backup` itself already exists and imports cleanly — Task 2 added it to `backup.py` and Step 2 above already re-exports it — only its registration in `_SCHEDULER_TRIGGERS` is still missing)

- [ ] **Step 5: Register the second scheduler trigger**

Modify `main.py`'s import block (the line reading `from services.backup import _maybe_run_backup  # noqa: E402`, near line 119) to:

```python
from services.backup import _maybe_run_backup, _maybe_run_large_backup  # noqa: E402
```

Modify `main.py:557-573` (`_SCHEDULER_TRIGGERS`) from:

```python
_SCHEDULER_TRIGGERS = (
    ("signal_resolution", _maybe_check_signal_resolutions),
    ("backup", _maybe_run_backup),
    ("research", _maybe_run_research),
```

to:

```python
_SCHEDULER_TRIGGERS = (
    ("signal_resolution", _maybe_check_signal_resolutions),
    ("backup", _maybe_run_backup),
    ("backup_large", _maybe_run_large_backup),
    ("research", _maybe_run_research),
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `pytest tests/test_main_scheduler_loops.py -k backup_large -v`
Expected: PASS

- [ ] **Step 7: Add the config keys via the config-field-edit skill**

Follow `.claude/skills/config-field-edit/SKILL.md` again (a second round in the same session — re-snapshot fresh per its "Doing this more than once" section, since Task 1 Step 6 already changed `HEAD`):

```bash
cp config/settings.yaml /tmp/settings.yaml.bak
git show HEAD:config/settings.yaml > config/settings.yaml
```

Edit the freshly-reset `config/settings.yaml`'s `backup:` section from:

```yaml
backup:
  enabled: true
  interval_sec: 21600  # 6h - see services/backup/CHEATSHEET.md
  retention_count: 14  # ~3.5 days at the default interval - kept modest because
  # series_watcher.db's raw_trades is deliberately never pruned (CLAUDE.md's
  # "accumulated history is a first-class asset" rule) and grows without
  # bound, so every snapshot's real size grows over time too - revisit this
  # number as data/'s total footprint grows (was 8.3GB as of 2026-08-23,
  # after pruning a since-fixed 5.7GB game_state.db bloat - see that
  # module's CHEATSHEET/status.html phase 126 for the full incident).
```

to:

```yaml
backup:
  enabled: true
  interval_sec: 21600  # 6h - see services/backup/CHEATSHEET.md. Regular
  # tier only (small, account-critical files) as of 2026-08-30 - see
  # large_file_interval_sec below for the second tier.
  retention_count: 14  # ~3.5 days at the default interval. Safe to keep
  # unchanged as of 2026-08-30's tiering split: large_files below (the
  # files that grow without bound per CLAUDE.md's "accumulated history is
  # a first-class asset" rule) are no longer part of this tier's snapshots,
  # so this tier's own files stay single-digit-MB regardless of retention
  # count. Before the split, every snapshot on this cadence carried a full
  # copy of series_watcher.db - measured at 292GB across 13 snapshots once
  # that file alone reached 24.4GB (2026-08-30 investigation, see
  # services/backup/backup.py's module docstring).
  large_files:
    - series_watcher.db
    - candidate_log.db
    - market_history.db
  large_file_interval_sec: 86400  # 24h - deliberately longer than the
  # regular tier's 6h: these files are already durable, continuously-
  # accumulating append logs, so losing a few hours of them to a backup gap
  # costs nothing like losing live trading state would.
  large_file_retention_count: 4  # ~4 days at that cadence.
```

```bash
git add config/settings.yaml
git commit -m "chore: add backup large-file tier config keys"
cp /tmp/settings.yaml.bak config/settings.yaml
```

Re-apply the identical `backup:` section changes (same keys, same position, same values) to this just-restored file, then verify:

```bash
git diff config/settings.yaml
```

Expected: only the live-tuned values differ from the new `HEAD` — the `large_files`/`large_file_interval_sec`/`large_file_retention_count` lines and the updated comments should be identical in both, i.e. no diff on those lines.

- [ ] **Step 8: Add the new snapshot directory to `.gitignore`**

Modify `.gitignore` from:

```
# services/backup/backup.py's snapshot output - one level below the flat
# data/*.db pattern above, so it needs its own explicit line.
data/backups/
```

to:

```
# services/backup/backup.py's snapshot output - one level below the flat
# data/*.db pattern above, so it needs its own explicit line. Two tiers as
# of 2026-08-30 - see that module's own docstring.
data/backups/
data/backups_large/
```

- [ ] **Step 9: Commit**

```bash
git add services/app_state.py services/backup/__init__.py main.py config/settings.yaml .gitignore tests/test_main_scheduler_loops.py
git commit -m "feat: wire the large-file backup tier into the app's scheduler and config"
```

---

### Task 4: Surface the large tier in `/api/backup/*`

**Files:**
- Modify: `services/backup/routes.py`
- Test: create `tests/test_backup_routes.py`

**Interfaces:**
- Consumes: `state["backup_large"]`, `backup.latest(tier=...)`, `backup.LARGE_BACKUP_DIR`, `backup._DEFAULT_LARGE_FILE_RETENTION_COUNT`, `backup._DEFAULT_LARGE_FILES` (all from Tasks 2-3).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_backup_routes.py`, following the exact convention
`tests/test_storage_health_routes.py` already established for testing a
routes-adjacent module in this repo (`import main`, `TestClient(main.app)`
at module scope, relying on `tests/conftest.py`'s global
`install_runtime_isolation()` for every `PERSISTENCE_MODULE_PATHS`/
`DATA_DIR_MODULE_PATHS` entry's `DB_PATH`/`DATA_DIR` — `services.backup.
backup` is in both, but `BACKUP_DIR`/`LARGE_BACKUP_DIR` are derived
constants that global mechanism does not re-derive after redirecting
`DATA_DIR`, so this file's own `_isolated` fixture still overrides them
explicitly, same as `test_storage_health_routes.py`'s own fixture does for
`storage_health.DATA_DIR`):

```python
"""GET/POST /api/backup/* (services/backup/routes.py), extended 2026-08-30
to report the large-file tier alongside the regular one - see
services/backup/backup.py's own module docstring for why the two tiers
exist. Relies on tests/conftest.py's global install_runtime_isolation() for
services.backup.backup's DB_PATH/DATA_DIR (it's in both
PERSISTENCE_MODULE_PATHS and DATA_DIR_MODULE_PATHS); BACKUP_DIR/
LARGE_BACKUP_DIR are derived constants that mechanism doesn't re-derive, so
this file's own _isolated fixture overrides them explicitly too - same
shape as tests/test_storage_health_routes.py's own fixture."""
import sqlite3
from pathlib import Path

import pytest

import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from services.app_state import state  # noqa: E402
from services.backup import backup  # noqa: E402

client = TestClient(main.app)


def _make_real_sqlite_file(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(backup, "DATA_DIR", data_dir)
    monkeypatch.setattr(backup, "BACKUP_DIR", data_dir / "backups")
    monkeypatch.setattr(backup, "LARGE_BACKUP_DIR", data_dir / "backups_large")
    monkeypatch.setattr(backup, "DB_PATH", data_dir / "backup_log.db")
    state["backup"] = {"running": False, "last_started_at": 0.0, "task": None}
    state["backup_large"] = {"running": False, "last_started_at": 0.0, "task": None}
    yield


def test_status_reports_both_tiers():
    resp = client.get("/api/backup/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "large_tier" in body
    assert body["large_tier"]["last_run"] is None
    assert body["large_tier"]["snapshot_count"] == 0


def test_status_large_tier_reflects_a_completed_run():
    _make_real_sqlite_file(backup.DATA_DIR / "series_watcher.db")
    backup.run_backup_cycle(retention_count=4, now=1000.0, tier="large",
                             only=frozenset({"series_watcher.db"}), snapshot_root=backup.LARGE_BACKUP_DIR)

    body = client.get("/api/backup/status").json()

    assert body["large_tier"]["last_run"]["tier"] == "large"
    assert body["large_tier"]["snapshot_count"] == 1


def test_run_route_defaults_to_regular_tier():
    _make_real_sqlite_file(backup.DATA_DIR / "paper_broker.db")
    _make_real_sqlite_file(backup.DATA_DIR / "series_watcher.db")

    resp = client.post("/api/backup/run")

    assert resp.status_code == 200
    body = resp.json()
    assert body["tier"] == "regular"
    snapshot_dir = backup.BACKUP_DIR / body["snapshot"]
    assert (snapshot_dir / "paper_broker.db").exists()
    assert not (snapshot_dir / "series_watcher.db").exists()


def test_run_route_large_tier_backs_up_only_large_files():
    _make_real_sqlite_file(backup.DATA_DIR / "paper_broker.db")
    _make_real_sqlite_file(backup.DATA_DIR / "series_watcher.db")

    resp = client.post("/api/backup/run?tier=large")

    assert resp.status_code == 200
    body = resp.json()
    assert body["tier"] == "large"
    snapshot_dir = backup.LARGE_BACKUP_DIR / body["snapshot"]
    assert (snapshot_dir / "series_watcher.db").exists()
    assert not (snapshot_dir / "paper_broker.db").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_backup_routes.py -v`
Expected: FAIL — `KeyError: 'large_tier'` on the status tests; `test_run_route_large_tier_backs_up_only_large_files` gets a 422 (unexpected `tier` query param) or backs up everything.

- [ ] **Step 3: Implement the route changes**

Replace `services/backup/routes.py` in full:

```python
"""
Backup status/history/manual-trigger routes - the informativeness half of
services/backup/backup.py (CLAUDE.md's "surface enough of its own behavior
for a human to trust and reason about it, not just run it blind" applied
to a mechanism whose whole job is to sit quietly in the background until
the day it's needed).

Two tiers as of 2026-08-30 (see backup.py's own module docstring): this
file surfaces both, but backup_overdue_finding (services/storage_health/
storage_health.py) and GET /api/quality/summary still check only the
regular tier's recency - that's the tier whose staleness actually matters
for "is my critical backup fresh" alerting. Large-tier staleness is visible
here (GET /api/backup/status's large_tier key) but not yet alerted on -
a deliberate scope boundary, not an oversight.
"""
import asyncio

from fastapi import APIRouter

from services.app_state import state
from services.backup import backup
from services.config.config_store import config_store

router = APIRouter()


@router.get("/api/backup/status")
async def get_backup_status():
    backup_state = state["backup"]
    large_state = state["backup_large"]
    last = backup.latest(tier="regular")
    last_large = backup.latest(tier="large")
    snapshot_count = len(list(backup.BACKUP_DIR.iterdir())) if backup.BACKUP_DIR.exists() else 0
    large_snapshot_count = len(list(backup.LARGE_BACKUP_DIR.iterdir())) if backup.LARGE_BACKUP_DIR.exists() else 0
    return {
        "enabled": (config_store.get().get("backup") or {}).get("enabled", True),
        "running": backup_state["running"],
        "last_started_at": backup_state["last_started_at"] or None,
        "last_run": last,
        "snapshot_count": snapshot_count,
        "large_tier": {
            "running": large_state["running"],
            "last_started_at": large_state["last_started_at"] or None,
            "last_run": last_large,
            "snapshot_count": large_snapshot_count,
        },
    }


@router.get("/api/backup/history")
async def get_backup_history(limit: int = 20, tier: str = "regular"):
    return {"runs": backup.recent(limit=limit, tier=tier)}


@router.post("/api/backup/run")
async def trigger_backup_run(tier: str = "regular"):
    """Manual, synchronous trigger - an operator gets the real result back
    immediately (files backed up, bytes, any errors), not just a fire-and-
    forget acknowledgement, since the whole point of running this by hand
    is usually "I want to know this succeeded before I do something risky."
    Runs off the event loop the same way the periodic path does
    (asyncio.to_thread) so it doesn't stall the trading loop or other
    in-flight requests for however long the backup takes.

    tier (2026-08-30): "regular" (default, unchanged behavior - the small,
    account-critical files) or "large" (the permanently-growing history
    files, backup.py's own large_files config)."""
    backup_cfg = config_store.get().get("backup") or {}
    large_files = frozenset(backup_cfg.get("large_files", backup._DEFAULT_LARGE_FILES))
    if tier == "large":
        retention_count = backup_cfg.get("large_file_retention_count", backup._DEFAULT_LARGE_FILE_RETENTION_COUNT)
        return await asyncio.to_thread(
            backup.run_backup_cycle, retention_count, tier="large", only=large_files,
            snapshot_root=backup.LARGE_BACKUP_DIR,
        )
    retention_count = backup_cfg.get("retention_count", backup._DEFAULT_RETENTION_COUNT)
    return await asyncio.to_thread(
        backup.run_backup_cycle, retention_count, tier="regular", exclude=large_files,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_backup_routes.py -v`
Expected: PASS (all 4 tests)

- [ ] **Step 5: Run the full backup-related suite together**

Run: `pytest tests/test_backup.py tests/test_backup_routes.py tests/test_market_history.py tests/test_main_scheduler_loops.py -v`
Expected: PASS (every test from all four tasks)

- [ ] **Step 6: Commit**

```bash
git add services/backup/routes.py tests/test_backup_routes.py
git commit -m "feat: surface the large-file backup tier through GET/POST /api/backup/*"
```

---

### Task 5: Live verification under ddev

**Files:** none (verification only).

- [ ] **Step 1: Confirm ddev is up**

Run: `ddev describe` — start it if not running (`ddev start`).

- [ ] **Step 2: Confirm the app picks up the new config without error**

Run: `curl -sk https://kalshi-whale-poc.ddev.site:8443/api/state | head -c 200` — expect a normal JSON response, no 500.

- [ ] **Step 3: Trigger both tiers manually and inspect real output**

Run: `curl -sk -X POST https://kalshi-whale-poc.ddev.site:8443/api/backup/run` — expect `"tier": "regular"`, `files_failed: 0`, and confirm via `ls data/backups/<returned snapshot>/` that `series_watcher.db`/`candidate_log.db`/`market_history.db` are *absent* from it.

Run: `curl -sk -X POST "https://kalshi-whale-poc.ddev.site:8443/api/backup/run?tier=large"` — expect `"tier": "large"`, and confirm via `ls data/backups_large/<returned snapshot>/` that exactly `series_watcher.db`, `candidate_log.db`, and `market_history.db` are present (and nothing else).

- [ ] **Step 4: Confirm status reporting**

Run: `curl -sk https://kalshi-whale-poc.ddev.site:8443/api/backup/status | python3 -m json.tool` — expect a `large_tier` key with a fresh `last_run`.

- [ ] **Step 5: Confirm market_history pruning is live-reachable**

Run: `curl -sk https://kalshi-whale-poc.ddev.site:8443/api/quality/summary | python3 -m json.tool | grep -A3 market_history` (or equivalent) — or, more directly, since `_maybe_prune_capture_stores` runs on an hourly gate, verify via `ddev logs -s fastapi | grep market_history` after forcing a call, or check `data/market_history.db`'s `snapshots` row count trends downward relative to its growth rate over the next few hours of normal operation — note this in `docs/next-action.md` as a delayed-verification follow-up if the session ends before an hour has passed.

- [ ] **Step 6: Do not delete the existing 292GB `data/backups/` directory as part of this plan**

This plan stops the *growth*; it does not retroactively shrink what already exists. Deleting old snapshots is a separate, explicit disk-reclamation decision (how many of the 13 existing full snapshots are worth keeping as a one-time disaster-recovery baseline before the tiering split) — flag it to the user rather than doing it unasked, per this repo's "genuine design/architecture decision needs a human call" convention.

## Self-Review Notes

- **Spec coverage:** every measured fact and every "explicitly does NOT do" item in the Design Summary maps to a task or an explicit scope-boundary note (Task 1 = market_history gap; Tasks 2-4 = backup tiering; Task 5 = live verification + the disk-reclamation call-out). No spec item is unaddressed.
- **Placeholder scan:** no TBD/TODO, no "add appropriate error handling," no unshown "similar to Task N" — every step's code is complete and copy-pasteable.
- **Type/signature consistency:** `run_backup_cycle`'s new `tier`/`exclude`/`only`/`snapshot_root` keyword-only params are used identically across Task 2 (definition + tests), Task 3 (`_maybe_run_backup`/`_maybe_run_large_backup`), and Task 4 (`routes.py`); `recent(limit, tier)`/`latest(tier)` match across Task 2's definition and every caller in Tasks 3-4; `state["backup_large"]` shape (`{"running", "last_started_at", "task"}`) matches `state["backup"]`'s existing shape exactly, as used in Task 2's `_run_backup_background`, Task 3's `app_state.py` entry, and Task 4's route.
