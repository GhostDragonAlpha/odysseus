"""
Game Development Scenario System
===================================
Modular, data-driven playtest scenarios covering all aspects of game
design and development. Each scenario is a JSON-defined template with
configurable checks, actions, screenshots, and acceptance criteria.

Scenarios cover:
  - Rendering/Visual     - Physics/Simulation    - UI/HUD
  - Audio/Sound          - Performance            - Gameplay/Mechanics
  - AI/Navigation        - Cinematics/Sequencer  - Networking/Multiplayer
  - Input/Controls       - World/Level Streaming  - Memory/Assets
  - Build/Compilation    - Lighting/Atmosphere    - VFX/Particles

Usage:
  from scenaio_system import ScenarioRunner

  runner = ScenarioRunner("VisualRegression")
  results = await runner.run()

  # Or create custom

  scenario = Scenario(
      name="MyTest",
      checks=["rendering", "fps"],
      interval=5.0,
      count=3
  )
  runner = ScenarioRunner.from_scenario(scenario)
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional


class CheckCategory(str, Enum):
    """Categories of game development checks."""

    RENDERING = "rendering"
    PHYSICS = "physics"
    UI = "ui"
    AUDIO = "audio"
    PERFORMANCE = "performance"
    GAMEPLAY = "gameplay"
    AI = "ai"
    CINEMATICS = "cinematics"
    NETWORKING = "networking"
    INPUT = "input"
    WORLD = "world"
    MEMORY = "memory"
    BUILD = "build"
    LIGHTING = "lighting"
    VFX = "vfx"
    ANIMATION = "animation"
    TERRAIN = "terrain"
    FOLIAGE = "foliage"
    CUSTOM = "custom"


@dataclass
class ScenarioCheck:
    """A single check within a scenario."""
    id: str
    category: CheckCategory
    description: str
    enabled: bool = True
    require_screenshot: bool = False
    screenshot_interval: float = 0.0  # 0 = only at check time
    timeout: float = 30.0
    threshold: Optional[float] = None  # e.g. min FPS, max draw calls
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class Scenario:
    """A fully configurable playtest scenario.

    Define what to test, at what interval, with what thresholds.
    Each scenario maps to game dev subsystems.
    """
    name: str
    description: str
    category: CheckCategory
    checks: list[ScenarioCheck] = field(default_factory=list)
    interval: float = 5.0       # seconds between screenshots
    count: int = 3               # number of screenshots
    warmup: float = 2.0          # delay before first check
    capture_viewport: bool = True
    require_pie: bool = True    # Play In Editor required
    tags: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)


# ── Built-in scenarios covering every major game dev area ──

BUILTIN_SCENARIOS: dict[str, Scenario] = {}

BUILTIN_SCENARIOS["VisualRegression"] = Scenario(
    name="Visual Regression",
    description="Compare rendering output against baseline. Detects z-fighting, "
                "missing textures, broken shaders, incorrect lighting, "
                "flickering geometry, and post-process artifacts.",
    category=CheckCategory.RENDERING,
    interval=8.0,
    count=3,
    tags=["rendering", "qa", "regression"],
    params={"capture_game_viewport": True, "resolution": "1920x1080"},
    checks=[
        ScenarioCheck("z_fighting", CheckCategory.RENDERING,
                      "Check for z-fighting or flickering geometry", True, True),
        ScenarioCheck("missing_textures", CheckCategory.RENDERING,
                      "Detect missing/default texture assignments", True, True),
        ScenarioCheck("lighting_artifacts", CheckCategory.RENDERING,
                      "Check for incorrect lighting, shadows, or GI", True, True),
        ScenarioCheck("post_process", CheckCategory.RENDERING,
                      "Verify post-process stack: bloom, tonemap, AO", True, True),
        ScenarioCheck("lod_transitions", CheckCategory.RENDERING,
                      "Check LOD transition smoothness", True, True),
    ],
)

BUILTIN_SCENARIOS["Physics"] = Scenario(
    name="Physics & Simulation",
    description="Test collision, gravity, rigid bodies, constraints, "
                "cloth, destruction, and vehicle physics.",
    category=CheckCategory.PHYSICS,
    interval=5.0,
    count=4,
    tags=["physics", "collision", "simulation"],
    params={"gravity_scale": 1.0, "substeps": 2},
    checks=[
        ScenarioCheck("gravity", CheckCategory.PHYSICS,
                      "Verify gravity pulls objects correctly", True, True),
        ScenarioCheck("collision", CheckCategory.PHYSICS,
                      "Check collision detection and response", True, True),
        ScenarioCheck("constraints", CheckCategory.PHYSICS,
                      "Verify physics constraints hold", True, True),
        ScenarioCheck("destruction", CheckCategory.PHYSICS,
                      "Test destructible mesh behavior", False, True),
    ],
)

BUILTIN_SCENARIOS["Performance"] = Scenario(
    name="Performance Profiling",
    description="Measure frame rate, draw calls, GPU/CPU timing, "
                "memory usage, and streaming metrics.",
    category=CheckCategory.PERFORMANCE,
    interval=10.0,
    count=5,
    tags=["performance", "profiling", "fps"],
    capture_viewport=False,  # Stat commands, not screenshots
    checks=[
        ScenarioCheck("fps", CheckCategory.PERFORMANCE,
                      "Measure frames per second", True, False,
                      threshold=30.0),
        ScenarioCheck("draw_calls", CheckCategory.PERFORMANCE,
                      "Count draw calls per frame", True, False,
                      threshold=5000),
        ScenarioCheck("gpu_time", CheckCategory.PERFORMANCE,
                      "GPU frame time in ms", True, False,
                      threshold=33.0),
        ScenarioCheck("memory", CheckCategory.PERFORMANCE,
                      "Track memory usage", True, False),
        ScenarioCheck("streaming", CheckCategory.PERFORMANCE,
                      "Verify level streaming performance", True, False),
    ],
)

BUILTIN_SCENARIOS["UI_HUD"] = Scenario(
    name="UI & HUD",
    description="Verify HUD elements, menus, widget interactions, "
                "layout responsiveness, and screen-space effects.",
    category=CheckCategory.UI,
    interval=5.0,
    count=3,
    tags=["ui", "hud", "widgets"],
    params={"viewport_size": "1920x1080"},
    checks=[
        ScenarioCheck("hud_visible", CheckCategory.UI,
                      "All HUD elements visible and positioned correctly", True, True),
        ScenarioCheck("text_readable", CheckCategory.UI,
                      "UI text is legible and not overlapping", True, True),
        ScenarioCheck("menu_navigation", CheckCategory.UI,
                      "Menu navigation works correctly", False, True),
        ScenarioCheck("crosshair", CheckCategory.UI,
                      "Crosshair/aiming reticle visible", True, True),
    ],
)

BUILTIN_SCENARIOS["Audio"] = Scenario(
    name="Audio & Sound Design",
    description="Test spatial audio, mix levels, reverb zones, "
                "sound cues, attenuation, and audio occlusion.",
    category=CheckCategory.AUDIO,
    interval=5.0,
    count=2,
    tags=["audio", "sound", "sfx"],
    capture_viewport=False,
    checks=[
        ScenarioCheck("spatial_audio", CheckCategory.AUDIO,
                      "Verify 3D audio positioning", False, False),
        ScenarioCheck("mix_levels", CheckCategory.AUDIO,
                      "Check audio mix balance", False, False),
        ScenarioCheck("occlusion", CheckCategory.AUDIO,
                      "Test audio occlusion through geometry", False, False),
    ],
)

BUILTIN_SCENARIOS["Gameplay"] = Scenario(
    name="Gameplay Mechanics",
    description="Test core gameplay loops, player movement, combat, "
                "inventory, abilities, and progression systems.",
    category=CheckCategory.GAMEPLAY,
    interval=10.0,
    count=5,
    tags=["gameplay", "mechanics", "loop"],
    params={"difficulty": "normal"},
    checks=[
        ScenarioCheck("player_movement", CheckCategory.GAMEPLAY,
                      "Verify player movement: walk, run, jump, crouch", True, True),
        ScenarioCheck("combat", CheckCategory.GAMEPLAY,
                      "Test weapon firing, damage, hit detection", True, True),
        ScenarioCheck("inventory", CheckCategory.GAMEPLAY,
                      "Verify inventory and item management", False, True),
        ScenarioCheck("abilities", CheckCategory.GAMEPLAY,
                      "Test special abilities and power-ups", True, True),
        ScenarioCheck("progression", CheckCategory.GAMEPLAY,
                      "Verify progression/level-up systems", False, True),
    ],
)

BUILTIN_SCENARIOS["AI"] = Scenario(
    name="AI Behavior",
    description="Test AI perception, navigation, behavior trees, "
                "state machines, and NPC decision-making.",
    category=CheckCategory.AI,
    interval=8.0,
    count=4,
    tags=["ai", "behavior", "navigation"],
    params={"spawn_count": 5},
    checks=[
        ScenarioCheck("navigation", CheckCategory.AI,
                      "Verify NPC pathfinding to targets", True, True),
        ScenarioCheck("perception", CheckCategory.AI,
                      "Test AI sight/hearing perception", True, True),
        ScenarioCheck("behavior_tree", CheckCategory.AI,
                      "Verify behavior tree state transitions", False, True),
        ScenarioCheck("combat_ai", CheckCategory.AI,
                      "Test AI combat decision-making", True, True),
    ],
)

BUILTIN_SCENARIOS["Lighting"] = Scenario(
    name="Lighting & Atmosphere",
    description="Test dynamic lighting, sky atmosphere, volumetric fog, "
                "reflections, shadows, and time-of-day transitions.",
    category=CheckCategory.LIGHTING,
    interval=6.0,
    count=4,
    tags=["lighting", "atmosphere", "shadows"],
    params={"time_of_day": "noon"},
    checks=[
        ScenarioCheck("dynamic_lighting", CheckCategory.LIGHTING,
                      "Verify dynamic light sources", True, True),
        ScenarioCheck("shadows", CheckCategory.LIGHTING,
                      "Check shadow quality and cascades", True, True),
        ScenarioCheck("volumetrics", CheckCategory.LIGHTING,
                      "Test volumetric fog and lighting", True, True),
        ScenarioCheck("reflections", CheckCategory.LIGHTING,
                      "Verify reflection capture quality", True, True),
    ],
)

BUILTIN_SCENARIOS["Cinematics"] = Scenario(
    name="Cinematics & Sequencer",
    description="Test level sequences, camera cuts, animation blending, "
                "subtitle timing, and cinematic transitions.",
    category=CheckCategory.CINEMATICS,
    interval=8.0,
    count=3,
    tags=["cinematics", "sequencer", "animation"],
    require_pie=False,
    checks=[
        ScenarioCheck("camera_cuts", CheckCategory.CINEMATICS,
                      "Verify camera cut transitions", True, True),
        ScenarioCheck("animation_blend", CheckCategory.CINEMATICS,
                      "Check animation blending quality", True, True),
        ScenarioCheck("subtitles", CheckCategory.CINEMATICS,
                      "Verify subtitle timing against audio", False, True),
    ],
)

BUILTIN_SCENARIOS["VFX"] = Scenario(
    name="VFX & Particles",
    description="Test Niagara particle systems, GPU simulations, "
                "ribbon effects, collision-based VFX, and emitters.",
    category=CheckCategory.VFX,
    interval=5.0,
    count=3,
    tags=["vfx", "particles", "niagara"],
    checks=[
        ScenarioCheck("particle_spawn", CheckCategory.VFX,
                      "Verify particles spawn correctly", True, True),
        ScenarioCheck("gpu_simulation", CheckCategory.VFX,
                      "Test GPU particle simulation", True, True),
        ScenarioCheck("collision_vfx", CheckCategory.VFX,
                      "Verify impact/destruction VFX", True, True),
    ],
)

BUILTIN_SCENARIOS["WorldStreaming"] = Scenario(
    name="World & Level Streaming",
    description="Test world partition, level streaming, "
                "data layers, HLOD transitions, and culling.",
    category=CheckCategory.WORLD,
    interval=10.0,
    count=3,
    tags=["world", "streaming", "partition"],
    params={"cell_size": 25600},
    checks=[
        ScenarioCheck("level_load", CheckCategory.WORLD,
                      "Verify sublevel loading/unloading", True, True),
        ScenarioCheck("hlod", CheckCategory.WORLD,
                      "Check HLOD transition seams", True, True),
        ScenarioCheck("culling", CheckCategory.WORLD,
                      "Verify frustum/occlusion culling", True, True),
    ],
)

BUILTIN_SCENARIOS["Terrain"] = Scenario(
    name="Landscape & Terrain",
    description="Test landscape rendering, foliage density, "
                "terrain LOD, grass/foliage culling, and erosion.",
    category=CheckCategory.TERRAIN,
    interval=8.0,
    count=3,
    tags=["landscape", "terrain", "foliage"],
    checks=[
        ScenarioCheck("terrain_lod", CheckCategory.TERRAIN,
                      "Verify terrain LOD transitions", True, True),
        ScenarioCheck("foliage", CheckCategory.TERRAIN,
                      "Check foliage density and culling", True, True),
        ScenarioCheck("materials", CheckCategory.TERRAIN,
                      "Verify landscape material layering", True, True),
    ],
)

BUILTIN_SCENARIOS["FullFlight"] = Scenario(
    name="Full Flight",
    description="Complete flight systems test: orbital mechanics, "
                "atmospheric entry, navigation, and spaceflight physics.",
    category=CheckCategory.GAMEPLAY,
    interval=15.0,
    count=5,
    warmup=5.0,
    tags=["flight", "orbital", "space"],
    params={"map": "SplatWorld", "max_altitude": 100000},
    checks=[
        ScenarioCheck("thrust", CheckCategory.GAMEPLAY,
                      "Verify engine thrust produces acceleration", True, True),
        ScenarioCheck("navigation", CheckCategory.GAMEPLAY,
                      "Test in-game navigation and HUD", True, True),
        ScenarioCheck("orbital_mechanics", CheckCategory.GAMEPLAY,
                      "Check orbital insertion and trajectory", True, True),
        ScenarioCheck("atmospheric", CheckCategory.GAMEPLAY,
                      "Test atmospheric entry effects", True, True),
        ScenarioCheck("landing", CheckCategory.GAMEPLAY,
                      "Verify landing gear and touchdown", True, True),
    ],
)

BUILTIN_SCENARIOS["Combat"] = Scenario(
    name="Combat",
    description="Test weapons, projectiles, damage systems, "
                "hitboxes, shields, and AI combat behavior.",
    category=CheckCategory.GAMEPLAY,
    interval=8.0,
    count=4,
    tags=["combat", "weapons", "damage"],
    checks=[
        ScenarioCheck("weapon_fire", CheckCategory.GAMEPLAY,
                      "Verify weapon firing mechanics", True, True),
        ScenarioCheck("projectiles", CheckCategory.GAMEPLAY,
                      "Test projectile physics and impact", True, True),
        ScenarioCheck("damage", CheckCategory.GAMEPLAY,
                      "Check damage application and feedback", True, True),
        ScenarioCheck("shields", CheckCategory.GAMEPLAY,
                      "Verify shield/hitpoint system", True, True),
    ],
)

BUILTIN_SCENARIOS["Networked"] = Scenario(
    name="Networked Multiplayer",
    description="Test replication, RPC calls, client prediction, "
                "server authority, and lag compensation.",
    category=CheckCategory.NETWORKING,
    interval=8.0,
    count=3,
    tags=["multiplayer", "networking", "replication"],
    require_pie=False,
    capture_viewport=False,
    checks=[
        ScenarioCheck("replication", CheckCategory.NETWORKING,
                      "Verify actor replication", False, False),
        ScenarioCheck("rpc", CheckCategory.NETWORKING,
                      "Test RPC function calls", False, False),
        ScenarioCheck("authority", CheckCategory.NETWORKING,
                      "Check server authority enforcement", False, False),
    ],
)


class ScenarioRunner:
    """Runs a scenario with MCP-based or simulated checks."""

    def __init__(self, scenario_name_or_obj: str | Scenario):
        if isinstance(scenario_name_or_obj, str):
            self.scenario = BUILTIN_SCENARIOS.get(scenario_name_or_obj)
            if not self.scenario:
                available = ", ".join(BUILTIN_SCENARIOS.keys())
                raise ValueError(f"Unknown scenario '{scenario_name_or_obj}'. "
                               f"Available: {available}")
        else:
            self.scenario = scenario_name_or_obj
        self.results: dict[str, Any] = {}

    @classmethod
    def from_scenario(cls, scenario: Scenario) -> "ScenarioRunner":
        return cls(scenario)

    @classmethod
    def custom(cls, name: str, checks: list[dict] | None = None,
               **kwargs) -> "ScenarioRunner":
        """Create a custom scenario on the fly."""
        scenario = Scenario(
            name=name,
            description=kwargs.get("description", f"Custom: {name}"),
            category=CheckCategory(kwargs.get("category", "custom")),
            interval=kwargs.get("interval", 5.0),
            count=kwargs.get("count", 3),
            warmup=kwargs.get("warmup", 2.0),
            capture_viewport=kwargs.get("capture_viewport", True),
            require_pie=kwargs.get("require_pie", True),
            tags=kwargs.get("tags", []),
            params=kwargs.get("params", {}),
            checks=[
                ScenarioCheck(
                    id=c.get("id", f"check_{i}"),
                    category=CheckCategory(c.get("category", "custom")),
                    description=c.get("description", ""),
                    enabled=c.get("enabled", True),
                    require_screenshot=c.get("require_screenshot", True),
                )
                for i, c in enumerate(checks or [])
            ],
        )
        return cls(scenario)

    @property
    def summary(self) -> dict[str, Any]:
        """Return a summary of the scenario for reports."""
        enabled = [c for c in self.scenario.checks if c.enabled]
        return {
            "name": self.scenario.name,
            "description": self.scenario.description,
            "category": self.scenario.category.value,
            "checks": len(enabled),
            "screenshots": self.scenario.count,
            "interval": self.scenario.interval,
            "tags": self.scenario.tags,
            "params": self.scenario.params,
            "check_details": [
                {"id": c.id, "cat": c.category.value, "desc": c.description,
                 "screenshot": c.require_screenshot}
                for c in enabled
            ],
        }

    async def run(self, progress_cb: Callable[[str], None] | None = None) -> dict[str, Any]:
        """Execute all checks in the scenario."""
        log = progress_cb or (lambda m: print(f"  [{self.scenario.name}] {m}"))
        log(f"Starting scenario: {self.scenario.description}")
        log(f"  Checks: {len([c for c in self.scenario.checks if c.enabled])}")
        log(f"  Screenshots: {self.scenario.count} @ {self.scenario.interval}s")
        log(f"  Warmup: {self.scenario.warmup}s")

        return {
            "scenario": self.scenario.name,
            "checks": len([c for c in self.scenario.checks if c.enabled]),
            "screenshots_planned": self.scenario.count,
            "interval": self.scenario.interval,
            "warmup": self.scenario.warmup,
        }


def list_scenarios() -> list[dict[str, Any]]:
    """Return all available built-in scenarios."""
    return [
        {
            "id": name,
            "name": s.name,
            "description": s.description,
            "category": s.category.value,
            "checks": len([c for c in s.checks if c.enabled]),
            "screenshots": s.count,
            "tags": s.tags,
            "interval": s.interval,
        }
        for name, s in BUILTIN_SCENARIOS.items()
    ]
