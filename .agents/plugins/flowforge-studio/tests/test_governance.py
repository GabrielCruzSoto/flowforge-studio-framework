"""Governance contract tests for flowforge-engine: HITL gates, manifest fingerprinting, quotes and ledger.

Run with: /usr/bin/python3 -m unittest discover -s .agents/plugins/flowforge-studio/tests -v
"""

from __future__ import annotations

import json
import unittest

import yaml

from support import ELENA_LOCKS, ELENA_SPEC, EP, SLUG, FlowforgeTestCase, valid_shot


class GovernanceTestCase(FlowforgeTestCase):
    def approve(self, gate: str, episode_id: str | None = None) -> None:
        extra = {"episode_id": episode_id} if episode_id else {}
        self.ok("flowforge_set_gate", production_slug=SLUG, gate=gate, state="in_review", **extra)
        self.ok(
            "flowforge_set_gate",
            production_slug=SLUG,
            gate=gate,
            state="approved",
            human_confirmation="Aprobado, adelante.",
            **extra,
        )

    def unlock_render(self) -> None:
        self.validate()
        self.approve("universe_bible")
        self.approve("cast_locked")
        self.approve("outline", EP)
        self.approve("script", EP)
        self.approve("storyboard", EP)

    def quote(self):
        return self.ok("flowforge_quote_and_log_credits", production_slug=SLUG, action="quote", episode_id=EP)


# ---------------------------------------------------------------------------
# HITL gates enforced in code
# ---------------------------------------------------------------------------


class GateTests(GovernanceTestCase):
    def test_approval_flow_unlocks_downstream_gate_and_is_audited(self):
        self.bootstrap()
        self.approve("universe_bible")
        out = self.ok("flowforge_check_gates", production_slug=SLUG, target_gate="cast_locked")
        self.assertTrue(out["unlocked"])
        history = yaml.safe_load((self.prod_dir / "production.yaml").read_text())["gate_history"]
        self.assertEqual([h["to"] for h in history], ["in_review", "approved"])
        self.assertEqual(history[-1]["human_confirmation"], "Aprobado, adelante.")

    def test_approve_requires_human_confirmation(self):
        self.bootstrap()
        self.ok("flowforge_set_gate", production_slug=SLUG, gate="universe_bible", state="in_review")
        self.fails("flowforge_set_gate", "human_confirmation", production_slug=SLUG, gate="universe_bible", state="approved")

    def test_approve_requires_in_review_first(self):
        self.bootstrap()
        self.fails(
            "flowforge_set_gate",
            "in_review",
            production_slug=SLUG,
            gate="universe_bible",
            state="approved",
            human_confirmation="ok dale",
        )

    def test_cannot_review_gate_with_missing_prerequisites(self):
        self.bootstrap()
        self.fails("flowforge_set_gate", "universe_bible", production_slug=SLUG, gate="cast_locked", state="in_review")

    def test_invalid_state_rejected(self):
        self.bootstrap()
        self.fails("flowforge_set_gate", "state", production_slug=SLUG, gate="universe_bible", state="done")

    def test_hand_edited_approval_is_not_trusted(self):
        self.bootstrap()
        manifest_path = self.prod_dir / "production.yaml"
        manifest = yaml.safe_load(manifest_path.read_text())
        manifest["production_gates"]["universe_bible"] = "approved"
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False))

        out = self.ok("flowforge_check_gates", production_slug=SLUG, target_gate="cast_locked")
        self.assertFalse(out["unlocked"])
        self.assertIn("universe_bible", out["unaudited_approvals"])

    def test_script_review_requires_current_validation(self):
        self.bootstrap()
        self.approve("universe_bible")
        self.approve("cast_locked")
        self.approve("outline", EP)
        self.validate()
        self.write_manifest([valid_shot(motion_prompt="Elena runs across the bar and vaults over the counter")])
        self.fails(
            "flowforge_set_gate", "validat", production_slug=SLUG, gate="script", state="in_review", episode_id=EP
        )


# ---------------------------------------------------------------------------
# Manifest linting feeding the quote
# ---------------------------------------------------------------------------


class LintTests(GovernanceTestCase):
    def test_unknown_model_is_an_error(self):
        self.bootstrap(shots=[valid_shot(model="Veo 3.1 Fast")])
        out = self.validate()
        self.assertFalse(out["valid"])
        self.assertTrue(any("Unknown model" in e for e in out["errors"]))

    def test_omni_12_invalid_duration_is_an_error(self):
        self.bootstrap(shots=[valid_shot(model="Omni 1.2 Flash", duration_sec=7)])
        self.assertFalse(self.validate()["valid"])

    def test_successful_validation_stores_fingerprint(self):
        self.bootstrap()
        out = self.validate()
        self.assertTrue(out["valid"])
        stored = json.loads(self.manifest_path().read_text())
        self.assertEqual(len(stored["validated_sha256"]), 64)
        self.assertEqual(stored["validated_sha256"], out["manifest_sha256"])


