"""Game Development Tools — API routes for the automated game dev workflow.

Provides status, cycle trigger, and history for the Game Dev Tools UI panel.
Cycles run asynchronously so the endpoint returns immediately.
"""

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from src.auth_helpers import get_current_user, require_privilege

logger = logging.getLogger(__name__)

CHIMERA_ROOT = os.environ.get("CHIMERA_ROOT", "E:/Chimera")
LOOP_LOG_DIR = Path("logs/gamedev_cycles")
LOOP_LOG_DIR.mkdir(parents=True, exist_ok=True)

# In-memory state for running cycles (survives across requests but not restarts)
_active_cycles: dict[str, dict[str, Any]] = {}
_completed_cycles: list[dict[str, Any]] = []

SCENARIOS = [
    {"id": "FullFlight", "label": "Full Flight", "desc": "General flight systems test"},
    {"id": "Combat", "label": "Combat", "desc": "Weapons and combat systems"},
    {"id": "OrbitalInsertion", "label": "Orbital Insertion", "desc": "Orbital mechanics test"},
    {"id": "AtmosphericEntry", "label": "Atmospheric Entry", "desc": "Re-entry and landing"},
    {"id": "FreeFlight", "label": "Free Flight", "desc": "Open sandbox exploration"},
]


def _project_status() -> dict[str, Any]:
    """Check Chimera project status without importing heavy modules."""
    status: dict[str, Any] = {
        "chimera_project": Path(CHIMERA_ROOT, "Chimera.uproject").exists(),
        "unreal_editor": Path(os.environ.get("UE_EDITOR", "E:/UnrealEngine/UE_5.7/Engine/Binaries/Win64/UnrealEditor.exe")).exists(),
        "gdd_found": Path(CHIMERA_ROOT, "Docs", "GAME_DESIGN_DOC.md").exists(),
        "vis_inbox": Path(CHIMERA_ROOT, "Saved", "VISInbox").exists(),
        "playtest_reports": Path(CHIMERA_ROOT, "Saved", "PlaytestReports").exists(),
    }
    # Count outstanding screenshots
    vis_inbox = Path(CHIMERA_ROOT, "Saved", "VISInbox")
    if vis_inbox.exists():
        status["pending_screenshots"] = len(list(vis_inbox.glob("*.png")))
    else:
        status["pending_screenshots"] = 0

    status["all_ok"] = all(status.values())
    return status


def _gallery_summary() -> dict[str, Any]:
    """Quick gallery stats via direct DB query (avoids HTTP call to self)."""
    try:
        from core.database import SessionLocal, GalleryImage
        db = SessionLocal()
        try:
            total = db.query(GalleryImage).filter(GalleryImage.is_active == True).count()
            recent = (
                db.query(GalleryImage)
                .filter(GalleryImage.is_active == True)
                .order_by(GalleryImage.created_at.desc())
                .limit(6)
                .all()
            )
            return {
                "total_photos": total,
                "recent": [
                    {
                        "id": img.id,
                        "filename": img.filename,
                        "prompt": (img.prompt or "untitled")[:80],
                        "width": img.width,
                        "height": img.height,
                        "url": f"/api/generated-image/{img.filename}",
                    }
                    for img in recent
                ],
            }
        finally:
            db.close()
    except Exception as e:
        return {"total_photos": 0, "recent": [], "error": str(e)}


def _load_cycle_history(limit: int = 20) -> list[dict[str, Any]]:
    """Load past cycle reports from disk."""
    cycles = []
    if LOOP_LOG_DIR.exists():
        for report_file in sorted(LOOP_LOG_DIR.glob("*.md"), reverse=True)[:limit]:
            try:
                text = report_file.read_text(encoding="utf-8")
                # Extract key info from report
                info: dict[str, Any] = {
                    "cycle_id": report_file.stem,
                    "report": text[:3000],
                }
                for line in text.splitlines():
                    if line.startswith("**Passed:**"):
                        info["passed"] = "PASSED" in line
                    elif line.startswith("**Scenario:**"):
                        info["scenario"] = line.split("**Scenario:**")[-1].strip()
                    elif line.startswith("**Started:**"):
                        info["started_at"] = line.split("**Started:**")[-1].strip()
                cycles.append(info)
            except Exception:
                pass

    # Also include in-memory completed cycles
    for c in _completed_cycles[-10:]:
        if not any(existing.get("cycle_id") == c.get("cycle_id") for existing in cycles):
            cycles.insert(0, c)

    return cycles[:limit]


