"""Danger Zone routes: preview, history, and the actual multi-domain paper/
signal_log/market_analyst/market_catalog/market_history/series_evaluator/
candidate_log/calibration_history/trade_category reset - moved out of main.py
2026-08-27 (backend services modularization). See this package's own
reset_log.py (the audit trail every reset writes to) and trade_archive.py
(the pre-reset snapshot taken before a paper reset destroys anything)."""
import time

from fastapi import APIRouter
from pydantic import BaseModel

from services import candidate_log, market_analyst_agent, market_history, series_evaluator, signal_log, trade_category
from services.app_state import bump_generation, broker, risk, shadow, state
from services.config.config_store import config_store
from services.market_catalog import market_catalog
from services.reset import reset_log, trade_archive
from services.whale_calibration import calibration_history

router = APIRouter()


class ResetBody(BaseModel):
    # Each flag wipes an independently-persisted domain — see the Danger Zone
    # panel in the Config tab. Paper defaults on (matches the button's
    # original, sole behavior); shadow/signal_log default off since they're
    # long-run track records that normally survive a paper reset on purpose.
    paper: bool = True
    # Naming for the archive snapshot taken before a paper reset (see
    # services/reset/trade_archive.py). Optional - both default to a generated
    # label/reason - but worth setting when the reset marks a deliberate
    # config experiment, since the label is how epochs are told apart in
    # trade_archive.compare().
    archive_label: str | None = None
    archive_reason: str | None = None
    # Opt-in (2026-08-27 direct request): a paper reset otherwise archives
    # open positions as-is, unrealized P&L never credited - abandoned, not
    # closed. When true (and the reset is unscoped - a ranged reset never
    # touches positions/bankroll to begin with, see range_start/range_end
    # below), every open position is flattened at its latest known price
    # via the same broker.close_all_positions() POST /api/trading/
    # flatten-all already uses, BEFORE the archive snapshot is taken - so
    # the permanent archive records real realized closes instead of
    # orphaned archived_positions rows.
    close_positions_first: bool = False
    shadow: bool = False
    signal_log: bool = False
    market_analyst: bool = False
    # market_catalog/market_history had no wired reset path at all despite
    # being the two largest data/*.db files on disk (data-robustness audit
    # finding, 2026-08-10) - market_catalog.clear_all() already existed,
    # written for exactly this, just never called from here.
    market_catalog: bool = False
    market_history: bool = False
    # Bulk-wipe, separate from the per-row POST /api/series-evaluator/reset
    # action - that one is a deliberate single-series re-evaluate; this one
    # is "start the whole series-worthiness log over."
    series_evaluator: bool = False
    candidate_log: bool = False
    calibration_history: bool = False
    trade_category: bool = False
    # Range scoping (2026-08-16 direct request, after a real incident this
    # session spent well over an hour reconstructing from git history and
    # config timestamps: a noisy tuning/dev stretch should be purgeable
    # without losing the valid history on either side of it, instead of
    # every Danger Zone action being all-or-nothing). Only affects the four
    # domains with a real clear_range/count_range (signal_log, candidate_log,
    # trade_category, and paper - scoped to the trades table only, never
    # positions/bankroll/pending_orders, see PaperBroker.clear_trade_range's
    # own docstring for why). Every other domain ignores these and does its
    # existing full clear when selected - unchanged behavior for them.
    # Unix timestamps (seconds); None on a side means unbounded that
    # direction, same "None = no limit" convention used everywhere else in
    # this app. Both None (the default) means "everything", identical to
    # today's behavior.
    range_start: float | None = None
    range_end: float | None = None


def _reset_domain_counts(body: ResetBody) -> dict[str, int | None]:
    """Best-effort 'how many rows would this remove' per selected domain,
    for both /api/reset/preview and the audit-log rows_before column.
    None for domains with no cheap count available (market_catalog/
    market_history/series_evaluator/calibration_history/shadow) rather than
    paying for a full-table scan just for the log - a domain-recorded
    but count-less audit row is still a categorical improvement over
    today's zero record of resets ever happening at all."""
    counts: dict[str, int | None] = {}
    if body.paper:
        counts["paper"] = (
            broker.count_trade_range(body.range_end, body.range_start)
            if (body.range_start or body.range_end) else len(broker.trade_log)
        )
        # Only meaningful when it will actually run - see reset_broker's own
        # "not ranged" guard for why a ranged reset never closes positions.
        if body.close_positions_first and not (body.range_start or body.range_end):
            counts["close_positions_first"] = len(broker.positions)
    if body.shadow:
        counts["shadow"] = None
    if body.signal_log:
        counts["signal_log"] = signal_log.count_range(body.range_end, body.range_start)
    if body.market_analyst:
        counts["market_analyst"] = market_analyst_agent.total_count()
    if body.market_catalog:
        counts["market_catalog"] = None
    if body.market_history:
        counts["market_history"] = None
    if body.series_evaluator:
        counts["series_evaluator"] = None
    if body.candidate_log:
        counts["candidate_log"] = candidate_log.count_range(body.range_end, body.range_start)
    if body.calibration_history:
        counts["calibration_history"] = None
    if body.trade_category:
        counts["trade_category"] = trade_category.count_range(body.range_end, body.range_start)
    return counts


