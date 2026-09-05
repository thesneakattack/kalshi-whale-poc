"""Read-only backup copies of the live data/*.db files the settlement /
decision paths touch, into a container-local scratch dir. Uses the sqlite3
backup API against a `mode=ro` URI connection - never opens the live file
read-write, never modifies it. Runs inside the fastapi container:

  docker exec ddev-kalshi-whale-poc-fastapi nice -n 19 ionice -c3 \
      python3 /app/.claude/worktrees/<wt>/bench/copy_dbs.py
"""
import os
import sqlite3
import sys
import time

SRC_DIR = "/app/data"
DST_DIR = "/tmp/bench_dbs"
NAMES = [
    "candidate_ledger.db",
    "market_analyst.db",
    "settlement_edge.db",
    "signal_log.db",
    "market_history.db",
    "candidate_log.db",
]


def main() -> int:
    only = set(sys.argv[1:])
    os.makedirs(DST_DIR, exist_ok=True)
    for name in NAMES:
        if only and name not in only:
            continue
        t = time.perf_counter()
        src = sqlite3.connect(f"file:{SRC_DIR}/{name}?mode=ro", uri=True)
        dst_path = os.path.join(DST_DIR, name)
        if os.path.exists(dst_path):
            os.remove(dst_path)
        dst = sqlite3.connect(dst_path)
        # pages=-1: ONE backup step. A stepped backup (pages=N) restarts from
        # page 0 every time another connection writes the source between
        # steps - and candidate_log.db is written ~every second by
        # capture_writer, so the first attempt (pages=4096) looped forever
        # (653 GB read, 327 GB written, destination never past 1.3 GB). A
        # single step holds one WAL read snapshot for the whole copy; WAL
        # readers never block the live writer.
        src.backup(dst, pages=-1)
        dst.close()
        src.close()
        mb = os.path.getsize(dst_path) / 1048576
        print(f"{name}: {mb:.1f} MB in {time.perf_counter() - t:.1f}s", flush=True)
    print("DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