def setup_gamedev_routes() -> APIRouter:
    router = APIRouter(tags=["gamedev"])

    @router.get("/api/gamedev/status")
    async def gamedev_status(request: Request) -> dict[str, Any]:
        """Get current game dev project status + gallery summary + active cycles."""
        user = get_current_user(request)
        return {
            "project": _project_status(),
            "gallery": _gallery_summary(),
            "scenarios": SCENARIOS,
            "active_cycles": list(_active_cycles.values()),
            "recent_cycles": _load_cycle_history(10),
        }

    @router.post("/api/gamedev/trigger")
    async def gamedev_trigger(request: Request) -> dict[str, Any]:
        """Trigger a game dev automation cycle. Runs asynchronously."""
        user = get_current_user(request) or "admin"

        try:
            body = await request.json()
        except Exception:
            body = {}

        scenario = body.get("scenario", "FullFlight")
        continuous = body.get("continuous", False)
        max_cycles = min(body.get("max_cycles", 5), 10)

        # Validate scenario
        valid_ids = {s["id"] for s in SCENARIOS}
        if scenario not in valid_ids:
            raise HTTPException(400, f"Unknown scenario: {scenario}. Valid: {', '.join(valid_ids)}")

        cycle_id = f"ui_{datetime.now(timezone.utc).strftime('%H%M%S')}_{uuid.uuid4().hex[:6]}"

        _active_cycles[cycle_id] = {
            "cycle_id": cycle_id,
            "scenario": scenario,
            "continuous": continuous,
            "max_cycles": max_cycles,
            "status": "starting",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "phases": [],
        }

        # Fire and forget — the cycle runs in background
        asyncio.create_task(_run_cycle_async(cycle_id, scenario, continuous, max_cycles))

        return {
            "ok": True,
            "cycle_id": cycle_id,
            "message": f"Cycle {cycle_id} started ({scenario})",
        }

    @router.get("/api/gamedev/cycles")
    async def gamedev_cycles(
        request: Request,
        limit: int = Query(20, ge=1, le=100),
    ) -> dict[str, Any]:
        """List past cycles."""
        user = get_current_user(request)
        return {
            "active": list(_active_cycles.values()),
            "completed": _load_cycle_history(limit),
        }

    @router.post("/api/gamedev/cycle/{cycle_id}/stop")
    async def gamedev_stop(request: Request, cycle_id: str) -> dict[str, Any]:
        """Request a running cycle to stop after current phase."""
        user = get_current_user(request)
        if cycle_id in _active_cycles:
            _active_cycles[cycle_id]["abort"] = True
            _active_cycles[cycle_id]["status"] = "stopping"
            return {"ok": True, "message": f"Stopping cycle {cycle_id}"}
        raise HTTPException(404, "Cycle not found or already completed")

    @router.get("/api/gamedev/gdd")
    async def gamedev_gdd(request: Request):
        """Serve the Chimera GDD file."""
        from fastapi.responses import FileResponse
        gdd_path = Path(CHIMERA_ROOT) / "Docs" / "GAME_DESIGN_DOC.md"
        if not gdd_path.exists():
            raise HTTPException(404, "GDD not found")
        return FileResponse(gdd_path, media_type="text/markdown")

    @router.post("/api/gamedev/run-bridge")
    async def gamedev_run_bridge(request: Request) -> dict[str, Any]:
        """Run the gallery bridge script."""
        import subprocess
        user = get_current_user(request)
        bridge_path = Path(CHIMERA_ROOT) / "Tools" / "gallery_bridge.py"
        if not bridge_path.exists():
            raise HTTPException(404, "Bridge script not found")
        try:
            result = subprocess.run(
                ["python", str(bridge_path), "--once", "--include-journey"],
                capture_output=True, text=True, timeout=60,
                cwd=str(bridge_path.parent),
            )
            return {"ok": True, "stdout": result.stdout[-2000:], "stderr": result.stderr[-500:]}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "Bridge timed out after 60s"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    return router


async def _run_cycle_async(cycle_id: str, scenario: str, continuous: bool, max_cycles: int):
    """Run a game dev cycle in the background, updating _active_cycles state."""
    try:
        from src.gamedev_workflow import GameDevWorkflow

        _active_cycles[cycle_id]["status"] = "running"

        def log_cb(msg: str):
            _active_cycles[cycle_id].setdefault("log", []).append(msg)
            if len(_active_cycles[cycle_id]["log"]) > 200:
                _active_cycles[cycle_id]["log"] = _active_cycles[cycle_id]["log"][-100:]

        wf = GameDevWorkflow(
            scenario=scenario,
            continuous=continuous,
            max_cycles=max_cycles,
            log_callback=log_cb,
        )

        if continuous:
            cycles = await wf.run_continuous()
            _active_cycles[cycle_id]["status"] = "completed"
            _active_cycles[cycle_id]["cycles_run"] = len(cycles)
            _active_cycles[cycle_id]["passed"] = all(c.passed for c in cycles)
        else:
            cycle = await wf.run_cycle()
            _active_cycles[cycle_id]["status"] = "completed"
            _active_cycles[cycle_id]["passed"] = cycle.passed
            _active_cycles[cycle_id]["phases"] = cycle.phases
            _active_cycles[cycle_id]["issues"] = len(cycle.issues)
            _active_cycles[cycle_id]["screenshots"] = len(cycle.screenshots)

        # Move to completed
        _completed_cycles.append(dict(_active_cycles[cycle_id]))

    except Exception as e:
        _active_cycles[cycle_id]["status"] = "failed"
        _active_cycles[cycle_id]["error"] = str(e)
        _completed_cycles.append(dict(_active_cycles[cycle_id]))
        logger.error(f"Game dev cycle {cycle_id} failed: {e}")
    finally:
        # Keep in active_cycles for 5 min then remove
        pass
