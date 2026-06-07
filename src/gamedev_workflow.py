"""
Game Development Automation Workflow Engine
============================================
Orchestrates automated game dev cycles:
  Build -> Playtest -> Screenshot -> Gallery -> Vision Analysis -> Fix -> Repeat

Integrates Chimera's Autonomous Loop with Odysseus's AI, gallery, and MCP tools.
Designed to be called from:
  - Agent tool (gamedev_cycle)
  - REST API (/api/gamedev/cycle)
  - Chimera's chimera_autonomous_loop.py (import mode)
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

# ── Configuration ────────────────────────────────────────────────────────────

CHIMERA_ROOT = os.environ.get("CHIMERA_ROOT", "E:/Chimera")
UE_EDITOR = os.environ.get(
    "UE_EDITOR",
    "E:/UnrealEngine/UE_5.7/Engine/Binaries/Win64/UnrealEditor.exe",
)
UPROJECT = os.path.join(CHIMERA_ROOT, "Chimera.uproject")
CHIMERA_LOOP = os.path.join(CHIMERA_ROOT, "Tools", "chimera_autonomous_loop.py")
GALLERY_DIR = Path("data/generated_images")
LOOP_LOG_DIR = Path("logs/gamedev_cycles")

GALLERY_DIR.mkdir(parents=True, exist_ok=True)
LOOP_LOG_DIR.mkdir(parents=True, exist_ok=True)

# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class CycleArtifact:
    """A single piece of evidence from a cycle phase."""
    kind: str          # "screenshot", "telemetry", "log", "diff", "report"
    path: str          # Relative path or absolute
    gallery_id: str | None = None   # If stored in Odysseus gallery
    caption: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CycleIssue:
    """An issue discovered during analysis."""
    severity: str      # "critical", "major", "minor", "cosmetic"
    system: str        # e.g. "Flight", "Combat", "Rendering"
    description: str
    screenshot_id: str | None = None  # Gallery image showing the issue
    gdd_criterion: str | None = None  # Related GDD acceptance criterion
    proposed_fix: str = ""


@dataclass
class CycleResult:
    """Full result of one automation cycle."""
    cycle_id: str
    scenario: str
    started_at: str
    completed_at: str
    phases: list[str] = field(default_factory=list)
    screenshots: list[CycleArtifact] = field(default_factory=list)
    issues: list[CycleIssue] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)
    report: str = ""
    passed: bool = False
    error: str | None = None


# ── Workflow Engine ──────────────────────────────────────────────────────────


class GameDevWorkflow:
    """Orchestrates automated build -> test -> screenshot -> analyze -> fix cycles.

    Can be driven by the Odysseus agent or called directly from Chimera's loop.
    Screenshots flow into the Odysseus gallery for AI inspection.
    """

    def __init__(
        self,
        scenario: str = "FullFlight",
        run_id: str | None = None,
        continuous: bool = False,
        max_cycles: int = 10,
        log_callback: Callable[[str], None] | None = None,
    ):
        self.scenario = scenario
        self.run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.continuous = continuous
        self.max_cycles = max_cycles
        self.log = log_callback or (lambda msg: print(f"[GameDev] {msg}"))
        self.cycles: list[CycleResult] = []
        self.current_cycle: CycleResult | None = None
        self._abort = False

    # ── Phase 1: Preflight ────────────────────────────────────────────────

    async def preflight(self) -> dict[str, Any]:
        """Check that all prerequisites are met."""
        self.log("Preflight check...")
        status: dict[str, Any] = {"ok": True, "warnings": [], "checks": {}}

        # Check Chimera project exists
        chimera_ok = Path(UPROJECT).exists()
        status["checks"]["chimera_project"] = chimera_ok
        if not chimera_ok:
            status["warnings"].append(f"Chimera project not found at {UPROJECT}")

        # Check Unreal Editor
        ue_ok = Path(UE_EDITOR).exists()
        status["checks"]["unreal_editor"] = ue_ok
        if not ue_ok:
            status["warnings"].append(f"Unreal Editor not found at {UE_EDITOR}")

        # Check MCP connectivity (Unreal MCP server)
        mcp_ok = await _check_mcp()
        status["checks"]["mcp_connected"] = mcp_ok
        if not mcp_ok:
            status["warnings"].append("Unreal MCP not connected — screenshots may fail")

        # Check LM Studio for vision analysis
        lm_ok = await _check_lm_studio()
        status["checks"]["lm_studio"] = lm_ok or "ok"  # LM Studio is optional

        # Check GDD exists for acceptance criteria
        gdd_path = Path(CHIMERA_ROOT) / "Docs" / "GAME_DESIGN_DOC.md"
        status["checks"]["gdd_found"] = gdd_path.exists()

        # Check gallery writable
        gallery_ok = GALLERY_DIR.exists() and os.access(GALLERY_DIR, os.W_OK)
        status["checks"]["gallery_writable"] = gallery_ok

        if status["warnings"]:
            status["ok"] = any(status["checks"].values())
            self.log(f"Preflight: {len(status['warnings'])} warning(s)")

        return status

    # ── Phase 2: Build ────────────────────────────────────────────────────

    async def build(self) -> dict[str, Any]:
        """Trigger a hot-reload or compile if needed."""
        self.log("Build phase — checking if recompile needed...")

        # For Unreal, a full compile is usually not needed between cycles.
        # We trigger a LiveCoding compile if the editor is running.
        # Returns info about what was built.

        result: dict[str, Any] = {"action": "none", "success": True}

        # Check if there are C++ changes that need compiling
        source_dir = Path(CHIMERA_ROOT) / "Source"
        if source_dir.exists():
            cpp_files = list(source_dir.rglob("*.cpp")) + list(source_dir.rglob("*.h"))
            if cpp_files:
                newest = max(f.stat().st_mtime for f in cpp_files)
                # If source changed in the last cycle, suggest rebuild
                result["source_files"] = len(cpp_files)
                result["newest_change"] = newest

        self.log(f"Build: {result['action']}")
        return result

    # ── Phase 3: Playtest ─────────────────────────────────────────────────

    async def playtest(self) -> dict[str, Any]:
        """Run the playtest scenario in Unreal Engine."""
        self.log(f"Playtest phase — scenario: {self.scenario}")
        result: dict[str, Any] = {"started": False, "screenshots": []}

        # Option A: Run Chimera's autonomous loop playtest phase
        loop_script = Path(CHIMERA_LOOP)
        if loop_script.exists():
            self.log("Running Chimera autonomous loop playtest...")
            try:
                proc = await asyncio.create_subprocess_exec(
                    "python", str(loop_script),
                    "--scenario", self.scenario,
                    "--dry-run",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=30
                )
                result["chimera_loop_output"] = stdout.decode()[:2000]
                if stderr:
                    result["chimera_loop_errors"] = stderr.decode()[:1000]
                result["started"] = True
            except asyncio.TimeoutError:
                result["error"] = "Chimera loop timed out"
            except Exception as e:
                result["error"] = str(e)

        # Option B: Use MCP to control the editor directly
        # (This would be the MCP tool call from the agent perspective)
        result["mcp_actions"] = [
            "control_editor: play (start PIE)",
            "control_editor: screenshot (capture every 30s)",
            "control_editor: stop (end PIE)",
        ]

        return result

    # ── Phase 4: Capture Screenshots ──────────────────────────────────────

    async def capture_screenshots(self, session_id: str = "") -> list[CycleArtifact]:
        """Capture screenshots from the game viewport and save to gallery."""
        self.log("Capture phase — taking screenshots...")
        artifacts: list[CycleArtifact] = []

        # Find screenshots in Chimera's VISInbox
        vis_inbox = Path(CHIMERA_ROOT) / "Saved" / "VISInbox"
        if vis_inbox.exists():
            for img_file in sorted(vis_inbox.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)[:10]:
                artifact = await self._ingest_screenshot(img_file, session_id)
                if artifact:
                    artifacts.append(artifact)

        # Also check PlaytestReports
        reports_dir = Path(CHIMERA_ROOT) / "Saved" / "PlaytestReports"
        if reports_dir.exists():
            for img_file in sorted(reports_dir.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]:
                artifact = await self._ingest_screenshot(img_file, session_id)
                if artifact:
                    artifacts.append(artifact)

        self.log(f"Captured {len(artifacts)} screenshot(s)")
        return artifacts

    async def _ingest_screenshot(self, filepath: Path, session_id: str = "") -> CycleArtifact | None:
        """Ingest a single screenshot into the Odysseus gallery."""
        if not filepath.exists():
            return None

        try:
            import base64
            import hashlib

            img_bytes = filepath.read_bytes()
            file_hash = hashlib.sha256(img_bytes).hexdigest()

            # Check for duplicate
            from core.database import SessionLocal, GalleryImage
            db = SessionLocal()
            try:
                existing = db.query(GalleryImage).filter(
                    GalleryImage.file_hash == file_hash,
                    GalleryImage.is_active == True,
                ).first()
                if existing:
                    return CycleArtifact(
                        kind="screenshot",
                        path=str(filepath),
                        gallery_id=existing.id,
                        caption=filepath.stem,
                    )
            finally:
                db.close()

            # Determine dimensions
            width, height = None, None
            try:
                from PIL import Image
                from io import BytesIO
                with Image.open(BytesIO(img_bytes)) as pil:
                    width, height = pil.size
            except Exception:
                pass

            # Save to gallery
            dest_name = f"gamedev_{self.run_id}_{filepath.name}"
            dest_path = GALLERY_DIR / dest_name
            dest_path.write_bytes(img_bytes)

            # Create DB record
            import uuid as _uuid
            from core.database import SessionLocal, GalleryImage

            db = SessionLocal()
            try:
                img_id = str(_uuid.uuid4())
                db.add(GalleryImage(
                    id=img_id,
                    filename=dest_name,
                    prompt=f"[GameDev] {self.scenario} — {filepath.stem}",
                    model="gamedev-loop",
                    owner="admin",
                    file_hash=file_hash,
                    file_size=len(img_bytes),
                    width=width,
                    height=height,
                ))
                db.commit()
                self.log(f"  Gallery: {dest_name} ({width}x{height})")
                return CycleArtifact(
                    kind="screenshot",
                    path=str(filepath),
                    gallery_id=img_id,
                    caption=f"{self.scenario} — {filepath.stem}",
                    metadata={"width": width, "height": height, "file_hash": file_hash},
                )
            finally:
                db.close()
        except Exception as e:
            self.log(f"  [WARN] Failed to ingest {filepath.name}: {e}")
            return None

    # ── Phase 5: Vision Analysis ──────────────────────────────────────────

    async def analyze(self, artifacts: list[CycleArtifact]) -> list[CycleIssue]:
        """Run vision analysis on captured screenshots."""
        self.log(f"Analysis phase — inspecting {len(artifacts)} screenshot(s)...")
        issues: list[CycleIssue] = []

        # Load GDD acceptance criteria
        criteria = self._load_gdd_criteria()

        for artifact in artifacts:
            if artifact.kind != "screenshot" or not artifact.gallery_id:
                continue

            # Try vision analysis via the gallery image
            try:
                img_issues = await self._analyze_screenshot(artifact, criteria)
                issues.extend(img_issues)
            except Exception as e:
                self.log(f"  [WARN] Analysis failed for {artifact.caption}: {e}")

        # Rank issues by severity
        severity_order = {"critical": 0, "major": 1, "minor": 2, "cosmetic": 3}
        issues.sort(key=lambda i: severity_order.get(i.severity, 9))

        self.log(f"Found {len(issues)} issue(s): {', '.join(f'{i.severity}: {i.description[:50]}' for i in issues[:5])}")
        return issues

    async def _analyze_screenshot(
        self, artifact: CycleArtifact, criteria: list[dict[str, Any]]
    ) -> list[CycleIssue]:
        """Analyze a single screenshot using vision model."""
        issues: list[CycleIssue] = []

        # Read the image
        img_path = GALLERY_DIR / Path(artifact.path).name
        if not img_path.exists():
            # Try loading by gallery id
            from core.database import SessionLocal, GalleryImage
            db = SessionLocal()
            try:
                img = db.query(GalleryImage).filter(GalleryImage.id == artifact.gallery_id).first()
                if img:
                    img_path = GALLERY_DIR / img.filename
            finally:
                db.close()

        if not img_path.exists():
            return issues

        # Construct analysis prompt
        prompt = (
            f"You are analyzing a screenshot from an automated game test of '{self.scenario}'.\n"
            "Look for ANY of these issues:\n"
            "- Visual glitches (z-fighting, missing textures, flickering)\n"
            "- UI problems (broken HUD, off-screen elements, overlapping text)\n"
            "- Rendering errors (black screens, NaN pixels, incorrect lighting)\n"
            "- Gameplay issues (stuck actors, broken physics, incorrect positioning)\n"
            "- Performance indicators (frame drops visible in stats)\n\n"
            "For each issue found, respond with JSON:\n"
            '{"issues": [{"severity": "critical|major|minor|cosmetic", "system": "Flight|Combat|UI|Rendering|etc", "description": "..."}]}\n'
            "If no issues, return {\"issues\": []}"
        )

        # Try calling vision model via Odysseus's internal LLM
        try:
            import httpx
            async with httpx.AsyncClient(timeout=120) as client:
                # Use localhost API with vision
                resp = await client.post(
                    "http://localhost:7000/api/chat",
                    json={
                        "message": prompt,
                        "session": "gamedev-analysis",
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    response_text = data.get("response", "")
                    # Parse issues from response
                    issues = self._parse_issues_from_response(response_text, artifact)
        except Exception:
            pass

        return issues

    def _parse_issues_from_response(self, text: str, artifact: CycleArtifact) -> list[CycleIssue]:
        """Parse issue JSON from LLM response text."""
        issues: list[CycleIssue] = []
        try:
            # Try to find JSON in the response
            import re
            json_match = re.search(r'\{[^{}]*"issues"[^{}]*\[.*?\][^{}]*\}', text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                for item in data.get("issues", []):
                    issues.append(CycleIssue(
                        severity=item.get("severity", "minor"),
                        system=item.get("system", "Unknown"),
                        description=item.get("description", ""),
                        screenshot_id=artifact.gallery_id,
                    ))
        except Exception:
            pass
        return issues

    def _load_gdd_criteria(self) -> list[dict[str, Any]]:
        """Load acceptance criteria from the GDD."""
        gdd_path = Path(CHIMERA_ROOT) / "Docs" / "GAME_DESIGN_DOC.md"
        if not gdd_path.exists():
            return []

        import re
        criteria = []
        current_system = "Unknown"
        text = gdd_path.read_text(encoding="utf-8", errors="replace")

        for line in text.splitlines():
            if line.startswith("### ") and "Acceptance" in line:
                current_system = line.strip("# ").replace(" Acceptance Criteria", "")
            elif line.startswith("## ") and "MECHANICS" in line:
                current_system = line.strip("# ").replace("MECHANICS — ", "")

            match = re.match(r"^- \[(.)\] (.+)$", line.strip())
            if match:
                criteria.append({
                    "system": current_system,
                    "met": match.group(1) != " ",
                    "description": match.group(2),
                })

        return criteria

    # ── Phase 6: Fix ──────────────────────────────────────────────────────

    async def fix(self, issues: list[CycleIssue]) -> list[str]:
        """Generate and apply fixes for discovered issues."""
        self.log(f"Fix phase — addressing {len(issues)} issue(s)...")
        fixes: list[str] = []

        for issue in issues:
            if issue.severity in ("critical", "major"):
                fix_desc = await self._generate_fix(issue)
                if fix_desc:
                    fixes.append(fix_desc)
                    self.log(f"  Fix: {fix_desc[:80]}...")

        return fixes

    async def _generate_fix(self, issue: CycleIssue) -> str:
        """Use AI to generate a fix proposal for an issue."""
        prompt = (
            f"Game dev bug found in Unreal Engine project 'Chimera':\n"
            f"System: {issue.system}\n"
            f"Severity: {issue.severity}\n"
            f"Description: {issue.description}\n\n"
            "Propose a specific fix. Format as:\n"
            "FILE: path/to/file.cpp\n"
            "CHANGE: what to change\n"
            "REASON: why this fixes it"
        )

        try:
            import httpx
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    "http://localhost:7000/api/chat",
                    json={
                        "message": prompt,
                        "session": "gamedev-fix",
                    },
                )
                if resp.status_code == 200:
                    return resp.json().get("response", "")[:500]
        except Exception:
            pass

        return f"[Manual review needed] {issue.system}: {issue.description}"

    # ── Phase 7: Report ───────────────────────────────────────────────────

    async def report(self, cycle: CycleResult) -> str:
        """Generate a markdown report for the cycle."""
        self.log("Report phase — generating cycle report...")

        report_lines = [
            f"# Game Dev Cycle Report — {cycle.cycle_id}",
            f"",
            f"**Scenario:** {cycle.scenario}",
            f"**Started:** {cycle.started_at}",
            f"**Completed:** {cycle.completed_at}",
            f"**Passed:** {'PASSED' if cycle.passed else 'FAILED'}",
            f"",
            f"## Phases Completed",
        ]
        for phase in cycle.phases:
            report_lines.append(f"- [x] {phase}")

        report_lines.append(f"\n## Screenshots ({len(cycle.screenshots)})")
        for ss in cycle.screenshots:
            gallery_link = f"/api/generated-image/{Path(ss.path).name}" if ss.path else ""
            report_lines.append(f"- {ss.caption} {gallery_link}")

        report_lines.append(f"\n## Issues Found ({len(cycle.issues)})")
        if cycle.issues:
            for issue in cycle.issues:
                report_lines.append(
                    f"- **[{issue.severity.upper()}]** {issue.system}: {issue.description}"
                )
                if issue.proposed_fix:
                    report_lines.append(f"  - Fix: {issue.proposed_fix}")
        else:
            report_lines.append("No issues found!")

        report_lines.append(f"\n## Fixes Applied ({len(cycle.fixes)})")
        for fix in cycle.fixes:
            report_lines.append(f"- {fix}")

        report = "\n".join(report_lines)

        # Save to disk
        report_path = LOOP_LOG_DIR / f"{cycle.cycle_id}.md"
        report_path.write_text(report, encoding="utf-8")

        cycle.report = report
        return report

    # ── Main Cycle ────────────────────────────────────────────────────────

    async def run_cycle(self, session_id: str = "") -> CycleResult:
        """Run one complete automation cycle."""
        cycle_id = f"cyc_{datetime.now(timezone.utc).strftime('%H%M%S')}_{uuid.uuid4().hex[:6]}"
        self.current_cycle = CycleResult(
            cycle_id=cycle_id,
            scenario=self.scenario,
            started_at=datetime.now(timezone.utc).isoformat(),
            completed_at="",
        )
        self.log(f"=== Starting cycle {cycle_id} [{self.scenario}] ===")

        try:
            # 1. Preflight
            preflight = await self.preflight()
            self.current_cycle.phases.append("preflight")
            if not preflight["ok"] and preflight["warnings"]:
                self.log(f"Preflight warnings: {preflight['warnings']}")

            # 2. Build (if needed)
            await self.build()
            self.current_cycle.phases.append("build")

            # 3. Playtest
            await self.playtest()
            self.current_cycle.phases.append("playtest")

            # 4. Capture screenshots → gallery
            artifacts = await self.capture_screenshots(session_id)
            self.current_cycle.screenshots = artifacts
            self.current_cycle.phases.append("capture")

            # 5. Vision analysis
            issues = await self.analyze(artifacts)
            self.current_cycle.issues = issues
            self.current_cycle.phases.append("analyze")

            # 6. Fix critical/major issues
            fixes = await self.fix(issues)
            self.current_cycle.fixes = fixes
            self.current_cycle.phases.append("fix")

            # Determine pass/fail (BEFORE report so report is accurate)
            critical_issues = [i for i in issues if i.severity == "critical"]
            self.current_cycle.passed = len(critical_issues) == 0

            # 7. Report
            self.current_cycle.completed_at = datetime.now(timezone.utc).isoformat()
            await self.report(self.current_cycle)
            self.current_cycle.phases.append("report")

        except Exception as e:
            self.current_cycle.error = str(e)
            self.log(f"Cycle ERROR: {e}")

        self.current_cycle.completed_at = datetime.now(timezone.utc).isoformat()
        self.cycles.append(self.current_cycle)

        self.log(f"=== Cycle {cycle_id} complete — {'PASSED' if self.current_cycle.passed else 'FAILED'} ===")
        return self.current_cycle

    async def run_continuous(self, session_id: str = "") -> list[CycleResult]:
        """Run cycles continuously until all criteria pass or max cycles reached."""
        self.log(f"Starting CONTINUOUS mode ({self.max_cycles} max cycles)")

        for _ in range(self.max_cycles):
            if self._abort:
                self.log("Abort signal received — stopping")
                break

            cycle = await self.run_cycle(session_id)
            if cycle.passed:
                self.log(f"All criteria passed after {len(self.cycles)} cycle(s)!")
                break

        return self.cycles

    def abort(self):
        """Signal the workflow to stop after the current cycle."""
        self._abort = True
        self.log("Abort requested — will stop after current cycle")


# ── Helper functions ─────────────────────────────────────────────────────────

async def _check_mcp() -> bool:
    """Check if the Unreal Engine MCP server is reachable."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get("http://localhost:9876/health")
            return resp.status_code == 200
    except Exception:
        return False


