# Follow-ups logged during autonomous 3-plan implementation (2026-08-31)

Per direct instruction: don't stop for input mid-execution; log anything needing a
human decision here instead, and continue. One entry per item, added as found.

## kalshi-python-async SDK pin is 2 minor versions behind (found during Task 1 of kalshi-category-data-completeness)

`requirements.txt` pins `kalshi-python-async==3.27.0`; PyPI's current latest is `3.29.0`
(confirmed via `pip index versions` inside the fastapi container). Concretely verified,
not assumed: the installed 3.27.0's `FeeType` enum (`kalshi_python_async.models.fee_type`)
has 3 values (`quadratic`, `quadratic_with_maker_fees`, `flat`); the 3.29.0 wheel (downloaded
and extracted directly to check) adds a 4th, `quadratic_with_combo_maker_fees`. Task 2's
`get_series_fee_changes` was *initially implemented* against the typed SDK path — a series
with that 4th fee type would have failed the call outright — but Task 2's own task-review
round caught this as a Critical finding (independently verified by the controller against
both wheels directly) and fixed it in the same task, before it shipped: `get_series_fee_changes`
now uses the raw `_get_json` path (commits `00b4ed3..7838c61`), matching `get_series_list`'s
existing precedent for the identical enum-gap risk. **Corrected 2026-08-31 (final whole-
branch review):** this entry originally said Task 2 shipped the bug "as a documented, flagged
limitation (not fixed)" — that was wrong; the specific `get_series_fee_changes` instance of
this gap is fixed. The underlying SDK-pin gap itself remains real and open (below).

This is a different thing from `docs/kalshi/`'s API docs mirror, which was already
re-synced to Trade API 3.29.0 per a resolved `docs/open-decisions.md` item — the SDK
*package* pin was not part of that resync and has separately drifted. This repo has a
prior, on-record incident where an old SDK pin (3.2.0) silently rejected real
positions/fills responses entirely, so a 2-version gap here could plausibly hide more
than just the one enum value found so far.

**Next action**: a dedicated pass (not squeezed into an unrelated task) comparing 3.27.0
vs 3.29.0's full changelog/schema diff against what this repo's `services/kalshi/`
boundary actually consumes, before deciding whether/how to bump the pin · you (decide
whether to prioritize this, and if so approve the version bump) · 2026-08-31
