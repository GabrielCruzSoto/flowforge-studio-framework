"""Shared fixtures for flowforge-engine tests (isolated temporary workspace per test)."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import flowforge_mcp_server as server  # noqa: E402

SLUG = "testprod"
EP = "ep_01_pilot"

ELENA_SPEC = {
    "apparent_age": "late thirties",
    "build": "tall and lean",
    "face": "angular jaw, high cheekbones",
    "skin": "pale olive skin",
    "hair": "short silver undercut",
    "eyes": "pale green eyes",
    "wardrobe": "long crimson wool coat over black turtleneck",
    "palette": "crimson, black and silver",
}
ELENA_LOCKS = ["thin scar across left eyebrow"]

BAR_SPEC = {
    "setting": "abandoned neon cocktail bar",
    "layout": "long narrow room, counter along left wall",
    "landmarks": "flickering pink neon clock above counter",
    "materials": "cracked black tiles and brass fittings",
    "palette": "magenta, teal and black",
    "lighting": "low-key neon practicals with haze",
    "atmosphere": "rain streaked windows, drifting haze",
}
BAR_LOCKS = ["neon clock always reads 3:15"]

WATCH_SPEC = {
    "scale": "fits inside a closed palm",
    "shape": "round hunter-case pocket watch",
    "materials": "tarnished brass with glass face",
    "palette": "brass gold and ivory",
    "surface_details": "engraved spiral on the lid",
    "orientation_rule": "crown always points up",
}
WATCH_LOCKS = ["spiral engraving always visible on lid"]

BEAST_SPEC = {
    "scale": "three times human height",
    "silhouette": "hunched quadruped with long forelimbs",
    "anatomy": "elongated jaw and bony dorsal spines",
    "surface": "wet obsidian scales",
    "palette": "black with bioluminescent cyan veins",
    "eyes": "six glowing cyan eyes",
    "locomotion": "slow deliberate knuckle walk",
}
BEAST_LOCKS = ["exactly six eyes, never more"]


def valid_shot(**overrides):
    shot = {
        "shot_id": "shot_01",
        "scene_number": 1,
        "model": "Veo 3.1 - Fast",
        "duration_sec": 8,
        "batches": "x1",
        "motion_prompt": "Slow dolly in as Elena walks to the counter and sets down her glass",
        "cast_ids": ["elena"],
        "location_id": "neon_bar",
    }
    shot.update(overrides)
    return shot


class FlowforgeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ff_test_"))
        self._orig = (server.WORKSPACE_ROOT, server.PRODUCTIONS_ROOT)
        server.WORKSPACE_ROOT = self.tmp
        server.PRODUCTIONS_ROOT = self.tmp / "productions"

    def tearDown(self) -> None:
        server.WORKSPACE_ROOT, server.PRODUCTIONS_ROOT = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- MCP call helpers ----------------------------------------------------

    def call(self, tool: str, **args):
        resp = server.handle_rpc_request(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}
        )
        result = resp["result"]
        return result["isError"], json.loads(result["content"][0]["text"])

    def ok(self, tool: str, **args):
        is_error, out = self.call(tool, **args)
        self.assertFalse(is_error, f"{tool} failed unexpectedly: {out}")
        return out

    def fails(self, tool: str, contains: str, **args):
        is_error, out = self.call(tool, **args)
        self.assertTrue(is_error, f"{tool} should have failed but returned: {out}")
        self.assertIn(contains.lower(), out["message"].lower())
        return out

    # -- production fixtures -------------------------------------------------

    @property
    def prod_dir(self) -> Path:
        return server.PRODUCTIONS_ROOT / SLUG

    def episode_dir(self, episode_id: str = EP) -> Path:
        return self.prod_dir / "episodes" / episode_id

    def manifest_path(self, episode_id: str = EP) -> Path:
        return self.episode_dir(episode_id) / "01_script" / "shot_manifest.json"

    def register(self, asset_id: str, kind: str, name: str, spec: dict, locks: list[str], **extra):
        return self.ok(
            "flowforge_register_cast_asset",
            production_slug=SLUG,
            asset_id=asset_id,
            kind=kind,
            name=name,
            visual_spec=spec,
            continuity_locks=locks,
            **extra,
        )

    def init_production(self, *, per_episode_budget: int = 600) -> None:
        self.ok(
            "flowforge_init_production",
            production_slug=SLUG,
            title="Test",
            aesthetic_style="neo-noir anamorphic",
            per_episode_credit_budget=per_episode_budget,
        )

    def init_episode(self, number: int = 1, slug: str = "pilot") -> str:
        return self.ok(
            "flowforge_init_episode", production_slug=SLUG, episode_number=number, episode_slug=slug, title=slug
        )["episode_id"]

    def bootstrap(self, *, per_episode_budget: int = 600, shots=None) -> None:
        self.init_production(per_episode_budget=per_episode_budget)
        self.register("elena", "character", "Elena", ELENA_SPEC, ELENA_LOCKS)
        self.register("neon_bar", "location", "Neon Bar", BAR_SPEC, BAR_LOCKS)
        self.init_episode()
        self.write_manifest(shots or [valid_shot()])

    def write_manifest(self, shots, episode_id: str = EP) -> None:
        self.manifest_path(episode_id).write_text(
            json.dumps({"production_slug": SLUG, "episode_id": episode_id, "shots": shots}), encoding="utf-8"
        )

    def validate(self, episode_id: str = EP):
        return self.ok("flowforge_validate_shot_manifest", production_slug=SLUG, episode_id=episode_id)

    def compiled_shots(self, episode_id: str = EP):
        return json.loads(self.manifest_path(episode_id).read_text())["shots"]