@router.get("/api/reset/preview")
async def reset_preview(
    paper: bool = False, shadow: bool = False, signal_log: bool = False, market_analyst: bool = False,
    market_catalog: bool = False, market_history: bool = False, series_evaluator: bool = False,
    candidate_log: bool = False, calibration_history: bool = False,
    trade_category: bool = False, close_positions_first: bool = False,
    range_start: float | None = None, range_end: float | None = None,
):
    # Dry-run counterpart to POST /api/reset - same domain/range selection,
    # deletes nothing. Powers the Danger Zone's "here's what you're about
    # to lose" step (2026-08-16 direct request) before the real request
    # fires. Query params, not a body, since this is a GET (no side effects).
    body = ResetBody(
        paper=paper, shadow=shadow, signal_log=signal_log, market_analyst=market_analyst,
        market_catalog=market_catalog, market_history=market_history, series_evaluator=series_evaluator,
        candidate_log=candidate_log, calibration_history=calibration_history,
        trade_category=trade_category, close_positions_first=close_positions_first,
        range_start=range_start, range_end=range_end,
    )
    return {"counts": _reset_domain_counts(body), "scope": "all" if not (range_start or range_end) else "range"}


@router.get("/api/reset/history")
async def get_reset_history(limit: int = 50):
    # The audit trail /api/reset now writes - 2026-08-16 direct request,
    # after a real incident this session spent well over an hour
    # reconstructing (from git history and config_performance.db
    # timestamps, since nothing recorded a reset had even happened) when
    # and why signal_log.db/paper_broker.db had lost days of history.
    return {"events": reset_log.recent(limit=min(max(limit, 1), 200))}


@router.post("/api/reset")
async def reset_broker(body: ResetBody = ResetBody()):
    cfg = config_store.get()
    cleared = []
    ranged = bool(body.range_start or body.range_end)
    scope = "between" if (body.range_start and body.range_end) else (
        "after" if body.range_start else ("before" if body.range_end else "all")
    )
    counts_before = _reset_domain_counts(body)

    def _log(domain: str, deleted: int | None):
        reset_log.record(
            domain=domain, scope=scope, rows_before=counts_before.get(domain), rows_deleted=deleted,
            range_start=body.range_start, range_end=body.range_end,
        )

    if body.paper:
        # Close open positions BEFORE the archive snapshot (2026-08-27 direct
        # request), so archive_epoch below records real, realized closes
        # instead of orphaned archived_positions rows with unrealized P&L
        # never credited. Skipped for a ranged reset - positions/bankroll are
        # current live state, never touched by range scoping (see
        # ResetBody.range_start's own docstring), so closing them here would
        # surprise a caller who only asked to purge a date range of history.
        if body.close_positions_first and not ranged:
            closed = broker.close_all_positions(
                state["latest_prices"], f"reset: closed before {scope} reset",
            )
            cleared.append({"domain": "close_positions_first", "closed": len(closed)})
        # Archive BEFORE anything else is destroyed (2026-08-17 direct
        # request: "a safe reset of the paper trading mechanic while
        # maintaining a log of important data"). The motivating incident is
        # concrete: a prior reset left paper_broker.db reaching back only to
        # 08/16 19:28, so every trade-level question about anything earlier -
        # realised win rate, mean entry unit cost, exit breakdown - was
        # unanswerable. reset_log recorded that a reset happened; nothing
        # recorded what it removed.
        #
        # Runs for ranged resets too: a scoped delete still destroys closed
        # history, which is exactly the evidence this preserves.
        archive_result = trade_archive.archive_epoch(
            label=body.archive_label or f"pre-reset {time.strftime('%Y-%m-%d %H:%M')}",
            reason=body.archive_reason or f"automatic archive before {scope} paper reset",
            cfg=cfg,
        )
        cleared.append({"domain": "archive", "epoch": archive_result})
        if ranged:
            # Scoped: only the trades table (closed history) - never
            # positions/bankroll/pending_orders, which are current live
            # state, not history to prune. See PaperBroker.clear_trade_range.
            deleted = broker.clear_trade_range(body.range_end, body.range_start)
        else:
            # Unscoped: full account reset, unchanged from before this change.
            broker.reset(cfg["risk"]["starting_bankroll"])
            risk.reset_day(cfg["risk"]["starting_bankroll"])
            state["signal_feed"] = []
            state["decision_feed"] = []
            state["stats"] = {"signals_seen": 0, "trades_placed": 0, "skipped": 0}
            state["equity_history"] = []
            deleted = counts_before.get("paper")
        _log("paper", deleted)
        cleared.append("paper")
    if body.shadow:
        shadow.clear(cfg["risk"]["starting_bankroll"])
        _log("shadow", None)
        cleared.append("shadow")
    if body.signal_log:
        deleted = signal_log.clear_range(body.range_end, body.range_start)
        _log("signal_log", deleted)
        cleared.append("signal_log")
    if body.market_analyst:
        market_analyst_agent.clear_all()
        _log("market_analyst", counts_before.get("market_analyst"))
        cleared.append("market_analyst")
    if body.market_catalog:
        market_catalog.clear_all()
        _log("market_catalog", None)
        cleared.append("market_catalog")
    if body.market_history:
        market_history.clear_all()
        _log("market_history", None)
        cleared.append("market_history")
    if body.series_evaluator:
        series_evaluator.clear_all()
        _log("series_evaluator", None)
        cleared.append("series_evaluator")
    if body.candidate_log:
        deleted = candidate_log.clear_range(body.range_end, body.range_start)
        _log("candidate_log", deleted)
        cleared.append("candidate_log")
    if body.calibration_history:
        calibration_history.clear_all()
        _log("calibration_history", None)
        cleared.append("calibration_history")
    if body.trade_category:
        deleted = trade_category.clear_range(body.range_end, body.range_start)
        _log("trade_category", deleted)
        cleared.append("trade_category")
    bump_generation()
    return {"ok": True, "cleared": cleared, "scope": scope}
