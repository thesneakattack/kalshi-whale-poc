---
name: persistence-safety
description: Use before adding or changing SQLite persistence, DB_PATH values, state stores, migrations, retention/pruning, backups, or tests involving persistent data. Ensures live data/*.db files cannot be touched by tests and new stores are wired into isolation, retention, backup, and diagnostics.
---

# Persistence Safety

Before broadly importing or wiring a persistent service:

1. Inventory every DB/file it owns and every `DB_PATH`.
2. Inspect the centralized pytest isolation mechanism and register the new
   persistence owner before any test can import runtime singletons.
3. Add/extend the hard guard proving tests cannot open repository `data/`.
4. Ensure static quality/audit tooling recognizes the persistence owner.
5. Use additive schema changes only. No destructive migration for this work.
6. Define:
   - schema initialization/migration,
   - retention/pruning,
   - backup inclusion,
   - restart behavior,
   - concurrency/locking assumptions,
   - failure logging.
7. Tests use only temp/copy fixtures. Never verify by injecting synthetic rows
   into the real historical DB.
8. Runtime health checks are read-only; integrity checks never auto-repair,
   vacuum, or delete.
9. If the store is sampled/diagnostic, bound its growth explicitly.
10. Verify a full test collection cannot reach the real DB even if this module
    is the first importer of `main`/`app_state`.
