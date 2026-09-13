"""Bygger feedets to udgaver ud fra databasen.

Laa foer inde i webserverens endpoints, men skal kunne koeres uden en server,
fordi vi ogsaa pusher resultatet til skyen. Derfor er genereringen skilt ud
her, og baade serve.py og publish.py bruger den samme kode - saa den lokale
og den offentlige udgave aldrig kan komme til at afvige.
"""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import format_datetime
from html import escape
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from . import config, db

CATEGORY_LABEL = {
    "LEKTIE": "Lektie",
    "MØDE": "Møde",
    "UDFLUGT": "Udflugt",
    "SKEMA": "Skema",
    "PRAKTISK": "Praktisk",
    "INFO": "Info",
}

BULLET_PREFIXES = ("- ", "* ", "• ")


def code_version() -> str:
    """Tidsstempel for den kode, der faktisk koerer.

    Uvicorn indlaeser modulerne én gang ved opstart. Redigerer man feedet og
    glemmer at genstarte, serveres den gamle udgave videre - og det ligner en
    fejl i indholdet i stedet for en gammel proces. Stemplet i <generator>
    goer forskellen synlig med det samme.
    """
    mtime = Path(__file__).stat().st_mtime
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")


def _parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


def entry_url(item: dict) -> str:
    """Dybt link tilbage til beskeden i Unikum."""
    return (
        f"{config.BASE}/unikum/infodok/messages.html"
        f"?__pid={config.GUARDIAN_PID}#/message/{item['id']}"
    )


def text_to_html(text: str) -> str:
    """Modellen skriver ren tekst med tomme linjer og "- " som punkttegn.

    Vi oversaetter til HTML her frem for at bede modellen om markup - saa kan
    den koncentrere sig om indholdet, og vi slipper for at stole paa markup
    fra en generativ model i et feed.

    Et afsnit blander gerne prosa og punkter, fx en indledende linje
    efterfulgt af en liste. Derfor behandles linjerne enkeltvis, og en tom
    linje afslutter det, der er i gang.
    """
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

    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            flush_bullets()
            flush_paragraph()
        elif line.startswith(BULLET_PREFIXES):
            # En liste afslutter den prosa, der stod foran den.
            flush_paragraph()
            bullets.append(line[2:].strip())
        else:
            flush_bullets()
            paragraph.append(line)

    flush_bullets()
    flush_paragraph()
    return "".join(blocks)


def description(item: dict) -> str:
    # Den lange tekst er skrevet til at staa alene, saa den erstatter den
    # korte - ellers ville laeseren se det samme sagt to gange.
    parts = [text_to_html(item.get("detail") or item["summary"])]
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


def build_feed(limit: int = 60) -> bytes:
    """RSS 2.0 til en almindelig laeser."""
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
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.now(timezone.utc))
    ET.SubElement(channel, "generator").text = f"unikum-automation (kode: {code_version()})"

    for item in items:
        node = ET.SubElement(channel, "item")
        prefix = CATEGORY_LABEL.get(item["category"], item["category"] or "")
        # Laerernes egne emnelinjer er ofte intetsigende ("utv.samtal"), saa
        # vi viser LLM'ens overskrift. Originaltitlen staar i metalinjen.
        headline = item.get("headline") or item["title"]
        ET.SubElement(node, "title").text = f"[{prefix}] {headline}".strip()
        ET.SubElement(node, "link").text = entry_url(item)
        # Stabil guid: samme post skal aldrig dukke op som ny i laeseren.
        guid = ET.SubElement(node, "guid", {"isPermaLink": "false"})
        guid.text = f"unikum-{item['id']}"
        ET.SubElement(node, "pubDate").text = format_datetime(_parse_dt(item["published"]))
        if item.get("category"):
            ET.SubElement(node, "category").text = item["category"]
        if item.get("author"):
            ET.SubElement(node, "dc:creator").text = item["author"]
        ET.SubElement(node, "description").text = description(item)

    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)


def build_display(count: int = 5, maxlen: int = 120) -> dict[str, Any]:
    """Fladtrykt udgave til e-ink-skaermen.

    ESP32'en skal kunne noejes med ét HTTP-kald og en simpel JSON-parse, saa
    alt er forkortet og formateret paa forhaand her i stedet for paa devicet.
    """
    out = []
    for item in db.feed_items(limit=count):
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

    return {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(out),
        "items": out,
    }