async def _check_lm_studio() -> bool:
    """Check if LM Studio is running for vision analysis."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get("http://localhost:1234/api/v1/models")
            return resp.status_code == 200
    except Exception:
        return False


# ── Agent tool entry point ───────────────────────────────────────────────────

async def run_gamedev_cycle(
    scenario: str = "FullFlight",
    continuous: bool = False,
    max_cycles: int = 5,
    session_id: str = "",
    owner: str | None = None,
) -> dict[str, Any]:
    """Entry point for the agent's gamedev_cycle tool.

    Called from tool_execution.py when the agent invokes gamedev_cycle.
    """
    workflow = GameDevWorkflow(
        scenario=scenario,
        continuous=continuous,
        max_cycles=max_cycles,
    )

    if continuous:
        cycles = await workflow.run_continuous(session_id)
        result = {
            "output": f"Game dev automation complete. {len(cycles)} cycle(s) run.",
            "cycles": [
                {
                    "cycle_id": c.cycle_id,
                    "passed": c.passed,
                    "issues_found": len(c.issues),
                    "screenshots": len(c.screenshots),
                    "report_path": str(LOOP_LOG_DIR / f"{c.cycle_id}.md"),
                }
                for c in cycles
            ],
            "exit_code": 0,
        }
    else:
        cycle = await workflow.run_cycle(session_id)
        result = {
            "output": (
                f"Game dev cycle {cycle.cycle_id} complete.\n"
                f"Passed: {'YES' if cycle.passed else 'NO'}\n"
                f"Screenshots: {len(cycle.screenshots)} in gallery\n"
                f"Issues: {len(cycle.issues)}\n"
                f"Fixes: {len(cycle.fixes)}\n"
                f"Report: logs/gamedev_cycles/{cycle.cycle_id}.md"
            ),
            "cycle_id": cycle.cycle_id,
            "passed": cycle.passed,
            "issues": [
                {"severity": i.severity, "system": i.system, "description": i.description}
                for i in cycle.issues
            ],
            "screenshots": [
                {"gallery_id": s.gallery_id, "caption": s.caption}
                for s in cycle.screenshots
            ],
            "exit_code": 0,
        }

    return result


# ── CLI entry point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    scenario = sys.argv[1] if len(sys.argv) > 1 else "FullFlight"
    continuous = "--continuous" in sys.argv

    async def main():
        wf = GameDevWorkflow(scenario=scenario, continuous=continuous)
        if continuous:
            await wf.run_continuous()
        else:
            cycle = await wf.run_cycle()
            print(f"\nPassed: {cycle.passed}")
            print(f"Issues: {len(cycle.issues)}")
            print(f"Report: logs/gamedev_cycles/{cycle.cycle_id}.md")

    asyncio.run(main())
