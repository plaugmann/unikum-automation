"""Webserver: RSS til mennesker, JSON til e-ink-skaermen.

Begge udgange laeser fra samme tabel, saa de aldrig kan komme ud af trit.
Adgang kraever en token i query-strengen. Det er bevidst simpelt - feedet
skal kunne hentes af en ESP32 paa hjemmenettet, og en ESP32 er daarlig til
baade TLS-certifikater og HTTP-basic-auth.
"""
from __future__ import annotations

import hmac
from html import escape
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, Response

from . import config, db

app = FastAPI(title="Unikum-feed", docs_url=None, redoc_url=None)


def _code_version() -> str:
    """Tidsstempel for den kode, der faktisk koerer.

    Uvicorn indlaeser dette modul én gang ved opstart. Redigerer man feedet
    og glemmer at genstarte, serveres den gamle udgave videre - og det ligner
    en fejl i indholdet i stedet for en gammel proces. Stemplet i <generator>
    goer forskellen synlig med det samme.
    """
    from datetime import datetime

    mtime = Path(__file__).stat().st_mtime
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")

CATEGORY_LABEL = {
    "LEKTIE": "Lektie",
    "MØDE": "Møde",
    "UDFLUGT": "Udflugt",
    "SKEMA": "Skema",
    "PRAKTISK": "Praktisk",
    "INFO": "Info",
}


def _check_token(token: str) -> None:
    if not config.FEED_TOKEN:
        raise HTTPException(500, "FEED_TOKEN er ikke sat i .env")
    # compare_digest undgaar at svartiden roeber, hvor langt et gaet naaede.
    if not hmac.compare_digest(token, config.FEED_TOKEN):
        raise HTTPException(403, "Forkert token")


def _parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


def _entry_url(item: dict) -> str:
    """Dybt link tilbage til beskeden i Unikum."""
    return (
        f"{config.BASE}/unikum/infodok/messages.html"
        f"?__pid={config.GUARDIAN_PID}#/message/{item['id']}"
    )


def _text_to_html(text: str) -> str:
    """Modellen skriver ren tekst med tomme linjer og "- " som punkttegn.

    Vi oversaetter til HTML her frem for at bede modellen om markup - saa kan
    den koncentrere sig om indholdet, og vi slipper for at stole paa markup
    fra en generativ model i et feed.

    Et afsnit kan sagtens blande prosa og punkter, fx en indledende linje
    efterfulgt af en liste. Derfor behandles linjerne enkeltvis i stedet for
    at kraeve at hele afsnittet er det ene eller det andet.
    """
    BULLETS = ("- ", "* ", "• ")
    blocks: list[str] = []
    paragraph: list[str] = []
    bullets: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append("<p>" + escape(" ".join(paragraph)) + "</p>")
            paragraph.clear()

    def flush_bullets() -> None:
        if bullets:
            items = "".join(f"<li>{escape(b)}</li>" for b in bullets)
            blocks.append(f"<ul>{items}</ul>")
            bullets.clear()

    for raw_block in text.split("\n\n"):
        for line in raw_block.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith(BULLETS):
                # En liste afslutter den prosa, der stod foran den.
                flush_paragraph()
                bullets.append(line[2:].strip())
            else:
                flush_bullets()
                paragraph.append(line)
        flush_bullets()
        flush_paragraph()

    flush_bullets()
    flush_paragraph()
    return "".join(blocks)


def _description(item: dict) -> str:
    # Den lange tekst er skrevet til at staa alene, saa den erstatter den
    # korte - ellers ville laeseren se det samme sagt to gange.
    body = item.get("detail") or item["summary"]
    parts = [_text_to_html(body)]
    if item.get("action"):
        parts.append(f"<p><strong>Skal gøres:</strong> {escape(item['action'])}</p>")
    if item.get("due_date"):
        parts.append(f"<p><strong>Dato:</strong> {escape(item['due_date'])}</p>")

    meta = []
    if item.get("title") and item["title"].strip() != (item.get("headline") or "").strip():
        meta.append(f"Emne: {item['title']}")
    if item.get("author"):
        meta.append(f"Fra: {item['author']}")
    if item.get("owner"):
        meta.append(f"Gruppe: {item['owner']}")
    names = [a["filename"] for a in item.get("attachments", [])]
    if names:
        meta.append(f"Bilag: {', '.join(names)}")
    if meta:
        parts.append("<p><small>" + " &middot; ".join(meta) + "</small></p>")
    return "".join(parts)


@app.get("/feed.xml")
def feed(token: str = Query(...), limit: int = Query(60, ge=1, le=300)) -> Response:
    _check_token(token)
    items = db.feed_items(limit=limit)

    rss = ET.Element("rss", {"version": "2.0",
                             "xmlns:dc": "http://purl.org/dc/elements/1.1/"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "Unikum – Max"
    ET.SubElement(channel, "link").text = f"{config.BASE}/unikum/start.html"
    ET.SubElement(channel, "description").text = (
        "Beskeder fra Glasbruksskolan og Kryssaren, opsummeret på dansk"
    )
    ET.SubElement(channel, "language").text = "da-DK"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(
        datetime.now(timezone.utc)
    )
    ET.SubElement(channel, "generator").text = f"unikum-automation (kode: {_code_version()})"

    for item in items:
        node = ET.SubElement(channel, "item")
        prefix = CATEGORY_LABEL.get(item["category"], item["category"] or "")
        # Laerernes egne emnelinjer er ofte intetsigende ("utv.samtal"), saa
        # vi viser LLM'ens overskrift. Originaltitlen staar i metalinjen.
        headline = item.get("headline") or item["title"]
        ET.SubElement(node, "title").text = f"[{prefix}] {headline}".strip()
        ET.SubElement(node, "link").text = _entry_url(item)
        # Stabil guid: samme post skal aldrig dukke op som ny i laeseren.
        guid = ET.SubElement(node, "guid", {"isPermaLink": "false"})
        guid.text = f"unikum-{item['id']}"
        ET.SubElement(node, "pubDate").text = format_datetime(_parse_dt(item["published"]))
        if item.get("category"):
            ET.SubElement(node, "category").text = item["category"]
        if item.get("author"):
            ET.SubElement(node, "dc:creator").text = item["author"]
        ET.SubElement(node, "description").text = _description(item)

    xml = ET.tostring(rss, encoding="utf-8", xml_declaration=True)
    return Response(content=xml, media_type="application/rss+xml; charset=utf-8")


@app.get("/display.json")
def display(
    token: str = Query(...),
    count: int = Query(5, ge=1, le=20),
    maxlen: int = Query(120, ge=20, le=500),
) -> JSONResponse:
    """Fladtrykt udgave til e-ink-skaermen.

    ESP32'en skal kunne noejes med ét HTTP-kald og en simpel JSON-parse, saa
    alt er forkortet og formateret paa forhaand her i stedet for paa devicet.
    """
    _check_token(token)
    items = db.feed_items(limit=count)

    out = []
    for item in items:
        line = item.get("action") or item["summary"]
        if len(line) > maxlen:
            line = line[: maxlen - 1].rstrip() + "…"
        out.append({
            "id": item["id"],
            "date": (item["published"] or "")[:10],
            "category": CATEGORY_LABEL.get(item["category"], item["category"] or ""),
            "headline": item.get("headline") or item["title"],
            "line": line,
            "due": item.get("due_date"),
            "action": bool(item.get("action")),
        })

    return JSONResponse({
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(out),
        "items": out,
    })


@app.get("/healthz")
def healthz() -> dict:
    from . import auth

    return {"ok": True, "session": auth.status()}
