"""Alert visibility routes - the informativeness half of alerting.py."""
from fastapi import APIRouter

from services import alerting

router = APIRouter()


@router.get("/api/alerts/active")
async def get_active_alerts():
    return {"alerts": alerting.active_alerts()}


@router.get("/api/alerts/history")
async def get_alert_history(limit: int = 50):
    return {"alerts": alerting.recent(limit=limit)}


@router.post("/api/alerts/{alert_id}/resolve")
async def resolve_alert(alert_id: int):
    return {"resolved": alerting.resolve_alert(alert_id)}
