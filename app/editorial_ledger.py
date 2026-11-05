"""Additive, single-SQLite editorial ledger schema (ADR-0003).

Phase 1 is STORAGE ONLY. This module does not infer facts from prose, approve
model suggestions, or change story_nodes, story_edges or authored manuscripts.
All new tables are scoped by (project_id, branch_id) and reference stable
graph IDs by convention; graph resolution will be validated by the future
author-reviewed write API, not by automatically promoting drafts.
"""
from __future__ import annotations
import sqlite3

SCHEMA_VERSION = "editorial-ledger-v1"

EDITORIAL_TABLES = (
    "story_world_events",
    "story_entity_states",
    "story_knowledge_ledger",
    "story_plot_threads",
    "story_thread_beats",
    "story_fact_evidence",
    "story_editorial_proposals",
    "story_editorial_findings",
    "story_canon_revisions",
    "story_chunk_embeddings",
)


def init_editorial_schema(db: sqlite3.Connection) -> None:
    """Idempotent additive migration; never drop or rewrite legacy records."""
    db.execute("""CREATE TABLE IF NOT EXISTS story_editorial_schema_meta (
        version TEXT PRIMARY KEY, installed_at TEXT NOT NULL
            DEFAULT CURRENT_TIMESTAMP
    )""")

    # Event ID refers to story_nodes(kind=event); avoid a second event graph.
    db.execute("""CREATE TABLE IF NOT EXISTS story_world_events (
        project_id TEXT NOT NULL,
        branch_id TEXT NOT NULL,
        event_id TEXT NOT NULL,
        scene_id TEXT NOT NULL,
        world_seq INTEGER NOT NULL,
        discourse_seq INTEGER NOT NULL,
        point_of_view_node_id TEXT,
        place_node_id TEXT,
        time_label TEXT NOT NULL DEFAULT '',
        canon_revision TEXT NOT NULL,
        PRIMARY KEY(project_id,branch_id,event_id)
    )""")
    db.execute("""CREATE INDEX IF NOT EXISTS idx_world_events_order
        ON story_world_events(project_id,branch_id,world_seq,discourse_seq)""")

    # State transitions are exact author-approved claims or explicit proposals.
    # JSON strings are opaque until the future schema-validated write API.
    db.execute("""CREATE TABLE IF NOT EXISTS story_entity_states (
        project_id TEXT NOT NULL,
        branch_id TEXT NOT NULL,
        id TEXT NOT NULL,
        event_id TEXT NOT NULL,
        entity_node_id TEXT NOT NULL,
        attribute TEXT NOT NULL,
        before_value TEXT,
        after_value TEXT NOT NULL,
        evidence_id TEXT,
        approval_status TEXT NOT NULL DEFAULT 'proposed'
           CHECK(approval_status IN ('proposed','approved','rejected')),
        canon_revision TEXT NOT NULL,
        PRIMARY KEY(project_id,branch_id,id)
    )""")
    db.execute("""CREATE INDEX IF NOT EXISTS idx_entity_state_timeline
        ON story_entity_states(project_id,branch_id,entity_node_id,attribute,event_id)""")

    # Reader and character knowledge are distinct and can include false belief.
    db.execute("""CREATE TABLE IF NOT EXISTS story_knowledge_ledger (
        project_id TEXT NOT NULL,
        branch_id TEXT NOT NULL,
        id TEXT NOT NULL,
        fact_node_id TEXT NOT NULL,
        knower_node_id TEXT NOT NULL,
        event_id TEXT,
        discourse_seq INTEGER NOT NULL,
        knowledge_level TEXT NOT NULL
           CHECK(knowledge_level IN ('unknown','suspects','knows','believes_false')),
        evidence_id TEXT,
        approval_status TEXT NOT NULL DEFAULT 'proposed'
           CHECK(approval_status IN ('proposed','approved','rejected')),
        canon_revision TEXT NOT NULL,
        PRIMARY KEY(project_id,branch_id,id)
    )""")
    db.execute("""CREATE INDEX IF NOT EXISTS idx_knowledge_track
        ON story_knowledge_ledger(project_id,branch_id,knower_node_id,
                                  fact_node_id,discourse_seq)""")

    db.execute("""CREATE TABLE IF NOT EXISTS story_plot_threads (
        project_id TEXT NOT NULL,
        branch_id TEXT NOT NULL,
        thread_id TEXT NOT NULL,
        title TEXT NOT NULL,
        mystery_node_id TEXT,
        intended_reveal_scene_id TEXT,
        status TEXT NOT NULL DEFAULT 'open'
           CHECK(status IN ('open','resolved','intentionally_open','abandoned')),
        approval_status TEXT NOT NULL DEFAULT 'proposed'
           CHECK(approval_status IN ('proposed','approved','rejected')),
        canon_revision TEXT NOT NULL,
        PRIMARY KEY(project_id,branch_id,thread_id)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS story_thread_beats (
        project_id TEXT NOT NULL,
        branch_id TEXT NOT NULL,
        beat_id TEXT NOT NULL,
        thread_id TEXT NOT NULL,
        scene_id TEXT NOT NULL,
        discourse_seq INTEGER NOT NULL,
        beat_kind TEXT NOT NULL
           CHECK(beat_kind IN ('setup','clue','complication','red_herring',
                               'reveal','payoff')),
        fact_node_id TEXT,
        evidence_id TEXT,
        approval_status TEXT NOT NULL DEFAULT 'proposed'
           CHECK(approval_status IN ('proposed','approved','rejected')),
        PRIMARY KEY(project_id,branch_id,beat_id)
    )""")
    db.execute("""CREATE INDEX IF NOT EXISTS idx_story_thread_beats
        ON story_thread_beats(project_id,branch_id,thread_id,discourse_seq)""")

    # Evidence preserves the exact source hash: it cannot silently follow a
    # rewritten chapter or become valid after a stale browser edit.
    db.execute("""CREATE TABLE IF NOT EXISTS story_fact_evidence (
        project_id TEXT NOT NULL,
        branch_id TEXT NOT NULL,
        evidence_id TEXT NOT NULL,
        fact_node_id TEXT NOT NULL,
        source_kind TEXT NOT NULL
           CHECK(source_kind IN ('synopsis','approved','draft','external')),
        source_id TEXT NOT NULL,
        source_text_sha256 TEXT NOT NULL,
        excerpt_start INTEGER NOT NULL DEFAULT 0,
        excerpt_end INTEGER NOT NULL DEFAULT 0,
        approval_status TEXT NOT NULL DEFAULT 'proposed'
           CHECK(approval_status IN ('proposed','approved','rejected')),
        canon_revision TEXT NOT NULL,
        PRIMARY KEY(project_id,branch_id,evidence_id)
    )""")
    db.execute("""CREATE INDEX IF NOT EXISTS idx_fact_evidence_lookup
        ON story_fact_evidence(project_id,branch_id,fact_node_id,approval_status)""")

    # Model output stays a candidate until an explicit author action.
    db.execute("""CREATE TABLE IF NOT EXISTS story_editorial_proposals (
        project_id TEXT NOT NULL,
        branch_id TEXT NOT NULL,
        proposal_id TEXT NOT NULL,
        proposal_kind TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        source_text_sha256 TEXT NOT NULL,
        model_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending'
           CHECK(status IN ('pending','accepted','rejected')),
        canon_revision TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(project_id,branch_id,proposal_id)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS story_editorial_findings (
        project_id TEXT NOT NULL,
        branch_id TEXT NOT NULL,
        finding_id TEXT NOT NULL,
        category TEXT NOT NULL,
        severity TEXT NOT NULL CHECK(severity IN ('info','warning','blocking')),
        basis TEXT NOT NULL CHECK(basis IN ('deterministic','model_hypothesis')),
        evidence_json TEXT NOT NULL,
        source_text_sha256 TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open'
           CHECK(status IN ('open','resolved','dismissed')),
        canon_revision TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(project_id,branch_id,finding_id)
    )""")

    # Canon snapshot hashes bind QA reports and model proposals to one version.
    db.execute("""CREATE TABLE IF NOT EXISTS story_canon_revisions (
        project_id TEXT NOT NULL,
        branch_id TEXT NOT NULL,
        revision_id TEXT NOT NULL,
        parent_revision_id TEXT,
        content_sha256 TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(project_id,branch_id,revision_id)
    )""")

    # Rebuildable derivative index: no AI embeddings generated on migrations.
    db.execute("""CREATE TABLE IF NOT EXISTS story_chunk_embeddings (
        project_id TEXT NOT NULL,
        branch_id TEXT NOT NULL,
        chunk_id TEXT NOT NULL,
        model_id TEXT NOT NULL,
        dimensions INTEGER NOT NULL CHECK(dimensions > 0),
        vector_f32 BLOB NOT NULL,
        source_text_sha256 TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(project_id,branch_id,chunk_id,model_id)
    )""")
    db.execute("INSERT OR IGNORE INTO story_editorial_schema_meta(version) VALUES(?)",
               (SCHEMA_VERSION,))


def editorial_schema_status(db: sqlite3.Connection, project_id: str,
                            branch_id: str = "main") -> dict:
    """Report storage capability without implying any prose was verified."""
    counts = {}
    for table in EDITORIAL_TABLES:
        counts[table] = db.execute(
            f"SELECT count(*) FROM {table} WHERE project_id=? AND branch_id=?",
            (project_id, branch_id)).fetchone()[0]
    return {
        "version": SCHEMA_VERSION,
        "project_id": project_id,
        "branch_id": branch_id,
        "counts": counts,
        "stage": "schema_only",
        "auto_extraction_ready": False,
        "causal_qa_ready": False,
        "semantic_embeddings_ready": False,
        "author_approval_required": True,
    }
