#!/usr/bin/env python3
"""FlowForge Studio — Deterministic MCP Server for Audiovisual Governance & Media Mastering.

Implements MCP Stdio JSON-RPC 2.0 (protocol 2025-03-26) with strict cartridge sandboxing,
production & episodic HITL gate enforcement, Google Flow prompt/physics linting,
Google AI Ultra credit budgeting/ledgering, frame-accurate continuity extraction,
storyboard contact sheet rendering, and broadcast-normalized (-14 LUFS) episode mastering.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

# Resolve workspace root: <root>/.agents/plugins/flowforge-studio/scripts/flowforge_mcp_server.py
SCRIPT_PATH = Path(__file__).resolve()
if str(SCRIPT_PATH.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPT_PATH.parent))

import memory_engine

SERVER_NAME = "flowforge-engine"
SERVER_VERSION = "1.3.0"
DEFAULT_PROTOCOL_VERSION = "2025-03-26"

WORKSPACE_ROOT = Path(os.environ.get("FLOWFORGE_ROOT", str(SCRIPT_PATH.parents[4]))).resolve()
PRODUCTIONS_ROOT = (WORKSPACE_ROOT / "productions").resolve()
TEMPLATES_ROOT = (WORKSPACE_ROOT / "templates").resolve()

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}$")
EPISODE_ID_RE = re.compile(r"^ep_\d{2}_[a-z0-9][a-z0-9_-]{1,50}$")

VALID_FORMATS = {"series", "short-film", "music-video"}
VALID_GATE_STATES = {"pending", "draft", "in_review", "approved", "blocked"}
PRODUCTION_GATES = ("universe_bible", "cast_locked", "sound_identity")
EPISODE_GATES = ("outline", "script", "storyboard", "render", "postprod", "master")

# Fields written by the validator itself; excluded from the manifest fingerprint so that
# validating a manifest does not change its own identity.
DERIVED_MANIFEST_KEYS = {"validated_at", "validated_sha256", "estimated_total_credits"}
DERIVED_SHOT_KEYS = {"compiled_flow_prompt", "estimated_credits", "resolved_reference_images"}

# Structured asset sheets: required + optional visual facets per kind (values in precise English).
ASSET_KIND_FACETS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "character": (
        ("apparent_age", "build", "face", "skin", "hair", "eyes", "wardrobe", "palette"),
        ("distinguishing_marks", "accessories", "posture"),
    ),
    "creature": (
        ("scale", "silhouette", "anatomy", "surface", "palette", "eyes", "locomotion"),
        ("distinguishing_marks", "movement_quirks"),
    ),
    "prop": (
        ("scale", "shape", "materials", "palette", "surface_details", "orientation_rule"),
        ("markings",),
    ),
    "location": (
        ("setting", "layout", "landmarks", "materials", "palette", "lighting", "atmosphere"),
        ("time_of_day", "weather"),
    ),
}
CAST_SLOT_KINDS = {"character", "creature"}
MIN_FACET_WORDS = 2
MIN_LOCK_WORDS = 3
MAX_DESCRIPTOR_WORDS = 90
MAX_REFERENCE_IMAGES = 6
# Heuristic (not a documented Flow limit): beyond this, video models tend to dilute attention and blend traits.
COMPILED_PROMPT_WARN_WORDS = 300

KIND_LABELS_ES = {"character": "Personaje", "creature": "Criatura", "prop": "Objeto", "location": "Escenario"}
FACET_LABELS_ES = {
    "apparent_age": "Edad aparente",
    "build": "Complexión",
    "face": "Rostro",
    "skin": "Piel",
    "hair": "Cabello",
    "eyes": "Ojos",
    "wardrobe": "Vestuario",
    "palette": "Paleta de color",
    "distinguishing_marks": "Marcas distintivas",
    "accessories": "Accesorios",
    "posture": "Postura",
    "scale": "Escala",
    "silhouette": "Silueta",
    "anatomy": "Anatomía",
    "surface": "Superficie (piel, escamas, pelaje)",
    "locomotion": "Locomoción",
    "movement_quirks": "Rasgos de movimiento",
    "shape": "Forma",
    "materials": "Materiales",
    "surface_details": "Detalles de superficie",
    "orientation_rule": "Regla de orientación",
    "markings": "Marcas",
    "setting": "Tipo de lugar",
    "layout": "Distribución espacial",
    "landmarks": "Elementos clave",
    "lighting": "Iluminación",
    "atmosphere": "Atmósfera",
    "time_of_day": "Momento del día",
    "weather": "Clima",
}

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


def manifest_fingerprint(manifest: dict[str, Any]) -> str:
    """SHA-256 of the author-controlled manifest content (validator-derived fields excluded)."""
    core = {k: v for k, v in manifest.items() if k not in DERIVED_MANIFEST_KEYS}
    core["shots"] = [
        {k: v for k, v in shot.items() if k not in DERIVED_SHOT_KEYS} if isinstance(shot, dict) else shot
        for shot in (manifest.get("shots") or [])
    ]
    canonical = json.dumps(core, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# HITL Gate Engine (prerequisites + audited transitions)
# ---------------------------------------------------------------------------


def gate_prerequisites(fmt: str) -> tuple[dict[str, list[str]], dict[str, dict[str, list[str]]]]:
    music_video = fmt == "music-video"
    if music_video:
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
    mv_sound = ["sound_identity"] if music_video else []
    ep_prereqs = {
        "outline": {"prod": ["universe_bible"], "ep": []},
        "script": {"prod": ["universe_bible", "cast_locked"] + mv_sound, "ep": ["outline"]},
        "storyboard": {"prod": ["universe_bible", "cast_locked"], "ep": ["script"]},
        "render": {"prod": ["universe_bible", "cast_locked"] + mv_sound, "ep": ["script", "storyboard"]},
        "postprod": {"prod": ["sound_identity"], "ep": ["render"]},
        "master": {"prod": ["sound_identity"], "ep": ["render", "postprod"]},
    }
    return prod_prereqs, ep_prereqs


def approval_is_audited(manifest: dict[str, Any], gate: str) -> bool:
    """An approval counts only if the latest recorded transition for that gate is to 'approved'."""
    for entry in reversed(manifest.get("gate_history") or []):
        if entry.get("gate") == gate:
            return entry.get("to") == "approved"
    return False


def evaluate_gate(prod_dir: Path, target_gate: str, episode_id: str | None = None) -> dict[str, Any]:
    prod_manifest = read_yaml(prod_dir / "production.yaml")
    fmt = prod_manifest.get("format_type", "series")
    prod_gates = prod_manifest.get("production_gates") or {}
    prod_prereqs, ep_prereqs = gate_prerequisites(fmt)
    unaudited: list[str] = []

    def is_approved(manifest: dict[str, Any], gates: dict[str, Any], gate: str) -> bool:
        if gates.get(gate) != "approved":
            return False
        if approval_is_audited(manifest, gate):
            return True
        unaudited.append(gate)
        return False

    if target_gate in prod_prereqs:
        missing = [g for g in prod_prereqs[target_gate] if not is_approved(prod_manifest, prod_gates, g)]
        return {
            "scope": "production",
            "production_slug": prod_manifest.get("production_slug"),
            "format_type": fmt,
            "target_gate": target_gate,
            "current_status": prod_gates.get(target_gate, "pending"),
            "unlocked": len(missing) == 0,
            "missing_approvals": missing,
            "unaudited_approvals": unaudited,
            "production_gates": prod_gates,
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

    missing_prod = [
        f"production.{g}" for g in ep_prereqs[target_gate]["prod"] if not is_approved(prod_manifest, prod_gates, g)
    ]
    missing_ep = [f"episode.{g}" for g in ep_prereqs[target_gate]["ep"] if not is_approved(ep_manifest, ep_gates, g)]
    all_missing = missing_prod + missing_ep

    return {
        "scope": "episode",
        "production_slug": prod_manifest.get("production_slug"),
        "episode_id": episode_id,
        "target_gate": target_gate,
        "current_status": ep_gates.get(target_gate, "pending"),
        "unlocked": len(all_missing) == 0,
        "missing_approvals": all_missing,
        "unaudited_approvals": unaudited,
        "production_gates": prod_gates,
        "episode_gates": ep_gates,
    }


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
        "00_references",
        "01_universe_bible",
        "02_cast_and_ingredients/portraits",
        "02_cast_and_ingredients/locations",
        "02_cast_and_ingredients/props",
        "03_sound_and_music/tracks",
        "episodes",
        "memory",
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
    memory_engine.index_production_files(prod_dir, force_rebuild=False)

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
    return evaluate_gate(prod_dir, args["target_gate"], args.get("episode_id"))


def tool_set_gate(args: dict[str, Any]) -> dict[str, Any]:
    """Audited gate transition. Approval requires prior review, met prerequisites and a human quote."""
    prod_dir = resolve_production_dir(args["production_slug"])
    gate = args["gate"]
    state = args["state"]
    if state not in VALID_GATE_STATES:
        raise ValueError(f"Invalid state '{state}'. Must be one of: {sorted(VALID_GATE_STATES)}")

    episode_id: str | None = None
    if gate in PRODUCTION_GATES:
        manifest_path = prod_dir / "production.yaml"
        gates_key = "production_gates"
    elif gate in EPISODE_GATES:
        episode_id = args.get("episode_id")
        if not episode_id:
            raise ValueError(f"episode_id is required for episodic gate '{gate}'.")
        manifest_path = resolve_episode_dir(prod_dir, episode_id) / "episode.yaml"
        gates_key = "episode_gates"
    else:
        raise ValueError(f"Unknown gate '{gate}'. Valid gates: {sorted(PRODUCTION_GATES + EPISODE_GATES)}")

    manifest = read_yaml(manifest_path)
    gates = manifest.setdefault(gates_key, {})
    current = gates.get(gate, "pending")
    if state == current:
        return {"status": "unchanged", "gate": gate, "state": state, "episode_id": episode_id}

    if state in {"in_review", "approved"}:
        evaluation = evaluate_gate(prod_dir, gate, episode_id)
        if not evaluation["unlocked"]:
            raise PermissionError(
                f"Gate '{gate}' cannot move to '{state}': missing approvals {evaluation['missing_approvals']}"
                + (f" (unaudited: {evaluation['unaudited_approvals']})" if evaluation["unaudited_approvals"] else "")
                + "."
            )

    confirmation = str(args.get("human_confirmation") or "").strip()
    if state == "approved":
        if current != "in_review":
            raise PermissionError(f"Gate '{gate}' must be 'in_review' before approval (current: '{current}').")
        if len(confirmation) < 2:
            raise PermissionError(
                "human_confirmation is required to approve a gate: quote the director's explicit approval verbatim."
            )

    if gate == "script" and state in {"in_review", "approved"}:
        require_render_ready_manifest(prod_dir, episode_id)

    entry: dict[str, Any] = {"gate": gate, "from": current, "to": state, "at": utc_now_iso()}
    if confirmation:
        entry["human_confirmation"] = confirmation
    notes = str(args.get("notes") or "").strip()
    if notes:
        entry["notes"] = notes

    gates[gate] = state
    manifest.setdefault("gate_history", []).append(entry)
    write_yaml(manifest_path, manifest)
    return {"status": "updated", "gate": gate, "from": current, "to": state, "episode_id": episode_id}


def validate_visual_spec(kind: str, spec: dict[str, Any]) -> None:
    required, optional = ASSET_KIND_FACETS[kind]
    problems: list[str] = []
    unknown = sorted(set(spec) - set(required) - set(optional))
    if unknown:
        problems.append(
            f"Unknown visual_spec facet(s) {unknown} for kind '{kind}'. Allowed: {list(required) + list(optional)}."
        )
    missing = [k for k in required if not str(spec.get(k) or "").strip()]
    if missing:
        problems.append(f"Missing required visual_spec facet(s) for {kind}: {missing}.")
    vague = [k for k, v in spec.items() if k not in unknown and str(v or "").strip() and len(str(v).split()) < MIN_FACET_WORDS]
    if vague:
        problems.append(f"visual_spec facet(s) {vague} are too vague: use at least {MIN_FACET_WORDS} descriptive English words each.")
    if problems:
        raise ValueError(" ".join(problems))


def validate_continuity_locks(locks: Any) -> list[str]:
    if not isinstance(locks, list) or not locks:
        raise ValueError("continuity_locks must list at least one trait that never changes (in English).")
    cleaned = [str(lock).strip() for lock in locks]
    short = [lock for lock in cleaned if len(lock.split()) < MIN_LOCK_WORDS]
    if short:
        raise ValueError(f"continuity_locks entries {short} are too vague: use at least {MIN_LOCK_WORDS} words each.")
    return cleaned


def compose_descriptor(name: str, kind: str, spec: dict[str, Any], locks: list[str]) -> str:
    """Deterministic compact prompt core: every facet in canonical order + immutable locks."""
    required, optional = ASSET_KIND_FACETS[kind]
    facets = [f"{key.replace('_', ' ')}: {str(spec[key]).strip()}" for key in required + optional if spec.get(key)]
    text = f"{name} — " + "; ".join(facets) + "."
    if locks:
        text += " Always: " + "; ".join(locks) + "."
    return text


def write_asset_sheet(prod_dir: Path, entry: dict[str, Any]) -> str:
    """Human-readable Spanish sheet for director review (also indexed by the canon memory)."""
    required, optional = ASSET_KIND_FACETS[entry["kind"]]
    lines = [
        f"# Ficha: {entry['name']} (`{entry['asset_id']}`)",
        "",
        f"- **Tipo:** {KIND_LABELS_ES[entry['kind']]}",
    ]
    if entry.get("base_asset_id"):
        span = f"desde el capítulo {entry['valid_from_episode']}"
        if entry.get("valid_until_episode"):
            span += f" hasta el {entry['valid_until_episode']}"
        lines.append(f"- **Variante de:** `{entry['base_asset_id']}` ({span})")
    elif entry.get("valid_from_episode") or entry.get("valid_until_episode"):
        lines.append(
            f"- **Vigencia:** capítulos {entry.get('valid_from_episode') or 1}–{entry.get('valid_until_episode') or 'fin'}"
        )
    lines += ["", "## Rasgos visuales (inglés, se inyectan en Google Flow)", "", "| Rasgo | Especificación |", "| :--- | :--- |"]
    for key in required + optional:
        if entry["visual_spec"].get(key):
            lines.append(f"| {FACET_LABELS_ES.get(key, key)} | {entry['visual_spec'][key]} |")
    lines += ["", "## Rasgos invariables (nunca cambian)", ""]
    lines += [f"- {lock}" for lock in entry["continuity_locks"]]
    lines += ["", "## Núcleo inyectado en cada plano", "", f"> {entry['verbatim_prompt_descriptor']}", ""]
    if entry.get("reference_notes"):
        lines += ["## Notas de referencia (no se inyectan)", "", entry["reference_notes"], ""]
    if entry.get("portrait_rel_path"):
        lines += ["## Retrato de referencia", "", f"`{entry['portrait_rel_path']}`", ""]
    rel = f"02_cast_and_ingredients/fichas/{entry['asset_id']}.md"
    write_text = "\n".join(lines)
    (prod_dir / rel).parent.mkdir(parents=True, exist_ok=True)
    (prod_dir / rel).write_text(write_text, encoding="utf-8")
    return rel


def _optional_episode(args: dict[str, Any], key: str) -> int | None:
    value = args.get(key)
    if value is None or value == "":
        return None
    number = int(value)
    if number < 1:
        raise ValueError(f"{key} must be >= 1.")
    return number


def tool_register_cast_asset(args: dict[str, Any]) -> dict[str, Any]:
    prod_dir = resolve_production_dir(args["production_slug"])
    reg_path = prod_dir / "02_cast_and_ingredients" / "cast_registry.json"
    registry = read_json(reg_path)
    assets: list[dict[str, Any]] = registry.setdefault("assets", [])
    assets_by_id = {a["asset_id"]: a for a in assets}

    if args.get("verbatim_prompt_descriptor"):
        raise ValueError(
            "verbatim_prompt_descriptor is compiled automatically from visual_spec + continuity_locks; do not pass it."
        )

    asset_id = args["asset_id"].strip().lower()
    if not SLUG_RE.match(asset_id):
        raise ValueError("Invalid asset_id. Use lowercase letters, digits, hyphens, or underscores.")

    kind = args["kind"]
    if kind not in ASSET_KIND_FACETS:
        raise ValueError(f"kind must be one of: {sorted(ASSET_KIND_FACETS)}.")

    name = args["name"].strip()
    spec = {k: str(v).strip() for k, v in (args.get("visual_spec") or {}).items()}
    locks_arg = args.get("continuity_locks")
    valid_from = _optional_episode(args, "valid_from_episode")
    valid_until = _optional_episode(args, "valid_until_episode")
    if valid_from and valid_until and valid_until < valid_from:
        raise ValueError("valid_until_episode must be >= valid_from_episode.")

    base_id = str(args.get("base_asset_id") or "").strip().lower()
    if base_id:
        base = assets_by_id.get(base_id)
        if base is None or base_id == asset_id:
            raise ValueError(f"base_asset_id '{base_id}' not found in cast_registry.json (or equals asset_id).")
        if base["kind"] != kind:
            raise ValueError(f"Variant kind '{kind}' must match base kind '{base['kind']}'.")
        if valid_from is None:
            raise ValueError("valid_from_episode is required for variants (first episode where this look applies).")
        spec = {**(base.get("visual_spec") or {}), **spec}
        locks = validate_continuity_locks(locks_arg) if locks_arg else list(base.get("continuity_locks") or [])
    else:
        locks = validate_continuity_locks(locks_arg)

    validate_visual_spec(kind, spec)
    descriptor = compose_descriptor(name, kind, spec, locks)
    word_count = len(descriptor.split())
    if word_count > MAX_DESCRIPTOR_WORDS:
        raise ValueError(
            f"Compiled descriptor has {word_count} words (max {MAX_DESCRIPTOR_WORDS}). Tighten the facets and move "
            "extra detail to reference_notes so prompts with several entities stay coherent."
        )

    portrait_source = args.get("portrait_source", "flow_character_describe")
    warnings: list[str] = []
    if kind in CAST_SLOT_KINDS and portrait_source == "real_photo":
        warnings.append(
            "SAFETY WARNING: Google Flow frequently blocks video generation on characters built from real-person photos. "
            "Prefer portrait_source='flow_character_describe' or stylized neutral sheets."
        )

    portrait_rel = args.get("portrait_rel_path", "")
    if portrait_rel:
        resolve_cartridge_path(prod_dir, portrait_rel, must_exist=False)

    entry: dict[str, Any] = {
        "asset_id": asset_id,
        "kind": kind,
        "name": name,
        "base_asset_id": base_id or None,
        "valid_from_episode": valid_from,
        "valid_until_episode": valid_until,
        "visual_spec": spec,
        "continuity_locks": locks,
        "verbatim_prompt_descriptor": descriptor,
        "reference_notes": str(args.get("reference_notes") or "").strip(),
        "flow_asset_ref": args.get("flow_asset_ref", f"asset:{name}"),
        "portrait_source": portrait_source,
        "portrait_rel_path": portrait_rel,
        "voice_id": args.get("voice_id", ""),
        "updated_at": utc_now_iso(),
    }
    entry["sheet_rel_path"] = write_asset_sheet(prod_dir, entry)

    replaced = asset_id in assets_by_id
    if replaced:
        assets[assets.index(assets_by_id[asset_id])] = entry
    else:
        assets.append(entry)

    registry["updated_at"] = utc_now_iso()
    write_json(reg_path, registry)
    memory_engine.index_production_files(prod_dir, force_rebuild=False)

    return {
        "status": "updated" if replaced else "registered",
        "asset": entry,
        "descriptor_word_count": word_count,
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


def episode_number_from_id(episode_id: str) -> int:
    if not EPISODE_ID_RE.match(episode_id or ""):
        raise SandboxViolationError("Invalid episode_id. Expected format 'ep_NN_slug' (e.g. 'ep_01_pilot').")
    return int(episode_id[3:5])


def asset_is_active(asset: dict[str, Any], episode: int) -> bool:
    start = asset.get("valid_from_episode") or 1
    until = asset.get("valid_until_episode")
    return episode >= start and (until is None or episode <= until)


def asset_lineage(asset: dict[str, Any], cast_by_id: dict[str, dict[str, Any]]) -> list[str]:
    ids = [asset["asset_id"]]
    current = asset
    while current.get("base_asset_id") and current["base_asset_id"] not in ids:
        ids.append(current["base_asset_id"])
        current = cast_by_id.get(current["base_asset_id"]) or {}
    return ids


def temporal_continuity_errors(
    asset: dict[str, Any], cast_by_id: dict[str, dict[str, Any]], episode: int, sid: str
) -> list[str]:
    aid = asset["asset_id"]
    errors: list[str] = []
    start, until = asset.get("valid_from_episode"), asset.get("valid_until_episode")
    if start and episode < start:
        errors.append(
            f"[{sid}] '{aid}' applies from episode {start} (valid_from_episode); this is episode {episode}. "
            "Set flashback=true on the shot if the earlier look is intentional."
        )
    if until and episode > until:
        errors.append(
            f"[{sid}] '{aid}' applies until episode {until} (valid_until_episode); this is episode {episode}. "
            "Set flashback=true on the shot if intentional."
        )
    superseding = sorted(
        a["asset_id"] for a in cast_by_id.values() if a.get("base_asset_id") == aid and asset_is_active(a, episode)
    )
    if superseding:
        errors.append(
            f"[{sid}] '{aid}' is superseded in episode {episode} by variant(s) {superseding}. "
            "Use the variant, or set flashback=true if the old look is intentional."
        )
    return errors


def state_change_warnings(
    asset: dict[str, Any], ctx: dict[str, Any], sid: str
) -> list[str]:
    lineage = set(asset_lineage(asset, ctx["cast_by_id"]))
    look_start = asset.get("valid_from_episode") or 0
    return [
        f"[{sid}] '{asset['asset_id']}' had a state change in episode {m['episode_number']} ('{m['title']}') after this "
        "look started. If it is visible on screen, register a variant (base_asset_id + valid_from_episode)."
        for m in ctx["state_changes"]
        if look_start < m["episode_number"] <= ctx["episode_number"] and lineage & set(m["entities"])
    ]


def normalize_shot_props(raw: Any, sid: str, errors: list[str]) -> list[dict[str, Any]]:
    props: list[dict[str, Any]] = []
    for item in raw or []:
        if isinstance(item, str):
            props.append({"prop_id": item, "held_by": None})
        elif isinstance(item, dict) and item.get("prop_id"):
            props.append({"prop_id": item["prop_id"], "held_by": item.get("held_by")})
        else:
            errors.append(f"[{sid}] Invalid props entry {item!r}: use 'prop_id' or {{'prop_id': ..., 'held_by': ...}}.")
    return props


def lint_manifest(
    manifest: dict[str, Any],
    ctx: dict[str, Any],
    *,
    compile_prompts: bool,
) -> tuple[list[str], list[str], int]:
    """Lint every shot in place (adds compiled_flow_prompt / resolved_reference_images / estimated_credits)."""
    shots = manifest.get("shots")
    if not isinstance(shots, list) or not shots:
        raise ValueError("shot_manifest.json must contain a non-empty 'shots' array.")

    cast_by_id: dict[str, dict[str, Any]] = ctx["cast_by_id"]
    visual_dna: dict[str, Any] = ctx["visual_dna"]
    episode = ctx["episode_number"]
    errors: list[str] = []
    warnings: list[str] = []
    total_credits = 0

    for i, shot in enumerate(shots, start=1):
        sid = shot.get("shot_id", f"shot_{i:02d}")
        model = shot.get("model", "Omni 1.1 Flash")
        media_type = shot.get("media_type", "video")
        duration = int(shot.get("duration_sec", 8))
        prompt = str(shot.get("motion_prompt", "")).strip()
        batches = str(shot.get("batches", "x1"))
        refs = shot.get("reference_images") or []
        cast_ids = shot.get("cast_ids") or []
        location_id = str(shot.get("location_id") or "").strip()
        first_frame = shot.get("first_frame_rel_path", "")
        last_frame = shot.get("last_frame_rel_path", "")
        chain_prev = bool(shot.get("chain_previous", False))
        exact_join = chain_prev and shot.get("continuity_mode", "exact") == "exact"
        flashback = bool(shot.get("flashback", False))
        dialogue = str(shot.get("spoken_dialogue", "")).strip()

        if not prompt:
            errors.append(f"[{sid}] Missing 'motion_prompt'.")

        # 1. Batch multiplier protection (prevent accidental double charges like x2)
        if batches != "x1" and not shot.get("allow_multi_batch", False):
            errors.append(
                f"[{sid}] batches='{batches}' multiplies credit spend! Set batches='x1' or set allow_multi_batch=true intentionally."
            )

        # 2. Model whitelist & keyframe duration legality
        rate = MODEL_CREDIT_RATES.get(model)
        if model not in MODEL_CREDIT_RATES:
            errors.append(f"[{sid}] Unknown model '{model}'. Valid models: {sorted(MODEL_CREDIT_RATES)}.")
        elif model.startswith("Veo 3.1") and duration != 8:
            errors.append(f"[{sid}] Model '{model}' in Google Flow uses fixed 8s duration (got {duration}s).")
        elif isinstance(rate, dict):
            allowed = sorted(k for k in rate if isinstance(k, int))
            if duration not in allowed:
                errors.append(f"[{sid}] Model '{model}' supports durations {allowed}s (got {duration}s).")
        if last_frame and not first_frame:
            errors.append(f"[{sid}] 'last_frame_rel_path' requires 'first_frame_rel_path' to also be specified.")
        if first_frame and last_frame and duration > 10:
            errors.append(f"[{sid}] First+Last frame transition is capped at 10s maximum (got {duration}s).")
        if len(refs) > MAX_REFERENCE_IMAGES:
            errors.append(f"[{sid}] Exceeds maximum {MAX_REFERENCE_IMAGES} reference images (got {len(refs)}).")

        # 3. Continuity paradox check (exact last frame join + character reference conflict)
        if exact_join and refs:
            warnings.append(
                f"[{sid}] Flow's exact last-frame continuation ignores attached character chips (likeness comes from the frame itself). "
                "Clear reference_images on exact joins or switch continuity_mode='likeness'."
            )

        # 4. Scene composition: every entity in its proper slot (cast / location / props)
        cast_assets: list[dict[str, Any]] = []
        for cid in cast_ids:
            asset = cast_by_id.get(cid)
            if asset is None:
                errors.append(f"[{sid}] Unknown cast_id '{cid}' (not found in cast_registry.json).")
            elif asset["kind"] not in CAST_SLOT_KINDS:
                slot = "location_id" if asset["kind"] == "location" else "props"
                errors.append(f"[{sid}] cast_id '{cid}' is a {asset['kind']}; reference it through '{slot}' instead.")
            else:
                cast_assets.append(asset)

        location_asset: dict[str, Any] | None = None
        if location_id:
            asset = cast_by_id.get(location_id)
            if asset is None:
                errors.append(f"[{sid}] Unknown location_id '{location_id}' (not found in cast_registry.json).")
            elif asset["kind"] != "location":
                errors.append(f"[{sid}] location_id '{location_id}' is a {asset['kind']}, not a location.")
            else:
                location_asset = asset
        elif media_type == "video":
            errors.append(f"[{sid}] Video shots require 'location_id' so the set stays identical across shots.")

        prop_entries: list[tuple[dict[str, Any], str | None]] = []
        for prop in normalize_shot_props(shot.get("props"), sid, errors):
            asset = cast_by_id.get(prop["prop_id"])
            if asset is None:
                errors.append(f"[{sid}] Unknown prop_id '{prop['prop_id']}' (not found in cast_registry.json).")
            elif asset["kind"] != "prop":
                errors.append(f"[{sid}] prop_id '{prop['prop_id']}' is a {asset['kind']}, not a prop.")
            elif prop["held_by"] and prop["held_by"] not in cast_ids:
                errors.append(
                    f"[{sid}] props: held_by '{prop['held_by']}' for '{prop['prop_id']}' must be one of the shot's cast_ids {cast_ids}."
                )
            else:
                prop_entries.append((asset, prop["held_by"]))

        scene_assets = cast_assets + ([location_asset] if location_asset else []) + [a for a, _ in prop_entries]

        # 5. Temporal continuity (look variants per episode) + canon memory bridge
        if not flashback:
            for asset in scene_assets:
                errors.extend(temporal_continuity_errors(asset, cast_by_id, episode, sid))
                warnings.extend(state_change_warnings(asset, ctx, sid))

        # 6. Verbatim memory injection check
        prompt_lower = prompt.lower()

        def absent(asset: dict[str, Any]) -> bool:
            return asset["verbatim_prompt_descriptor"].lower() not in prompt_lower

        missing_cast = [a["verbatim_prompt_descriptor"] for a in cast_assets if absent(a)]
        missing_props = [
            a["verbatim_prompt_descriptor"].replace(
                f"{a['name']} — ", f"{a['name']} (held by {cast_by_id[h]['name']}) — ", 1
            )
            if h
            else a["verbatim_prompt_descriptor"]
            for a, h in prop_entries
            if absent(a)
        ]
        missing_location = location_asset["verbatim_prompt_descriptor"] if location_asset and absent(location_asset) else ""

        if not compile_prompts and (missing_cast or missing_props or missing_location):
            names = [a["name"] for a in scene_assets if absent(a)]
            errors.append(f"[{sid}] Flow has no cross-scene memory; missing verbatim descriptors for: {names}.")

        # 7. Negative object hallucination check
        for pat in NEGATIVE_OBJECT_PATTERNS:
            m = pat.search(prompt)
            if m:
                warnings.append(
                    f"[{sid}] Negative object phrase detected ({m.group(0)!r}). Naming unwanted objects often spawns them in Flow; describe positive camera framing instead."
                )

        # 8. Physics hazard check
        for pat, msg in PHYSICS_HAZARD_PATTERNS:
            if pat.search(prompt):
                warnings.append(f"[{sid}] Physics hazard: {msg}")

        # 9. Spoken dialogue timestamp check
        if dialogue and not TIMECODE_RE.search(prompt):
            errors.append(
                f"[{sid}] Shot includes spoken_dialogue ({dialogue!r}) without second-by-second timestamps (e.g. '0.0-1.8s:') inside motion_prompt."
            )

        if compile_prompts:
            # Canonical order: subjects -> props -> action & camera -> location -> visual DNA
            if prompt:
                parts: list[str] = []
                if missing_cast:
                    parts.append("Subject & Cast Invariants: " + " | ".join(missing_cast))
                if missing_props:
                    parts.append("Props: " + " | ".join(missing_props))
                parts.append(prompt if prompt[-1] in '.!?"' else prompt + ".")
                if missing_location:
                    parts.append("Location: " + missing_location)
                dna_parts = []
                for label, key in (
                    ("Visual Style", "aesthetic_style"),
                    ("Lighting", "lighting_signature"),
                    ("Color palette", "color_palette"),
                    ("Lens", "lens_package"),
                ):
                    value = str(visual_dna.get(key) or "").strip()
                    if value and "Pending" not in value and value.lower() not in prompt_lower:
                        dna_parts.append(f"{label}: {value}.")
                if dna_parts:
                    parts.append(" ".join(dna_parts))
                shot["compiled_flow_prompt"] = " ".join(parts)
                compiled_words = len(shot["compiled_flow_prompt"].split())
                if compiled_words > COMPILED_PROMPT_WARN_WORDS:
                    warnings.append(
                        f"[{sid}] Compiled prompt has {compiled_words} words (> {COMPILED_PROMPT_WARN_WORDS}); long prompts "
                        "dilute the model's attention and blend traits between entities. Split the shot or reduce entities on screen."
                    )

            # Visual anchors: portraits of every entity in scene (never on exact last-frame joins)
            resolved = list(dict.fromkeys(refs))
            if not exact_join:
                for asset in scene_assets:
                    portrait = asset.get("portrait_rel_path")
                    if not portrait:
                        continue
                    if not (ctx["prod_dir"] / portrait).is_file():
                        warnings.append(f"[{sid}] Portrait for '{asset['asset_id']}' not found at {portrait}; it cannot anchor likeness.")
                    elif portrait not in resolved:
                        resolved.append(portrait)
            if len(resolved) > MAX_REFERENCE_IMAGES:
                warnings.append(
                    f"[{sid}] {len(resolved)} reference images exceed Flow's limit of {MAX_REFERENCE_IMAGES}; "
                    f"dropped {resolved[MAX_REFERENCE_IMAGES:]} (cast first, then location, then props)."
                )
                resolved = resolved[:MAX_REFERENCE_IMAGES]
            shot["resolved_reference_images"] = resolved

        shot["estimated_credits"] = estimate_shot_credits(shot)
        total_credits += shot["estimated_credits"]

    return errors, warnings, total_credits


def load_lint_context(prod_dir: Path, episode_id: str) -> dict[str, Any]:
    episode = episode_number_from_id(episode_id)
    prod_manifest = read_yaml(prod_dir / "production.yaml")
    cast_reg = read_json(prod_dir / "02_cast_and_ingredients" / "cast_registry.json")
    return {
        "prod_dir": prod_dir,
        "episode_number": episode,
        "cast_by_id": {a["asset_id"]: a for a in cast_reg.get("assets", [])},
        "visual_dna": prod_manifest.get("visual_dna") or {},
        "state_changes": memory_engine.active_state_changes(prod_dir, up_to_episode=episode),
    }


def require_render_ready_manifest(prod_dir: Path, episode_id: str | None) -> tuple[dict[str, Any], str]:
    """Ensure the episode manifest is exactly what was last validated and is still valid today.

    Raises PermissionError when the manifest was edited after validation, no longer passes the linter
    (e.g. a cast asset was removed), or its compiled prompts/references are stale (cast/visual_dna changed).
    """
    if not episode_id:
        raise ValueError("episode_id is required.")
    manifest_path = resolve_episode_dir(prod_dir, episode_id) / "01_script" / "shot_manifest.json"
    manifest = read_json(manifest_path)
    fingerprint = manifest_fingerprint(manifest)
    if manifest.get("validated_sha256") != fingerprint:
        raise PermissionError(
            "shot_manifest.json changed since its last successful validation (or was never validated). "
            "Run flowforge_validate_shot_manifest again before continuing."
        )

    relinted = copy.deepcopy(manifest)
    errors, _, _ = lint_manifest(relinted, load_lint_context(prod_dir, episode_id), compile_prompts=True)
    if errors:
        raise PermissionError("shot_manifest.json no longer passes validation: " + " ".join(errors))
    stale = [
        s.get("shot_id", f"shot_{i:02d}")
        for i, (s, fresh) in enumerate(zip(manifest["shots"], relinted["shots"]), start=1)
        if any(s.get(k) != fresh.get(k) for k in ("compiled_flow_prompt", "resolved_reference_images"))
    ]
    if stale:
        raise PermissionError(
            f"Stale compiled_flow_prompt/resolved_reference_images for {stale}: cast_registry.json or visual_dna "
            "changed after validation. Run flowforge_validate_shot_manifest again."
        )
    return manifest, fingerprint


def tool_validate_shot_manifest(args: dict[str, Any]) -> dict[str, Any]:
    prod_dir = resolve_production_dir(args["production_slug"])
    resolve_episode_dir(prod_dir, args["episode_id"])
    manifest_rel = args.get("manifest_rel_path", f"episodes/{args['episode_id']}/01_script/shot_manifest.json")
    manifest_path = resolve_cartridge_path(prod_dir, manifest_rel, must_exist=True)

    manifest = read_json(manifest_path)
    ctx = load_lint_context(prod_dir, args["episode_id"])
    compile_prompts = bool(args.get("compile_prompts", True))
    errors, warnings, total_credits = lint_manifest(manifest, ctx, compile_prompts=compile_prompts)
    fingerprint = manifest_fingerprint(manifest)

    fingerprint_stored = compile_prompts and not errors
    if fingerprint_stored:
        manifest["validated_at"] = utc_now_iso()
        manifest["estimated_total_credits"] = total_credits
        manifest["validated_sha256"] = fingerprint
        write_json(manifest_path, manifest)
        memory_engine.index_production_files(prod_dir, force_rebuild=False)

    return {
        "valid": len(errors) == 0,
        "shot_count": len(manifest["shots"]),
        "estimated_total_credits": total_credits,
        "errors": errors,
        "warnings": warnings,
        "manifest_path": str(manifest_path),
        "manifest_sha256": fingerprint,
        "fingerprint_stored": fingerprint_stored,
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

    def episode_spent(ep: str) -> int:
        return sum(int(e.get("credits", 0)) for e in ledger.get("entries", []) if ep and e.get("episode_id") == ep)

    if action == "quote":
        if not episode_id:
            raise ValueError("episode_id is required for action='quote'.")
        render_gate = evaluate_gate(prod_dir, "render", episode_id)
        if not render_gate["unlocked"]:
            raise PermissionError(
                f"Render gate is locked for {episode_id}; missing approvals: {render_gate['missing_approvals']}. "
                "Quotes are only issued for an approved script and storyboard."
            )
        manifest, fingerprint = require_render_ready_manifest(prod_dir, episode_id)

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
        spent_episode = episode_spent(episode_id)
        within_episode_cap = (spent_episode + quote_total) <= ep_cap
        within_monthly_cap = (spent_total + quote_total) <= monthly_cap
        within_budget = within_episode_cap and within_monthly_cap

        quote_id = None
        if within_budget:
            quote_id = f"q_{secrets.token_hex(6)}"
            ledger.setdefault("quotes", []).append(
                {
                    "quote_id": quote_id,
                    "episode_id": episode_id,
                    "manifest_sha256": fingerprint,
                    "quoted_credits": quote_total,
                    "created_at": utc_now_iso(),
                }
            )
            write_json(ledger_path, ledger)

        return {
            "action": "quote",
            "production_slug": args["production_slug"],
            "episode_id": episode_id,
            "quote_id": quote_id,
            "manifest_sha256": fingerprint,
            "quoted_credits": quote_total,
            "episode_spent_so_far": spent_episode,
            "episode_cap": ep_cap,
            "monthly_spent_so_far": spent_total,
            "monthly_cap": monthly_cap,
            "within_budget": within_budget,
            "requires_human_confirmation": quote_total > 0,
            "shot_breakdown": breakdown,
        }

    if action == "record":
        raw_credits = args.get("credits")
        if isinstance(raw_credits, bool) or not isinstance(raw_credits, (int, float, str)):
            raise ValueError("credits must be a non-negative integer.")
        try:
            credits = int(raw_credits)
        except ValueError as exc:
            raise ValueError("credits must be a non-negative integer.") from exc
        if credits < 0:
            raise ValueError(f"credits must be a non-negative integer (got {credits}).")

        quote_id = str(args.get("quote_id") or "").strip()
        if not quote_id:
            raise PermissionError("quote_id is required: every recorded spend must reference a quote from action='quote'.")
        quote = next((q for q in ledger.get("quotes", []) if q.get("quote_id") == quote_id), None)
        if quote is None:
            raise PermissionError(f"Unknown quote_id '{quote_id}'. Issue a quote with action='quote' first.")
        if episode_id and quote["episode_id"] != episode_id:
            raise PermissionError(
                f"quote_id '{quote_id}' belongs to episode '{quote['episode_id']}', not '{episode_id}'."
            )
        episode_id = quote["episode_id"]

        already = sum(int(e.get("credits", 0)) for e in ledger.get("entries", []) if e.get("quote_id") == quote_id)
        overrun = already + credits > int(quote["quoted_credits"])
        overrun_reason = str(args.get("overrun_reason") or "").strip()
        if overrun and not overrun_reason:
            raise PermissionError(
                f"Recording {credits} credits exceeds quote {quote_id} ({already} of {quote['quoted_credits']} already recorded). "
                "Provide overrun_reason to record the overrun explicitly."
            )

        entry: dict[str, Any] = {
            "timestamp": utc_now_iso(),
            "episode_id": episode_id,
            "quote_id": quote_id,
            "operation": args.get("operation", "flow_generate"),
            "model": args.get("model", ""),
            "credits": credits,
            "overrun": overrun,
            "notes": args.get("notes", ""),
        }
        if overrun_reason:
            entry["overrun_reason"] = overrun_reason
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
    memory_engine.index_production_files(prod_dir, force_rebuild=False)
    return report


def tool_index_production(args: dict[str, Any]) -> dict[str, Any]:
    prod_dir = resolve_production_dir(args["production_slug"])
    return memory_engine.index_production_files(prod_dir, force_rebuild=bool(args.get("force_rebuild", False)))


def tool_search(args: dict[str, Any]) -> dict[str, Any]:
    prod_dir = resolve_production_dir(args["production_slug"])
    return memory_engine.search_production(
        prod_dir,
        query=args["query"],
        scope=args.get("scope", "all"),
        episode_number=args.get("episode_number"),
        memory_type=args.get("memory_type"),
        entity_id=args.get("entity_id"),
        limit=int(args.get("limit", 10)),
    )


def tool_memory_save(args: dict[str, Any]) -> dict[str, Any]:
    prod_dir = resolve_production_dir(args["production_slug"])
    return memory_engine.save_canon_memory(
        prod_dir,
        title=args["title"],
        content=args["content"],
        memory_type=args.get("memory_type", "canon_fact"),
        topic_key=args.get("topic_key"),
        episode_number=args.get("episode_number"),
        entities=args.get("entities"),
        status=args.get("status", "active"),
    )


def tool_memory_context(args: dict[str, Any]) -> dict[str, Any]:
    prod_dir = resolve_production_dir(args["production_slug"])
    return memory_engine.get_canon_context(
        prod_dir,
        up_to_episode=args.get("up_to_episode"),
        limit=int(args.get("limit", 30)),
    )


def tool_graph_link(args: dict[str, Any]) -> dict[str, Any]:
    prod_dir = resolve_production_dir(args["production_slug"])
    return memory_engine.upsert_graph_relation(
        prod_dir,
        subject_id=args["subject_id"],
        relation=args["relation"],
        object_id=args["object_id"],
        since_episode=int(args.get("since_episode", 1)),
        until_episode=args.get("until_episode"),
        notes=args.get("notes", ""),
    )


def tool_entity_dossier(args: dict[str, Any]) -> dict[str, Any]:
    prod_dir = resolve_production_dir(args["production_slug"])
    return memory_engine.build_entity_dossier(
        prod_dir,
        entity_id=args["entity_id"],
        up_to_episode=args.get("up_to_episode"),
    )


def tool_craft_kb(args: dict[str, Any]) -> dict[str, Any]:
    return memory_engine.manage_craft_kb(WORKSPACE_ROOT, args)


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
        "name": "flowforge_set_gate",
        "description": "Audited HITL gate transition (appends to gate_history). Moving to 'in_review' or 'approved' requires all prerequisites approved through this tool; 'approved' additionally requires the gate to be 'in_review' and a verbatim human_confirmation from the director. Moving 'script' forward requires a currently valid shot manifest. Hand-edited approvals are not trusted by flowforge_check_gates.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "production_slug": {"type": "string"},
                "gate": {
                    "type": "string",
                    "enum": list(PRODUCTION_GATES + EPISODE_GATES),
                },
                "state": {"type": "string", "enum": sorted(VALID_GATE_STATES)},
                "episode_id": {"type": "string", "description": "Required for episode gates (e.g. 'ep_01_piloto')"},
                "human_confirmation": {
                    "type": "string",
                    "description": "Required for state='approved': the director's explicit approval message, quoted verbatim",
                },
                "notes": {"type": "string"},
            },
            "required": ["production_slug", "gate", "state"],
        },
    },
    {
        "name": "flowforge_register_cast_asset",
        "description": "Register or update a character, creature, prop, or location as a structured visual sheet. Required English facets depend on kind (character: apparent_age, build, face, skin, hair, eyes, wardrobe, palette; creature: scale, silhouette, anatomy, surface, palette, eyes, locomotion; prop: scale, shape, materials, palette, surface_details, orientation_rule; location: setting, layout, landmarks, materials, palette, lighting, atmosphere), each >= 2 words, plus continuity_locks (traits that never change). The verbatim_prompt_descriptor injected into every Flow shot is compiled automatically (max 90 words). Look changes are variants: base_asset_id + valid_from_episode (facets inherited, pass only overrides). Writes a Spanish review sheet to 02_cast_and_ingredients/fichas/.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "production_slug": {"type": "string"},
                "asset_id": {"type": "string"},
                "kind": {"type": "string", "enum": sorted(ASSET_KIND_FACETS)},
                "name": {"type": "string"},
                "visual_spec": {
                    "type": "object",
                    "description": "Facet -> precise English description (see tool description for required facets per kind)",
                    "additionalProperties": {"type": "string"},
                },
                "continuity_locks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "English traits that must never change (>= 3 words each). Inherited by variants if omitted.",
                },
                "reference_notes": {"type": "string", "description": "Extended notes for humans; never injected into prompts"},
                "base_asset_id": {"type": "string", "description": "Register this asset as a look variant of an existing asset"},
                "valid_from_episode": {"type": "integer", "minimum": 1, "description": "Required for variants"},
                "valid_until_episode": {"type": "integer", "minimum": 1},
                "flow_asset_ref": {"type": "string"},
                "portrait_source": {"type": "string", "enum": ["flow_character_describe", "stylized_sheet", "real_photo"]},
                "portrait_rel_path": {"type": "string", "description": "Reference image auto-attached to every shot featuring this asset"},
                "voice_id": {"type": "string"},
            },
            "required": ["production_slug", "asset_id", "kind", "name", "visual_spec"],
        },
    },
    {
        "name": "flowforge_validate_shot_manifest",
        "description": "Pre-flight linter for shot_manifest.json: checks the Google Flow model whitelist and per-model durations, x1 batch protection, verbatim cast memory injection, negative-prompt traps, physics hazards, and dialogue timestamps. On success stores validated_sha256 (required by script approval and credit quotes).",
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
        "description": "Quote Google AI Ultra Flow credits for an episode (action='quote') or record spent credits into credit_ledger.json (action='record'). A quote requires the render gate unlocked and a manifest unchanged since validation; within budget it returns a quote_id bound to the manifest fingerprint. Every record requires that quote_id, non-negative credits, and an overrun_reason if the quote is exceeded.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "production_slug": {"type": "string"},
                "action": {"type": "string", "enum": ["quote", "record"]},
                "episode_id": {"type": "string"},
                "quote_id": {"type": "string", "description": "Required for action='record' (returned by action='quote')"},
                "credits": {"type": "integer", "minimum": 0},
                "overrun_reason": {"type": "string", "description": "Required when recorded credits exceed the quote"},
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
    {
        "name": "flowforge_index_production",
        "description": "Incrementally index (SHA-256 deduplicated) all Markdown bibles, scripts, shot manifests, cast registries, SRT subtitles, and 00_references/ files of a production into its FTS5 BM25 database.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "production_slug": {"type": "string"},
                "force_rebuild": {"type": "boolean", "default": False},
            },
            "required": ["production_slug"],
        },
    },
    {
        "name": "flowforge_search",
        "description": "Hybrid FTS5 + BM25 search (diacritic-insensitive) across a production's indexed scripts, bibles, shots, subtitles, references, and canon memories.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "production_slug": {"type": "string"},
                "query": {"type": "string"},
                "scope": {
                    "type": "string",
                    "enum": ["all", "memories", "documents", "bible", "scripts", "shots", "cast", "subtitles", "reference"],
                    "default": "all",
                },
                "episode_number": {"type": "integer", "description": "Filter up to a given episode number"},
                "memory_type": {"type": "string"},
                "entity_id": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["production_slug", "query"],
        },
    },
    {
        "name": "flowforge_memory_save",
        "description": "Save or upsert (via topic_key with revision history) a persistent canon fact, character/prop state mutation, open plot thread, visual rule, or director preference.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "production_slug": {"type": "string"},
                "title": {"type": "string"},
                "content": {"type": "string"},
                "memory_type": {
                    "type": "string",
                    "enum": [
                        "canon_fact",
                        "state_change",
                        "plot_thread",
                        "visual_rule",
                        "director_preference",
                        "continuity_warning",
                    ],
                    "default": "canon_fact",
                },
                "topic_key": {"type": "string", "description": "Stable key for evolving topics (e.g. 'state/elena/watch')"},
                "episode_number": {"type": "integer"},
                "entities": {"type": "array", "items": {"type": "string"}},
                "status": {"type": "string", "enum": ["active", "resolved", "archived"], "default": "active"},
            },
            "required": ["production_slug", "title", "content"],
        },
    },
    {
        "name": "flowforge_memory_context",
        "description": "Retrieve structured active canon context (open plot threads, character state changes, director preferences, and index stats) up to a given episode.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "production_slug": {"type": "string"},
                "up_to_episode": {"type": "integer"},
                "limit": {"type": "integer", "default": 30},
            },
            "required": ["production_slug"],
        },
    },
    {
        "name": "flowforge_graph_link",
        "description": "Record or update a directed relationship in the production's Canon Knowledge Graph (subject_id -> relation -> object_id) with episode validity bounds.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "production_slug": {"type": "string"},
                "subject_id": {"type": "string"},
                "relation": {"type": "string"},
                "object_id": {"type": "string"},
                "since_episode": {"type": "integer", "default": 1},
                "until_episode": {"type": "integer"},
                "notes": {"type": "string"},
            },
            "required": ["production_slug", "subject_id", "relation", "object_id"],
        },
    },
    {
        "name": "flowforge_entity_dossier",
        "description": "Compile a 360-degree canon dossier for any character, prop, or location up to a target episode (cast_registry descriptor + state mutations + graph edges + past appearances).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "production_slug": {"type": "string"},
                "entity_id": {"type": "string"},
                "up_to_episode": {"type": "integer"},
            },
            "required": ["production_slug", "entity_id"],
        },
    },
    {
        "name": "flowforge_craft_kb",
        "description": "Save or search cross-production technical learnings on Google Flow models, optics, camera moves, and audio mastering (zero narrative lore).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["save", "search"]},
                "query": {"type": "string"},
                "topic_key": {"type": "string"},
                "category": {"type": "string", "default": "cinematography"},
                "flow_model": {"type": "string", "default": "general"},
                "title": {"type": "string"},
                "content": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["action"],
        },
    },
]

TOOL_HANDLERS = {
    "flowforge_init_production": tool_init_production,
    "flowforge_init_episode": tool_init_episode,
    "flowforge_check_gates": tool_check_gates,
    "flowforge_set_gate": tool_set_gate,
    "flowforge_register_cast_asset": tool_register_cast_asset,
    "flowforge_validate_shot_manifest": tool_validate_shot_manifest,
    "flowforge_quote_and_log_credits": tool_quote_and_log_credits,
    "flowforge_extract_frame": tool_extract_frame,
    "flowforge_probe_media": tool_probe_media,
    "flowforge_render_contact_sheet": tool_render_contact_sheet,
    "flowforge_master_episode": tool_master_episode,
    "flowforge_index_production": tool_index_production,
    "flowforge_search": tool_search,
    "flowforge_memory_save": tool_memory_save,
    "flowforge_memory_context": tool_memory_context,
    "flowforge_graph_link": tool_graph_link,
    "flowforge_entity_dossier": tool_entity_dossier,
    "flowforge_craft_kb": tool_craft_kb,
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
