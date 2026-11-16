#!/usr/bin/env python3
"""FlowForge Studio — Hermetic Canon Memory, FTS5 Document Indexer & Knowledge Graph Engine.

Provides two strictly separated SQLite WAL + FTS5 (BM25) storage tiers:
1. Per-Production Canon DB (`productions/{slug}/memory/canon.db`):
   - Incremental SHA-256 chunk indexer for Markdown (.md), YAML (.yaml/.yml), JSON (.json), and Subtitles (.srt).
   - Episodic state & canon memory with `topic_key` upsert and immutable `memory_history` audit trail.
   - Directed Canon Knowledge Graph (`entity_relations`) and time-aware `entity_dossier` builder.
2. Cross-Production Studio Craft KB (`.studio_memory/craft_kb.db`):
   - Stores reusable technical discoveries about Google Flow models, optics, camera moves, and audio mastering
     without ever leaking narrative lore across isolated productions.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import yaml

INDEXABLE_EXTENSIONS = {".md", ".markdown", ".txt", ".yaml", ".yml", ".json", ".srt"}
SKIP_FILENAMES = {"credit_ledger.json"}
HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$", re.M)
EPISODE_FROM_PATH_RE = re.compile(r"(?:^|/)episodes/ep_(\d{2})_[^/]+/")
TOKEN_CLEAN_RE = re.compile(r"[^\w]+", re.U)


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _connect_sqlite(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_canon_db(prod_dir: Path) -> sqlite3.Connection:
    """Open and initialize schema for a production's isolated canon.db."""
    db_path = prod_dir / "memory" / "canon.db"
    conn = _connect_sqlite(db_path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS indexed_files (
            rel_path TEXT PRIMARY KEY,
            sha256 TEXT NOT NULL,
            file_kind TEXT NOT NULL,
            episode_number INTEGER,
            chunk_count INTEGER NOT NULL DEFAULT 0,
            indexed_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS doc_chunks (
            chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
            rel_path TEXT NOT NULL REFERENCES indexed_files(rel_path) ON DELETE CASCADE,
            chunk_index INTEGER NOT NULL,
            doc_kind TEXT NOT NULL,
            episode_number INTEGER,
            section_title TEXT NOT NULL,
            entity_tags TEXT NOT NULL DEFAULT '[]',
            content TEXT NOT NULL
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS doc_chunks_fts USING fts5(
            rel_path,
            section_title,
            entity_tags,
            content,
            content='doc_chunks',
            content_rowid='chunk_id',
            tokenize='unicode61 remove_diacritics 2'
        );

        CREATE TRIGGER IF NOT EXISTS doc_chunks_ai AFTER INSERT ON doc_chunks BEGIN
            INSERT INTO doc_chunks_fts(rowid, rel_path, section_title, entity_tags, content)
            VALUES (new.chunk_id, new.rel_path, new.section_title, new.entity_tags, new.content);
        END;

        CREATE TRIGGER IF NOT EXISTS doc_chunks_ad AFTER DELETE ON doc_chunks BEGIN
            INSERT INTO doc_chunks_fts(doc_chunks_fts, rowid, rel_path, section_title, entity_tags, content)
            VALUES ('delete', old.chunk_id, old.rel_path, old.section_title, old.entity_tags, old.content);
        END;

        CREATE TRIGGER IF NOT EXISTS doc_chunks_au AFTER UPDATE ON doc_chunks BEGIN
            INSERT INTO doc_chunks_fts(doc_chunks_fts, rowid, rel_path, section_title, entity_tags, content)
            VALUES ('delete', old.chunk_id, old.rel_path, old.section_title, old.entity_tags, old.content);
            INSERT INTO doc_chunks_fts(rowid, rel_path, section_title, entity_tags, content)
            VALUES (new.chunk_id, new.rel_path, new.section_title, new.entity_tags, new.content);
        END;

        CREATE TABLE IF NOT EXISTS memories (
            memory_id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_key TEXT UNIQUE,
            memory_type TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            episode_number INTEGER,
            entities_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'active',
            revision INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS memory_history (
            history_id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id INTEGER NOT NULL REFERENCES memories(memory_id) ON DELETE CASCADE,
            revision INTEGER NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            episode_number INTEGER,
            status TEXT NOT NULL,
            archived_at TEXT NOT NULL
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
            topic_key,
            title,
            content,
            entities_json,
            content='memories',
            content_rowid='memory_id',
            tokenize='unicode61 remove_diacritics 2'
        );

        CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
            INSERT INTO memories_fts(rowid, topic_key, title, content, entities_json)
            VALUES (new.memory_id, COALESCE(new.topic_key, ''), new.title, new.content, new.entities_json);
        END;

        CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, topic_key, title, content, entities_json)
            VALUES ('delete', old.memory_id, COALESCE(old.topic_key, ''), old.title, old.content, old.entities_json);
        END;

        CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, topic_key, title, content, entities_json)
            VALUES ('delete', old.memory_id, COALESCE(old.topic_key, ''), old.title, old.content, old.entities_json);
            INSERT INTO memories_fts(rowid, topic_key, title, content, entities_json)
            VALUES (new.memory_id, COALESCE(new.topic_key, ''), new.title, new.content, new.entities_json);
        END;

        CREATE TABLE IF NOT EXISTS entity_relations (
            relation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_id TEXT NOT NULL,
            relation TEXT NOT NULL,
            object_id TEXT NOT NULL,
            since_episode INTEGER NOT NULL DEFAULT 1,
            until_episode INTEGER,
            notes TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            UNIQUE(subject_id, relation, object_id)
        );
        """
    )
    _rehydrate_from_snapshot_if_empty(conn, prod_dir)
    return conn


def _export_canon_snapshot(conn: sqlite3.Connection, prod_dir: Path) -> None:
    """Persist structured memories and graph edges as Git-friendly JSON alongside canon.db."""
    snap_path = prod_dir / "memory" / "canon_snapshot.json"
    memories = [
        {
            "memory_id": r["memory_id"],
            "topic_key": r["topic_key"],
            "memory_type": r["memory_type"],
            "title": r["title"],
            "content": r["content"],
            "episode_number": r["episode_number"],
            "entities": json.loads(r["entities_json"]),
            "status": r["status"],
            "revision": r["revision"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
        for r in conn.execute("SELECT * FROM memories ORDER BY memory_id ASC").fetchall()
    ]
    relations = [
        {
            "subject_id": r["subject_id"],
            "relation": r["relation"],
            "object_id": r["object_id"],
            "since_episode": r["since_episode"],
            "until_episode": r["until_episode"],
            "notes": r["notes"],
            "updated_at": r["updated_at"],
        }
        for r in conn.execute("SELECT * FROM entity_relations ORDER BY relation_id ASC").fetchall()
    ]
    payload = {"updated_at": utc_now_iso(), "memories": memories, "entity_relations": relations}
    snap_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _rehydrate_from_snapshot_if_empty(conn: sqlite3.Connection, prod_dir: Path) -> None:
    """Restore memories and graph links from canon_snapshot.json when canon.db is freshly created."""
    snap_path = prod_dir / "memory" / "canon_snapshot.json"
    if not snap_path.is_file():
        return
    has_mem = conn.execute("SELECT COUNT(*) AS c FROM memories").fetchone()["c"]
    has_rel = conn.execute("SELECT COUNT(*) AS c FROM entity_relations").fetchone()["c"]
    if has_mem > 0 or has_rel > 0:
        return
    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    for m in snap.get("memories", []):
        conn.execute(
            """
            INSERT INTO memories(topic_key, memory_type, title, content, episode_number, entities_json, status, revision, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                m.get("topic_key"),
                m["memory_type"],
                m["title"],
                m["content"],
                m.get("episode_number"),
                json.dumps(m.get("entities", []), ensure_ascii=False),
                m.get("status", "active"),
                m.get("revision", 1),
                m.get("created_at", utc_now_iso()),
                m.get("updated_at", utc_now_iso()),
            ),
        )
    for r in snap.get("entity_relations", []):
        conn.execute(
            """
            INSERT OR IGNORE INTO entity_relations(subject_id, relation, object_id, since_episode, until_episode, notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                r["subject_id"],
                r["relation"],
                r["object_id"],
                r.get("since_episode", 1),
                r.get("until_episode"),
                r.get("notes", ""),
                r.get("updated_at", utc_now_iso()),
            ),
        )
    conn.commit()


def init_craft_db(workspace_root: Path) -> sqlite3.Connection:
    """Open and initialize schema for cross-production technical Flow craft knowledge."""
    db_path = workspace_root / ".studio_memory" / "craft_kb.db"
    conn = _connect_sqlite(db_path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS craft_notes (
            note_id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_key TEXT UNIQUE,
            category TEXT NOT NULL,
            flow_model TEXT NOT NULL DEFAULT 'general',
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            tags_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS craft_notes_fts USING fts5(
            topic_key,
            category,
            flow_model,
            title,
            content,
            tags_json,
            content='craft_notes',
            content_rowid='note_id',
            tokenize='unicode61 remove_diacritics 2'
        );

        CREATE TRIGGER IF NOT EXISTS craft_notes_ai AFTER INSERT ON craft_notes BEGIN
            INSERT INTO craft_notes_fts(rowid, topic_key, category, flow_model, title, content, tags_json)
            VALUES (new.note_id, COALESCE(new.topic_key, ''), new.category, new.flow_model, new.title, new.content, new.tags_json);
        END;

        CREATE TRIGGER IF NOT EXISTS craft_notes_ad AFTER DELETE ON craft_notes BEGIN
            INSERT INTO craft_notes_fts(craft_notes_fts, rowid, topic_key, category, flow_model, title, content, tags_json)
            VALUES ('delete', old.note_id, COALESCE(old.topic_key, ''), old.category, old.flow_model, old.title, old.content, old.tags_json);
        END;

        CREATE TRIGGER IF NOT EXISTS craft_notes_au AFTER UPDATE ON craft_notes BEGIN
            INSERT INTO craft_notes_fts(craft_notes_fts, rowid, topic_key, category, flow_model, title, content, tags_json)
            VALUES ('delete', old.note_id, COALESCE(old.topic_key, ''), old.category, old.flow_model, old.title, old.content, old.tags_json);
            INSERT INTO craft_notes_fts(rowid, topic_key, category, flow_model, title, content, tags_json)
            VALUES (new.note_id, COALESCE(new.topic_key, ''), new.category, new.flow_model, new.title, new.content, new.tags_json);
        END;
        """
    )
    return conn


# ---------------------------------------------------------------------------
# Smart Domain Chunkers (Markdown, Shot Manifests, Cast Registry, SRT, YAML)
# ---------------------------------------------------------------------------


def _infer_doc_kind(rel_path: str) -> str:
    if "shot_manifest.json" in rel_path:
        return "shots"
    if "cast_registry.json" in rel_path:
        return "cast"
    if "literary_script" in rel_path or "/01_script/" in rel_path:
        return "scripts"
    if rel_path.endswith(".srt"):
        return "subtitles"
    if "01_universe_bible/" in rel_path:
        return "bible"
    if "00_references/" in rel_path:
        return "reference"
    return "manifest"


def _extract_episode_num(rel_path: str) -> int | None:
    m = EPISODE_FROM_PATH_RE.search(rel_path)
    return int(m.group(1)) if m else None


def _chunk_markdown(text: str, fallback_title: str) -> list[dict[str, Any]]:
    matches = list(HEADING_RE.finditer(text))
    if not matches:
        stripped = text.strip()
        return [{"section_title": fallback_title, "entity_tags": [], "content": stripped}] if stripped else []

    chunks: list[dict[str, Any]] = []
    if matches[0].start() > 0:
        preamble = text[: matches[0].start()].strip()
        if preamble:
            chunks.append({"section_title": f"{fallback_title} (Preamble)", "entity_tags": [], "content": preamble})

    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        section_body = text[start:end].strip()
        if section_body:
            chunks.append(
                {
                    "section_title": match.group(2).strip(),
                    "entity_tags": [],
                    "content": section_body,
                }
            )
    return chunks


def _chunk_json_file(rel_path: str, text: str) -> list[dict[str, Any]]:
    data = json.loads(text)
    chunks: list[dict[str, Any]] = []

    if rel_path.endswith("cast_registry.json") and isinstance(data, dict):
        for asset in data.get("assets", []):
            aid = asset.get("asset_id", "unknown")
            name = asset.get("name", aid)
            kind = asset.get("kind", "asset")
            desc = asset.get("verbatim_prompt_descriptor", "")
            body = f"[{kind.upper()}] {name} (id={aid})\nDescriptor: {desc}\nFlow Ref: {asset.get('flow_asset_ref', '')}"
            chunks.append(
                {
                    "section_title": f"Cast/{kind}: {name}",
                    "entity_tags": [aid, name.lower()],
                    "content": body,
                }
            )
        return chunks

    if rel_path.endswith("shot_manifest.json") and isinstance(data, dict):
        for shot in data.get("shots", []):
            sid = shot.get("shot_id", "shot")
            cast_ids = shot.get("cast_ids") or []
            dialogue = shot.get("spoken_dialogue", "")
            prompt = shot.get("compiled_flow_prompt") or shot.get("motion_prompt", "")
            body = (
                f"Shot {sid} (Scene {shot.get('scene_number', 1)} | {shot.get('model', '')} | {shot.get('duration_sec', 8)}s)\n"
                f"Cast: {', '.join(cast_ids)}\n"
                f"Dialogue: {dialogue}\n"
                f"Prompt: {prompt}"
            )
            chunks.append(
                {
                    "section_title": f"Shot {sid}",
                    "entity_tags": list(cast_ids),
                    "content": body,
                }
            )
        return chunks

    pretty = json.dumps(data, indent=2, ensure_ascii=False)
    if pretty.strip():
        chunks.append({"section_title": Path(rel_path).name, "entity_tags": [], "content": pretty[:8000]})
    return chunks


def _chunk_srt(text: str, fallback_title: str) -> list[dict[str, Any]]:
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
    if not blocks:
        return []
    grouped: list[dict[str, Any]] = []
    batch_size = 10
    for i in range(0, len(blocks), batch_size):
        segment = "\n\n".join(blocks[i : i + batch_size])
        grouped.append(
            {
                "section_title": f"{fallback_title} (Cues {i + 1}-{min(i + batch_size, len(blocks))})",
                "entity_tags": [],
                "content": segment,
            }
        )
    return grouped


def chunk_document(rel_path: str, raw_text: str) -> list[dict[str, Any]]:
    suffix = Path(rel_path).suffix.lower()
    fallback = Path(rel_path).stem
    if suffix in {".md", ".markdown", ".txt"}:
        return _chunk_markdown(raw_text, fallback)
    if suffix == ".json":
        return _chunk_json_file(rel_path, raw_text)
    if suffix == ".srt":
        return _chunk_srt(raw_text, fallback)
    if suffix in {".yaml", ".yml"}:
        parsed = yaml.safe_load(raw_text)
        rendered = yaml.safe_dump(parsed, sort_keys=False, allow_unicode=True) if parsed else raw_text
        return [{"section_title": Path(rel_path).name, "entity_tags": [], "content": rendered.strip()}] if rendered.strip() else []
    return []


# ---------------------------------------------------------------------------
# Indexing, Search, Canon Memory & Knowledge Graph Operations
# ---------------------------------------------------------------------------


def index_production_files(prod_dir: Path, *, force_rebuild: bool = False) -> dict[str, Any]:
    """Incrementally index all text/manifest/script/subtitle files inside a production cartridge."""
    conn = init_canon_db(prod_dir)
    try:
        if force_rebuild:
            conn.execute("DELETE FROM indexed_files;")

        existing_hashes = {
            row["rel_path"]: row["sha256"]
            for row in conn.execute("SELECT rel_path, sha256 FROM indexed_files").fetchall()
        }

        seen_paths: set[str] = set()
        indexed_count = 0
        skipped_unchanged = 0
        total_chunks_written = 0

        for path in sorted(prod_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = str(path.relative_to(prod_dir))
            if rel.startswith("memory/") or path.name in SKIP_FILENAMES or path.name.startswith("."):
                continue
            if path.suffix.lower() not in INDEXABLE_EXTENSIONS:
                continue

            seen_paths.add(rel)
            raw_bytes = path.read_bytes()
            digest = hashlib.sha256(raw_bytes).hexdigest()

            if not force_rebuild and existing_hashes.get(rel) == digest:
                skipped_unchanged += 1
                continue

            raw_text = raw_bytes.decode("utf-8", errors="replace")
            doc_kind = _infer_doc_kind(rel)
            ep_num = _extract_episode_num(rel)
            chunks = chunk_document(rel, raw_text)

            conn.execute("DELETE FROM indexed_files WHERE rel_path = ?", (rel,))
            conn.execute(
                """
                INSERT INTO indexed_files(rel_path, sha256, file_kind, episode_number, chunk_count, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (rel, digest, doc_kind, ep_num, len(chunks), utc_now_iso()),
            )

            for idx, ch in enumerate(chunks):
                conn.execute(
                    """
                    INSERT INTO doc_chunks(rel_path, chunk_index, doc_kind, episode_number, section_title, entity_tags, content)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        rel,
                        idx,
                        doc_kind,
                        ep_num,
                        ch["section_title"],
                        json.dumps(ch["entity_tags"], ensure_ascii=False),
                        ch["content"],
                    ),
                )
            indexed_count += 1
            total_chunks_written += len(chunks)

        # Prune deleted files from index
        stale_paths = set(existing_hashes.keys()) - seen_paths
        for stale in stale_paths:
            conn.execute("DELETE FROM indexed_files WHERE rel_path = ?", (stale,))

        conn.commit()
        totals = conn.execute(
            "SELECT COUNT(*) AS files, COALESCE(SUM(chunk_count), 0) AS chunks FROM indexed_files"
        ).fetchone()
        return {
            "status": "indexed",
            "files_updated": indexed_count,
            "files_unchanged": skipped_unchanged,
            "files_pruned": len(stale_paths),
            "new_chunks_written": total_chunks_written,
            "total_indexed_files": totals["files"],
            "total_indexed_chunks": totals["chunks"],
        }
    finally:
        conn.close()


def _build_fts_queries(raw_query: str) -> tuple[str, str]:
    tokens = [t for t in TOKEN_CLEAN_RE.split(raw_query.strip()) if len(t) >= 2]
    if not tokens:
        raise ValueError("Search query must contain at least one word of 2+ characters.")
    strict_and = " AND ".join(f'"{t}"*' for t in tokens)
    relaxed_or = " OR ".join(f'"{t}"*' for t in tokens)
    return strict_and, relaxed_or


def search_production(
    prod_dir: Path,
    *,
    query: str,
    scope: str = "all",
    episode_number: int | None = None,
    memory_type: str | None = None,
    entity_id: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Hybrid BM25 search across both indexed documents/scripts/shots and episodic canon memories."""
    # Run lightweight incremental sync first so search never misses newly edited files
    index_production_files(prod_dir, force_rebuild=False)

    strict_q, relaxed_q = _build_fts_queries(query)
    conn = init_canon_db(prod_dir)
    try:
        doc_hits: list[dict[str, Any]] = []
        mem_hits: list[dict[str, Any]] = []

        if scope in {"all", "documents", "bible", "scripts", "shots", "cast", "subtitles", "reference"}:
            for fts_q in (strict_q, relaxed_q):
                clauses = ["doc_chunks_fts MATCH ?"]
                params: list[Any] = [fts_q]
                if scope not in {"all", "documents"}:
                    clauses.append("c.doc_kind = ?")
                    params.append(scope)
                if episode_number is not None:
                    clauses.append("(c.episode_number IS NULL OR c.episode_number <= ?)")
                    params.append(int(episode_number))
                if entity_id:
                    clauses.append("LOWER(c.entity_tags) LIKE ?")
                    params.append(f'%"{entity_id.lower()}"%')
                params.append(limit)

                sql = f"""
                    SELECT
                        c.chunk_id,
                        c.rel_path,
                        c.doc_kind,
                        c.episode_number,
                        c.section_title,
                        c.entity_tags,
                        snippet(doc_chunks_fts, 3, '>>>', '<<<', ' ... ', 36) AS snippet,
                        bm25(doc_chunks_fts, 1.0, 2.5, 2.0, 1.0) AS rank_score
                    FROM doc_chunks_fts
                    JOIN doc_chunks c ON c.chunk_id = doc_chunks_fts.rowid
                    WHERE {' AND '.join(clauses)}
                    ORDER BY rank_score ASC
                    LIMIT ?
                """
                rows = conn.execute(sql, params).fetchall()
                if rows:
                    doc_hits = [
                        {
                            "source": "indexed_document",
                            "rel_path": r["rel_path"],
                            "doc_kind": r["doc_kind"],
                            "episode_number": r["episode_number"],
                            "section_title": r["section_title"],
                            "entities": json.loads(r["entity_tags"]),
                            "snippet": r["snippet"],
                            "bm25_score": round(abs(float(r["rank_score"])), 4),
                        }
                        for r in rows
                    ]
                    break

        if scope in {"all", "memories"}:
            for fts_q in (strict_q, relaxed_q):
                clauses = ["memories_fts MATCH ?", "m.status = 'active'"]
                params = [fts_q]
                if episode_number is not None:
                    clauses.append("(m.episode_number IS NULL OR m.episode_number <= ?)")
                    params.append(int(episode_number))
                if memory_type:
                    clauses.append("m.memory_type = ?")
                    params.append(memory_type)
                if entity_id:
                    clauses.append("LOWER(m.entities_json) LIKE ?")
                    params.append(f'%"{entity_id.lower()}"%')
                params.append(limit)

                sql = f"""
                    SELECT
                        m.memory_id,
                        m.topic_key,
                        m.memory_type,
                        m.title,
                        m.episode_number,
                        m.entities_json,
                        m.revision,
                        snippet(memories_fts, 2, '>>>', '<<<', ' ... ', 36) AS snippet,
                        bm25(memories_fts, 2.0, 3.0, 1.5, 2.0) AS rank_score
                    FROM memories_fts
                    JOIN memories m ON m.memory_id = memories_fts.rowid
                    WHERE {' AND '.join(clauses)}
                    ORDER BY rank_score ASC
                    LIMIT ?
                """
                rows = conn.execute(sql, params).fetchall()
                if rows:
                    mem_hits = [
                        {
                            "source": "canon_memory",
                            "memory_id": r["memory_id"],
                            "topic_key": r["topic_key"],
                            "memory_type": r["memory_type"],
                            "title": r["title"],
                            "episode_number": r["episode_number"],
                            "entities": json.loads(r["entities_json"]),
                            "revision": r["revision"],
                            "snippet": r["snippet"],
                            "bm25_score": round(abs(float(r["rank_score"])), 4),
                        }
                        for r in rows
                    ]
                    break

        return {
            "query": query,
            "scope": scope,
            "memory_hits": mem_hits,
            "document_hits": doc_hits,
            "total_hits": len(mem_hits) + len(doc_hits),
        }
    finally:
        conn.close()


def save_canon_memory(
    prod_dir: Path,
    *,
    title: str,
    content: str,
    memory_type: str = "canon_fact",
    topic_key: str | None = None,
    episode_number: int | None = None,
    entities: list[str] | None = None,
    status: str = "active",
) -> dict[str, Any]:
    """Save or upsert an episodic canon memory, archiving prior revisions in memory_history."""
    valid_types = {
        "canon_fact",
        "state_change",
        "plot_thread",
        "visual_rule",
        "director_preference",
        "continuity_warning",
    }
    if memory_type not in valid_types:
        raise ValueError(f"Invalid memory_type '{memory_type}'. Choose from: {sorted(valid_types)}")
    if status not in {"active", "resolved", "archived"}:
        raise ValueError("status must be 'active', 'resolved', or 'archived'.")

    entities_norm = sorted({e.strip().lower() for e in (entities or []) if e.strip()})
    now = utc_now_iso()
    clean_key = topic_key.strip() if topic_key and topic_key.strip() else None

    conn = init_canon_db(prod_dir)
    try:
        if clean_key:
            existing = conn.execute("SELECT * FROM memories WHERE topic_key = ?", (clean_key,)).fetchone()
            if existing:
                conn.execute(
                    """
                    INSERT INTO memory_history(memory_id, revision, title, content, episode_number, status, archived_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        existing["memory_id"],
                        existing["revision"],
                        existing["title"],
                        existing["content"],
                        existing["episode_number"],
                        existing["status"],
                        now,
                    ),
                )
                new_rev = int(existing["revision"]) + 1
                conn.execute(
                    """
                    UPDATE memories
                    SET memory_type = ?, title = ?, content = ?, episode_number = ?,
                        entities_json = ?, status = ?, revision = ?, updated_at = ?
                    WHERE memory_id = ?
                    """,
                    (
                        memory_type,
                        title.strip(),
                        content.strip(),
                        episode_number,
                        json.dumps(entities_norm, ensure_ascii=False),
                        status,
                        new_rev,
                        now,
                        existing["memory_id"],
                    ),
                )
                conn.commit()
                _export_canon_snapshot(conn, prod_dir)
                return {
                    "action": "updated",
                    "memory_id": existing["memory_id"],
                    "topic_key": clean_key,
                    "revision": new_rev,
                    "status": status,
                }

        cur = conn.execute(
            """
            INSERT INTO memories(topic_key, memory_type, title, content, episode_number, entities_json, status, revision, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                clean_key,
                memory_type,
                title.strip(),
                content.strip(),
                episode_number,
                json.dumps(entities_norm, ensure_ascii=False),
                status,
                now,
                now,
            ),
        )
        conn.commit()
        _export_canon_snapshot(conn, prod_dir)
        return {
            "action": "created",
            "memory_id": cur.lastrowid,
            "topic_key": clean_key,
            "revision": 1,
            "status": status,
        }
    finally:
        conn.close()


def get_canon_context(prod_dir: Path, *, up_to_episode: int | None = None, limit: int = 30) -> dict[str, Any]:
    """Retrieve structured active canon context (preferences, open plot threads, state mutations) for an episode."""
    conn = init_canon_db(prod_dir)
    try:
        clauses = ["status = 'active'"]
        params: list[Any] = []
        if up_to_episode is not None:
            clauses.append("(episode_number IS NULL OR episode_number <= ?)")
            params.append(int(up_to_episode))
        params.append(limit)

        rows = conn.execute(
            f"""
            SELECT memory_id, topic_key, memory_type, title, content, episode_number, entities_json, revision, updated_at
            FROM memories
            WHERE {' AND '.join(clauses)}
            ORDER BY COALESCE(episode_number, 0) DESC, updated_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()

        grouped: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            grouped.setdefault(r["memory_type"], []).append(
                {
                    "memory_id": r["memory_id"],
                    "topic_key": r["topic_key"],
                    "title": r["title"],
                    "content": r["content"],
                    "episode_number": r["episode_number"],
                    "entities": json.loads(r["entities_json"]),
                    "revision": r["revision"],
                }
            )

        idx_stats = conn.execute(
            "SELECT COUNT(*) AS files, COALESCE(SUM(chunk_count), 0) AS chunks FROM indexed_files"
        ).fetchone()
        rel_count = conn.execute("SELECT COUNT(*) AS cnt FROM entity_relations").fetchone()["cnt"]

        return {
            "up_to_episode": up_to_episode,
            "active_memories_count": len(rows),
            "indexed_files": idx_stats["files"],
            "indexed_chunks": idx_stats["chunks"],
            "graph_relations_count": rel_count,
            "memories_by_type": grouped,
        }
    finally:
        conn.close()


def upsert_graph_relation(
    prod_dir: Path,
    *,
    subject_id: str,
    relation: str,
    object_id: str,
    since_episode: int = 1,
    until_episode: int | None = None,
    notes: str = "",
) -> dict[str, Any]:
    """Create or update a directed edge in the production's Canon Knowledge Graph."""
    subj = subject_id.strip().lower()
    rel = re.sub(r"\s+", "_", relation.strip().lower())
    obj = object_id.strip().lower()
    now = utc_now_iso()

    conn = init_canon_db(prod_dir)
    try:
        conn.execute(
            """
            INSERT INTO entity_relations(subject_id, relation, object_id, since_episode, until_episode, notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(subject_id, relation, object_id) DO UPDATE SET
                since_episode = excluded.since_episode,
                until_episode = excluded.until_episode,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (subj, rel, obj, int(since_episode), until_episode, notes.strip(), now),
        )
        conn.commit()
        _export_canon_snapshot(conn, prod_dir)
        return {
            "status": "linked",
            "edge": {
                "subject_id": subj,
                "relation": rel,
                "object_id": obj,
                "since_episode": since_episode,
                "until_episode": until_episode,
                "notes": notes.strip(),
            },
        }
    finally:
        conn.close()


def build_entity_dossier(prod_dir: Path, *, entity_id: str, up_to_episode: int | None = None) -> dict[str, Any]:
    """Assemble a 360-degree canon dossier for a character, prop, or location up to a target episode."""
    eid = entity_id.strip().lower()
    index_production_files(prod_dir, force_rebuild=False)

    cast_entry = None
    reg_path = prod_dir / "02_cast_and_ingredients" / "cast_registry.json"
    if reg_path.is_file():
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
        cast_entry = next((a for a in reg.get("assets", []) if a.get("asset_id") == eid), None)

    conn = init_canon_db(prod_dir)
    try:
        ep_clause = ""
        ep_params: list[Any] = []
        if up_to_episode is not None:
            ep_clause = " AND (episode_number IS NULL OR episode_number <= ?)"
            ep_params = [int(up_to_episode)]

        memories = [
            {
                "memory_id": r["memory_id"],
                "topic_key": r["topic_key"],
                "memory_type": r["memory_type"],
                "title": r["title"],
                "content": r["content"],
                "episode_number": r["episode_number"],
                "status": r["status"],
                "revision": r["revision"],
            }
            for r in conn.execute(
                f"""
                SELECT * FROM memories
                WHERE LOWER(entities_json) LIKE ? {ep_clause}
                ORDER BY COALESCE(episode_number, 0) ASC, memory_id ASC
                """,
                [f'%"{eid}"%', *ep_params],
            ).fetchall()
        ]

        rel_sql = "SELECT * FROM entity_relations WHERE (subject_id = ? OR object_id = ?)"
        rel_params: list[Any] = [eid, eid]
        if up_to_episode is not None:
            rel_sql += " AND since_episode <= ? AND (until_episode IS NULL OR until_episode >= ?)"
            rel_params.extend([int(up_to_episode), int(up_to_episode)])

        relations = [
            {
                "subject_id": r["subject_id"],
                "relation": r["relation"],
                "object_id": r["object_id"],
                "since_episode": r["since_episode"],
                "until_episode": r["until_episode"],
                "notes": r["notes"],
            }
            for r in conn.execute(rel_sql, rel_params).fetchall()
        ]

        appearances = [
            {
                "rel_path": r["rel_path"],
                "episode_number": r["episode_number"],
                "section_title": r["section_title"],
            }
            for r in conn.execute(
                f"""
                SELECT rel_path, episode_number, section_title
                FROM doc_chunks
                WHERE LOWER(entity_tags) LIKE ? {ep_clause}
                ORDER BY COALESCE(episode_number, 0) ASC, chunk_id ASC
                LIMIT 25
                """,
                [f'%"{eid}"%', *ep_params],
            ).fetchall()
        ]

        return {
            "entity_id": eid,
            "up_to_episode": up_to_episode,
            "cast_registry_profile": cast_entry,
            "canon_memories": memories,
            "graph_relations": relations,
            "indexed_appearances": appearances,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tier 2: Cross-Production Technical Craft Knowledge Base (.studio_memory/)
# ---------------------------------------------------------------------------


def manage_craft_kb(workspace_root: Path, args: dict[str, Any]) -> dict[str, Any]:
    """Save or search reusable Google Flow technical cinematography & audio discoveries."""
    action = args["action"]
    conn = init_craft_db(workspace_root)
    try:
        if action == "save":
            now = utc_now_iso()
            topic_key = (args.get("topic_key") or "").strip() or None
            category = args.get("category", "cinematography").strip().lower()
            flow_model = args.get("flow_model", "general").strip()
            title = args["title"].strip()
            content = args["content"].strip()
            tags = json.dumps(args.get("tags") or [], ensure_ascii=False)

            if topic_key:
                existing = conn.execute("SELECT note_id FROM craft_notes WHERE topic_key = ?", (topic_key,)).fetchone()
                if existing:
                    conn.execute(
                        """
                        UPDATE craft_notes
                        SET category = ?, flow_model = ?, title = ?, content = ?, tags_json = ?, updated_at = ?
                        WHERE note_id = ?
                        """,
                        (category, flow_model, title, content, tags, now, existing["note_id"]),
                    )
                    conn.commit()
                    return {"action": "updated", "note_id": existing["note_id"], "topic_key": topic_key}

            cur = conn.execute(
                """
                INSERT INTO craft_notes(topic_key, category, flow_model, title, content, tags_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (topic_key, category, flow_model, title, content, tags, now, now),
            )
            conn.commit()
            return {"action": "created", "note_id": cur.lastrowid, "topic_key": topic_key}

        if action == "search":
            raw_q = (args.get("query") or "").strip()
            limit = int(args.get("limit", 10))
            if not raw_q:
                rows = conn.execute(
                    "SELECT * FROM craft_notes ORDER BY updated_at DESC LIMIT ?", (limit,)
                ).fetchall()
                return {
                    "hits": [
                        {
                            "note_id": r["note_id"],
                            "topic_key": r["topic_key"],
                            "category": r["category"],
                            "flow_model": r["flow_model"],
                            "title": r["title"],
                            "content": r["content"],
                            "tags": json.loads(r["tags_json"]),
                        }
                        for r in rows
                    ]
                }

            strict_q, relaxed_q = _build_fts_queries(raw_q)
            for fts_q in (strict_q, relaxed_q):
                rows = conn.execute(
                    """
                    SELECT
                        n.*,
                        bm25(craft_notes_fts, 2.0, 1.5, 1.5, 3.0, 1.5, 2.0) AS rank_score
                    FROM craft_notes_fts
                    JOIN craft_notes n ON n.note_id = craft_notes_fts.rowid
                    WHERE craft_notes_fts MATCH ?
                    ORDER BY rank_score ASC
                    LIMIT ?
                    """,
                    (fts_q, limit),
                ).fetchall()
                if rows:
                    return {
                        "query": raw_q,
                        "hits": [
                            {
                                "note_id": r["note_id"],
                                "topic_key": r["topic_key"],
                                "category": r["category"],
                                "flow_model": r["flow_model"],
                                "title": r["title"],
                                "content": r["content"],
                                "tags": json.loads(r["tags_json"]),
                                "bm25_score": round(abs(float(r["rank_score"])), 4),
                            }
                            for r in rows
                        ],
                    }
            return {"query": raw_q, "hits": []}

        raise ValueError("action must be 'save' or 'search'.")
    finally:
        conn.close()