# ---------------------------------------------------------------------------
# Quotes bound to approved, validated manifests
# ---------------------------------------------------------------------------


class QuoteTests(GovernanceTestCase):
    def test_quote_requires_render_gate_unlocked(self):
        self.bootstrap()
        self.validate()
        self.fails("flowforge_quote_and_log_credits", "render", production_slug=SLUG, action="quote", episode_id=EP)

    def test_quote_rejects_manifest_edited_after_validation(self):
        self.bootstrap()
        self.unlock_render()
        manifest = json.loads(self.manifest_path().read_text())
        manifest["shots"][0].update({"model": "Veo 3.1 - Quality", "batches": "x4"})
        self.manifest_path().write_text(json.dumps(manifest))
        self.fails("flowforge_quote_and_log_credits", "validat", production_slug=SLUG, action="quote", episode_id=EP)

    def test_quote_rejects_stale_compiled_prompts_after_cast_change(self):
        self.bootstrap()
        self.unlock_render()
        self.register(
            "elena", "character", "Elena", {**ELENA_SPEC, "wardrobe": "green hooded raincoat over grey sweater"}, ELENA_LOCKS
        )
        self.fails("flowforge_quote_and_log_credits", "stale", production_slug=SLUG, action="quote", episode_id=EP)

    def test_quote_issues_quote_id_bound_to_manifest(self):
        self.bootstrap()
        self.unlock_render()
        out = self.quote()
        self.assertEqual(out["quoted_credits"], 20)
        self.assertTrue(out["quote_id"].startswith("q_"))
        ledger = json.loads((self.prod_dir / "credit_ledger.json").read_text())
        stored = next(q for q in ledger["quotes"] if q["quote_id"] == out["quote_id"])
        self.assertEqual(stored["episode_id"], EP)
        self.assertEqual(stored["quoted_credits"], 20)
        self.assertEqual(stored["manifest_sha256"], json.loads(self.manifest_path().read_text())["validated_sha256"])

    def test_quote_over_budget_issues_no_quote_id(self):
        self.bootstrap(per_episode_budget=10)
        self.unlock_render()
        out = self.quote()
        self.assertFalse(out["within_budget"])
        self.assertIsNone(out["quote_id"])


# ---------------------------------------------------------------------------
# Ledger records require a valid quote
# ---------------------------------------------------------------------------


class RecordTests(GovernanceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.bootstrap()
        self.unlock_render()
        self.quote_id = self.quote()["quote_id"]

    def record(self, **args):
        base = {"production_slug": SLUG, "action": "record", "episode_id": EP}
        base.update(args)
        return base

    def test_record_happy_path(self):
        out = self.ok("flowforge_quote_and_log_credits", **self.record(credits=20, quote_id=self.quote_id))
        self.assertEqual(out["total_credits_spent"], 20)
        self.assertEqual(out["recorded_entry"]["quote_id"], self.quote_id)

    def test_record_rejects_negative_credits(self):
        self.fails("flowforge_quote_and_log_credits", "credits", **self.record(credits=-500, quote_id=self.quote_id))

    def test_record_requires_quote_id(self):
        self.fails("flowforge_quote_and_log_credits", "quote_id", **self.record(credits=20))

    def test_record_rejects_unknown_quote_id(self):
        self.fails("flowforge_quote_and_log_credits", "quote_id", **self.record(credits=20, quote_id="q_deadbeef0000"))

    def test_record_rejects_quote_from_other_episode(self):
        self.init_episode(2, "second")
        self.fails(
            "flowforge_quote_and_log_credits",
            "episode",
            production_slug=SLUG,
            action="record",
            episode_id="ep_02_second",
            credits=20,
            quote_id=self.quote_id,
        )

    def test_record_overrun_requires_reason_and_is_flagged(self):
        self.ok("flowforge_quote_and_log_credits", **self.record(credits=20, quote_id=self.quote_id))
        self.fails("flowforge_quote_and_log_credits", "overrun_reason", **self.record(credits=5, quote_id=self.quote_id))
        out = self.ok(
            "flowforge_quote_and_log_credits",
            **self.record(credits=5, quote_id=self.quote_id, overrun_reason="Flow charged a failed retry"),
        )
        self.assertTrue(out["recorded_entry"]["overrun"])


if __name__ == "__main__":
    unittest.main()
