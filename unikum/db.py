"""SQLite-lag. Raa data og LLM-resumeer holdes adskilt.

Pointen med at gemme raa tekst separat fra resumeet er, at vi kan skifte
model eller prompt og koere opsummeringen om uden at hente alt fra Unikum
igen.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,           -- MESSAGE | MANAGEMENTINFO | PLANNING ...
    title         TEXT NOT NULL,
    body_html     TEXT,
    body_text     TEXT,
    published     TEXT,                    -- ISO8601, UTC
    last_edited   TEXT,
    author        TEXT,
    owner         TEXT,                    -- fx "Glasbruksskolan" eller "4C"
    contexts      TEXT,                    -- JSON-liste over modtagerkontekster
    important     INTEGER DEFAULT 0,
    raw           TEXT,                    -- hele API-svaret, til fejlsoegning
    fetched_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_entries_published ON entries(published DESC);

CREATE TABLE IF NOT EXISTS attachments (
    id          TEXT PRIMARY KEY,
    entry_id    TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    filename    TEXT,
    path        TEXT,                      -- sti hos Unikum
    local_path  TEXT,                      -- hvor vi gemte filen
    text        TEXT,                      -- udtrukket tekst (PDF/Word/txt)
    error       TEXT,                      -- hvis udtraekket fejlede
    fetched_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_attachments_entry ON attachments(entry_id);

-- Hvad der allerede ligger i skyen. Uden det ville hver koersel skrive
-- alle beskeder igen, og KV har et dagligt loft paa skrivninger.
CREATE TABLE IF NOT EXISTS published (
    key         TEXT PRIMARY KEY,
    hash        TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS summaries (
    entry_id    TEXT PRIMARY KEY REFERENCES entries(id) ON DELETE CASCADE,
    summary     TEXT NOT NULL,           -- kort: oversigt og e-ink-skaerm
    detail      TEXT,                    -- lang: vises naar man klikker ind
    category    TEXT,
    action      TEXT,                      -- hvad forael skal goere, hvis noget
    due_date    TEXT,                      -- ISO-dato hvis der er en deadline
    headline    TEXT,                      -- kort version til e-ink-skaermen
    model       TEXT,
    prompt_hash TEXT,                      -- saa vi kan koere om ved promptskift
    created_at  TEXT NOT NULL
);
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# Kolonner der er kommet til efter at databasen blev taget i brug. De
# tilfoejes ved opstart, saa en eksisterende database ikke skal smides vaek.
MIGRATIONS = {
    "summaries": {"detail": "TEXT"},
}


def _migrate(conn: sqlite3.Connection) -> list[str]:
    applied = []
    for table, columns in MIGRATIONS.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                applied.append(f"{table}.{name}")
    return applied


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def known_entry_ids() -> set[str]:
    with connect() as conn:
        return {r["id"] for r in conn.execute("SELECT id FROM entries")}


def upsert_entry(entry: dict[str, Any]) -> None:
    cols = (
        "id", "kind", "title", "body_html", "body_text", "published",
        "last_edited", "author", "owner", "contexts", "important", "raw",
        "fetched_at",
    )
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "id")
    with connect() as conn:
        conn.execute(
            f"INSERT INTO entries ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}",
            [entry.get(c) for c in cols],
        )


def upsert_attachment(att: dict[str, Any]) -> None:
    cols = ("id", "entry_id", "filename", "path", "local_path", "text", "error", "fetched_at")
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "id")
    with connect() as conn:
        conn.execute(
            f"INSERT INTO attachments ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}",
            [att.get(c) for c in cols],
        )


def save_summary(entry_id: str, data: dict[str, Any]) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO summaries "
            "(entry_id, summary, detail, category, action, due_date, headline, model, prompt_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(entry_id) DO UPDATE SET "
            "summary=excluded.summary, detail=excluded.detail, category=excluded.category, "
            "action=excluded.action, due_date=excluded.due_date, headline=excluded.headline, "
            "model=excluded.model, prompt_hash=excluded.prompt_hash, created_at=excluded.created_at",
            [
                entry_id, data["summary"], data.get("detail"), data.get("category"),
                data.get("action"), data.get("due_date"), data.get("headline"),
                data.get("model"), data.get("prompt_hash"), data["created_at"],
            ],
        )


def entries_needing_summary(prompt_hash: str) -> list[sqlite3.Row]:
    """Poster uden resume - eller hvor resumeet stammer fra en aeldre prompt."""
    with connect() as conn:
        return list(conn.execute(
            "SELECT e.* FROM entries e "
            "LEFT JOIN summaries s ON s.entry_id = e.id "
            "WHERE s.entry_id IS NULL OR s.prompt_hash IS NOT ? "
            "ORDER BY e.published DESC",
            (prompt_hash,),
        ))


def attachment_text(entry_id: str) -> list[sqlite3.Row]:
    with connect() as conn:
        return list(conn.execute(
            "SELECT filename, text FROM attachments WHERE entry_id = ? AND text IS NOT NULL",
            (entry_id,),
        ))


def attachment_images(entry_id: str) -> list[sqlite3.Row]:
    """Billedbilag der blev hentet uden fejl - input til LLM'ens billedkanal."""
    with connect() as conn:
        return list(conn.execute(
            "SELECT filename, local_path FROM attachments "
            "WHERE entry_id = ? AND local_path IS NOT NULL AND text IS NULL AND error IS NULL",
            (entry_id,),
        ))


def published_hashes() -> dict[str, str]:
    with connect() as conn:
        return {r["key"]: r["hash"] for r in conn.execute("SELECT key, hash FROM published")}


def mark_published(key: str, digest: str, when: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO published (key, hash, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET hash=excluded.hash, updated_at=excluded.updated_at",
            (key, digest, when),
        )


def forget_published() -> None:
    """Glem hvad der er publiceret, saa naeste koersel sender alt igen."""
    with connect() as conn:
        conn.execute("DELETE FROM published")


def feed_items(limit: int = 100) -> list[dict[str, Any]]:
    """Poster med resume, nyeste foerst - grundlaget for baade RSS og skaerm."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT e.id, e.kind, e.title, e.published, e.author, e.owner, e.contexts, "
            "       e.important, e.body_text, "
            "       s.summary, s.detail, s.category, s.action, s.due_date, s.headline "
            "FROM entries e JOIN summaries s ON s.entry_id = e.id "
            "ORDER BY e.published DESC LIMIT ?",
            (limit,),
        ).fetchall()
    items = []
    for r in rows:
        item = dict(r)
        item["contexts"] = json.loads(item["contexts"] or "[]")
        item["attachments"] = [
            dict(a) for a in attachment_text(item["id"])
        ]
        items.append(item)
    return items
