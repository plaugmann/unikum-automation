"""Hent beskeder og bilag fra Unikum ned i den lokale database.

Pipelinen er bevidst idempotent: den kan koeres saa tit man vil, og henter
kun det, den ikke allerede har. Det goer polling billigt og genkoersler
ufarlige.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from . import api, config, db, extract


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _person(node: dict | None) -> str | None:
    if not node:
        return None
    name = f"{node.get('firstName', '')} {node.get('lastName', '')}".strip()
    return name or None


def _owner_name(node: dict | None) -> str | None:
    if not node:
        return None
    return node.get("fullName") or _person(node)


def _contexts(entry: dict) -> list[str]:
    """Hvilke klasser/grupper beskeden blev sendt til - fx 4C eller Kryssaren."""
    edges = ((entry.get("recipientContexts") or {}).get("edges")) or []
    names = []
    for edge in edges:
        node = edge.get("node") or {}
        name = node.get("fullName")
        if name and name not in names:
            names.append(name)
    return names


def _store_attachments(client: api.Client, entry_id: str, attachments: list[dict]) -> int:
    """Hent hvert bilag, udtraek tekst, og gem baade fil og tekst.

    En enkelt defekt PDF maa ikke vaelte hele koerslen, saa fejl gemmes paa
    raekken i stedet for at blive kastet videre.
    """
    saved = 0
    for att in attachments or []:
        path = att.get("path")
        if not path:
            continue
        filename = extract.filename_from_path(path)
        local = config.ATTACHMENT_DIR / filename
        row = {
            "id": str(att["_id"]),
            "entry_id": entry_id,
            "filename": filename,
            "path": path,
            "local_path": None,
            "text": None,
            "error": None,
            "fetched_at": _now(),
        }
        try:
            data = client.download_attachment(path)
            local.write_bytes(data)
            row["local_path"] = str(local)
            if extract.is_image(filename):
                # Billeder kan ikke blive til tekst her - de sendes til
                # LLM'en som billeder i opsummeringstrinnet.
                row["text"] = None
            else:
                row["text"] = extract.attachment_to_text(filename, data)
            saved += 1
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        db.upsert_attachment(row)
    return saved


def sync(*, since: str | None = None, refetch: bool = False) -> dict[str, int]:
    """Hent nye beskeder. Returnerer en optaelling til logning."""
    db.init()
    known = set() if refetch else db.known_entry_ids()
    stats = {"set": 0, "nye": 0, "bilag": 0}

    with api.Client() as client:
        for node in client.list_entries(since=since):
            stats["set"] += 1
            entry_id = str(node["_id"])
            if entry_id in known:
                continue

            detail = client.get_entry(entry_id)
            body_html = detail.get("body")
            db.upsert_entry({
                "id": entry_id,
                "kind": detail.get("informationType") or "MESSAGE",
                "title": (detail.get("title") or "").strip(),
                "body_html": body_html,
                "body_text": extract.html_to_text(body_html),
                "published": detail.get("published"),
                "last_edited": detail.get("lastEdited"),
                "author": _person(detail.get("author")),
                "owner": _owner_name(detail.get("owner")),
                "contexts": json.dumps(_contexts(detail), ensure_ascii=False),
                "important": 1 if detail.get("important") else 0,
                "raw": json.dumps(detail, ensure_ascii=False),
                "fetched_at": _now(),
            })
            stats["nye"] += 1
            stats["bilag"] += _store_attachments(client, entry_id, detail.get("attachments") or [])

    return stats
