"""Fix from the #601 benchmark's adversarial review (PR #603): the WORK
copy candidate_log_fix_families.py/family1_followup.py's destructive
(UPDATE) sections depend on was being made by an undocumented manual `cp`
step, not by anything committed - a reader trying to reproduce Section 3/4
purely from committed code hit FileNotFoundError. This is that step, made
explicit and re-runnable.

Run after bench/copy_dbs.py (needs /tmp/bench_dbs/candidate_log.db to
already exist):

  docker exec ddev-kalshi-whale-poc-fastapi nice -n 19 ionice -c3 \
      python3 /app/.claude/worktrees/<wt>/bench/make_work_copy.py

A plain `cp`, not another backup-API pass - the pristine copy is already a
consistent single-step WAL snapshot (bench/copy_dbs.py), and this file's
only job is to give the destructive sections their own disposable copy so
they never mutate the read-only timing baseline.
"""
import shutil
import sys

PRISTINE = "/tmp/bench_dbs/candidate_log.db"
WORK = "/tmp/bench_dbs/candidate_log_work.db"


def main() -> int:
    shutil.copy(PRISTINE, WORK)
    print(f"copied {PRISTINE} -> {WORK}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
