# The data layer and the trade/history/logging layer: a contract

Standing architectural note, 2026-08-30. Companion to `tools/soak_analyzer.py`,
which is the executable form of everything below.

## Why the separation is load-bearing

Two layers, two different jobs, and they must not absorb each other:

- **Data layer** — ingest, websocket, queues, settlement resolver. It
  *produces* telemetry. Its only question: does my own accounting hold
  together?
- **Trade/history/logging layer** — trade archive, series statistics, P&L,
  advisory, netting/exit reasons. It *consumes* that telemetry to draw
  conclusions about money. Its only question: are my inputs complete enough
  that a conclusion drawn from them is true?

The failure this separation prevents is specific and has already happened
here: the trade layer computed and displayed statistics that were wrong for
reasons entirely outside itself, and nothing in the trade layer could have
noticed. Its numbers were internally consistent. The data feeding them was
short.

## The contract, in both directions

**Trade layer → data layer: a gap here defines a metric there.**
When an analysis cannot be trusted, the reason is a missing or unverifiable
data-layer metric, and that metric becomes a requirement. Every
`ANALYSIS_READINESS` check in `tools/soak_analyzer.py` therefore names the
analysis it invalidates — a failure that does not say what conclusion it
poisons is not actionable, and a test enforces that
(`test_every_failing_analysis_check_states_what_it_invalidates`).

**Data layer → trade layer: a new metric there sharpens an analysis here.**
`pending_tickers` is the worked example. Until it was exposed,
`oldest_message_age_sec` could report `0.0` with a wedged coalescing map and
nothing downstream could tell health from silence. With it, the two are
cross-checked (`staleness_metric_trustworthy`), and the analyzer can return
**BLIND** — a distinct verdict meaning "the metric this criterion reads is
capable of lying right now." BLIND outranks FAIL: a FAIL is a known-bad
measurement, BLIND is an untrustworthy one, which is strictly worse.

## Measured cross-layer breaches (2026-08-30, live)

Each is a data-layer condition whose damage lands in the trade layer.

| Data-layer condition | Measured | Lands in the trade layer as |
|---|---|---|
| `settlement_resolver.dropped_total` | 64 of 32,128 (0.199%) | Up to 64 markets whose positions never received a settlement result: realized P&L, win rate, and per-series stats are short by that much, invisibly |
| Ticker conservation gap | 74 updates unaccounted | Mark-to-market, unrealized P&L, exit decisions, and the netting materiality bar all read a price the exchange may have already superseded |
| `capture_writer` faults | 220 in 24h ("database is locked") | Holes in the `raw_trades` archive — every backtest, replay, and whale-density statistic computed from it |
| `exit_engine.stale_price_uncorroborated` | 3 | Exits recorded as decisions whose justifying price was never confirmed |

The settlement figure is the sharpest illustration of why the layers stay
apart: `docs/next-action.md` names `dropped_total == 0` as a soak pass
criterion. It was 64. A human reading a prose checklist did not catch it.
That is the entire argument for the analyzer being a mechanism rather than a
list.

## The reverse gap: logging that defeats analysis

A gap in the trade/history/logging layer can make a correct analysis
impossible, and one was hit while producing this document.

To test whether the `position_netting` zero-volatility defect explained the
netting losses, the materiality bar behind each decision was needed. It is
not a column. It survives only inside a human-readable sentence:

```python
"reason": f"estimated ${improvement:.2f} expected-value improvement over holding (bar ${bar:.2f})"
```

Recovering it required regex-parsing prose out of `trades.reason`
(`bar \$([0-9.]+)`). It happened to work, and the falsification it enabled
was decisive — but had the sentence been phrased differently, or had the
value been rounded away, the question would have been unanswerable and the
defect's blast radius would have stayed a guess.

**Requirement this defines:** a decision's numeric inputs belong in
structured fields, not only in the prose that explains the decision to a
human. The bar, the improvement, and the `vol_ratio` that scaled the bar are
each a number some future analysis will need. Prose is for the reader;
columns are for the analysis.

## Rules

1. A tool in this layer reads the app through its real API and never
   imports it (`CLAUDE.md`, workflow/application separation).
2. A data-layer check never names a downstream analysis — that is the other
   layer's job, and a test enforces the boundary
   (`test_data_layer_checks_never_claim_to_invalidate_an_analysis`).
3. An absent counter is `UNKNOWN`, never a pass. A criterion no one measured
   has not been met.
4. Conservation is asserted only on a quiescent sample (queue depth 0);
   with items in flight the difference is legitimately in-flight, and a
   check that fires on it is noise — and noise is what gets baselined away.
5. A new displayed or persisted decision input gets a structured field in
   the same change that introduces it.
