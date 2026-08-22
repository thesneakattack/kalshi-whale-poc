"""
GET/POST /api/config - reads and writes config/settings.yaml through
services/config_store.py, with change-history logging via
services/config_performance.py. Extracted 2026-08-21 as part of main.py's
modularization pass, following the services/diagnostics/routes.py
convention: an APIRouter, shared state from services.app_state only,
main.py does app.include_router(...) at the same path as before.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import config_performance
from services.app_state import bump_generation
from services.config_store import config_store

router = APIRouter()


class ConfigPatch(BaseModel):
    patch: dict


@router.get("/api/config")
async def get_config():
    return config_store.get()


@router.post("/api/config")
async def update_config(body: ConfigPatch):
    if "kalshi_account" in body.patch and "trading_enabled" in (body.patch.get("kalshi_account") or {}):
        raise HTTPException(
            status_code=400,
            detail=(
                "kalshi_account.trading_enabled can't be changed through /api/config — "
                "use POST /api/trading/enable (requires a connected account and a typed "
                "confirmation phrase) or POST /api/trading/disable."
            ),
        )
    if "advisory" in body.patch and "auto_apply_enabled" in (body.patch.get("advisory") or {}):
        raise HTTPException(
            status_code=400,
            detail=(
                "advisory.auto_apply_enabled can't be changed through /api/config — "
                "use POST /api/advisory/auto-apply/enable (requires a typed confirmation "
                "phrase) or POST /api/advisory/auto-apply/disable."
            ),
        )
    if "confidence_calibration" in body.patch and "auto_apply_enabled" in (body.patch.get("confidence_calibration") or {}):
        raise HTTPException(
            status_code=400,
            detail=(
                "confidence_calibration.auto_apply_enabled can't be changed through /api/config — "
                "use POST /api/confidence-calibration/auto-apply/enable (requires a typed "
                "confirmation phrase) or POST /api/confidence-calibration/auto-apply/disable."
            ),
        )
    # Change-history logging (Item 3D, 2026-08-10) - this was the one real
    # gap in config_performance.log_applied_change()'s coverage: every plain
    # Config-tab save went completely unlogged before this, even though the
    # Advisory apply route has always had a full audit trail. Logged AFTER
    # config_store.update() so fingerprint_after reflects the config that
    # actually took effect, but the diff itself is computed against the
    # pre-update snapshot (diff_patch reads old_cfg, not the live store).
    old_cfg = config_store.get()
    fp_before = config_performance.fingerprint(old_cfg)
    changes = config_performance.diff_patch(old_cfg, body.patch)
    new_cfg = config_store.update(body.patch)
    fp_after = config_performance.fingerprint(new_cfg)
    for config_path, old_value, new_value in changes:
        config_performance.log_applied_change(
            config_path=config_path, old_value=old_value, new_value=new_value,
            rationale="Manual edit via the Config tab.", trade_count=0,
            fingerprint_before=fp_before, fingerprint_after=fp_after,
            auto_applied=False, source="manual",
        )
    bump_generation()
    return new_cfg
