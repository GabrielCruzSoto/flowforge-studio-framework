"""Visual continuity contract tests: structured asset sheets, scene composition, look variants and memory bridge.

Run with: /usr/bin/python3 -m unittest discover -s .agents/plugins/flowforge-studio/tests -v
"""

from __future__ import annotations

import json
import unittest

from support import (
    BAR_LOCKS,
    BAR_SPEC,
    BEAST_LOCKS,
    BEAST_SPEC,
    ELENA_LOCKS,
    ELENA_SPEC,
    EP,
    SLUG,
    WATCH_LOCKS,
    WATCH_SPEC,
    FlowforgeTestCase,
    valid_shot,
)


# ---------------------------------------------------------------------------
# Structured asset sheets (registration)
# ---------------------------------------------------------------------------


class AssetSheetTests(FlowforgeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_production()

    def register_fails(self, contains: str, **args):
        base = {
            "production_slug": SLUG,
            "asset_id": "elena",
            "kind": "character",
            "name": "Elena",
            "visual_spec": ELENA_SPEC,
            "continuity_locks": ELENA_LOCKS,
        }
        base.update(args)
        return self.fails("flowforge_register_cast_asset", contains, **base)

    def test_missing_required_facet_is_rejected_by_name(self):
        spec = {k: v for k, v in ELENA_SPEC.items() if k != "eyes"}
        self.register_fails("eyes", visual_spec=spec)

    def test_one_word_facet_is_too_vague(self):
        self.register_fails("hair", visual_spec={**ELENA_SPEC, "hair": "silver"})

    def test_unknown_facet_is_rejected(self):
        self.register_fails("wardobe", visual_spec={**ELENA_SPEC, "wardobe": "red coat with buttons"})

    def test_continuity_locks_are_required(self):
        self.register_fails("continuity_locks", continuity_locks=[])

    def test_manual_descriptor_is_rejected(self):
        self.register_fails("verbatim_prompt_descriptor", verbatim_prompt_descriptor="a woman in a red coat walking")

    def test_descriptor_over_word_cap_is_rejected(self):
        long_wardrobe = " ".join(["layered"] * 120)
        self.register_fails("words", visual_spec={**ELENA_SPEC, "wardrobe": long_wardrobe})

    def test_creature_kind_composes_descriptor_with_scale_and_locks(self):
        out = self.register("abyss_beast", "creature", "Abyss Beast", BEAST_SPEC, BEAST_LOCKS)
        desc = out["asset"]["verbatim_prompt_descriptor"]
        self.assertIn("three times human height", desc)
        self.assertIn("exactly six eyes, never more", desc)
        self.assertTrue(desc.startswith("Abyss Beast"))

    def test_creature_requires_its_own_facets(self):
        spec = {k: v for k, v in BEAST_SPEC.items() if k != "locomotion"}
        self.fails(
            "flowforge_register_cast_asset",
            "locomotion",
            production_slug=SLUG,
            asset_id="abyss_beast",
            kind="creature",
            name="Abyss Beast",
            visual_spec=spec,
            continuity_locks=BEAST_LOCKS,
        )

    def test_human_readable_sheet_is_generated_in_spanish(self):
        self.register("elena", "character", "Elena", ELENA_SPEC, ELENA_LOCKS, reference_notes="Ex detective.")
        sheet = (self.prod_dir / "02_cast_and_ingredients" / "fichas" / "elena.md").read_text(encoding="utf-8")
        self.assertIn("Vestuario", sheet)
        self.assertIn("long crimson wool coat over black turtleneck", sheet)
        self.assertIn("thin scar across left eyebrow", sheet)
        self.assertIn("Ex detective.", sheet)

    def test_variant_inherits_base_and_overrides_facets(self):
        self.register("elena", "character", "Elena", ELENA_SPEC, ELENA_LOCKS)
        out = self.register(
            "elena_winter",
            "character",
            "Elena",
            {"wardrobe": "grey shearling parka over black turtleneck"},
            [],
            base_asset_id="elena",
            valid_from_episode=3,
        )
        asset = out["asset"]
        self.assertEqual(asset["visual_spec"]["hair"], ELENA_SPEC["hair"])
        self.assertEqual(asset["visual_spec"]["wardrobe"], "grey shearling parka over black turtleneck")
        self.assertEqual(asset["continuity_locks"], ELENA_LOCKS)
        self.assertIn("grey shearling parka", asset["verbatim_prompt_descriptor"])

    def test_variant_requires_existing_base_of_same_kind_and_start_episode(self):
        self.register("elena", "character", "Elena", ELENA_SPEC, ELENA_LOCKS)
        self.register("neon_bar", "location", "Neon Bar", BAR_SPEC, BAR_LOCKS)
        self.register_fails("base_asset_id", asset_id="elena_x", base_asset_id="ghost", valid_from_episode=2)
        self.register_fails("kind", asset_id="elena_x", base_asset_id="neon_bar", valid_from_episode=2)
        self.register_fails("valid_from_episode", asset_id="elena_x", base_asset_id="elena")


# ---------------------------------------------------------------------------
# Scene composition: location, props, holders, visual DNA
# ---------------------------------------------------------------------------


class SceneCompositionTests(FlowforgeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.bootstrap()
        self.register("pocket_watch", "prop", "Pocket Watch", WATCH_SPEC, WATCH_LOCKS)
        self.register("abyss_beast", "creature", "Abyss Beast", BEAST_SPEC, BEAST_LOCKS)

    def test_video_shot_requires_location(self):
        shot = valid_shot()
        shot.pop("location_id")
        self.write_manifest([shot])
        out = self.validate()
        self.assertFalse(out["valid"])
        self.assertTrue(any("location_id" in e for e in out["errors"]))

    def test_entities_must_be_used_in_the_right_slot(self):
        self.write_manifest([valid_shot(location_id="elena", cast_ids=["pocket_watch"])])
        errors = " ".join(self.validate()["errors"])
        self.assertIn("location_id 'elena'", errors)
        self.assertIn("cast_id 'pocket_watch'", errors)

    def test_compiled_prompt_injects_cast_creature_props_location_and_full_visual_dna(self):
        self.write_manifest(
            [
                valid_shot(
                    cast_ids=["elena", "abyss_beast"],
                    props=[{"prop_id": "pocket_watch", "held_by": "elena"}],
                )
            ]
        )
        self.assertTrue(self.validate()["valid"])
        prompt = self.compiled_shots()[0]["compiled_flow_prompt"]
        self.assertIn("short silver undercut", prompt)
        self.assertIn("six glowing cyan eyes", prompt)
        self.assertIn("held by Elena", prompt)
        self.assertIn("round hunter-case pocket watch", prompt)
        self.assertIn("flickering pink neon clock above counter", prompt)
        self.assertIn("Lens:", prompt)
        # Canonical order: subjects -> action/camera -> location -> style
        self.assertLess(prompt.index("short silver undercut"), prompt.index("Slow dolly in"))
        self.assertLess(prompt.index("Slow dolly in"), prompt.index("flickering pink neon clock"))
        self.assertLess(prompt.index("flickering pink neon clock"), prompt.index("Visual Style"))

    def test_prop_holder_must_be_in_shot(self):
        self.write_manifest([valid_shot(props=[{"prop_id": "pocket_watch", "held_by": "abyss_beast"}])])
        errors = " ".join(self.validate()["errors"])
        self.assertIn("held_by", errors)

    def test_compiled_prompt_reads_cleanly(self):
        self.write_manifest(
            [valid_shot(motion_prompt="Elena lifts the watch to her ear", props=[{"prop_id": "pocket_watch", "held_by": "elena"}])]
        )
        self.assertTrue(self.validate()["valid"])
        prompt = self.compiled_shots()[0]["compiled_flow_prompt"]
        self.assertIn("Pocket Watch (held by Elena) —", prompt)
        self.assertIn("Elena lifts the watch to her ear. Location:", prompt)

    def test_overlong_compiled_prompt_warns(self):
        import flowforge_mcp_server as server

        original = server.COMPILED_PROMPT_WARN_WORDS
        server.COMPILED_PROMPT_WARN_WORDS = 50
        try:
            out = self.validate()
        finally:
            server.COMPILED_PROMPT_WARN_WORDS = original
        self.assertTrue(out["valid"])
        self.assertTrue(any("words" in w and "dilute" in w for w in out["warnings"]))

    def test_portraits_are_attached_as_references_except_on_exact_joins(self):
        portrait = self.prod_dir / "02_cast_and_ingredients" / "portraits" / "elena.png"
        portrait.write_bytes(b"png")
        self.register(
            "elena", "character", "Elena", ELENA_SPEC, ELENA_LOCKS,
            portrait_rel_path="02_cast_and_ingredients/portraits/elena.png",
        )
        self.write_manifest(
            [
                valid_shot(),
                valid_shot(shot_id="shot_02", chain_previous=True, continuity_mode="exact"),
            ]
        )
        self.assertTrue(self.validate()["valid"])
        shots = self.compiled_shots()
        self.assertEqual(shots[0]["resolved_reference_images"], ["02_cast_and_ingredients/portraits/elena.png"])
        self.assertEqual(shots[1]["resolved_reference_images"], [])


# ---------------------------------------------------------------------------
# Look variants across episodes + memory bridge
# ---------------------------------------------------------------------------


class VariantContinuityTests(FlowforgeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.bootstrap()
        self.register(
            "elena_winter", "character", "Elena",
            {"wardrobe": "grey shearling parka over black turtleneck"}, [],
            base_asset_id="elena", valid_from_episode=3,
        )
        self.ep3 = self.init_episode(3, "winter")

    def test_base_look_superseded_by_active_variant_is_an_error(self):
        self.write_manifest([valid_shot()], self.ep3)
        errors = " ".join(self.validate(self.ep3)["errors"])
        self.assertIn("elena_winter", errors)

    def test_variant_used_before_its_episode_is_an_error(self):
        self.write_manifest([valid_shot(cast_ids=["elena_winter"])])
        errors = " ".join(self.validate()["errors"])
        self.assertIn("valid_from_episode", errors)

    def test_correct_variant_passes_and_flashback_allows_old_look(self):
        self.write_manifest(
            [valid_shot(cast_ids=["elena_winter"]), valid_shot(shot_id="shot_02", flashback=True)],
            self.ep3,
        )
        self.assertTrue(self.validate(self.ep3)["valid"])

    def test_state_change_memory_after_look_start_raises_warning(self):
        self.ok(
            "flowforge_memory_save",
            production_slug=SLUG,
            title="Elena loses her right glove",
            content="En el capítulo 3 Elena pierde el guante derecho.",
            memory_type="state_change",
            episode_number=3,
            entities=["elena"],
        )
        self.write_manifest([valid_shot()])
        out = self.validate()
        self.assertTrue(out["valid"])
        self.assertFalse(any("Elena loses her right glove" in w for w in out["warnings"]))

        ep4 = self.init_episode(4, "after")
        self.write_manifest([valid_shot(cast_ids=["elena_winter"])], ep4)
        out = self.validate(ep4)
        self.assertTrue(out["valid"])
        self.assertFalse(any("right glove" in w for w in out["warnings"]))

        self.ok(
            "flowforge_memory_save",
            production_slug=SLUG,
            title="Elena's parka gets torn",
            content="En el capítulo 4 se rompe la parka.",
            memory_type="state_change",
            episode_number=4,
            entities=["elena"],
        )
        out = self.validate(ep4)
        self.assertTrue(any("parka gets torn" in w for w in out["warnings"]))


if __name__ == "__main__":
    unittest.main()
