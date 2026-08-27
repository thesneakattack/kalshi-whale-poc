"""
Exit-management routes. Only one today: the read-only position-netting
group view, moved out of services/position/routes.py (2026-08-22
modularization pass, Phase 1/9) so exit-related HTTP surface lives
alongside the exit-decision logic that backs it (exit_engine.py,
position_netting.py) rather than next to the account/broker-admin routes
in services/position/.
"""
from fastapi import APIRouter

from services.app_state import broker, state
from services.config.config_store import config_store
from services.exits import position_netting

router = APIRouter()


@router.get("/api/position-netting/groups")
async def get_position_netting_groups():
    # services/exits/position_netting.py - read-only, safe to call anytime
    # regardless of position_netting.enabled (same "observe before you
    # choose to act" principle as the rest of this app's history/advisory
    # surfaces). Lets the user see exactly how any currently-open
    # mutually-exclusive-event group (a real hedge/concentration pattern
    # or not) is classified before ever turning automated action on.
    return {"groups": position_netting.describe_groups(
        broker, state["market_titles"], state["event_titles"], state["latest_prices"], config_store.get(),
    )}
