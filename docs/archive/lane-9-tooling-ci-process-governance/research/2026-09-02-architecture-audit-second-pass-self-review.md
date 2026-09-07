# Self-review — architecture audit second pass (2026-09-02)

Artifact: `docs/archive/lane-9-tooling-ci-process-governance/research/2026-09-02-architecture-audit-second-pass.md`.
Same author and context as the artifact, per the "nothing advances on one
pass" rule's first layer: a consistency and unaddressed-scope check before
independent effort is spent. Every number in the artifact was re-read
against the raw tool output it came from; the arithmetic below is the
check, not a re-derivation from the artifact's own prose.

## Internal-consistency checks (all passed after the fixes listed below)

- 1,187 "unable to open / readonly" lines = sum of the 10-minute buckets
  192+28+53+142+223+51+34+6+352+43+63 = 1,187. ✓
- 1,127 dropped capture_writer batches = 414 + 357 + 356. ✓ Matches the
  1,127 `capture_writer` ERROR lines counted in the 08:20–15:20Z window. ✓
- 156 of 258 `receive_to_decision` windows > 10 s = per-hour column sum
  19+22+18+18+28+9+2+0+9+10+0+5+7+2+2+4+1 = 156. ✓
- `market_history` 723 faults = 717 + 5 + 1. ✓ 1,042 error-severity faults
  in 24 h from `/api/health/pipeline.faults_last_24h.by_severity`. ✓
- Loop-stall figures (288 rows / 20.81 h / median 12,081 ms / p90 71,895 /
  max 114,779 / 147 ≥10 s / 34 ≥60 s / last 2 h 60-26-114,779) match the
  script output verbatim. ✓
- nginx hourly table (browser rows, 504/499, per-hour) matches the parsed
  output verbatim; "History-5" is the same five paths the script summed. ✓
- Observability gaps and per-hour sample counts match the history query
  output; the 408-minute gap starts 08:24Z. ✓
- 961,639 B / 6 s = 160 KB/s (C5). ✓ 14 runtime pins (C7) counted from
  `requirements.txt`. ✓
- Timeline ordering: 07:23:56 start → 08:24:45 first `unable to open` →
  08:24:48 first capture_writer drop → EMFILE at log line 14737 (150 lines
  after 14585 = 08:24:48) → reloads 14:45:34, 16:26–16:34, 17:31–17:32 →
  current worker 17:32:06. Consistent with the fault_log `first_seen`/
  `last_seen` decodes. ✓
- Units: every ms figure that is restated in minutes was divided by 60,000
  (562,930 → 9.4; 818,541 → 13.6; 533,989 → 8.9; 636,065 → 10.6; 408 min =
  6.8 h). ✓ Local (UTC−5) vs UTC is labeled on every log-derived time. ✓

## Found and fixed before adversarial review

1. §6.5 said `alerting.py` "does not read `fault_log` at all (grep: zero
   references)". Wrong as written: the module imports `fault_log` and calls
   `fault_log.record` for its own dispatch failures. Corrected to the
   precise fact: `check_and_alert` monitors three edge-detected conditions
   and never *reads* a fault row. §9's matching item made precise too.
2. §3.5 / §4.1 / §5 C6 / §6.3 reported `title_cache.db` at "40" handles;
   the fdinfo sample was `head -40`, so 40 is a lower bound. Corrected to
   "≥40 (sample capped at 40)" everywhere.
3. §3.5's fdinfo row said "all opened read-write"; the 60 sampled handles
   were 59 read-write and 1 read-only. Corrected.
4. §3.5's first census row said "during a `/api/quality/summary` probe";
   that probe had finished ten minutes earlier. Corrected to the probes
   that were actually concurrent (`/api/health/faults`,
   `/api/observability/history`).
5. §4.4 cited the wiped comment block as HEAD lines 184–205; the diff hunk
   (`@@ -179,30 +179,6 @@`, 3 context lines) removes lines 182–205.
   Corrected.
6. §1 item 4 and §3.4 said the queue "hit its cap in three hours"; the 21Z
   maximum was 19,999, one message short. Corrected in both places.
7. §4.1 said "three Claude sessions" were active during the incident; the
   evidence supports "at least three". Corrected.
8. Appendix: the 167 `with _connect(` count could read as contradicting the
   first audit's 142; added the regex-scope note (the first audit counted
   the narrower `with _connect() as conn:` form).

## Unaddressed scope, stated rather than hidden

- The fd-leak *mechanism* is not pinned (§4.1 says so and lists
  falsifiers in order). The artifact does not claim a mechanism.
- The stalls are not attributed (§4.3). The artifact names candidates and
  labels them unmeasured.
- `services/backup`, the 9 unclassified SQLite files, security/auth
  posture, and `alerting`'s condition set beyond the three named remain
  unaudited (Appendix "Not done in this pass").
- The Chromium background-throttling explanation for the ~1/min cadence is
  labeled as inference; the browser was not checked.
- The ruamel comment-attachment mechanism (§4.4) is labeled consistent-
  with, not verified-by-experiment; the falsifier is named.
- The first audit's "every pin carries a written justification" claim was
  not re-checked (C7 says so).
- No dimensional-analysis *plugin* pass was run on this document; the
  rule's mechanism targets code, and the artifact states inline units and
  its two conversions instead. If the adversarial review considers that a
  gap, it should say so.

## Scope check against the request

"Review the architectural audit ... do another pass on it, especially now
that Claude's understanding of project rules has changed." The artifact
(a) re-derives the sections that leaned on removed rules (§6, with §2 as
the map), (b) re-measures the live claims (§3), (c) corrects what no longer
holds (§5), (d) adds what the first audit could not have seen (§4), and
(e) restates the plan and open questions (§8, §9). It does not modify the
first audit beyond a pointer note at its top, and it changes no code,
config, or data. The `config/settings.yaml` working-tree change is
described, not touched.
