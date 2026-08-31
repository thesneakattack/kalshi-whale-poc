# claudesuperpower.com toolkit assessment — 2026-08-31

Status: DONE. Scan complete, verdict below is final as of 2026-08-31.

## Task

User asked for an exhaustive, thorough scan of https://claudesuperpower.com/
— its plugins, subagents, skills, hooks, commands, and MCP servers — filtered
for what's actually useful to autotrade (kalshi-whale-poc): a Python/FastAPI
paper-trading terminal for Kalshi prediction markets, running under ddev
(Docker), SQLite persistence (no ORM), a vanilla-JS/esbuild frontend, real
Kalshi REST+WebSocket integration, Woodpecker CI, GitHub-hosted repo, already
using: GitNexus (code-graph MCP), chrome-devtools MCP, context7 MCP,
dimensional-analysis plugin, superpowers plugin (brainstorming/TDD/plans/etc),
github MCP. Explicitly NOT installed/available: Devil's Advocate MCP and
PagerDuty MCP (both need paid auth), 42crunch and second-opinion (disabled,
no account).

Site structure (confirmed via fetch of homepage):
- /plugins, /mcp, /skills, /subagents, /hooks, /commands — the 6 target
  categories, each presumably paginated/filterable.
- /use-cases/* — curated cross-category bundles (query-your-database,
  automate-code-review, give-claude-memory, search-the-web,
  write-and-run-tests, ship-to-production, monitor-and-debug, secure-your-code)
  — all plausibly relevant to this project, will check these first since
  they pre-filter to relevant intersections.
- /best/mcp/<category> — best-of lists by category (ai-tools, analytics,
  api-tools, automation, browser, cloud, communication, crypto, ...) — crypto
  is NOT relevant (autotrade is Kalshi prediction markets / regulated
  exchange, not blockchain/DeFi — confirm no overlap, do not conflate).

## Relevance filter (what counts as in-scope)

IN SCOPE: anything touching — Python backend testing/quality, FastAPI,
SQLite, async/WebSocket real-time data pipelines, REST API client
correctness, observability/monitoring/logging, CI/CD (GitHub Actions-adjacent
concepts even though this repo uses Woodpecker), code review /
static-analysis / security-review, database inspection tools, browser
automation/E2E testing (already have chrome-devtools — check for
overlap/upgrades), documentation-drift checking, git/GitHub workflow tooling,
financial/trading-domain-specific tooling, general-purpose debugging/planning
skills that could sit alongside the existing superpowers plugin.

OUT OF SCOPE (skip, don't deep-dive): mobile app dev (iOS/Android/React
Native), game dev, blockchain/crypto/DeFi/smart contracts, no-code/low-code
builders, marketing/SEO/CRM/Salesforce/HR tools, non-Python language-specific
tooling with no relevance (Rust/Go/Java/PHP-specific unless generically
useful), image/video generation, translation, anything requiring a paid
account we don't have and isn't clearly worth acquiring one for.

## Methodology (revised after initial recon)

The raw category listings are enormous and dominated by noise: 829 MCP
servers, 284 skills, 39 plugins-page-1-of-many, 108 pages of skills, etc.,
spanning Shopify/WordPress/crypto/gaming/mobile/etc. The site's `/use-cases/*`
pages turned out to be generic "featured" grabs, not hand-curated for a
trading app — mostly irrelevant results (DuckDB, Shopify, VoIP, Salesforce).

Better lever found: **`/best/<type>/<category>` pages** — curated top-N lists
per (type × category), e.g. `/best/mcp/finance`, `/best/skills/testing`,
`/best/subagents/security`. Full category list captured (174 total
type×category combos). Scan proceeds by fetching the priority-relevant
categories across all 5 populated types (plugins, mcp, skills, subagents,
commands — hooks has only one category, "other", checked once for its
entirety) rather than brute-forcing raw paginated listings.

Priority categories selected as in-scope for this project (rationale: maps to
actual autotrade work — Kalshi REST/WS integration, FastAPI backend, SQLite
persistence, real-time data pipeline correctness, quality/observability
tooling, CI, git/GitHub workflow, CLAUDE.md-heavy documentation discipline):

finance, databases, testing, security, monitoring, code-review,
code-intelligence, debugging, devops, developer-tools, version-control,
documentation, backend, api-tools, data-engineering, project-management
(light pass), automation (light pass).

Explicitly deprioritized/skipped as out of scope: analytics, communication,
content, design, ecommerce, frontend (vanilla-JS/esbuild, no framework —
low match), gaming, hardware, iot, learning, location, marketing, media,
memory (project already has a bespoke file-based memory system + CLAUDE.md
rulebook; only flagging something exceptional), mobile, payments, sales,
science, vector-search, web-scraping, writing, ai-ml (mostly agent-building
frameworks unrelated to this app's runtime), crypto (Kalshi is a CFTC-regulated
exchange, not blockchain/DeFi — do not conflate).

## Progress log

- [x] Homepage fetched, nav structure confirmed.
- [x] `/use-cases/*` — 8 pages fetched, found to be low-signal/generic; not
      pursuing further use-case pages.
- [x] `/best` index fetched — full 174 category-combo list captured above.
- [x] `/best/plugins/<cat>` × priority categories (finance, databases,
      testing, devops, security via secure-your-code use-case page)
- [x] `/best/mcp/<cat>` × priority categories (finance, databases,
      version-control, monitoring) + full sitemap keyword sweep (20,047 URLs
      across all 7 sitemap files, grepped for ~60 domain terms)
- [x] `/best/skills/<cat>` × priority categories (finance, databases,
      testing, backend, debugging)
- [x] `/best/subagents/<cat>` × priority categories (databases, testing,
      code-review)
- [x] `/best/commands/<cat>` × priority categories (finance)
- [x] `/best/hooks/other` (only hook category — full ~30-item population)
- [x] Cross-check candidates against what's already installed — verified via
      `.claude/settings.json` (project: context7/dimensional-analysis/
      chrome-devtools-mcp only) and user-level settings
      (superpowers/mcpmarket-me); grepped for all candidate plugin names,
      none pre-existing.
- [x] Final consolidated recommendation list with rationale — see FINAL
      VERDICT below.

## Findings so far

### finance (mcp/plugins/skills/commands) + databases (mcp/plugins/skills/subagents) + hooks/other (full population)

Strong candidates surfaced:

- **bellwether-mcp** (MCP server, finance) — "Live prediction market data from
  Polymarket and Kalshi with VWAP pricing." Directly on-topic (Kalshi
  prediction markets). NEEDS DEEP DIVE: could either (a) offer a
  cross-check/second data source for whale-signal validation, or (b) be a
  reason to *not* adopt it — CLAUDE.md's Kalshi Integration Authority rule
  requires all Kalshi semantic interpretation to live in `services/kalshi/`
  and be sourced from `docs/kalshi/`, not a third-party MCP's own
  normalization. Likely verdict: interesting for read-only cross-verification
  /manual research, NOT as a data-plane input (would violate "fidelity: store
  and replay what the exchange sent" — a third party's VWAP recompute is a
  semantic reinterpretation, not raw Kalshi truth). Flag as optional
  research/cross-check tool only.
- **migration-review** (skill, databases) — "Assess SQLite migrations and
  schema changes." Directly matches CLAUDE.md's hard invariant "schema
  changes are additive only (`CREATE TABLE IF NOT EXISTS` +
  `_add_column_if_missing`)". Candidate to adopt as a review aid before any
  `data/*.db` schema edit.
- **dbhub** (MCP server, databases, 3.4K stars) — zero-dep multi-DB server
  incl. SQLite. Tempting for read-only inspection of `data/*.db`, but
  CLAUDE.md's "Start investigations here" ordering explicitly puts the app's
  own API endpoints and `tools.quality_audit` before any ad hoc `sqlite3`;
  a generic DB MCP would sit at the same rung as "ad hoc sqlite3" in that
  ordering, not above it. Marginal value — only if it enforces read-only
  better than raw sqlite3 (some listed alternatives, e.g. **dbmcp**, do
  advertise "PII redaction and write-prevention" as a first-class feature,
  which is a real edge over raw `sqlite3`). Low priority.
- **claude-security** plugin — appeared repeatedly across unrelated category
  pages (own skill, own subagent trio: scan-verifier/scan-researcher/
  scan-inventory, own hook set). Reads as a substantial, possibly
  Anthropic-adjacent security-scanning system distinct from the built-in
  `/security-review` skill. NEEDS DEEP DIVE under the security category pass.
- **security-guidance** plugin — lighter weight: PostToolUse pattern warnings
  on edits + Stop-hook LLM diff review + UserPromptSubmit checks. Possible
  complement to (not replacement for) the project's own `guard_workflow.py`
  hook. Needs deep dive — risk of overlapping/conflicting with existing
  hooks.
- **DJZS Trust MCP** ("Deterministic pre-execution audit for trading agents",
  3 stars) and **Valta** ("Financial governance for AI agents — spend gates
  and audit trail", 1 star) — both thematically adjacent to the project's own
  `risk_manager.py` kill switch, but low-star/unproven, and the project's
  safety invariants explicitly say automation must never touch risk/sizing
  code. Not worth adopting; noted only for completeness.
- Noise confirmed and set aside: Shopify/Airwallex/Carta/Splitwise/TRES
  Finance/cap-table/ClickHouse/AlloyDB/BigQuery/Redshift/PlanetScale/SAP
  HANA/MongoDB/Pinecone/Firestore — all enterprise-SaaS or cloud-warehouse
  tooling with zero surface area against a single-file SQLite, ddev-hosted
  app. Confirmed out of scope, not revisiting.
- Hooks (`/best/hooks/other` — only hook category on the whole site, so this
  is the FULL population, ~30 items): almost entirely undocumented/
  unlabeled generic hook-event names (PreToolUse/PostToolUse/SessionStart/
  Stop/etc. with "No description provided"), each presumably belonging to
  some plugin. Only two named, described hook sets appear:
  **security-guidance**'s (PostToolUse Edit/Write/MultiEdit/NotebookEdit,
  Stop, PostToolUse Bash, UserPromptSubmit) and one unnamed "Hookify"
  PreToolUse entry ("user-configurable hooks from .local.md files" — a
  generic hook-authoring meta-tool, not domain-relevant on its own).
  Conclusion: the hooks category has nothing else project-relevant beyond
  what plugin-level pages already surface; not pursuing hooks as a
  standalone axis further.
- Subagents worth a deep dive noted from spillover on other pages (not yet
  formally categorized): **Silent-Failure-Hunter** ("identifies silent
  failures and inadequate error handling") — strong match for the data-plane
  HARD RULE's "these properties fail silently... never infer health from the
  absence of errors" language. **pr-test-analyzer** ("reviews PRs for test
  coverage quality/completeness") — matches the "nothing advances on one
  pass" PR-review discipline. **deploy-guardian**, **incident-investigator**,
  **retro-analyst** — noted for the devops/monitoring pass.

### security, code-review, CLAUDE.md-management, testing/devops/version-control/monitoring sweep

Confirmed via `.claude/settings.json` (project) that only `context7`,
`dimensional-analysis`, `chrome-devtools-mcp` are project-enabled;
`superpowers` and `mcpmarket-me` are user-level. None of the candidates below
are currently installed anywhere — verified by grep, not assumed.

**Official Anthropic `claude-plugins-official` marketplace** — all six of
these carry 32.8–32.9K stars and 100/100 trust (first-party, Apache-2.0,
`claude plugin install <name>@claude-plugins-official`):

- **pr-review-toolkit** — bundles 6 subagents (`code-reviewer`,
  `code-simplifier`, `comment-analyzer`, `pr-test-analyzer`,
  `silent-failure-hunter`, `type-design-analyzer`) + `/review-pr` command.
  `silent-failure-hunter` explicitly hunts "silent failures and inadequate
  error handling" — a near-verbatim match for CLAUDE.md's data-plane rule
  ("these properties fail silently... never infer health from the absence of
  errors"). `pr-test-analyzer` checks PR test-coverage adequacy — matches the
  "nothing advances on one pass" rule's demand for adversarial review before
  merge. **Top recommendation.**
- **claude-security** — deep vulnerability scanner: partitions the repo,
  dispatches research/verification subagents (scan-inventory, scan-researcher,
  scan-verifier), generates confidence-scored patches for human review. Matches
  the "never guess; verify or falsify" rule's own methodology (claims backed
  by independent verification, not the first agent's say-so). **Strong
  candidate** — but per this repo's safety invariants, its generated patches
  must never be auto-applied to trading/risk/sizing/calibration/settlement/
  auth code; treat its output as an audit report requiring the same human
  review any PR gets, same as the built-in `/security-review` skill already
  does.
- **security-guidance** — lighter-weight hook set (pattern warnings on
  edit, LLM diff review on Stop). Overlaps in spirit with this repo's own
  `guard_workflow.py`/`guard_data_db.py` hooks. Possible complement, but
  installing it means two independent Stop/PostToolUse hook chains doing
  adjacent things — verify no double-firing/latency stacking on the hot edit
  path before adopting; not a clear must-have given the repo already has
  bespoke guards tuned to its own domain (trading/risk code paths).
- **claude-md-management** — bundles `claude-md-improver` skill (audits
  CLAUDE.md against quality templates, produces a report, applies targeted
  fixes) + `/revise-claude-md` command (captures session learnings into
  CLAUDE.md with user approval). Excellent fit on paper: this repo's CLAUDE.md
  is unusually large and rule-dense with dated HARD RULEs and an explicit
  provenance convention (`git log -S'<phrase>'` instead of inline narrative).
  **Caveat that must be checked before trusting its output:** the skill
  audits "against templates" — this repo's CLAUDE.md deliberately violates
  generic best-practice templates on purpose (one line per rule, no incident
  story, terse to the point of cryptic) per its own explicit self-description.
  Any suggested edit needs a human/Claude sanity check against that
  deliberate terseness convention before accepting — a generic "quality
  template" pass could easily recommend re-bloating it. Recommended to *try*
  (especially the audit-only pass), not to blindly auto-apply.
- **commit-commands** (`/commit`, `/commit-push-pr`, `/clean-gone`) and
  **skill-creator** (skill authoring + analyzer/comparator/grader subagents)
  — both solid, official, low-risk. Lower priority than the above three
  because this repo already has bespoke equivalents that do more in each
  case: `/checkpoint` skill already does commit+push+CI-confirmation per
  `.claude/rules/branching-and-ci.md`, and `superpowers:writing-skills`
  already covers skill authoring. Worth having only if their narrower,
  faster commands are preferred for quick one-offs; not a gap-filler.
- **code-review** (plugin) and **code-simplifier** (standalone plugin) —
  each just a subset of what `pr-review-toolkit` already includes (the
  `/code-review` and `/simplify` skills already present in this session's
  skill list are very likely already sourced from Anthropic's built-in
  plugin ecosystem, functionally equivalent). Installing these standalone
  would be redundant once/if `pr-review-toolkit` is adopted. Not
  recommended as separate installs.

**codspeed** (official-marketplace-listed, 87/100 trust, 236 GitHub stars) —
performance-benchmarking toolkit (`codspeed-setup-harness` +
`codspeed-optimize` skills) supporting Python via `pytest-benchmark`, with
differential flamegraphs and <1% measurement variance via CPU simulation.
Directly relevant to the data-plane HARD RULE's "measured before it ships"
requirement for hot-path diagnostics and the "speed of execution" property.
**Worth a pilot** on `services/kalshi/` and `whale_stream`/`market_events`
hot-path modules specifically — but note it "autonomously improves
performance," which for this repo means its *proposed* changes still go
through the same review gate as any other hot-path change (measure the
bottleneck's mechanism first, per the HARD RULE — a benchmarking harness
supplies exactly that measurement, it doesn't replace the judgment step).

**Kalshi/prediction-market-specific findings (important, mostly negative):**
Sitemap-grepped every Kalshi/Polymarket/prediction-market-named listing site-wide
(not just a sample). Found 4 Kalshi-named MCP servers
(`kalshi-mcp-server`/cejor6, `kalshi`/joinQuantish, `mcp-server-kalshi`/9crusher,
`crosswire-polymarket-kalshi-arbitrage`) plus `bellwether-mcp` (Polymarket+Kalshi
VWAP data) and `quantifyme`/`quantrisk`/`quant-research-mcp` (generic quant
tooling, not Kalshi-specific). **None are recommended for this project**, for
concrete reasons, not just "unproven":
- All have 0 displayed GitHub stars/reviews — no independent trust signal.
- `kalshi` (joinQuantish) isn't even the real Kalshi exchange — it's a
  Solana/DFlow on-chain wrapper; misleading name.
- Every trading-capable one ("discover, research, trade") would require
  handing a real Kalshi API/RSA private key to unaudited third-party code —
  a direct conflict with this repo's safety invariant that nothing outside
  the vetted `kalshi_account_client.py`/`services/kalshi_account.trading_enabled`
  gate should be able to place real orders, and with "never guess; verify or
  falsify" (their exact request/response handling is unverifiable without
  reading their source, which the project's Kalshi-authority rule says to
  never substitute for `docs/kalshi/`).
- `bellwether-mcp`'s recomputed VWAP is exactly the kind of "vendor-specific
  semantic interpretation outside `services/kalshi/`" the Kalshi Integration
  Authority rule prohibits for any data feeding decisions — usable only as a
  human's manual side-channel curiosity check, never as a data-plane input,
  and even then its Kalshi/Polymarket ToS compliance for redistributing
  exchange data through an unaffiliated third party is unverified (the same
  category of legal-exposure gap already tracked in ROADMAP for sports
  categories — don't create a second untracked one).
- `quantrisk`'s VaR/Monte-Carlo/stress-testing toolset targets continuous-price
  equity/derivatives portfolios; Kalshi's binary/bounded-price contracts and
  this repo's own `risk_manager.py` kill-switch model don't map onto it.
**Conclusion: no external MCP/plugin should touch live Kalshi credentials or
Kalshi-shaped data interpretation.** This is a "checked and correctly declined"
finding, not a gap.

**Categories swept and found to add nothing beyond what's already
covered/mandated** (GitNexus for code-graph/impact-analysis, chrome-devtools
for browser E2E, github MCP + commit-commands for git/PR workflow,
superpowers:writing-plans/test-driven-development/systematic-debugging for
planning/TDD/debugging): version-control MCP servers (github-mcp-server is
already the tool in use; everything else is either a non-GitHub platform this
repo doesn't use, or a 0-star code-graph competitor to GitNexus), debugging
skills (dominated by Airflow/cloud-vendor-specific tooling with zero Python/
SQLite/FastAPI relevance), devops plugins (cloud-vendor-specific: AWS/Azure/
Databricks/ServiceNow — this repo runs on ddev/Docker locally with no chosen
deployment target yet, so nothing here is actionable until that ROADMAP
decision is made), backend skills (Adobe/Appwrite/ASP.NET/Convex-specific,
no FastAPI-specific tooling found under that name), testing skills/subagents/
plugins (Airflow/Adobe/Salesforce-specific noise; `agent-sdk-verifier-py`
only applies to Claude Agent SDK apps, not this FastAPI app).

**Monitoring** — `AnomalyArmor` (schema drift/freshness/quality) is the one
MCP that terminologically matches this repo's own data-plane completeness/
timeliness language, but it's a 1-star, data-warehouse-oriented tool and this
repo already has a purpose-built, more specific equivalent
(`GET /api/quality/summary`, `/api/health/pipeline`, `/api/observability/summary`,
`tools/quality_audit`) — not a gap. `sentry` and `grafana` (both self-hostable)
are legitimate and would be worth reconsidering specifically once ROADMAP's
"no deployment target" open item is resolved — premature before that.

**Hooks** — the site's only hooks category (`/best/hooks/other`, full
population ~30 items) surfaced nothing beyond what the plugin-level pages
above already cover (`security-guidance`'s hook set, one generic
"Hookify"-style user-hook-authoring meta-tool not specific to this domain).

## FINAL VERDICT

**Adopt / pilot (in priority order):**
1. `pr-review-toolkit@claude-plugins-official` — highest-confidence pick.
   `silent-failure-hunter` and `pr-test-analyzer` operationalize two things
   CLAUDE.md already mandates by hand (silent-failure vigilance, PR
   test-coverage review in the "nothing advances on one pass" cycle).
2. `claude-security@claude-plugins-official` — deep scan pass, run
   periodically as an audit (never auto-applying its patches to
   trading/risk/settlement/auth code, per this repo's own automation
   restriction).
3. `claude-md-management@claude-plugins-official` — try the audit-only
   output first; treat its "quality template" suggestions as proposals to
   evaluate against this repo's deliberate terseness convention, not
   auto-accept.
4. `codspeed@claude-plugins-official` — pilot on one or two hot-path modules
   (`services/kalshi/`, `whale_stream`) to get a real before/after benchmark
   next time a hot-path change needs the HARD RULE's "measured... before it
   ships" evidence.

**Optional, lower priority:** `commit-commands`, `skill-creator` — official
and safe, but narrower than what this repo's own `/checkpoint` and
`superpowers:writing-skills` already do.

**Explicitly do not adopt:** every Kalshi/Polymarket-named MCP server found
(credential-custody and Kalshi-integration-authority conflicts — see above);
`security-guidance` unless a concrete gap in the existing `guard_workflow.py`
hooks is identified first (avoid two overlapping hook chains without cause);
`code-review`/`code-simplifier` standalone plugins (redundant with
`pr-review-toolkit` and the already-available `/code-review`/`/simplify`
skills); `AnomalyArmor`/`quantrisk`/`bellwether-mcp`/generic DB MCPs
(`dbhub`/`dbmcp`) — all either redundant with purpose-built equivalents this
repo already has, or a worse fit than they first appear once checked against
this repo's actual architecture and rules.

**Not yet actionable (revisit when the relevant ROADMAP item resolves):**
`sentry`/`grafana` for production monitoring — blocked on the "no deployment
target" open decision, not on tool quality.

**Process note:** none of the above were installed during this scan — take-
the-wheel mode covers research and non-destructive investigation, but adding
a plugin changes `.claude/settings.json` (a shared, version-controlled config
file this repo's CLAUDE.md Toolchain section explicitly documents) and, for
the security/CLAUDE.md-editing ones, grants write access to CLAUDE.md itself —
that's a toolchain decision worth a explicit go-ahead rather than a unilateral
change, consistent with this repo's "nothing advances on one pass" bar for
process/rule changes.
