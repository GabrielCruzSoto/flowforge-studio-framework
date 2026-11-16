#!/usr/bin/env python3
"""FlowForge Studio — Deterministic MCP Server for Audiovisual Governance & Media Mastering.

Implements MCP Stdio JSON-RPC 2.0 (protocol 2025-03-26) with strict cartridge sandboxing,
production & episodic HITL gate enforcement, Google Flow prompt/physics linting,
Google AI Ultra credit budgeting/ledgering, frame-accurate continuity extraction,
storyboard contact sheet rendering, and broadcast-normalized (-14 LUFS) episode mastering.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

SERVER_NAME = "flowforge-engine"
SERVER_VERSION = "1.0.0"
DEFAULT_PROTOCOL_VERSION = "2025-03-26"

# Resolve workspace root: <root>/.agents/plugins/flowforge-studio/scripts/flowforge_mcp_server.py
SCRIPT_PATH = Path(__file__).resolve()
WORKSPACE_ROOT = Path(os.environ.get("FLOWFORGE_ROOT", str(SCRIPT_PATH.parents[4]))).resolve()
PRODUCTIONS_ROOT = (WORKSPACE_ROOT / "productions").resolve()
TEMPLATES_ROOT = (WORKSPACE_ROOT / "templates").resolve()

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}$")
EPISODE_ID_RE = re.compile(r"^ep_\d{2}_[a-z0-9][a-z0-9_-]{1,50}$")

VALID_FORMATS = {"series", "short-film", "music-video"}
VALID_GATE_STATES = {"pending", "draft", "in_review", "approved", "blocked"}

# Empirical Google Flow credit schedule (per x1 generation)
MODEL_CREDIT_RATES: dict[str, Any] = {
    "Nano Banana 2.1": 0,
    "Nano Banana Pro": 0,
    "Nano Banana 2 Lite": 0,
    "Omni 1.1 Flash": {4: 7, 6: 9, 8: 12, 10: 15, "default": 12},
    "Omni 1.2 Flash": {4: 7, 6: 9, 8: 12, 10: 15, 12: 18, 15: 22, "default": 12},
    "Veo 3.1 - Lite": 10,
    "Veo 3.1 - Fast": 20,
    "Veo 3.1 - Quality": 100,
    "Flow Video Edit": 20,
}

# Empirical Google Flow prompt hazards (verified against real Flow runs)
NEGATIVE_OBJECT_PATTERNS = [
    re.compile(r"\b(?:no|without\s+a|without\s+any|never\s+holding|do\s+not\s+show|don't\s+show)\s+[a-z]{3,}", re.I),
]
PHYSICS_HAZARD_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bclear\s+straw\b|\btransparent\s+straw\b", re.I), "Flow fails to animate liquid rising in clear straws; use an opaque straw or rim drinking."),
    (re.compile(r"\bupside[\s-]+down\b|\bend\s+over\s+end\b", re.I), "Flipping rigid objects upside-down causes Flow to swap cap/lid ends mid-turn; keep orientation fixed."),
    (re.compile(r"\bconfetti\b|\bflying\s+debris\b|\bsmashing\s+crates\b", re.I), "Dense small-particle physics often morph or break coherence; simplify environmental action."),
]
TIMECODE_RE = re.compile(r"\b\d+(?:\.\d+)?\s*-\s*\d+(?:\.\d+)?\s*s\b|\[\d+\s*-\s*\d+s\]", re.I)


class SandboxViolationError(ValueError):
    """Raised when any path attempts to escape its isolated production cartridge."""


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def resolve_production_dir(production_slug: str, *, must_exist: bool = True) -> Path:
    if not isinstance(production_slug, str) or not SLUG_RE.match(production_slug):
        raise SandboxViolationError(
            "Invalid production_slug. Use lowercase letters, digits, hyphens, or underscores (2-63 chars)."
        )
    PRODUCTIONS_ROOT.mkdir(parents=True, exist_ok=True)
    prod_dir = (PRODUCTIONS_ROOT / production_slug).resolve()
    if not prod_dir.is_relative_to(PRODUCTIONS_ROOT) or prod_dir == PRODUCTIONS_ROOT:
        raise SandboxViolationError("Sandbox violation: production path escapes productions/ root.")
    if must_exist and not prod_dir.is_dir():
        raise FileNotFoundError(f"Production cartridge not found: productions/{production_slug}/")
    return prod_dir


def resolve_episode_dir(prod_dir: Path, episode_id: str, *, must_exist: bool = True) -> Path:
    if not isinstance(episode_id, str) or not EPISODE_ID_RE.match(episode_id):
        raise SandboxViolationError(
            "Invalid episode_id. Expected format 'ep_NN_slug' (e.g. 'ep_01_pilot')."
        )
    ep_root = (prod_dir / "episodes").resolve()
    ep_dir = (ep_root / episode_id).resolve()
    if not ep_dir.is_relative_to(ep_root) or ep_dir == ep_root:
        raise SandboxViolationError("Sandbox violation: episode path escapes episodes/ directory.")
    if must_exist and not ep_dir.is_dir():
        raise FileNotFoundError(f"Episode directory not found: episodes/{episode_id}/")
    return ep_dir


def resolve_cartridge_path(prod_dir: Path, rel_path: str, *, must_exist: bool = False) -> Path:
    if not rel_path or os.path.isabs(rel_path):
        raise SandboxViolationError(f"Path must be relative to the production cartridge, got: {rel_path!r}")
    target = (prod_dir / rel_path).resolve()
    if not target.is_relative_to(prod_dir):
        raise SandboxViolationError(f"Sandbox violation: '{rel_path}' escapes {prod_dir.name}/.")
    if must_exist and not target.exists():
        raise FileNotFoundError(f"File not found inside {prod_dir.name}/: {rel_path}")
    return target


def read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Missing YAML manifest: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid YAML root object in {path.name}")
    return data


def write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")


def read_json(path: Path) -> Any:
    if not path.is_file():
        raise FileNotFoundError(f"Missing JSON file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Tool Implementations
# ---------------------------------------------------------------------------


def tool_init_production(args: dict[str, Any]) -> dict[str, Any]:
    slug = args["production_slug"]
    title = args["title"].strip()
    fmt = args.get("format_type", "series")
    if fmt not in VALID_FORMATS:
        raise ValueError(f"Invalid format_type '{fmt}'. Must be one of: {sorted(VALID_FORMATS)}")

    prod_dir = resolve_production_dir(slug, must_exist=False)
    if prod_dir.exists() and not args.get("overwrite", False):
        raise FileExistsError(f"Production '{slug}' already exists. Pass overwrite=true only if intentional.")

    subdirs = [
        "01_universe_bible",
        "02_cast_and_ingredients/portraits",
        "02_cast_and_ingredients/locations",
        "02_cast_and_ingredients/props",
        "03_sound_and_music/tracks",
        "episodes",
    ]
    for sub in subdirs:
        (prod_dir / sub).mkdir(parents=True, exist_ok=True)

    aspect_ratio = args.get("aspect_ratio", "16:9")
    monthly_budget = int(args.get("monthly_credit_budget", 25000))
    per_episode_budget = int(args.get("per_episode_credit_budget", 600))

    production_manifest = {
        "schema_version": "1.0.0",
        "production_slug": slug,
        "title": title,
        "format_type": fmt,
        "language": args.get("language", "es"),
        "created_at": utc_now_iso(),
        "visual_dna": {
            "aspect_ratio": aspect_ratio,
            "master_resolution": args.get("master_resolution", "1080p"),
            "frame_rate": 24,
            "aesthetic_style": args.get("aesthetic_style", "Pending definition in universe_bible"),
            "lens_package": args.get("lens_package", "35mm & 85mm cinema primes, controlled depth of field"),
            "color_palette": args.get("color_palette", "Pending definition in universe_bible"),
            "lighting_signature": args.get("lighting_signature", "High-contrast motivated cinema lighting"),
        },
        "credit_budget": {
            "subscription_tier": args.get("subscription_tier", "Google AI Ultra"),
            "monthly_cap": monthly_budget,
            "per_episode_cap": per_episode_budget,
            "default_draft_model": "Omni 1.1 Flash",
            "default_hero_model": "Veo 3.1 - Fast",
        },
        "flow_project": {
            "active_project_id": "",
            "active_project_url": "",
            "active_project_title": title,
        },
        "production_gates": {
            "universe_bible": "pending",
            "cast_locked": "pending",
            "sound_identity": "pending",
        },
        "episodes_index": [],
    }
    write_yaml(prod_dir / "production.yaml", production_manifest)

    write_json(
        prod_dir / "credit_ledger.json",
        {
            "production_slug": slug,
            "monthly_cap": monthly_budget,
            "total_credits_spent": 0,
            "entries": [],
        },
    )
    write_json(
        prod_dir / "02_cast_and_ingredients" / "cast_registry.json",
        {
            "production_slug": slug,
            "updated_at": utc_now_iso(),
            "assets": [],
        },
    )
    write_json(
        prod_dir / "03_sound_and_music" / "voice_cast.json",
        {
            "production_slug": slug,
            "default_engine": "kokoro",
            "voices": [],
        },
    )

    return {
        "status": "initialized",
        "production_slug": slug,
        "production_dir": str(prod_dir),
        "format_type": fmt,
        "pipeline_order": "beat-first" if fmt == "music-video" else "narrative-first",
        "next_gate": "sound_identity" if fmt == "music-video" else "universe_bible",
    }


def tool_init_episode(args: dict[str, Any]) -> dict[str, Any]:
    prod_dir = resolve_production_dir(args["production_slug"])
    ep_num = int(args["episode_number"])
    ep_slug = re.sub(r"[^a-z0-9_-]+", "-", args["episode_slug"].strip().lower()).strip("-")
    episode_id = f"ep_{ep_num:02d}_{ep_slug}"
    ep_dir = resolve_episode_dir(prod_dir, episode_id, must_exist=False)

    if ep_dir.exists() and not args.get("overwrite", False):
        raise FileExistsError(f"Episode '{episode_id}' already exists.")

    for sub in [
        "01_script",
        "02_storyboard/keyframes",
        "03_flow_renders/raw_clips",
        "03_flow_renders/continuity_frames",
        "04_dialogue_and_sfx",
        "05_master",
    ]:
        (ep_dir / sub).mkdir(parents=True, exist_ok=True)

    # Auto-link continuity from Episode N-1 if present
    prev_frame_rel = ""
    if ep_num > 1:
        episodes_root = prod_dir / "episodes"
        prev_prefix = f"ep_{ep_num - 1:02d}_"
        for candidate in sorted(episodes_root.iterdir()):
            if candidate.is_dir() and candidate.name.startswith(prev_prefix):
                anchor = candidate / "05_master" / "ep_final_continuity_frame.png"
                if anchor.is_file():
                    prev_frame_rel = str(anchor.relative_to(prod_dir))
                break

    episode_manifest = {
        "schema_version": "1.0.0",
        "production_slug": args["production_slug"],
        "episode_id": episode_id,
        "episode_number": ep_num,
        "title": args["title"].strip(),
        "logline": args.get("logline", ""),
        "target_duration_sec": int(args.get("target_duration_sec", 60)),
        "created_at": utc_now_iso(),
        "continuity_in": {
            "previous_episode_final_frame": prev_frame_rel,
            "narrative_state_in": args.get("narrative_state_in", ""),
        },
        "flow_episode_project": {
            "flow_project_id": "",
            "flow_project_url": "",
            "cast_rehydrated": False,
        },
        "episode_gates": {
            "outline": "pending",
            "script": "pending",
            "storyboard": "pending",
            "render": "pending",
            "postprod": "pending",
            "master": "pending",
        },
    }
    write_yaml(ep_dir / "episode.yaml", episode_manifest)

    prod_manifest = read_yaml(prod_dir / "production.yaml")
    idx = prod_manifest.setdefault("episodes_index", [])
    if not any(item.get("episode_id") == episode_id for item in idx):
        idx.append({"episode_id": episode_id, "episode_number": ep_num, "title": args["title"].strip()})
        idx.sort(key=lambda x: x["episode_number"])
        write_yaml(prod_dir / "production.yaml", prod_manifest)

    return {
        "status": "initialized",
        "episode_id": episode_id,
        "episode_dir": str(ep_dir),
        "previous_episode_final_frame": prev_frame_rel or None,
    }


def tool_check_gates(args: dict[str, Any]) -> dict[str, Any]:
    prod_dir = resolve_production_dir(args["production_slug"])
    prod_manifest = read_yaml(prod_dir / "production.yaml")
    fmt = prod_manifest.get("format_type", "series")
    prod_gates = prod_manifest.get("production_gates") or {}

    if fmt == "music-video":
        prod_prereqs = {
            "sound_identity": [],
            "universe_bible": ["sound_identity"],
            "cast_locked": ["sound_identity", "universe_bible"],
        }
    else:
        prod_prereqs = {
            "universe_bible": [],
            "cast_locked": ["universe_bible"],
            "sound_identity": ["universe_bible"],
        }

    target_gate = args["target_gate"]
    episode_id = args.get("episode_id")

    if target_gate in prod_prereqs:
        missing = [g for g in prod_prereqs[target_gate] if prod_gates.get(g) != "approved"]
        return {
            "scope": "production",
            "production_slug": args["production_slug"],
            "format_type": fmt,
            "target_gate": target_gate,
            "current_status": prod_gates.get(target_gate, "pending"),
            "unlocked": len(missing) == 0,
            "missing_approvals": missing,
            "production_gates": prod_gates,
        }

    ep_prereqs = {
        "outline": {"prod": ["universe_bible"], "ep": []},
        "script": {"prod": ["universe_bible", "cast_locked"] + (["sound_identity"] if fmt == "music-video" else []), "ep": ["outline"]},
        "storyboard": {"prod": ["universe_bible", "cast_locked"], "ep": ["script"]},
        "render": {"prod": ["universe_bible", "cast_locked"] + (["sound_identity"] if fmt == "music-video" else []), "ep": ["script", "storyboard"]},
        "postprod": {"prod": ["sound_identity"], "ep": ["render"]},
        "master": {"prod": ["sound_identity"], "ep": ["render", "postprod"]},
    }

    if target_gate not in ep_prereqs:
        raise ValueError(
            f"Unknown target_gate '{target_gate}'. Valid gates: {sorted(list(prod_prereqs) + list(ep_prereqs))}"
        )
    if not episode_id:
        raise ValueError(f"episode_id is required when checking episodic gate '{target_gate}'.")

    ep_dir = resolve_episode_dir(prod_dir, episode_id)
    ep_manifest = read_yaml(ep_dir / "episode.yaml")
    ep_gates = ep_manifest.get("episode_gates") or {}

    missing_prod = [f"production.{g}" for g in ep_prereqs[target_gate]["prod"] if prod_gates.get(g) != "approved"]
    missing_ep = [f"episode.{g}" for g in ep_prereqs[target_gate]["ep"] if ep_gates.get(g) != "approved"]
    all_missing = missing_prod + missing_ep

    return {
        "scope": "episode",
        "production_slug": args["production_slug"],
        "episode_id": episode_id,
        "target_gate": target_gate,
        "current_status": ep_gates.get(target_gate, "pending"),
        "unlocked": len(all_missing) == 0,
        "missing_approvals": all_missing,
        "production_gates": prod_gates,
        "episode_gates": ep_gates,
    }


def tool_register_cast_asset(args: dict[str, Any]) -> dict[str, Any]:
    prod_dir = resolve_production_dir(args["production_slug"])
    reg_path = prod_dir / "02_cast_and_ingredients" / "cast_registry.json"
    registry = read_json(reg_path)

    asset_id = args["asset_id"].strip().lower()
    if not SLUG_RE.match(asset_id):
        raise ValueError("Invalid asset_id. Use lowercase letters, digits, hyphens, or underscores.")

    kind = args["kind"]
    if kind not in {"character", "prop", "location"}:
        raise ValueError("kind must be 'character', 'prop', or 'location'.")

    descriptor = args["verbatim_prompt_descriptor"].strip()
    if len(descriptor.split()) < 8:
        raise ValueError(
            "verbatim_prompt_descriptor is too vague (<8 words). Provide specific immutable visual traits in English."
        )

    portrait_source = args.get("portrait_source", "flow_character_describe")
    warnings: list[str] = []
    if kind == "character" and portrait_source == "real_photo":
        warnings.append(
            "SAFETY WARNING: Google Flow frequently blocks video generation on characters built from real-person photos. "
            "Prefer portrait_source='flow_character_describe' or stylized neutral sheets."
        )

    portrait_rel = args.get("portrait_rel_path", "")
    if portrait_rel:
        resolve_cartridge_path(prod_dir, portrait_rel, must_exist=False)

    entry = {
        "asset_id": asset_id,
        "kind": kind,
        "name": args["name"].strip(),
        "flow_asset_ref": args.get("flow_asset_ref", f"asset:{args['name'].strip()}"),
        "verbatim_prompt_descriptor": descriptor,
        "portrait_source": portrait_source,
        "portrait_rel_path": portrait_rel,
        "voice_id": args.get("voice_id", ""),
        "updated_at": utc_now_iso(),
    }

    assets: list[dict[str, Any]] = registry.setdefault("assets", [])
    replaced = False
    for idx, existing in enumerate(assets):
        if existing.get("asset_id") == asset_id:
            assets[idx] = entry
            replaced = True
            break
    if not replaced:
        assets.append(entry)

    registry["updated_at"] = utc_now_iso()
    write_json(reg_path, registry)

    return {
        "status": "updated" if replaced else "registered",
        "asset": entry,
        "total_registered_assets": len(assets),
        "warnings": warnings,
    }


def estimate_shot_credits(shot: dict[str, Any]) -> int:
    media_type = shot.get("media_type", "video")
    if media_type == "image":
        return 0
    model = shot.get("model", "Omni 1.1 Flash")
    duration = int(shot.get("duration_sec", 8))
    batches_str = str(shot.get("batches", "x1")).lower().lstrip("x")
    multiplier = int(batches_str) if batches_str.isdigit() else 1

    rate = MODEL_CREDIT_RATES.get(model)
    if isinstance(rate, int):
        base = rate
    elif isinstance(rate, dict):
        base = int(rate.get(duration, rate["default"]))
    else:
        base = 20
    return base * multiplier


def tool_validate_shot_manifest(args: dict[str, Any]) -> dict[str, Any]:
    prod_dir = resolve_production_dir(args["production_slug"])
    ep_dir = resolve_episode_dir(prod_dir, args["episode_id"])
    manifest_rel = args.get("manifest_rel_path", f"episodes/{args['episode_id']}/01_script/shot_manifest.json")
    manifest_path = resolve_cartridge_path(prod_dir, manifest_rel, must_exist=True)

    manifest = read_json(manifest_path)
    prod_manifest = read_yaml(prod_dir / "production.yaml")
    visual_dna = prod_manifest.get("visual_dna") or {}
    cast_reg = read_json(prod_dir / "02_cast_and_ingredients" / "cast_registry.json")
    cast_by_id = {a["asset_id"]: a for a in cast_reg.get("assets", [])}

    shots = manifest.get("shots")
    if not isinstance(shots, list) or not shots:
        raise ValueError("shot_manifest.json must contain a non-empty 'shots' array.")

    errors: list[str] = []
    warnings: list[str] = []
    compile_prompts = bool(args.get("compile_prompts", True))
    total_credits = 0

    for i, shot in enumerate(shots, start=1):
        sid = shot.get("shot_id", f"shot_{i:02d}")
        model = shot.get("model", "Omni 1.1 Flash")
        duration = int(shot.get("duration_sec", 8))
        prompt = str(shot.get("motion_prompt", "")).strip()
        batches = str(shot.get("batches", "x1"))
        refs = shot.get("reference_images") or []
        cast_ids = shot.get("cast_ids") or []
        first_frame = shot.get("first_frame_rel_path", "")
        last_frame = shot.get("last_frame_rel_path", "")
        chain_prev = bool(shot.get("chain_previous", False))
        dialogue = str(shot.get("spoken_dialogue", "")).strip()

        if not prompt:
            errors.append(f"[{sid}] Missing 'motion_prompt'.")

        # 1. Batch multiplier protection (prevent accidental double charges like x2)
        if batches != "x1" and not shot.get("allow_multi_batch", False):
            errors.append(
                f"[{sid}] batches='{batches}' multiplies credit spend! Set batches='x1' or set allow_multi_batch=true intentionally."
            )

        # 2. Model & keyframe duration legality
        if model.startswith("Veo 3.1") and duration != 8:
            errors.append(f"[{sid}] Model '{model}' in Google Flow uses fixed 8s duration (got {duration}s).")
        if model == "Omni 1.1 Flash" and duration not in {4, 6, 8, 10}:
            errors.append(f"[{sid}] Model 'Omni 1.1 Flash' supports durations 4, 6, 8, or 10s (got {duration}s).")
        if last_frame and not first_frame:
            errors.append(f"[{sid}] 'last_frame_rel_path' requires 'first_frame_rel_path' to also be specified.")
        if first_frame and last_frame and duration > 10:
            errors.append(f"[{sid}] First+Last frame transition is capped at 10s maximum (got {duration}s).")
        if len(refs) > 6:
            errors.append(f"[{sid}] Exceeds maximum 6 reference images (got {len(refs)}).")

        # 3. Continuity paradox check (exact last frame join + character reference conflict)
        if chain_prev and refs and shot.get("continuity_mode", "exact") == "exact":
            warnings.append(
                f"[{sid}] Flow's exact last-frame continuation ignores attached character chips (likeness comes from the frame itself). "
                "Clear reference_images on exact joins or switch continuity_mode='likeness'."
            )

        # 4. Cast memory injection check
        missing_descriptors: list[str] = []
        for cid in cast_ids:
            if cid not in cast_by_id:
                errors.append(f"[{sid}] Unknown cast_id '{cid}' (not found in cast_registry.json).")
                continue
            desc = cast_by_id[cid]["verbatim_prompt_descriptor"]
            if desc.lower() not in prompt.lower():
                missing_descriptors.append(f"{cast_by_id[cid]['name']}: {desc}")

        if missing_descriptors and not compile_prompts:
            errors.append(
                f"[{sid}] Flow has no cross-scene memory; missing verbatim cast descriptors for: "
                + "; ".join(missing_descriptors)
            )

        # 5. Negative object hallucination check
        for pat in NEGATIVE_OBJECT_PATTERNS:
            m = pat.search(prompt)
            if m:
                warnings.append(
                    f"[{sid}] Negative object phrase detected ({m.group(0)!r}). Naming unwanted objects often spawns them in Flow; describe positive camera framing instead."
                )

        # 6. Physics hazard check
        for pat, msg in PHYSICS_HAZARD_PATTERNS:
            if pat.search(prompt):
                warnings.append(f"[{sid}] Physics hazard: {msg}")

        # 7. Spoken dialogue timestamp check
        if dialogue and not TIMECODE_RE.search(prompt):
            errors.append(
                f"[{sid}] Shot includes spoken_dialogue ({dialogue!r}) without second-by-second timestamps (e.g. '0.0-1.8s:') inside motion_prompt."
            )

        # Compile final self-contained English Flow prompt
        if compile_prompts and prompt:
            parts = []
            if missing_descriptors:
                parts.append("Subject & Cast Invariants: " + " | ".join(missing_descriptors) + ".")
            parts.append(prompt)
            style_sig = visual_dna.get("aesthetic_style", "")
            light_sig = visual_dna.get("lighting_signature", "")
            if style_sig and "Pending" not in style_sig and style_sig.lower() not in prompt.lower():
                parts.append(f"Visual Style: {style_sig}. Lighting: {light_sig}.")
            shot["compiled_flow_prompt"] = " ".join(parts)

        shot["estimated_credits"] = estimate_shot_credits(shot)
        total_credits += shot["estimated_credits"]

    if compile_prompts and not errors:
        manifest["validated_at"] = utc_now_iso()
        manifest["estimated_total_credits"] = total_credits
        write_json(manifest_path, manifest)

    return {
        "valid": len(errors) == 0,
        "shot_count": len(shots),
        "estimated_total_credits": total_credits,
        "errors": errors,
        "warnings": warnings,
        "manifest_path": str(manifest_path),
    }


def tool_quote_and_log_credits(args: dict[str, Any]) -> dict[str, Any]:
    prod_dir = resolve_production_dir(args["production_slug"])
    prod_manifest = read_yaml(prod_dir / "production.yaml")
    ledger_path = prod_dir / "credit_ledger.json"
    ledger = read_json(ledger_path)

    budget = prod_manifest.get("credit_budget") or {}
    monthly_cap = int(budget.get("monthly_cap", 25000))
    ep_cap = int(budget.get("per_episode_cap", 600))
    spent_total = int(ledger.get("total_credits_spent", 0))

    action = args["action"]
    episode_id = args.get("episode_id", "")

    spent_episode = sum(
        int(e.get("credits", 0)) for e in ledger.get("entries", []) if episode_id and e.get("episode_id") == episode_id
    )

    if action == "quote":
        if not episode_id:
            raise ValueError("episode_id is required for action='quote'.")
        ep_dir = resolve_episode_dir(prod_dir, episode_id)
        manifest_path = ep_dir / "01_script" / "shot_manifest.json"
        manifest = read_json(manifest_path)
        breakdown = []
        quote_total = 0
        for s in manifest.get("shots", []):
            c = estimate_shot_credits(s)
            quote_total += c
            breakdown.append(
                {
                    "shot_id": s.get("shot_id"),
                    "model": s.get("model", "Omni 1.1 Flash"),
                    "duration_sec": s.get("duration_sec", 8),
                    "batches": s.get("batches", "x1"),
                    "credits": c,
                }
            )
        within_episode_cap = (spent_episode + quote_total) <= ep_cap
        within_monthly_cap = (spent_total + quote_total) <= monthly_cap
        return {
            "action": "quote",
            "production_slug": args["production_slug"],
            "episode_id": episode_id,
            "quoted_credits": quote_total,
            "episode_spent_so_far": spent_episode,
            "episode_cap": ep_cap,
            "monthly_spent_so_far": spent_total,
            "monthly_cap": monthly_cap,
            "within_budget": within_episode_cap and within_monthly_cap,
            "requires_human_confirmation": quote_total > 0,
            "shot_breakdown": breakdown,
        }

    if action == "record":
        credits = int(args["credits"])
        entry = {
            "timestamp": utc_now_iso(),
            "episode_id": episode_id,
            "operation": args.get("operation", "flow_generate"),
            "model": args.get("model", ""),
            "credits": credits,
            "notes": args.get("notes", ""),
        }
        ledger.setdefault("entries", []).append(entry)
        ledger["total_credits_spent"] = spent_total + credits
        write_json(ledger_path, ledger)
        return {
            "action": "record",
            "recorded_entry": entry,
            "total_credits_spent": ledger["total_credits_spent"],
            "remaining_monthly_budget": monthly_cap - ledger["total_credits_spent"],
        }

    raise ValueError("action must be 'quote' or 'record'.")


def probe_video_info(file_path: Path) -> dict[str, Any]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(file_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    raw = json.loads(res.stdout)
    streams = raw.get("streams", [])
    v_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = raw.get("format", {})

    fps_raw = v_stream.get("r_frame_rate", "24/1")
    num, _, den = fps_raw.partition("/")
    fps = round(float(num) / float(den or 1), 3) if float(den or 1) != 0 else 24.0

    return {
        "path": str(file_path),
        "duration_sec": round(float(fmt.get("duration", v_stream.get("duration", 0.0))), 3),
        "width": int(v_stream.get("width", 0)),
        "height": int(v_stream.get("height", 0)),
        "video_codec": v_stream.get("codec_name", ""),
        "fps": fps,
        "has_audio": a_stream is not None,
        "audio_codec": a_stream.get("codec_name", "") if a_stream else None,
        "sample_rate": int(a_stream.get("sample_rate", 0)) if a_stream and a_stream.get("sample_rate") else None,
        "size_bytes": int(fmt.get("size", 0)),
    }


def tool_probe_media(args: dict[str, Any]) -> dict[str, Any]:
    prod_dir = resolve_production_dir(args["production_slug"])
    media_path = resolve_cartridge_path(prod_dir, args["media_rel_path"], must_exist=True)
    return probe_video_info(media_path)


def tool_extract_frame(args: dict[str, Any]) -> dict[str, Any]:
    prod_dir = resolve_production_dir(args["production_slug"])
    video_path = resolve_cartridge_path(prod_dir, args["video_rel_path"], must_exist=True)
    out_path = resolve_cartridge_path(prod_dir, args["output_rel_path"], must_exist=False)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    position = args.get("position", "last")
    info = probe_video_info(video_path)
    duration = info["duration_sec"]

    if position == "first":
        cmd = ["ffmpeg", "-y", "-i", str(video_path), "-frames:v", "1", "-q:v", "1", str(out_path)]
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    elif position == "last":
        # -sseof -0.5 + -update 1 decodes the tail packets and keeps the exact final video frame,
        # avoiding container-vs-audio duration drift at EOF.
        subprocess.run(
            ["ffmpeg", "-y", "-sseof", "-0.5", "-i", str(video_path), "-vsync", "0", "-q:v", "1", "-update", "1", str(out_path)],
            capture_output=True,
            text=True,
            check=True,
        )
        if not out_path.is_file():
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(video_path), "-vsync", "0", "-q:v", "1", "-update", "1", str(out_path)],
                capture_output=True,
                text=True,
                check=True,
            )
    elif position == "timestamp":
        ts = float(args.get("timestamp_sec", 0.0))
        ts = min(max(0.0, ts), max(0.0, duration - 0.05))
        cmd = ["ffmpeg", "-y", "-ss", f"{ts:.4f}", "-i", str(video_path), "-frames:v", "1", "-q:v", "1", str(out_path)]
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    else:
        raise ValueError("position must be 'first', 'last', or 'timestamp'.")

    out_info = probe_video_info(out_path)
    return {
        "status": "extracted",
        "position": position,
        "source_video": args["video_rel_path"],
        "output_png": args["output_rel_path"],
        "width": out_info["width"],
        "height": out_info["height"],
    }


def tool_render_contact_sheet(args: dict[str, Any]) -> dict[str, Any]:
    prod_dir = resolve_production_dir(args["production_slug"])
    image_rels: list[str] = args["image_rel_paths"]
    if not image_rels:
        raise ValueError("image_rel_paths must contain at least one image.")
    image_paths = [resolve_cartridge_path(prod_dir, r, must_exist=True) for r in image_rels]
    out_path = resolve_cartridge_path(prod_dir, args["output_rel_path"], must_exist=False)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cols = int(args.get("columns", min(3, len(image_paths))))
    cols = max(1, cols)
    rows = math.ceil(len(image_paths) / cols)
    cell_w = int(args.get("cell_width", 640))
    cell_h = int(args.get("cell_height", 360))

    total_slots = cols * rows
    padded_paths = list(image_paths)
    temp_blacks: list[Path] = []

    try:
        while len(padded_paths) < total_slots:
            tmp = Path(tempfile.mktemp(suffix=".png"))
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c=#0F172A:s={cell_w}x{cell_h}:d=1",
                    "-frames:v",
                    "1",
                    str(tmp),
                ],
                capture_output=True,
                check=True,
            )
            temp_blacks.append(tmp)
            padded_paths.append(tmp)

        cmd = ["ffmpeg", "-y"]
        filter_parts = []
        for idx, p in enumerate(padded_paths):
            cmd.extend(["-i", str(p)])
            filter_parts.append(
                f"[{idx}:v]scale={cell_w}:{cell_h}:force_original_aspect_ratio=decrease,"
                f"pad={cell_w}:{cell_h}:(ow-iw)/2:(oh-ih)/2:color=#0F172A,setsar=1[v{idx}]"
            )

        inputs_concat = "".join(f"[v{i}]" for i in range(total_slots))
        layout_coords = []
        for r in range(rows):
            for c in range(cols):
                x_expr = "0" if c == 0 else "+".join(f"w{k}" for k in range(c))
                y_expr = "0" if r == 0 else "+".join(f"h{k * cols}" for k in range(r))
                layout_coords.append(f"{x_expr}_{y_expr}")
        filter_parts.append(
            f"{inputs_concat}xstack=inputs={total_slots}:layout={'|'.join(layout_coords)}:fill=#0F172A[outv]"
        )

        cmd.extend(["-filter_complex", ";".join(filter_parts), "-map", "[outv]", "-frames:v", "1", str(out_path)])
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    finally:
        for t in temp_blacks:
            t.unlink(missing_ok=True)

    return {
        "status": "rendered",
        "grid": f"{cols}x{rows}",
        "image_count": len(image_paths),
        "output_rel_path": args["output_rel_path"],
        "absolute_path": str(out_path),
    }


def tool_master_episode(args: dict[str, Any]) -> dict[str, Any]:
    prod_dir = resolve_production_dir(args["production_slug"])
    ep_dir = resolve_episode_dir(prod_dir, args["episode_id"])
    clip_rels: list[str] = args["clip_rel_paths"]
    if not clip_rels:
        raise ValueError("clip_rel_paths must contain at least one video clip.")

    clip_paths = [resolve_cartridge_path(prod_dir, r, must_exist=True) for r in clip_rels]
    prod_manifest = read_yaml(prod_dir / "production.yaml")
    aspect = (prod_manifest.get("visual_dna") or {}).get("aspect_ratio", "16:9")

    default_w, default_h = (1080, 1920) if aspect == "9:16" else (1920, 1080)
    target_w = int(args.get("width", default_w))
    target_h = int(args.get("height", default_h))
    fps = int(args.get("fps", 24))

    out_rel = args.get("output_rel_path", f"episodes/{args['episode_id']}/05_master/{args['episode_id']}_master.mp4")
    out_path = resolve_cartridge_path(prod_dir, out_rel, must_exist=False)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    narration_rel = args.get("narration_rel_path", "")
    music_rel = args.get("music_rel_path", "")
    narration_path = resolve_cartridge_path(prod_dir, narration_rel, must_exist=True) if narration_rel else None
    music_path = resolve_cartridge_path(prod_dir, music_rel, must_exist=True) if music_rel else None

    clip_gain = float(args.get("clip_audio_volume", 0.75 if narration_path else 1.0))
    voice_gain = float(args.get("narration_volume", 1.0))
    music_gain = float(args.get("music_volume", 0.22 if narration_path else 0.45))

    with tempfile.TemporaryDirectory(prefix="flowforge_master_") as tmpdir:
        tmp = Path(tmpdir)
        norm_clips: list[Path] = []

        for idx, cp in enumerate(clip_paths):
            info = probe_video_info(cp)
            norm_p = tmp / f"norm_{idx:03d}.mp4"
            vf = (
                f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
                f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={fps},format=yuv420p"
            )
            if info["has_audio"]:
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(cp),
                    "-vf",
                    vf,
                    "-af",
                    f"aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,volume={clip_gain}",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "fast",
                    "-crf",
                    "18",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    str(norm_p),
                ]
            else:
                dur = max(0.1, info["duration_sec"])
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(cp),
                    "-f",
                    "lavfi",
                    "-t",
                    f"{dur:.3f}",
                    "-i",
                    "anullsrc=channel_layout=stereo:sample_rate=48000",
                    "-vf",
                    vf,
                    "-c:v",
                    "libx264",
                    "-preset",
                    "fast",
                    "-crf",
                    "18",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-shortest",
                    str(norm_p),
                ]
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            norm_clips.append(norm_p)

        concat_list = tmp / "concat.txt"
        concat_list.write_text("".join(f"file '{p}'\n" for p in norm_clips), encoding="utf-8")
        stitched = tmp / "stitched.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(stitched)],
            capture_output=True,
            text=True,
            check=True,
        )

        stitched_info = probe_video_info(stitched)
        total_dur = stitched_info["duration_sec"]

        # Mix audio tracks & normalize loudness to EBU R128 (-14 LUFS)
        mix_cmd = ["ffmpeg", "-y", "-i", str(stitched)]
        audio_nodes = ["[0:a]aresample=48000[a_clips]"]
        mix_inputs = ["[a_clips]"]
        input_idx = 1

        if narration_path:
            mix_cmd.extend(["-i", str(narration_path)])
            audio_nodes.append(
                f"[{input_idx}:a]aresample=48000,aformat=channel_layouts=stereo,volume={voice_gain},"
                f"apad=whole_dur={total_dur:.3f},atrim=0:{total_dur:.3f}[a_voice]"
            )
            mix_inputs.append("[a_voice]")
            input_idx += 1

        if music_path:
            mix_cmd.extend(["-stream_loop", "-1", "-i", str(music_path)])
            fade_out_start = max(0.0, total_dur - 2.0)
            audio_nodes.append(
                f"[{input_idx}:a]aresample=48000,aformat=channel_layouts=stereo,volume={music_gain},"
                f"atrim=0:{total_dur:.3f},afade=t=out:st={fade_out_start:.3f}:d=2.0[a_music]"
            )
            mix_inputs.append("[a_music]")
            input_idx += 1

        amix_count = len(mix_inputs)
        if amix_count > 1:
            audio_nodes.append(
                f"{''.join(mix_inputs)}amix=inputs={amix_count}:duration=first:dropout_transition=0:normalize=0,"
                "loudnorm=I=-14:TP=-1.5:LRA=11[a_master]"
            )
        else:
            audio_nodes.append("[a_clips]loudnorm=I=-14:TP=-1.5:LRA=11[a_master]")

        mix_cmd.extend(
            [
                "-filter_complex",
                ";".join(audio_nodes),
                "-map",
                "0:v",
                "-map",
                "[a_master]",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "256k",
                "-movflags",
                "+faststart",
                str(out_path),
            ]
        )
        subprocess.run(mix_cmd, capture_output=True, text=True, check=True)

    # Extract final continuity frame for Episode N+1 seamless chaining
    anchor_rel = f"episodes/{args['episode_id']}/05_master/ep_final_continuity_frame.png"
    tool_extract_frame(
        {
            "production_slug": args["production_slug"],
            "video_rel_path": out_rel,
            "output_rel_path": anchor_rel,
            "position": "last",
        }
    )

    final_info = probe_video_info(out_path)
    report = {
        "episode_id": args["episode_id"],
        "mastered_at": utc_now_iso(),
        "clip_count": len(clip_paths),
        "master_rel_path": out_rel,
        "continuity_anchor_rel_path": anchor_rel,
        "duration_sec": final_info["duration_sec"],
        "resolution": f"{final_info['width']}x{final_info['height']}",
        "fps": final_info["fps"],
        "loudness_target": "-14 LUFS (EBU R128)",
    }
    write_json(ep_dir / "05_master" / "master_report.json", report)
    return report


# ---------------------------------------------------------------------------
# MCP Tool Registry & JSON-RPC 2.0 Stdio Dispatcher
# ---------------------------------------------------------------------------

TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "name": "flowforge_init_production",
        "description": "Initialize an isolated production cartridge (series, short-film, or music-video) with production.yaml, credit_ledger.json, and cast_registry.json.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "production_slug": {"type": "string", "description": "Lowercase slug (e.g. 'relojero-neon')"},
                "title": {"type": "string", "description": "Human-readable production title"},
                "format_type": {"type": "string", "enum": ["series", "short-film", "music-video"], "default": "series"},
                "aspect_ratio": {"type": "string", "enum": ["16:9", "9:16"], "default": "16:9"},
                "aesthetic_style": {"type": "string"},
                "monthly_credit_budget": {"type": "integer", "default": 25000},
                "per_episode_credit_budget": {"type": "integer", "default": 600},
                "overwrite": {"type": "boolean", "default": False},
            },
            "required": ["production_slug", "title"],
        },
    },
    {
        "name": "flowforge_init_episode",
        "description": "Initialize an episode directory (episodes/ep_NN_slug/) inside a production and auto-link continuity from the previous episode's final frame.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "production_slug": {"type": "string"},
                "episode_number": {"type": "integer", "minimum": 1},
                "episode_slug": {"type": "string", "description": "Short slug (e.g. 'piloto')"},
                "title": {"type": "string"},
                "logline": {"type": "string"},
                "target_duration_sec": {"type": "integer", "default": 60},
                "overwrite": {"type": "boolean", "default": False},
            },
            "required": ["production_slug", "episode_number", "episode_slug", "title"],
        },
    },
    {
        "name": "flowforge_check_gates",
        "description": "Verify whether a production-level gate (universe_bible, cast_locked, sound_identity) or episode-level gate (outline, script, storyboard, render, postprod, master) has all prerequisites approved.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "production_slug": {"type": "string"},
                "target_gate": {"type": "string"},
                "episode_id": {"type": "string", "description": "Required for episode gates (e.g. 'ep_01_piloto')"},
            },
            "required": ["production_slug", "target_gate"],
        },
    },
    {
        "name": "flowforge_register_cast_asset",
        "description": "Register or update an immutable character, prop, or location in cast_registry.json with its verbatim English visual descriptor for cross-scene/cross-episode Flow consistency.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "production_slug": {"type": "string"},
                "asset_id": {"type": "string"},
                "kind": {"type": "string", "enum": ["character", "prop", "location"]},
                "name": {"type": "string"},
                "verbatim_prompt_descriptor": {"type": "string", "description": "Detailed English visual traits (>=8 words) injected verbatim into every Flow shot"},
                "flow_asset_ref": {"type": "string"},
                "portrait_source": {"type": "string", "enum": ["flow_character_describe", "stylized_sheet", "real_photo"]},
                "portrait_rel_path": {"type": "string"},
                "voice_id": {"type": "string"},
            },
            "required": ["production_slug", "asset_id", "kind", "name", "verbatim_prompt_descriptor"],
        },
    },
    {
        "name": "flowforge_validate_shot_manifest",
        "description": "Pre-flight linter for shot_manifest.json: checks Google Flow model durations, x1 batch protection, verbatim cast memory injection, negative-prompt traps, physics hazards, and dialogue timestamps.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "production_slug": {"type": "string"},
                "episode_id": {"type": "string"},
                "manifest_rel_path": {"type": "string"},
                "compile_prompts": {"type": "boolean", "default": True},
            },
            "required": ["production_slug", "episode_id"],
        },
    },
    {
        "name": "flowforge_quote_and_log_credits",
        "description": "Quote exact Google AI Ultra Flow credits before rendering an episode (action='quote') or record spent credits into credit_ledger.json (action='record').",
        "inputSchema": {
            "type": "object",
            "properties": {
                "production_slug": {"type": "string"},
                "action": {"type": "string", "enum": ["quote", "record"]},
                "episode_id": {"type": "string"},
                "credits": {"type": "integer"},
                "operation": {"type": "string"},
                "model": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["production_slug", "action"],
        },
    },
    {
        "name": "flowforge_extract_frame",
        "description": "Extract exact first, last, or timestamped PNG frame from a video inside the production sandbox for seamless Google Flow shot/episode continuity.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "production_slug": {"type": "string"},
                "video_rel_path": {"type": "string"},
                "output_rel_path": {"type": "string"},
                "position": {"type": "string", "enum": ["first", "last", "timestamp"], "default": "last"},
                "timestamp_sec": {"type": "number"},
            },
            "required": ["production_slug", "video_rel_path", "output_rel_path"],
        },
    },
    {
        "name": "flowforge_probe_media",
        "description": "Inspect resolution, duration, FPS, codecs, and audio stream presence of any media file inside the active production.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "production_slug": {"type": "string"},
                "media_rel_path": {"type": "string"},
            },
            "required": ["production_slug", "media_rel_path"],
        },
    },
    {
        "name": "flowforge_render_contact_sheet",
        "description": "Compose storyboard keyframes or character turnaround images into a clean visual grid PNG for human director review.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "production_slug": {"type": "string"},
                "image_rel_paths": {"type": "array", "items": {"type": "string"}},
                "output_rel_path": {"type": "string"},
                "columns": {"type": "integer", "default": 3},
                "cell_width": {"type": "integer", "default": 640},
                "cell_height": {"type": "integer", "default": 360},
            },
            "required": ["production_slug", "image_rel_paths", "output_rel_path"],
        },
    },
    {
        "name": "flowforge_master_episode",
        "description": "Assemble an episode's approved clips in order, mix voiceover and music with ducking, normalize loudness to -14 LUFS (EBU R128), and export final master MP4 + continuity frame for the next episode.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "production_slug": {"type": "string"},
                "episode_id": {"type": "string"},
                "clip_rel_paths": {"type": "array", "items": {"type": "string"}},
                "narration_rel_path": {"type": "string"},
                "music_rel_path": {"type": "string"},
                "output_rel_path": {"type": "string"},
                "width": {"type": "integer"},
                "height": {"type": "integer"},
                "fps": {"type": "integer", "default": 24},
                "clip_audio_volume": {"type": "number"},
                "narration_volume": {"type": "number"},
                "music_volume": {"type": "number"},
            },
            "required": ["production_slug", "episode_id", "clip_rel_paths"],
        },
    },
]

TOOL_HANDLERS = {
    "flowforge_init_production": tool_init_production,
    "flowforge_init_episode": tool_init_episode,
    "flowforge_check_gates": tool_check_gates,
    "flowforge_register_cast_asset": tool_register_cast_asset,
    "flowforge_validate_shot_manifest": tool_validate_shot_manifest,
    "flowforge_quote_and_log_credits": tool_quote_and_log_credits,
    "flowforge_extract_frame": tool_extract_frame,
    "flowforge_probe_media": tool_probe_media,
    "flowforge_render_contact_sheet": tool_render_contact_sheet,
    "flowforge_master_episode": tool_master_episode,
}


def handle_rpc_request(msg: dict[str, Any]) -> dict[str, Any] | None:
    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    if req_id is None:
        return None

    if method == "initialize":
        client_proto = params.get("protocolVersion", DEFAULT_PROTOCOL_VERSION)
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": client_proto,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS_SCHEMA}}

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments") or {}
        handler = TOOL_HANDLERS.get(tool_name)
        if not handler:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
            }
        try:
            out = handler(tool_args)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(out, indent=2, ensure_ascii=False)}],
                    "isError": False,
                },
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"error": type(exc).__name__, "message": str(exc)},
                                indent=2,
                                ensure_ascii=False,
                            ),
                        }
                    ],
                    "isError": True,
                },
            }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main() -> None:
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer

    while True:
        first_line = stdin.readline()
        if not first_line:
            break
        stripped = first_line.strip()
        if not stripped:
            continue

        if stripped.lower().startswith(b"content-length:"):
            length = int(stripped.split(b":", 1)[1].strip())
            while True:
                hdr = stdin.readline()
                if not hdr or hdr in (b"\r\n", b"\n"):
                    break
            raw_body = stdin.read(length)
            msg = json.loads(raw_body.decode("utf-8"))
            resp = handle_rpc_request(msg)
            if resp is not None:
                encoded = json.dumps(resp, ensure_ascii=False).encode("utf-8")
                stdout.write(f"Content-Length: {len(encoded)}\r\n\r\n".encode("ascii") + encoded)
                stdout.flush()
        else:
            msg = json.loads(stripped.decode("utf-8"))
            resp = handle_rpc_request(msg)
            if resp is not None:
                stdout.write((json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8"))
                stdout.flush()


if __name__ == "__main__":
    main()
