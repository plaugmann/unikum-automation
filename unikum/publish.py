"""Pusher det faerdige feed til Cloudflare-workeren.

Hentningen bliver hjemme, fordi BankID-sessionen ikke kan flytte. Men det
faerdige feed er en lille statisk fil, saa den lagges ud, hvor telefonerne
kan naa den. Vi pusher frem for at aabne hul ind til hjemmenettet.

Er CLOUD_URL ikke sat, springes publiceringen stille over - saa virker en
ren lokal opsaetning uden aendringer.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import httpx

from . import config, db, render

TIMEOUT = 30.0


class PublishError(RuntimeError):
    pass


def _put(
    client: httpx.Client,
    name: str,
    body: bytes,
    content_type: str,
    *,
    kendte: dict[str, str] | None = None,
) -> dict | None:
    """Laeg en fil op. Springer over, hvis den allerede ligger der uaendret.

    KV har et dagligt loft paa skrivninger, og med én side pr. besked ville
    en koersel hver time ellers skrive det samme igen og igen.
    """
    # Hashen sendes med, saa workeren kan svare 304 uden at regne paa
    # indholdet ved hver eneste laesning.
    digest = hashlib.sha256(body).hexdigest()[:32]
    if kendte is not None and kendte.get(name) == digest:
        return None
    res = client.put(
        f"{config.CLOUD_URL.rstrip('/')}/{name}",
        content=body,
        headers={
            "Authorization": f"Bearer {config.CLOUD_PUSH_TOKEN}",
            "Content-Type": content_type,
            "X-Content-Hash": digest,
        },
    )
    if res.status_code != 200:
        raise PublishError(f"{name}: HTTP {res.status_code} - {res.text.strip()[:200]}")
    if kendte is not None:
        db.mark_published(name, digest, datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return {"navn": name, "bytes": len(body), "hash": digest}


def publish(limit: int = 60, count: int = 8, maxlen: int = 120) -> list[dict]:
    """Byg begge udgaver og laeg dem op. Returnerer hvad der blev sendt."""
    if not config.CLOUD_URL:
        return []
    if not config.CLOUD_PUSH_TOKEN:
        raise PublishError("CLOUD_PUSH_TOKEN mangler i .env")

    feed = render.build_feed(limit=limit)
    display = json.dumps(
        render.build_display(count=count, maxlen=maxlen),
        ensure_ascii=False,
    ).encode("utf-8")

    page = render.build_page(limit=limit)
    kendte = db.published_hashes()
    HTML = "text/html; charset=utf-8"
    sendt: list[dict] = []

    with httpx.Client(timeout=TIMEOUT) as client:
        # Én side pr. besked, saa et klik i RSS-appen viser netop den besked.
        for item in db.feed_items(limit=limit):
            res = _put(
                client, f"item/{item['id']}.html",
                render.build_item_page(item), HTML, kendte=kendte,
            )
            if res:
                sendt.append(res)

        for navn, krop, type_ in (
            ("feed.xml", feed, "application/rss+xml; charset=utf-8"),
            ("items.html", page, HTML),
            ("display.json", display, "application/json; charset=utf-8"),
        ):
            res = _put(client, navn, krop, type_, kendte=kendte)
            if res:
                sendt.append(res)

    return sendt


def verify() -> dict:
    """Hent feedet tilbage gennem den offentlige URL, som en telefon ville."""
    if not config.CLOUD_URL:
        raise PublishError("CLOUD_URL mangler i .env")
    url = f"{config.CLOUD_URL.rstrip('/')}/feed.xml"
    with httpx.Client(timeout=TIMEOUT) as client:
        res = client.get(url, params={"token": config.FEED_TOKEN})
        uden = client.get(url, params={"token": "forkert"})
    return {
        "status": res.status_code,
        "bytes": len(res.content),
        "etag": res.headers.get("ETag"),
        "afviser_forkert_token": uden.status_code == 403,
    }
